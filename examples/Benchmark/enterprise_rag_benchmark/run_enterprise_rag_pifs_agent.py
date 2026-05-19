from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BENCHMARK_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BENCHMARK_DIR / "prompts"

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import (
    EnterpriseRAGBenchmark,
    EnterpriseRAGQuestion,
    load_questions,
)
from pageindex.filesystem import PageIndexFileSystem
from pageindex.filesystem.agent import (
    AGENT_STREAM_MODE_CHOICES,
    REASONING_EFFORT_CHOICES,
    REASONING_SUMMARY_CHOICES,
    run_pifs_agent,
)


DEFAULT_QUESTION_IDS = ["qst_0001", "qst_0002", "qst_0004", "qst_0011", "qst_0012"]
RETRIEVAL_MODE_CHOICES = ["hybrid", "folder", "metadata"]


class PIFSCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = ""
    source_path: str = ""
    ref: str = ""
    line_start: int = 0
    line_end: int = 0
    quote: str = ""


class PIFSAgentAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(description="Final answer based only on opened PageIndex FileSystem documents.")
    document_ids: list[str] = Field(
        description=(
            "Exact EnterpriseRAG dsid_* ids copied from a document_id line or the second "
            "column of ls/grep output. Do not include file_ref values or rewritten ids."
        )
    )
    citations: list[PIFSCitation] = Field(
        default_factory=list,
        description=(
            "Optional internal provenance rows. Each row should include document_id, source_path, "
            "ref, line_start, line_end, and quote when available."
        ),
    )


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BENCHMARK_DIR / ".env")

    args = parse_args()
    dataset_root = (BENCHMARK_DIR / args.dataset).resolve()
    questions_path = dataset_root / "questions.jsonl"
    run_dir = BENCHMARK_DIR / "runs" / args.run_name
    workspace = (BENCHMARK_DIR / args.workspace).resolve()

    if not (workspace / "filesystem.sqlite").exists():
        raise SystemExit(f"workspace is not registered: {workspace}")

    if args.base_url:
        os.environ["OPENAI_BASE_URL"] = args.base_url

    run_dir.mkdir(parents=True, exist_ok=True)
    filesystem = PageIndexFileSystem(workspace=workspace)
    system_prompt = read_prompt_file(args.system_prompt_file)
    question_prompt_file = args.question_prompt_file or default_question_prompt_file(args.retrieval_mode)
    question_prompt_template = read_prompt_file(question_prompt_file)
    questions = select_questions(
        load_questions(questions_path),
        question_ids="" if args.all_questions else args.question_ids,
        max_questions=args.max_questions,
    )

    results_path = run_dir / "results.jsonl"
    answers_path = run_dir / "answers.jsonl"
    results = []
    with (
        results_path.open("w", encoding="utf-8", buffering=1) as results_file,
        answers_path.open("w", encoding="utf-8", buffering=1) as answers_file,
    ):
        for question in questions:
            print(f"\n[benchmark question]\n{question.question_id}: {question.question}", flush=True)
            result = run_question(
                filesystem,
                question,
                model=args.model,
                retrieval_mode=args.retrieval_mode,
                system_prompt=system_prompt,
                question_prompt_template=question_prompt_template,
                skip_agent=args.skip_agent,
                verbose=args.verbose,
                stream_mode=args.stream_mode,
                reasoning_effort=args.reasoning_effort,
                reasoning_summary=args.reasoning_summary,
                max_seconds=args.max_seconds,
            )
            results.append(result)
            results_file.write(json.dumps(result, ensure_ascii=False) + "\n")
            answers_file.write(
                json.dumps(EnterpriseRAGBenchmark.answer_row(result), ensure_ascii=False) + "\n"
            )
            print("\n[benchmark question result]", flush=True)
            print(
                json.dumps(
                    {
                        "question_id": result["question_id"],
                        "document_ids": result["document_ids"],
                        "expected_doc_ids": result["expected_doc_ids"],
                        "doc_hit": result["doc_hit"],
                        "error": result["error"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            write_progress_summary(
                run_dir / "progress_summary.json",
                results=results,
                questions=questions,
                answers_path=answers_path,
                results_path=results_path,
            )

    timeout_count = sum(
        1
        for result in results
        if result.get("error") and "MaxSecondsExceeded" in str(result.get("error"))
    )
    seconds_values = [float(result.get("seconds") or 0) for result in results]
    summary = {
        "questions": len(questions),
        "question_ids": [question.question_id for question in questions],
        "source_types": sorted({item for q in questions for item in q.source_types}),
        "workspace": str(workspace),
        "dataset_root": str(dataset_root),
        "model": args.model,
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "stream_mode": args.stream_mode,
        "retrieval_mode": args.retrieval_mode,
        "system_prompt_file": str(Path(args.system_prompt_file).resolve()),
        "question_prompt_file": str(Path(question_prompt_file).resolve()),
        "reasoning_effort": args.reasoning_effort,
        "reasoning_summary": args.reasoning_summary,
        "max_seconds": args.max_seconds,
        "timeout_count": timeout_count,
        "avg_seconds": sum(seconds_values) / len(seconds_values) if seconds_values else 0,
        "doc_hit_rate": (
            sum(1 for result in results if result["doc_hit"]) / len(results) if results else 0
        ),
        "skip_agent": args.skip_agent,
        "answers_path": str(answers_path),
        "results_path": str(results_path),
    }
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n[benchmark run summary]", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def write_progress_summary(
    path: Path,
    *,
    results: list[dict[str, Any]],
    questions: list[EnterpriseRAGQuestion],
    answers_path: Path,
    results_path: Path,
) -> None:
    timeout_count = sum(
        1
        for result in results
        if result.get("error") and "MaxSecondsExceeded" in str(result.get("error"))
    )
    seconds_values = [float(result.get("seconds") or 0) for result in results]
    path.write_text(
        json.dumps(
            {
                "completed_questions": len(results),
                "total_questions": len(questions),
                "last_question_id": results[-1]["question_id"] if results else None,
                "doc_hit_rate_so_far": (
                    sum(1 for result in results if result["doc_hit"]) / len(results)
                    if results
                    else 0
                ),
                "timeout_count_so_far": timeout_count,
                "avg_seconds_so_far": (
                    sum(seconds_values) / len(seconds_values) if seconds_values else 0
                ),
                "answers_path": str(answers_path),
                "results_path": str(results_path),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EnterpriseRAG questions through an existing PIFS workspace")
    parser.add_argument("--dataset", default="dataset")
    parser.add_argument("--workspace", default="runs/pifs-workspace/workspace")
    parser.add_argument("--run-name", default="pifs-agent-smoke")
    parser.add_argument("--question-ids", default=",".join(DEFAULT_QUESTION_IDS))
    parser.add_argument("--all-questions", action="store_true")
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--model", default=os.environ.get("PIFS_AGENT_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--max-seconds", type=float, default=float(os.environ.get("PIFS_MAX_SECONDS", "60")))
    parser.add_argument(
        "--retrieval-mode",
        default=os.environ.get("PIFS_RETRIEVAL_MODE", "hybrid"),
        choices=RETRIEVAL_MODE_CHOICES,
        help="Constrain agent retrieval strategy: hybrid, folder, or metadata.",
    )
    parser.add_argument(
        "--system-prompt-file",
        default=os.environ.get("PIFS_SYSTEM_PROMPT_FILE", str(PROMPTS_DIR / "pifs_agent_system.md")),
        help="Path to the editable system prompt used by the PIFS agent.",
    )
    parser.add_argument(
        "--question-prompt-file",
        default=os.environ.get("PIFS_QUESTION_PROMPT_FILE", ""),
        help="Path to an editable question prompt template. Defaults to retrieval-mode prompt.",
    )
    parser.add_argument("--skip-agent", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--stream-mode",
        default=os.environ.get("PIFS_AGENT_STREAM_MODE", "off"),
        choices=AGENT_STREAM_MODE_CHOICES,
        help="Stream agent internals: off, tools, model output/think, all, or aliases like think/debug.",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=os.environ.get("PIFS_AGENT_REASONING_EFFORT"),
        choices=REASONING_EFFORT_CHOICES,
        help="Enable reasoning for models that support it: none, minimal, low, medium, high, or xhigh.",
    )
    parser.add_argument(
        "--reasoning-summary",
        default=os.environ.get("PIFS_AGENT_REASONING_SUMMARY"),
        choices=REASONING_SUMMARY_CHOICES,
        help="Request visible reasoning summary deltas when supported: auto, concise, detailed, or none.",
    )
    return parser.parse_args()


def select_questions(
    questions: list[EnterpriseRAGQuestion],
    *,
    question_ids: str,
    max_questions: int,
) -> list[EnterpriseRAGQuestion]:
    by_id = {question.question_id: question for question in questions}
    selected_ids = [item.strip() for item in question_ids.split(",") if item.strip()]
    selected = [by_id[item] for item in selected_ids if item in by_id]
    if not selected:
        selected = questions
    if max_questions > 0:
        selected = selected[:max_questions]
    return selected


def run_question(
    filesystem: PageIndexFileSystem,
    question: EnterpriseRAGQuestion,
    *,
    model: str,
    retrieval_mode: str,
    system_prompt: str,
    question_prompt_template: str,
    skip_agent: bool,
    verbose: bool,
    stream_mode: str,
    reasoning_effort: str | None,
    reasoning_summary: str | None,
    max_seconds: float,
) -> dict[str, Any]:
    prompt = agent_prompt(
        question,
        retrieval_mode=retrieval_mode,
        template=question_prompt_template,
    )
    raw_output = ""
    error = None
    agent_log: list[dict[str, Any]] = []
    parsed = {"answer": "", "document_ids": [], "citations": []}
    started = time.time()
    if skip_agent:
        error = "skipped"
    else:
        try:
            raw_output = run_pifs_agent(
                filesystem,
                prompt,
                model=model,
                root="/",
                system_prompt=system_prompt,
                verbose=verbose,
                stream_mode=stream_mode,
                reasoning_effort=reasoning_effort,
                reasoning_summary=reasoning_summary,
                max_seconds=max_seconds,
                output_type=PIFSAgentAnswer,
                agent_log=agent_log,
            )
            parsed = parse_agent_json(raw_output)
        except Exception as exc:  # noqa: BLE001 - benchmark runner records failures.
            error = f"{type(exc).__name__}: {exc}"
    expected = set(question.expected_doc_ids)
    document_ids = list(dict.fromkeys(str(item) for item in parsed.get("document_ids", [])))
    seconds = time.time() - started
    return {
        "question_id": question.question_id,
        "source_types": question.source_types,
        "question": question.question,
        "expected_doc_ids": question.expected_doc_ids,
        "retrieval_mode": retrieval_mode,
        "answer": parsed.get("answer", ""),
        "document_ids": document_ids,
        "citations": parsed.get("citations", []),
        "doc_hit": bool(expected.intersection(document_ids)),
        "raw_output": raw_output,
        "agent_log": agent_log,
        "error": error,
        "seconds": round(seconds, 3),
    }


def agent_prompt(
    question: EnterpriseRAGQuestion,
    *,
    retrieval_mode: str = "hybrid",
    template: str | None = None,
) -> str:
    prompt_template = template or read_prompt_file(default_question_prompt_file(retrieval_mode))
    return prompt_template.format(
        question_id=question.question_id,
        question_type=question.question_type,
        source_types=", ".join(question.source_types),
        question=question.question,
        expected_doc_ids=", ".join(question.expected_doc_ids),
        retrieval_mode=retrieval_mode,
    ).strip()


def default_question_prompt_file(retrieval_mode: str) -> Path:
    mode = retrieval_mode if retrieval_mode in RETRIEVAL_MODE_CHOICES else "hybrid"
    return PROMPTS_DIR / f"pifs_question_{mode}.md"


def read_prompt_file(path: str | Path) -> str:
    prompt_path = Path(path).expanduser()
    if not prompt_path.is_absolute():
        prompt_path = (REPO_ROOT / prompt_path).resolve()
    return prompt_path.read_text(encoding="utf-8")


def parse_agent_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        data = json.loads(match.group(0)) if match else {}
    if not isinstance(data, dict):
        data = {}
    answer = data.get("answer", stripped)
    document_ids = data.get("document_ids", [])
    if not isinstance(document_ids, list):
        document_ids = []
    if not document_ids:
        document_ids = re.findall(r"dsid_[A-Za-z0-9]+", stripped)
    citations = data.get("citations", [])
    if not isinstance(citations, list):
        citations = []
    return {
        "answer": "" if answer is None else str(answer),
        "document_ids": document_ids,
        "citations": citations,
    }


if __name__ == "__main__":
    raise SystemExit(main())
