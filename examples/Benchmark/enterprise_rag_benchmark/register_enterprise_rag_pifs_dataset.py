from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import (
    EnterpriseRAGBenchmark,
    EnterpriseRAGQuestion,
)
from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem


DEFAULT_QUESTION_IDS = ["qst_0001", "qst_0002"]
MANUAL_METADATA_SCHEMA = {
    "fields": {
        "dataset_doc_uuid": {"type": "string", "description": "EnterpriseRAG document id"},
        "source_type": {"type": "string", "description": "Top-level EnterpriseRAG source system"},
        "title": {"type": "string", "description": "Document title"},
        "repo": {"type": "string", "description": "GitHub repository name"},
        "state": {"type": "string", "description": "GitHub pull request state"},
    }
}


def main() -> int:
    args = parse_args()
    benchmark_dir = Path(__file__).resolve().parent
    dataset_root = (benchmark_dir / args.dataset).resolve()
    source_root = dataset_root / "generated_data" / "sources"
    questions_path = dataset_root / "questions.jsonl"
    workspace = (benchmark_dir / args.workspace).resolve()

    if args.reset and workspace.parent.exists():
        shutil.rmtree(workspace.parent)
    workspace.mkdir(parents=True, exist_ok=True)

    filesystem = PageIndexFileSystem(workspace=workspace)
    filesystem._register_metadata_schema(MANUAL_METADATA_SCHEMA)
    benchmark = EnterpriseRAGBenchmark(filesystem)
    questions = select_questions(
        benchmark.load_questions(questions_path),
        question_ids=args.question_ids,
        max_questions=args.max_questions,
    )
    paths = dataset_paths_for_questions(
        source_root,
        questions,
        limit_per_source=args.limit_per_source,
    )

    started = time.time()
    file_refs = benchmark.ingest_paths(source_root, paths, batch_size=args.batch_size)
    elapsed = time.time() - started
    summary = {
        "workspace": str(workspace),
        "dataset_root": str(dataset_root),
        "question_ids": [question.question_id for question in questions],
        "source_types_indexed": sorted({item for q in questions for item in q.source_types}),
        "indexed_files": len(file_refs),
        "registered_files_total": catalog_count(workspace, "files"),
        "metadata_fields_total": catalog_count(workspace, "metadata_fields"),
        "metadata_values_total": catalog_count(workspace, "metadata_values"),
        "manual_metadata_schema": MANUAL_METADATA_SCHEMA,
        "seconds": round(elapsed, 3),
        "smoke": smoke(filesystem),
    }
    summary_path = workspace.parent / "registration_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register EnterpriseRAG data into a PIFS workspace")
    parser.add_argument("--dataset", default="dataset")
    parser.add_argument("--workspace", default="runs/pifs-workspace/workspace")
    parser.add_argument("--question-ids", default=",".join(DEFAULT_QUESTION_IDS))
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--limit-per-source", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--reset", action="store_true")
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


def dataset_paths_for_questions(
    source_root: Path,
    questions: list[EnterpriseRAGQuestion],
    *,
    limit_per_source: int,
) -> list[Path]:
    source_types = sorted({item for question in questions for item in question.source_types})
    paths: list[Path] = []
    for source_type in source_types:
        source_dir = source_root / source_type
        source_paths = sorted(source_dir.rglob("*.json"))
        if limit_per_source > 0:
            source_paths = source_paths[:limit_per_source]
        paths.extend(source_paths)
    return paths


def catalog_count(workspace: Path, table: str) -> int:
    with sqlite3.connect(workspace / "filesystem.sqlite") as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def smoke(filesystem: PageIndexFileSystem) -> dict[str, Any]:
    executor = PIFSCommandExecutor(filesystem, json_output=True)
    return {
        "ls_root": json.loads(executor.execute("ls /")),
        "schema": json.loads(executor.execute("stat --schema /")),
        "metadata_find": json.loads(
            executor.execute('find /github --where \'{"source_type":"github"}\' --limit 3')
        ),
        "grep": json.loads(executor.execute('grep -R "multipart upload" /github --limit 3')),
    }


if __name__ == "__main__":
    raise SystemExit(main())
