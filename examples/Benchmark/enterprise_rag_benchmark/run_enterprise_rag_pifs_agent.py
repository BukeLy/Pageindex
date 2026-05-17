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

from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import (
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


class PIFSAgentAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(description="Final answer based only on opened PageIndex FileSystem documents.")
    document_ids: list[str] = Field(
        description=(
            "Exact EnterpriseRAG dsid_* ids copied from a document_id line or the second "
            "column of ls/grep output. Do not include file_ref values or rewritten ids."
        )
    )


def main() -> int:
    benchmark_dir = Path(__file__).resolve().parent
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(benchmark_dir / ".env")

    args = parse_args()
    dataset_root = (benchmark_dir / args.dataset).resolve()
    questions_path = dataset_root / "questions.jsonl"
    run_dir = benchmark_dir / "runs" / args.run_name
    workspace = (benchmark_dir / args.workspace).resolve()

    if not (workspace / "filesystem.sqlite").exists():
        raise SystemExit(f"workspace is not registered: {workspace}")

    if args.base_url:
        os.environ["OPENAI_BASE_URL"] = args.base_url

    run_dir.mkdir(parents=True, exist_ok=True)
    filesystem = PageIndexFileSystem(workspace=workspace)
    questions = select_questions(
        load_questions(questions_path),
        question_ids=args.question_ids,
        max_questions=args.max_questions,
    )

    results = []
    for question in questions:
        print(f"\n[benchmark question]\n{question.question_id}: {question.question}", flush=True)
        result = run_question(
            filesystem,
            question,
            model=args.model,
            skip_agent=args.skip_agent,
            verbose=args.verbose,
            stream_mode=args.stream_mode,
            reasoning_effort=args.reasoning_effort,
            reasoning_summary=args.reasoning_summary,
        )
        results.append(result)
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

    results_path = run_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    summary = {
        "questions": len(questions),
        "question_ids": [question.question_id for question in questions],
        "source_types": sorted({item for q in questions for item in q.source_types}),
        "workspace": str(workspace),
        "dataset_root": str(dataset_root),
        "model": args.model,
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "stream_mode": args.stream_mode,
        "reasoning_effort": args.reasoning_effort,
        "reasoning_summary": args.reasoning_summary,
        "doc_hit_rate": (
            sum(1 for result in results if result["doc_hit"]) / len(results) if results else 0
        ),
        "skip_agent": args.skip_agent,
        "results_path": str(results_path),
    }
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n[benchmark run summary]", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EnterpriseRAG questions through an existing PIFS workspace")
    parser.add_argument("--dataset", default="dataset")
    parser.add_argument("--workspace", default="runs/pifs-workspace/workspace")
    parser.add_argument("--run-name", default="pifs-agent-smoke")
    parser.add_argument("--question-ids", default=",".join(DEFAULT_QUESTION_IDS))
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--model", default=os.environ.get("PIFS_AGENT_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
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
    skip_agent: bool,
    verbose: bool,
    stream_mode: str,
    reasoning_effort: str | None,
    reasoning_summary: str | None,
) -> dict[str, Any]:
    prompt = agent_prompt(question)
    raw_output = ""
    error = None
    agent_log: list[dict[str, Any]] = []
    parsed = {"answer": "", "document_ids": []}
    if skip_agent:
        error = "skipped"
    else:
        try:
            raw_output = run_pifs_agent(
                filesystem,
                prompt,
                model=model,
                root="/",
                verbose=verbose,
                stream_mode=stream_mode,
                reasoning_effort=reasoning_effort,
                reasoning_summary=reasoning_summary,
                output_type=PIFSAgentAnswer,
                agent_log=agent_log,
            )
            parsed = parse_agent_json(raw_output)
        except Exception as exc:  # noqa: BLE001 - benchmark runner records failures.
            error = f"{type(exc).__name__}: {exc}"
    expected = set(question.expected_doc_ids)
    document_ids = list(dict.fromkeys(str(item) for item in parsed.get("document_ids", [])))
    return {
        "question_id": question.question_id,
        "source_types": question.source_types,
        "question": question.question,
        "expected_doc_ids": question.expected_doc_ids,
        "answer": parsed.get("answer", ""),
        "document_ids": document_ids,
        "doc_hit": bool(expected.intersection(document_ids)),
        "raw_output": raw_output,
        "agent_log": agent_log,
        "error": error,
    }


def agent_prompt(question: EnterpriseRAGQuestion) -> str:
    return f"""
Question ID: {question.question_id}
Source types: {", ".join(question.source_types)}
Question: {question.question}

Use the PageIndex virtual shell only. Command output is shell-like plain text.
Start with folder inspection using `ls` or `tree`. When `grep -R` on a folder
returns folder matches, choose a narrower folder and run `grep -R` again there.
Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only after refs appear should you use `grep` on a ref for line evidence and
then `cat <ref> --all` for the final candidate leaf documents. Do not answer
before a successful `cat --all` call.

Use the configured structured output schema.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of ls/grep output. Do not include file_ref values or rewritten ids.
""".strip()


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
    return {"answer": "" if answer is None else str(answer), "document_ids": document_ids}


if __name__ == "__main__":
    raise SystemExit(main())
