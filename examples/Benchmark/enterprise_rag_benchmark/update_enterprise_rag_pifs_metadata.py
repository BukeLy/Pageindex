from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pageindex.filesystem import PageIndexFileSystem
from pageindex.filesystem.store import metadata_text


KEY_FIELDS = {
    "document_id",
    "external_id",
    "doc_id",
    "dataset_doc_uuid",
    "file_ref",
    "metadata",
    "metadata_json",
}


def main() -> int:
    args = parse_args()
    benchmark_dir = Path(__file__).resolve().parent
    workspace = (benchmark_dir / args.workspace).resolve()
    if not (workspace / "filesystem.sqlite").exists():
        raise SystemExit(f"workspace is not registered: {workspace}")

    filesystem = PageIndexFileSystem(workspace=workspace)
    if args.schema_json:
        schema = read_json(resolve_path(args.schema_json))
        if "fields" not in schema:
            schema = {"fields": schema}
        if not args.dry_run:
            filesystem._register_metadata_schema(schema)

    started = time.time()
    rows = read_jsonl(resolve_path(args.metadata_jsonl))
    if args.limit > 0:
        rows = rows[: args.limit]

    summary = update_metadata_rows(
        filesystem,
        rows,
        replace=args.replace,
        dry_run=args.dry_run,
    )
    summary.update(
        {
            "workspace": str(workspace),
            "metadata_jsonl": str(resolve_path(args.metadata_jsonl)),
            "schema_json": str(resolve_path(args.schema_json)) if args.schema_json else "",
            "replace": args.replace,
            "dry_run": args.dry_run,
            "seconds": round(time.time() - started, 3),
        }
    )
    summary_path = resolve_path(args.summary_path) if args.summary_path else workspace.parent / "metadata_update_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update metadata in an existing EnterpriseRAG PIFS workspace")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--metadata-jsonl", required=True, help="JSONL rows with document id plus metadata object.")
    parser.add_argument("--schema-json", default="", help="Optional frozen schema JSON to register before updates.")
    parser.add_argument("--summary-path", default="")
    parser.add_argument("--replace", action="store_true", help="Replace existing metadata instead of merging.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def update_metadata_rows(
    filesystem: PageIndexFileSystem,
    rows: list[dict[str, Any]],
    *,
    replace: bool,
    dry_run: bool,
) -> dict[str, Any]:
    updated = 0
    missing = 0
    skipped = 0
    with filesystem.store.connect() as conn:
        for row in rows:
            target = row_target(row)
            metadata = row_metadata(row)
            if not target or not metadata:
                skipped += 1
                continue
            file_ref = resolve_file_ref(conn, target)
            if file_ref is None:
                missing += 1
                continue
            if dry_run:
                updated += 1
                continue
            update_file_metadata(filesystem, conn, file_ref, metadata, replace=replace)
            updated += 1
    return {
        "rows": len(rows),
        "updated": updated,
        "missing": missing,
        "skipped": skipped,
    }


def update_file_metadata(
    filesystem: PageIndexFileSystem,
    conn: sqlite3.Connection,
    file_ref: str,
    metadata: dict[str, Any],
    *,
    replace: bool,
) -> None:
    row = conn.execute(
        """
        SELECT file_ref, title, text_artifact_path, metadata_json
        FROM files
        WHERE file_ref = ? AND deleted_at IS NULL
        """,
        (file_ref,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown file_ref: {file_ref}")
    existing = json.loads(row["metadata_json"] or "{}")
    next_metadata = metadata if replace else {**existing, **metadata}
    conn.execute(
        """
        UPDATE files
        SET metadata_json = ?, updated_at = CURRENT_TIMESTAMP
        WHERE file_ref = ?
        """,
        (json.dumps(next_metadata, ensure_ascii=False), file_ref),
    )
    filesystem.store.replace_metadata_values(conn, file_ref, next_metadata)
    content = Path(row["text_artifact_path"]).read_text(encoding="utf-8")
    filesystem.store.replace_fts(
        conn,
        {
            "file_ref": file_ref,
            "title": row["title"],
            "content": content,
            "metadata_text": metadata_text(next_metadata),
        },
    )


def row_target(row: dict[str, Any]) -> str:
    for key in ("document_id", "external_id", "doc_id", "dataset_doc_uuid", "file_ref"):
        value = row.get(key)
        if value:
            return str(value)
    return ""


def row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    metadata_json = row.get("metadata_json")
    if isinstance(metadata_json, str) and metadata_json.strip():
        parsed = json.loads(metadata_json)
        return parsed if isinstance(parsed, dict) else {}
    return {key: value for key, value in row.items() if key not in KEY_FIELDS}


def resolve_file_ref(conn: sqlite3.Connection, target: str) -> str | None:
    row = conn.execute(
        """
        SELECT file_ref
        FROM files
        WHERE deleted_at IS NULL
          AND (file_ref = ? OR external_id = ? OR source_path = ?)
        LIMIT 1
        """,
        (target, target, target.strip("/")),
    ).fetchone()
    return None if row is None else str(row["file_ref"])


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"metadata row must be an object: {line[:80]}")
            rows.append(row)
    return rows


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
