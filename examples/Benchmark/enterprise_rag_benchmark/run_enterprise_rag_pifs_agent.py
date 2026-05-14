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

from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import (
    EnterpriseRAGQuestion,
    load_questions,
)
from pageindex.filesystem import PageIndexFileSystem
from pageindex.filesystem.agent import run_pifs_agent


DEFAULT_QUESTION_IDS = ["qst_0001", "qst_0002", "qst_0004", "qst_0011", "qst_0012"]


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
        result = run_question(
            filesystem,
            question,
            model=args.model,
            skip_agent=args.skip_agent,
            verbose=args.verbose,
        )
        results.append(result)
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
        "doc_hit_rate": (
            sum(1 for result in results if result["doc_hit"]) / len(results) if results else 0
        ),
        "skip_agent": args.skip_agent,
        "results_path": str(results_path),
    }
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
) -> dict[str, Any]:
    prompt = agent_prompt(question)
    raw_output = ""
    error = None
    parsed = {"answer": "", "document_ids": []}
    if skip_agent:
        error = "skipped"
    else:
        try:
            raw_output = run_pifs_agent(filesystem, prompt, model=model, root="/", verbose=verbose)
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
        "error": error,
    }


def agent_prompt(question: EnterpriseRAGQuestion) -> str:
    return f"""
Question ID: {question.question_id}
Source types: {", ".join(question.source_types)}
Question: {question.question}

Use the PageIndex virtual shell only. Start with folder inspection, search
within the relevant source folder, and open full leaf documents with `cat --all`
before answering. Your first content search should use the complete Question
text with `grep -R` in the source folder; refine only if the top results do not
contain enough evidence.

Return final output as a single JSON object only:
{{"answer":"...","document_ids":["dsid_..."]}}

Only include document_ids that appeared in tool output as external_id/document_id.
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
