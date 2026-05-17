from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from examples.Benchmark.wixqa_benchmark.wixqa import (
    WixQAQuestion,
    load_questions,
    write_official_predictions,
)
from pageindex.filesystem import PageIndexFileSystem
from pageindex.filesystem.agent import (
    AGENT_STREAM_MODE_CHOICES,
    REASONING_EFFORT_CHOICES,
    REASONING_SUMMARY_CHOICES,
    run_pifs_agent,
)


BENCHMARK_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BENCHMARK_DIR / "prompts"
RETRIEVAL_MODE_CHOICES = ["hybrid", "folder", "metadata"]


class WixQAAgentAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(description="Final answer based only on opened WixQA articles.")
    article_ids: list[str] = Field(description="Exact WixQA article ids copied from PIFS tool output.")


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BENCHMARK_DIR / ".env")
    args = parse_args()
    if args.base_url:
        os.environ["OPENAI_BASE_URL"] = args.base_url

    workspace = (BENCHMARK_DIR / args.workspace).resolve()
    ensure_workspace(workspace)
    run_dir = BENCHMARK_DIR / "runs" / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    questions = load_questions(args.questions_jsonl or None, split=args.split)
    if args.max_questions > 0:
        questions = questions[: args.max_questions]
    filesystem = PageIndexFileSystem(workspace=workspace)
    system_prompt = read_prompt_file(args.system_prompt_file)
    question_prompt = read_prompt_file(args.question_prompt_file or default_question_prompt_file(args.retrieval_mode))

    results = []
    predictions = []
    for question in questions:
        print(f"\n[wixqa question]\n{question.question_id}: {question.question}", flush=True)
        result = run_question(
            filesystem,
            question,
            model=args.model,
            retrieval_mode=args.retrieval_mode,
            system_prompt=system_prompt,
            question_prompt_template=question_prompt,
            stream_mode=args.stream_mode,
            reasoning_effort=args.reasoning_effort,
            reasoning_summary=args.reasoning_summary,
        )
        results.append(result)
        predictions.append(
            {
                "question": question.question,
                "answer": result["answer"],
                "article_ids": result["article_ids"],
            }
        )
        print(
            json.dumps(
                {
                    "question_id": result["question_id"],
                    "article_ids": result["article_ids"],
                    "expected_article_ids": result["expected_article_ids"],
                    "article_hit": result["article_hit"],
                    "error": result["error"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    predictions_path = run_dir / "predictions.jsonl"
    results_path = run_dir / "results.jsonl"
    write_official_predictions(predictions_path, predictions)
    with results_path.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
    summary = {
        "questions": len(questions),
        "split": args.split,
        "workspace": str(workspace),
        "model": args.model,
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "retrieval_mode": args.retrieval_mode,
        "article_hit_rate": sum(1 for result in results if result["article_hit"]) / len(results) if results else 0,
        "predictions_path": str(predictions_path),
        "results_path": str(results_path),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n[wixqa run summary]", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run WixQA questions through an existing PIFS workspace")
    parser.add_argument("--workspace", default="runs/pifs-workspace/workspace")
    parser.add_argument("--run-name", default="wixqa-agent-smoke")
    parser.add_argument("--split", default="wixqa_expertwritten")
    parser.add_argument("--questions-jsonl", default="")
    parser.add_argument("--max-questions", type=int, default=20)
    parser.add_argument("--model", default=os.environ.get("PIFS_AGENT_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--retrieval-mode", default="hybrid", choices=RETRIEVAL_MODE_CHOICES)
    parser.add_argument("--system-prompt-file", default=str(PROMPTS_DIR / "pifs_agent_system.md"))
    parser.add_argument("--question-prompt-file", default="")
    parser.add_argument("--stream-mode", default=os.environ.get("PIFS_AGENT_STREAM_MODE", "off"), choices=AGENT_STREAM_MODE_CHOICES)
    parser.add_argument("--reasoning-effort", default=os.environ.get("PIFS_AGENT_REASONING_EFFORT"), choices=REASONING_EFFORT_CHOICES)
    parser.add_argument("--reasoning-summary", default=os.environ.get("PIFS_AGENT_REASONING_SUMMARY"), choices=REASONING_SUMMARY_CHOICES)
    return parser.parse_args()


def ensure_workspace(workspace: Path) -> None:
    if not (workspace / "filesystem.sqlite").exists():
        raise SystemExit(f"workspace is not registered: {workspace}")


def run_question(
    filesystem: PageIndexFileSystem,
    question: WixQAQuestion,
    *,
    model: str,
    retrieval_mode: str,
    system_prompt: str,
    question_prompt_template: str,
    stream_mode: str,
    reasoning_effort: str | None,
    reasoning_summary: str | None,
) -> dict[str, Any]:
    prompt = question_prompt_template.format(
        question_id=question.question_id,
        question=question.question,
        expected_article_ids=", ".join(question.expected_article_ids),
        retrieval_mode=retrieval_mode,
    ).strip()
    raw_output = ""
    error = None
    agent_log: list[dict[str, Any]] = []
    parsed = {"answer": "", "article_ids": []}
    try:
        raw_output = run_pifs_agent(
            filesystem,
            prompt,
            model=model,
            root="/",
            system_prompt=system_prompt,
            stream_mode=stream_mode,
            reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary,
            output_type=WixQAAgentAnswer,
            agent_log=agent_log,
        )
        parsed = parse_agent_json(raw_output)
    except Exception as exc:  # noqa: BLE001 - benchmark runner records failures.
        error = f"{type(exc).__name__}: {exc}"
    article_ids = list(dict.fromkeys(str(item) for item in parsed.get("article_ids", [])))
    expected = set(question.expected_article_ids)
    return {
        "question_id": question.question_id,
        "question": question.question,
        "gold_answer": question.answer,
        "expected_article_ids": question.expected_article_ids,
        "retrieval_mode": retrieval_mode,
        "answer": parsed.get("answer", ""),
        "article_ids": article_ids,
        "article_hit": bool(expected.intersection(article_ids)),
        "raw_output": raw_output,
        "agent_log": agent_log,
        "error": error,
    }


def parse_agent_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        data = json.loads(match.group(0)) if match else {}
    if not isinstance(data, dict):
        data = {}
    article_ids = data.get("article_ids", [])
    if not isinstance(article_ids, list):
        article_ids = []
    return {"answer": str(data.get("answer") or ""), "article_ids": article_ids}


def default_question_prompt_file(retrieval_mode: str) -> Path:
    mode = retrieval_mode if retrieval_mode in RETRIEVAL_MODE_CHOICES else "hybrid"
    return PROMPTS_DIR / f"pifs_question_{mode}.md"


def read_prompt_file(path: str | Path) -> str:
    prompt_path = Path(path).expanduser()
    if not prompt_path.is_absolute():
        prompt_path = (REPO_ROOT / prompt_path).resolve()
    return prompt_path.read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
