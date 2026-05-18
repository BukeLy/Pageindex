from __future__ import annotations

import argparse
import json
import logging
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
from pageindex.filesystem.store import metadata_text


LOGGER = logging.getLogger("enterprise_rag_register")
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
    all_questions = benchmark.load_questions(questions_path)
    questions = select_questions(
        all_questions,
        question_ids=args.question_ids,
        max_questions=args.max_questions,
    )
    if args.all_documents:
        paths = sorted(source_root.rglob("*.json"))
        source_types_indexed = sorted(path.name for path in source_root.iterdir() if path.is_dir())
    else:
        paths = dataset_paths_for_questions(
            source_root,
            questions,
            limit_per_source=args.limit_per_source,
        )
        source_types_indexed = sorted({item for q in questions for item in q.source_types})

    started = time.time()
    existing_ids = existing_external_ids(workspace) if not args.refresh_existing else set()
    file_refs, skipped_existing = ingest_paths_filtered(
        benchmark,
        source_root,
        paths,
        batch_size=args.batch_size,
        existing_ids=existing_ids,
        refresh_existing=args.refresh_existing,
        source_json_artifacts=args.source_json_artifacts,
        lite_fts=args.lite_fts,
        skip_fts=args.skip_fts,
    )
    elapsed = time.time() - started
    summary = {
        "workspace": str(workspace),
        "dataset_root": str(dataset_root),
        "all_documents": args.all_documents,
        "source_json_artifacts": args.source_json_artifacts,
        "lite_fts": args.lite_fts,
        "skip_fts": args.skip_fts,
        "question_ids": [question.question_id for question in questions],
        "questions_total": len(all_questions),
        "source_types_indexed": source_types_indexed,
        "candidate_files": len(paths),
        "skipped_existing": skipped_existing,
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
    parser.add_argument("--all-documents", action="store_true", help="Register every JSON document in generated_data/sources.")
    parser.add_argument("--refresh-existing", action="store_true", help="Overwrite documents already present in the workspace.")
    parser.add_argument(
        "--source-json-artifacts",
        action="store_true",
        help="Reuse each source JSON file as the open() artifact and skip raw artifact duplication.",
    )
    parser.add_argument(
        "--lite-fts",
        action="store_true",
        help="Index title/source_path/metadata for candidate recall instead of full document body.",
    )
    parser.add_argument("--skip-fts", action="store_true", help="Do not prebuild SQLite FTS rows.")
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


def existing_external_ids(workspace: Path) -> set[str]:
    db_path = workspace / "filesystem.sqlite"
    if not db_path.exists():
        return set()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT external_id FROM files WHERE external_id IS NOT NULL AND deleted_at IS NULL"
        ).fetchall()
    return {str(row[0]) for row in rows if row[0]}


def ingest_paths_filtered(
    benchmark: EnterpriseRAGBenchmark,
    source_root: Path,
    paths: list[Path],
    *,
    batch_size: int,
    existing_ids: set[str],
    refresh_existing: bool,
    source_json_artifacts: bool,
    lite_fts: bool,
    skip_fts: bool,
) -> tuple[list[str], int]:
    file_refs: list[str] = []
    batch: list[dict[str, Any]] = []
    skipped_existing = 0
    seen_ids = set(existing_ids)
    total = len(paths)
    for index, path in enumerate(paths, 1):
        data = benchmark._read_json(path)
        external_id = data.get("dataset_doc_uuid")
        if external_id and external_id in seen_ids and not refresh_existing:
            skipped_existing += 1
            if index % 10000 == 0:
                LOGGER.info(
                    "scan %d/%d indexed=%d skipped_existing=%d",
                    index,
                    total,
                    len(file_refs),
                    skipped_existing,
                )
            continue
        if source_json_artifacts and lite_fts:
            spec = lite_document_spec(source_root, path, data)
        else:
            spec = benchmark._document_spec(source_root, path, data)
        if source_json_artifacts:
            spec["text_artifact_path"] = str(path)
            spec["write_raw_artifact"] = False
        if lite_fts:
            spec["fts_content"] = lite_fts_content(spec)
        if skip_fts:
            spec["skip_fts"] = True
        batch.append(spec)
        if external_id:
            seen_ids.add(str(external_id))
        if len(batch) >= batch_size:
            file_refs.extend(benchmark.filesystem.register_files(batch))
            LOGGER.info(
                "registered scan=%d/%d indexed=%d skipped_existing=%d",
                index,
                total,
                len(file_refs),
                skipped_existing,
            )
            batch = []
    if batch:
        file_refs.extend(benchmark.filesystem.register_files(batch))
        LOGGER.info(
            "registered scan=%d/%d indexed=%d skipped_existing=%d",
            total,
            total,
            len(file_refs),
            skipped_existing,
        )
    return file_refs, skipped_existing


def lite_fts_content(spec: dict[str, Any]) -> str:
    metadata = spec.get("metadata") or {}
    return "\n".join(
        item
        for item in [
            str(spec.get("title") or ""),
            str(spec.get("source_path") or ""),
            str(metadata.get("dataset_doc_uuid") or ""),
            metadata_text(metadata),
        ]
        if item
    )


def lite_document_spec(source_root: Path, path: Path, data: dict[str, Any]) -> dict[str, Any]:
    relative_path = path.relative_to(source_root)
    title_field = data.get("title_field_name") or "title"
    title = str(data.get(title_field) or data.get("dataset_doc_uuid") or "Untitled")
    content_fields = set(data.get("content_field_names") or [])
    metadata = {
        key: value
        for key, value in data.items()
        if key not in content_fields and key not in {"content_field_names", "title_field_name"}
    }
    metadata["source_type"] = relative_path.parts[0] if relative_path.parts else None
    folder_path = "/" + "/".join(relative_path.parent.parts)
    spec = {
        "storage_uri": str(path),
        "source_path": str(relative_path),
        "folder_path": folder_path,
        "metadata": metadata,
        "external_id": data.get("dataset_doc_uuid"),
        "title": title,
        "content": "",
        "content_type": "application/json",
        "source_type": metadata["source_type"],
    }
    spec["text_artifact_path"] = str(path)
    spec["write_raw_artifact"] = False
    spec["fts_content"] = lite_fts_content(spec)
    return spec


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
