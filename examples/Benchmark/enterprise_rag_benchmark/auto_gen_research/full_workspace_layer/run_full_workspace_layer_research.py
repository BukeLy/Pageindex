from __future__ import annotations

import argparse
import collections
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pageindex.filesystem.semantic_index import (
    SemanticIndexRecord,
    SemanticSearchResult,
    SQLiteVecSemanticIndex,
)


BENCHMARK_DIR = REPO_ROOT / "examples" / "Benchmark" / "enterprise_rag_benchmark"
RESEARCH_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = RESEARCH_DIR / "prompts"
DEFAULT_SOURCE_WORKSPACE = BENCHMARK_DIR / "runs" / "full-20260519-pifs-source-grep" / "workspace"
DEFAULT_BASELINE_RESULTS = (
    BENCHMARK_DIR
    / "runs"
    / "full-20260519-pifs-source-grep-agent-v6-low-60s-retry"
    / "results.jsonl"
)
DEFAULT_DERIVED_WORKSPACE = RESEARCH_DIR / "workspaces" / "structured-metadata-folders-v1" / "workspace"
DEFAULT_RESULTS_DIR = RESEARCH_DIR / "results"
DEFAULT_VECTOR_INDEX_DIRNAME = "semantic_vector_indexes"

QUESTION_SETS = {
    "timeout10": [
        "qst_0007",
        "qst_0009",
        "qst_0011",
        "qst_0018",
        "qst_0021",
        "qst_0028",
        "qst_0030",
        "qst_0047",
        "qst_0051",
        "qst_0056",
    ],
    "wrong5": ["qst_0016", "qst_0017", "qst_0110", "qst_0128", "qst_0161"],
    "slow5": ["qst_0009", "qst_0055", "qst_0064", "qst_0099", "qst_0249"],
}

SCHEMA_FIELDS: dict[str, str] = {
    "source_type": "Top-level source system such as github, slack, gmail, jira, linear, confluence, google_drive, hubspot, or fireflies.",
    "doc_type": "Source-native document type, issue type, thread type, call type, or article/report type.",
    "source_bucket": "Coarse source bucket such as repo, channel, mailbox owner, Confluence space, Jira project, Linear team, Google Drive area, or CRM account tier.",
    "title": "Document title, subject, summary, channel name, or company name.",
    "domain": "Broad business or technical domain inferred from source-native tags, path, and title.",
    "topic": "Canonical retrieval topic from tags, labels, components, project names, title terms, or source-native topics.",
    "intent": "What the document helps answer or operational goal implied by the document.",
    "entities": "Queryable systems, products, repos, projects, customers, companies, API names, issue keys, people, and important identifiers.",
    "constraints": "Limits, defaults, deadlines, dates, versions, config names, metrics, regions, SLAs, and other exact constraints.",
    "summary": "Compact retrieval summary built from the title and the first source text field.",
    "repo": "GitHub repository when present.",
    "project": "Jira, Linear, CRM, or product project when present.",
    "channel": "Slack channel or communication channel when present.",
    "space": "Confluence or knowledge-space name when present.",
    "customer": "Customer, account, company, or related organization when present.",
    "owner": "Primary internal owner, author, assignee, mailbox owner, or account owner.",
    "participants": "Important participants, reviewers, collaborators, attendees, reporters, or message authors.",
    "status": "State, status, stage, priority/severity, or lifecycle status.",
    "year": "Primary document year extracted from source dates.",
    "month": "Primary document month in YYYY-MM form extracted from source dates.",
}

SOURCE_TYPE_FIELDS = {
    "github": ("pull_request", "repo", "repo", "repo"),
    "gmail": ("email_thread", "mailbox_owner", "mailbox_owner", ""),
    "slack": ("slack_thread", "channel", "channel", "channel"),
    "jira": ("jira_issue", "project", "project", ""),
    "linear": ("linear_issue", "team", "project", ""),
    "confluence": ("knowledge_page", "space", "space", "space"),
    "google_drive": ("drive_doc", "drive_area", "drive_area", ""),
    "hubspot": ("crm_record", "company_name", "company_name", ""),
    "fireflies": ("meeting_transcript", "call_type", "customer_company", ""),
}

STOPWORDS = {
    "about",
    "after",
    "and",
    "api",
    "are",
    "for",
    "from",
    "how",
    "into",
    "new",
    "not",
    "the",
    "this",
    "that",
    "with",
    "what",
    "when",
    "where",
    "which",
    "why",
    "will",
    "redwood",
    "quick",
    "sync",
    "heads",
    "head",
    "heads-up",
    "headsup",
    "fyi",
    "seeing",
    "sudden",
    "jump",
    "need",
    "someone",
    "tomorrow",
    "morning",
    "planning",
    "publish",
    "check",
    "notes",
    "note",
    "lab",
}

LOGGER = logging.getLogger("full_workspace_layer")


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BENCHMARK_DIR / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    if args.command == "build":
        summary = build_workspace(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "project":
        summary = project_workspace(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "probe-metadata":
        summary = probe_metadata(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "probe-folders":
        summary = probe_folders(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "probe-fts":
        summary = probe_fts(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "build-term-index":
        summary = build_term_index(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "build-term-stats":
        summary = build_term_stats(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "probe-term-index":
        summary = probe_term_index(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "build-vector-index":
        summary = build_vector_index(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "probe-vector-index":
        summary = probe_vector_index(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "probe-vector-rerank":
        summary = probe_vector_rerank(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "probe-vector-union":
        summary = probe_vector_union(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "probe-vector-groups":
        summary = probe_vector_groups(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "compare-vector-fields":
        summary = compare_vector_fields(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "run":
        summary = run_experiments(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    if args.command == "analyze":
        summary = analyze_run_dir(Path(args.run_dir).resolve())
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    raise SystemExit(f"unknown command: {args.command}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Full-workspace PIFS metadata/folder layer research")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Create a derived workspace with structured metadata and semantic folders")
    build.add_argument("--source-workspace", default=str(DEFAULT_SOURCE_WORKSPACE))
    build.add_argument("--workspace", default=str(DEFAULT_DERIVED_WORKSPACE))
    build.add_argument("--reset", action="store_true")
    build.add_argument("--batch-size", type=int, default=5000)
    build.add_argument("--max-docs", type=int, default=0)
    build.add_argument("--no-lite-fts", action="store_true")
    build.add_argument("--no-semantic-folders", action="store_true")

    project = sub.add_parser("project", help="Add semantic folders and/or compact FTS to an existing metadata workspace")
    project.add_argument("--workspace", default=str(DEFAULT_DERIVED_WORKSPACE))
    project.add_argument("--batch-size", type=int, default=20000)
    project.add_argument("--max-docs", type=int, default=0)
    project.add_argument("--semantic-folders", action="store_true")
    project.add_argument("--lite-fts", action="store_true")
    project.add_argument("--reset-semantic-folders", action="store_true")
    project.add_argument("--reset-lite-fts", action="store_true")

    probe_metadata_parser = sub.add_parser("probe-metadata", help="Probe whether metadata DSL can shrink candidates for question text")
    probe_metadata_parser.add_argument("--workspace", default=str(DEFAULT_DERIVED_WORKSPACE))
    probe_metadata_parser.add_argument("--question-set", default="timeout10", choices=sorted(QUESTION_SETS))
    probe_metadata_parser.add_argument("--question-ids", default="")
    probe_metadata_parser.add_argument("--limit-terms", type=int, default=18)
    probe_metadata_parser.add_argument("--output", default="")

    probe_folders_parser = sub.add_parser("probe-folders", help="Probe whether semantic folder names expose likely candidate scopes")
    probe_folders_parser.add_argument("--workspace", default=str(DEFAULT_DERIVED_WORKSPACE))
    probe_folders_parser.add_argument("--question-set", default="timeout10", choices=sorted(QUESTION_SETS))
    probe_folders_parser.add_argument("--question-ids", default="")
    probe_folders_parser.add_argument("--limit-terms", type=int, default=18)
    probe_folders_parser.add_argument("--output", default="")

    probe_fts_parser = sub.add_parser("probe-fts", help="Probe compact FTS recall without opening documents")
    probe_fts_parser.add_argument("--workspace", default=str(DEFAULT_DERIVED_WORKSPACE))
    probe_fts_parser.add_argument("--question-set", default="timeout10", choices=sorted(QUESTION_SETS))
    probe_fts_parser.add_argument("--question-ids", default="")
    probe_fts_parser.add_argument("--all-questions", action="store_true")
    probe_fts_parser.add_argument("--limit-terms", type=int, default=18)
    probe_fts_parser.add_argument("--output", default="")

    term_index = sub.add_parser("build-term-index", help="Build a semantic term index from generated metadata")
    term_index.add_argument("--workspace", default=str(DEFAULT_DERIVED_WORKSPACE))
    term_index.add_argument("--batch-size", type=int, default=20000)
    term_index.add_argument("--max-docs", type=int, default=0)

    term_stats = sub.add_parser("build-term-stats", help="Build source-specific semantic term document frequencies")
    term_stats.add_argument("--workspace", default=str(DEFAULT_DERIVED_WORKSPACE))

    probe_term_index = sub.add_parser("probe-term-index", help="Probe semantic term index recall")
    probe_term_index.add_argument("--workspace", default=str(DEFAULT_DERIVED_WORKSPACE))
    probe_term_index.add_argument("--question-set", default="timeout10", choices=sorted(QUESTION_SETS))
    probe_term_index.add_argument("--question-ids", default="")
    probe_term_index.add_argument("--limit-terms", type=int, default=24)
    probe_term_index.add_argument("--output", default="")

    vector_common = argparse.ArgumentParser(add_help=False)
    vector_common.add_argument("--workspace", default=str(DEFAULT_DERIVED_WORKSPACE))
    vector_common.add_argument(
        "--field-mode",
        default="metadata",
        choices=["summary", "metadata", "fulltext", "sampled_fulltext"],
        help=(
            "Which document text goes into embeddings: summary only, generated metadata fields, "
            "source text preview, or sampled salient source text."
        ),
    )
    vector_common.add_argument("--index-name", default="")
    vector_common.add_argument(
        "--embedding-provider",
        default=os.environ.get("PIFS_EMBEDDING_PROVIDER", "openai"),
        choices=["openai", "hash"],
        help="Use openai for real embeddings or hash for local smoke tests without API spend.",
    )
    vector_common.add_argument(
        "--embedding-model",
        default=os.environ.get("PIFS_EMBEDDING_MODEL", "text-embedding-3-small"),
    )
    vector_common.add_argument(
        "--embedding-dimensions",
        type=int,
        default=int(os.environ.get("PIFS_EMBEDDING_DIMENSIONS", "256")),
        help="Embedding dimensions for models that support shortening. 256 keeps local full-workspace indexes small.",
    )
    vector_common.add_argument(
        "--embedding-timeout",
        type=float,
        default=float(os.environ.get("PIFS_EMBEDDING_TIMEOUT", "60")),
        help="Seconds before one embedding API request times out.",
    )
    vector_common.add_argument("--batch-size", type=int, default=64)
    vector_common.add_argument("--max-docs", type=int, default=0)
    vector_common.add_argument("--max-fulltext-chars", type=int, default=8000)

    build_vector = sub.add_parser(
        "build-vector-index",
        parents=[vector_common],
        help="Build a rebuildable sqlite-vec semantic recall index",
    )
    build_vector.add_argument("--reset", action="store_true")

    probe_vector = sub.add_parser(
        "probe-vector-index",
        parents=[vector_common],
        help="Probe vector index recall for selected benchmark questions",
    )
    probe_vector.add_argument("--question-set", default="timeout10", choices=sorted(QUESTION_SETS))
    probe_vector.add_argument("--question-ids", default="")
    probe_vector.add_argument("--all-questions", action="store_true")
    probe_vector.add_argument("--candidate-limit", type=int, default=200)
    probe_vector.add_argument("--fetch-multiplier", type=int, default=100)
    probe_vector.add_argument("--output", default="")

    rerank_vector = sub.add_parser(
        "probe-vector-rerank",
        parents=[vector_common],
        help="Probe vector recall plus cheap LLM reranking outside PIFS core",
    )
    rerank_vector.add_argument("--question-set", default="timeout10", choices=sorted(QUESTION_SETS))
    rerank_vector.add_argument("--question-ids", default="")
    rerank_vector.add_argument("--all-questions", action="store_true")
    rerank_vector.add_argument("--candidate-limit", type=int, default=50)
    rerank_vector.add_argument("--rerank-limit", type=int, default=20)
    rerank_vector.add_argument("--fetch-multiplier", type=int, default=100)
    rerank_vector.add_argument("--reranker-provider", default=os.environ.get("PIFS_RERANKER_PROVIDER", "openai"), choices=["openai", "heuristic"])
    rerank_vector.add_argument("--reranker-model", default=os.environ.get("PIFS_RERANKER_MODEL", "gpt-4.1-nano"))
    rerank_vector.add_argument("--reranker-timeout", type=float, default=float(os.environ.get("PIFS_RERANKER_TIMEOUT", "25")))
    rerank_vector.add_argument("--output", default="")

    union_vector = sub.add_parser(
        "probe-vector-union",
        parents=[vector_common],
        help="Probe reciprocal-rank union across multiple semantic recall indexes",
    )
    union_vector.add_argument("--index-specs", default="metadata:metadata-256-full,fulltext:fulltext-256-2k")
    union_vector.add_argument("--question-set", default="timeout10", choices=sorted(QUESTION_SETS))
    union_vector.add_argument("--question-ids", default="")
    union_vector.add_argument("--all-questions", action="store_true")
    union_vector.add_argument("--per-index-limit", type=int, default=200)
    union_vector.add_argument("--candidate-limit", type=int, default=200)
    union_vector.add_argument("--fetch-multiplier", type=int, default=100)
    union_vector.add_argument("--output", default="")

    group_vector = sub.add_parser(
        "probe-vector-groups",
        parents=[vector_common],
        help="Probe whether deep vector candidates can be compressed into useful metadata group rows",
    )
    group_vector.add_argument("--index-specs", default="metadata:metadata-256-full,fulltext:fulltext-256-2k")
    group_vector.add_argument("--question-set", default="timeout10", choices=sorted(QUESTION_SETS))
    group_vector.add_argument("--question-ids", default="")
    group_vector.add_argument("--all-questions", action="store_true")
    group_vector.add_argument("--per-index-limit", type=int, default=1000)
    group_vector.add_argument("--candidate-limit", type=int, default=1000)
    group_vector.add_argument("--fetch-multiplier", type=int, default=300)
    group_vector.add_argument(
        "--group-fields",
        default="source_bucket,topic,customer,repo,project,channel,space,doc_type",
        help="Comma-separated metadata fields used to aggregate deep vector candidates.",
    )
    group_vector.add_argument("--group-limit", type=int, default=20)
    group_vector.add_argument("--output", default="")

    compare_vector = sub.add_parser(
        "compare-vector-fields",
        parents=[vector_common],
        help="Build and probe summary/metadata/fulltext vector indexes as a field ablation",
    )
    compare_vector.add_argument("--field-modes", default="summary,metadata,fulltext")
    compare_vector.add_argument("--question-set", default="timeout10", choices=sorted(QUESTION_SETS))
    compare_vector.add_argument("--question-ids", default="")
    compare_vector.add_argument("--all-questions", action="store_true")
    compare_vector.add_argument("--candidate-limit", type=int, default=200)
    compare_vector.add_argument("--fetch-multiplier", type=int, default=100)
    compare_vector.add_argument("--reset", action="store_true")
    compare_vector.add_argument("--output", default="")

    run = sub.add_parser("run", help="Run controlled small-question experiments")
    run.add_argument("--workspace", default=str(DEFAULT_DERIVED_WORKSPACE))
    run.add_argument("--run-name", default="")
    run.add_argument("--question-set", default="timeout10", choices=sorted(QUESTION_SETS))
    run.add_argument("--question-ids", default="")
    run.add_argument("--model", default=os.environ.get("PIFS_AGENT_MODEL", "gpt-5.4-mini"))
    run.add_argument("--max-seconds", type=float, default=60)
    run.add_argument("--max-turns", type=int, default=200)
    run.add_argument("--agent-retries", type=int, default=1)
    run.add_argument("--strategies", default="metadata_original,metadata_source_aware,folder_semantic")
    run.add_argument("--baseline-results", default=str(DEFAULT_BASELINE_RESULTS))

    analyze = sub.add_parser("analyze", help="Summarize one research run directory")
    analyze.add_argument("--run-dir", required=True)
    return parser.parse_args()


def build_workspace(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    source_workspace = Path(args.source_workspace).expanduser().resolve()
    target_workspace = Path(args.workspace).expanduser().resolve()
    copy_workspace(source_workspace, target_workspace, reset=args.reset)

    db_path = target_workspace / "filesystem.sqlite"
    schema = {"fields": {name: {"type": "string", "description": desc} for name, desc in SCHEMA_FIELDS.items()}}
    schema_path = target_workspace.parent / "structured_schema.json"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA temp_store = MEMORY")
        register_schema(conn)
        summary = enrich_catalog(
            conn,
            batch_size=args.batch_size,
            max_docs=args.max_docs,
            populate_lite_fts=not args.no_lite_fts,
            populate_semantic_folders=not args.no_semantic_folders,
        )

    summary.update(
        {
            "source_workspace": str(source_workspace),
            "workspace": str(target_workspace),
            "schema_path": str(schema_path),
            "populate_lite_fts": not args.no_lite_fts,
            "populate_semantic_folders": not args.no_semantic_folders,
            "seconds": round(time.time() - started, 3),
        }
    )
    (target_workspace.parent / "build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def copy_workspace(source_workspace: Path, target_workspace: Path, *, reset: bool) -> None:
    source_db = source_workspace / "filesystem.sqlite"
    if not source_db.exists():
        raise SystemExit(f"source workspace is missing filesystem.sqlite: {source_workspace}")
    if reset and target_workspace.parent.exists():
        shutil.rmtree(target_workspace.parent)
    target_workspace.mkdir(parents=True, exist_ok=True)
    for artifact_dir in ("artifacts/text", "artifacts/raw", "artifacts/pageindex_trees"):
        (target_workspace / artifact_dir).mkdir(parents=True, exist_ok=True)
    target_db = target_workspace / "filesystem.sqlite"
    if target_db.exists() and not reset:
        LOGGER.info("reuse existing derived workspace: %s", target_workspace)
        return
    LOGGER.info("copy sqlite catalog %s -> %s", source_db, target_db)
    shutil.copy2(source_db, target_db)


def register_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO metadata_schema(schema_id, scope_path, version, status)
        VALUES ('default', NULL, 1, 'active')
        """
    )
    rows = [
        (
            field_id(name),
            name,
            "string",
            description,
        )
        for name, description in SCHEMA_FIELDS.items()
    ]
    conn.executemany(
        """
        INSERT INTO metadata_fields(field_id, schema_id, name, type, description, indexed, faceted, sortable, source, updated_at)
        VALUES (?, 'default', ?, ?, ?, 1, 0, 0, 'research_generated', CURRENT_TIMESTAMP)
        ON CONFLICT(schema_id, name) DO UPDATE SET
            type = excluded.type,
            description = excluded.description,
            source = excluded.source,
            updated_at = CURRENT_TIMESTAMP
        """,
        rows,
    )


def enrich_catalog(
    conn: sqlite3.Connection,
    *,
    batch_size: int,
    max_docs: int,
    populate_lite_fts: bool,
    populate_semantic_folders: bool,
) -> dict[str, Any]:
    field_ids = {
        row["name"]: row["field_id"]
        for row in conn.execute(
            "SELECT name, field_id FROM metadata_fields WHERE schema_id = 'default'"
        ).fetchall()
    }
    total = conn.execute("SELECT COUNT(*) FROM files WHERE deleted_at IS NULL").fetchone()[0]
    if max_docs > 0:
        total = min(total, max_docs)
    LOGGER.info("enriching %d files", total)

    drop_rebuildable_indexes(conn, include_folders=populate_semantic_folders)
    conn.execute("DELETE FROM metadata_values")
    if populate_lite_fts:
        conn.execute("DELETE FROM file_fts")

    processed = 0
    source_counts: collections.Counter[str] = collections.Counter()
    folder_counts: collections.Counter[str] = collections.Counter()
    empty_content_reads = 0
    folder_cache = preload_folder_paths(conn)
    last_file_ref = ""
    while True:
        rows = conn.execute(
            """
            SELECT file_ref, external_id, storage_uri, source_path, title, text_artifact_path, metadata_json
            FROM files
            WHERE deleted_at IS NULL AND file_ref > ?
            ORDER BY file_ref
            LIMIT ?
            """,
            (last_file_ref, batch_size),
        ).fetchall()
        if not rows:
            break
        if max_docs > 0 and processed >= max_docs:
            break
        if max_docs > 0 and processed + len(rows) > max_docs:
            rows = rows[: max_docs - processed]
        last_file_ref = rows[-1]["file_ref"]

        file_updates = []
        metadata_rows = []
        fts_rows = []
        membership_rows = []
        for row in rows:
            raw = load_document_json(row)
            if not raw:
                empty_content_reads += 1
            existing = safe_json_obj(row["metadata_json"])
            metadata = extract_metadata(row, raw or existing)
            source_counts[metadata.get("source_type", "")] += 1
            merged = {**existing, **metadata}
            file_updates.append((json.dumps(merged, ensure_ascii=False), row["file_ref"]))
            metadata_rows.extend(metadata_value_rows(row["file_ref"], metadata, field_ids))
            if populate_lite_fts:
                fts_rows.append(
                    (
                        row["file_ref"],
                        metadata.get("title") or row["title"] or "",
                        lite_fts_text(metadata),
                        lite_fts_text(metadata),
                    )
                )
            if populate_semantic_folders:
                for folder_path in semantic_folder_paths(metadata):
                    ensure_folder(conn, folder_cache, folder_path, kind="semantic")
                    membership_rows.append((row["file_ref"], folder_id(folder_path), "{}"))
                    folder_counts[folder_path] += 1

        conn.executemany(
            """
            UPDATE files
            SET metadata_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE file_ref = ?
            """,
            file_updates,
        )
        if metadata_rows:
            conn.executemany(
                """
                INSERT INTO metadata_values(file_ref, field_id, value_text, value_number, value_bool, value_json)
                VALUES (?, ?, ?, NULL, NULL, ?)
                """,
                metadata_rows,
            )
        if populate_lite_fts and fts_rows:
            conn.executemany(
                """
                INSERT INTO file_fts(file_ref, title, body, metadata_text)
                VALUES (?, ?, ?, ?)
                """,
                fts_rows,
            )
        if membership_rows:
            conn.executemany(
                """
                INSERT INTO file_folders(file_ref, folder_id, metadata_json)
                VALUES (?, ?, ?)
                ON CONFLICT(file_ref, folder_id) DO UPDATE SET metadata_json = excluded.metadata_json
                """,
                membership_rows,
            )
        processed += len(rows)
        conn.commit()
        LOGGER.info("enriched %d/%d files", processed, total)
        if max_docs > 0 and processed >= max_docs:
            break

    recreate_rebuildable_indexes(conn, include_folders=populate_semantic_folders)
    conn.commit()

    return {
        "processed_files": processed,
        "source_counts": dict(sorted(source_counts.items())),
        "folder_memberships_added": sum(folder_counts.values()),
        "unique_semantic_folders": len(folder_counts),
        "top_semantic_folders": folder_counts.most_common(30),
        "empty_document_reads": empty_content_reads,
        "metadata_fields": len(SCHEMA_FIELDS),
        "metadata_values_total": conn.execute("SELECT COUNT(*) FROM metadata_values").fetchone()[0],
        "file_fts_total": conn.execute("SELECT COUNT(*) FROM file_fts").fetchone()[0],
        "folders_total": conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0],
        "file_folders_total": conn.execute("SELECT COUNT(*) FROM file_folders").fetchone()[0],
    }


def drop_rebuildable_indexes(conn: sqlite3.Connection, *, include_folders: bool) -> None:
    conn.execute("DROP INDEX IF EXISTS idx_metadata_values_field_text")
    conn.execute("DROP INDEX IF EXISTS idx_metadata_values_field_number")
    if include_folders:
        conn.execute("DROP INDEX IF EXISTS idx_file_folders_folder")


def recreate_rebuildable_indexes(conn: sqlite3.Connection, *, include_folders: bool) -> None:
    LOGGER.info("rebuilding sqlite indexes")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metadata_values_field_text ON metadata_values(field_id, value_text)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metadata_values_field_number ON metadata_values(field_id, value_number)")
    if include_folders:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_file_folders_folder ON file_folders(folder_id)")


def load_document_json(row: sqlite3.Row) -> dict[str, Any]:
    for key in ("text_artifact_path", "storage_uri"):
        value = row[key]
        if not value:
            continue
        path = Path(value)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        return data if isinstance(data, dict) else {}
    return {}


def extract_metadata(row: sqlite3.Row, data: dict[str, Any]) -> dict[str, str]:
    source_path = str(row["source_path"] or "")
    source_type = source_path.split("/", 1)[0] if "/" in source_path else str(data.get("source_type") or "")
    title = text_value(
        data.get(data.get("title_field_name") or "")
        or data.get("title")
        or data.get("subject")
        or data.get("summary")
        or data.get("company_name")
        or row["title"]
    )
    source_defaults = SOURCE_TYPE_FIELDS.get(source_type, ("document", "", "", ""))
    doc_type = text_value(data.get("doc_type") or data.get("issue_type") or data.get("thread_type") or data.get("call_type") or source_defaults[0])
    source_bucket = source_bucket_for(source_type, source_path, data, source_defaults)
    repo = text_value(data.get("repo"))
    project = text_value(data.get("project") or data.get("team"))
    channel = text_value(data.get("channel"))
    space = text_value(data.get("space") or data.get("drive_area"))
    customer = text_value(
        data.get("customer_company")
        or data.get("company_name")
        or data.get("related_account")
        or data.get("company_domain")
    )
    owner = text_value(
        data.get("owner")
        or data.get("author")
        or data.get("assignee")
        or data.get("mailbox_owner")
        or data.get("redwood_owner")
        or data.get("reporter")
        or data.get("creator")
    )
    status = text_value(
        data.get("status")
        or data.get("state")
        or data.get("stage")
        or data.get("priority")
        or data.get("severity")
        or data.get("ci_status")
    )
    participants = compact_join(
        values_from_keys(
            data,
            [
                "participants",
                "participants_external",
                "participants_internal",
                "reviewers",
                "collaborators",
                "customer_attendees",
                "redwood_attendees",
            ],
        )
    )
    date_text = first_date(data)
    year = date_text[:4] if date_text else ""
    month = date_text[:7] if len(date_text) >= 7 else ""
    tags = values_from_keys(
        data,
        [
            "labels",
            "tags",
            "topics",
            "components",
            "notes_tags",
            "interested_products",
            "use_cases",
            "affected_models",
            "affected_regions",
        ],
    )
    entity_values = values_from_keys(
        data,
        [
            "repo",
            "key",
            "project",
            "team",
            "channel",
            "space",
            "customer_company",
            "company_name",
            "related_account",
            "company_domain",
            "crm_deal_id",
            "linked_jira",
            "linked_linear",
            "related_github_prs",
            "linked_support_tickets",
            "related_links",
            "files_changed",
            "affected_models",
            "affected_regions",
        ],
    )
    entity_values.extend(keyword_terms(title))
    topic = canonical_topic(tags, title, source_path)
    domain = canonical_domain(source_type, source_path, tags, title)
    content_preview = source_text_preview(data, max_chars=900)
    constraints = compact_join(extract_constraint_terms(" ".join([title, content_preview])), limit=18)
    summary = compact_summary(title, content_preview)
    intent = compact_summary(title, " ".join(keyword_terms(content_preview)[:16]))
    return clean_metadata(
        {
            "source_type": source_type,
            "doc_type": doc_type,
            "source_bucket": source_bucket,
            "title": title,
            "domain": domain,
            "topic": topic,
            "intent": intent,
            "entities": compact_join(entity_values, limit=24),
            "constraints": constraints,
            "summary": summary,
            "repo": repo,
            "project": project,
            "channel": channel,
            "space": space,
            "customer": customer,
            "owner": owner,
            "participants": participants,
            "status": status,
            "year": year,
            "month": month,
        }
    )


def source_bucket_for(
    source_type: str,
    source_path: str,
    data: dict[str, Any],
    source_defaults: tuple[str, str, str, str],
) -> str:
    primary_key = source_defaults[1]
    if primary_key and data.get(primary_key):
        return text_value(data.get(primary_key))
    parts = [part for part in source_path.split("/") if part]
    if len(parts) >= 2:
        return parts[1]
    return source_type


def canonical_topic(tags: list[str], title: str, source_path: str) -> str:
    for value in tags:
        normalized = phrase(value)
        if normalized:
            return normalized
    path_parts = [part for part in Path(source_path).parts[1:-1] if part]
    for value in reversed(path_parts):
        normalized = phrase(value)
        if normalized:
            return normalized
    terms = keyword_terms(title)
    return " ".join(terms[:4]) if terms else phrase(title)


def canonical_domain(source_type: str, source_path: str, tags: list[str], title: str) -> str:
    haystack = " ".join([source_type, source_path, " ".join(tags), title]).lower()
    rules = [
        ("api", ["api", "endpoint", "multipart", "request", "response", "openai"]),
        ("runtime", ["runtime", "kernel", "latency", "gpu", "kv", "cache", "inference"]),
        ("security", ["security", "auth", "token", "permission", "privacy", "retention"]),
        ("sales", ["crm", "deal", "customer", "arr", "pipeline", "trial", "poc"]),
        ("support", ["support", "escalation", "incident", "ticket", "sev", "rollback"]),
        ("docs", ["docs", "quickstart", "runbook", "confluence", "drive", "article"]),
        ("design", ["design", "ux", "figma", "accessibility", "card"]),
        ("finance", ["invoice", "finance", "billing", "payment", "contract"]),
    ]
    for domain, needles in rules:
        if any(needle in haystack for needle in needles):
            return domain
    return source_type or "general"


def extract_constraint_terms(text: str) -> list[str]:
    patterns = [
        r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b\s*(?:=|:)\s*[A-Za-z0-9_.:/-]+",
        r"\b\d+(?:\.\d+)?\s*(?:MiB|GiB|MB|GB|ms|s|sec|seconds|minutes|hours|days|%|tokens?|req/s|rps)\b",
        r"\b(?:default|max|min|limit|timeout|deadline|SLA|P\d|Sev\d|HTTP|status)\b[^.;,\n]{0,80}",
        r"\b(?:20\d{2})[-/]\d{2}(?:[-/]\d{2})?\b",
        r"\b[A-Z]{2,10}-\d{2,}\b",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(match.strip() for match in re.findall(pattern, text, flags=re.IGNORECASE))
    return dedupe(found)


def source_text_preview(data: dict[str, Any], *, max_chars: int) -> str:
    parts = []
    for key in data.get("content_field_names") or []:
        value = data.get(key)
        if value is None:
            continue
        parts.append(text_value(value))
        if sum(len(part) for part in parts) >= max_chars:
            break
    return " ".join(parts)[:max_chars]


def semantic_folder_paths(metadata: dict[str, str]) -> list[str]:
    source_type = slug(metadata.get("source_type")) or "unknown"
    paths = [f"/semantic/source={source_type}"]
    dimension_names = {
        "source_bucket": "bucket",
        "doc_type": "type",
        "status": "status",
        "domain": "domain",
        "topic": "topic",
        "customer": "customer",
    }
    for field, dimension in dimension_names.items():
        value = slug(metadata.get(field))
        if value:
            paths.append(f"/semantic/source={source_type}/{dimension}/{value}")
    for term in semantic_keyword_terms(metadata, limit=10):
        paths.append(f"/semantic/source={source_type}/term/{term}")
    return paths[:17]


def semantic_keyword_terms(metadata: dict[str, str], *, limit: int) -> list[str]:
    source_type = slug(metadata.get("source_type"))
    generic = {
        source_type,
        slug(metadata.get("doc_type")),
        slug(metadata.get("source_bucket")),
        "redwood",
        "thread",
        "quick",
        "sync",
        "notes",
        "docs",
        "doc",
        "issue",
        "support",
        "customer",
    }
    terms: list[str] = []
    field_quotas = [
        ("entities", 2),
        ("title", 2),
        ("constraints", 2),
        ("summary", 4),
        ("intent", 3),
    ]
    for field, quota in field_quotas:
        for term in ranked_field_terms(metadata.get(field, ""), field=field, limit=quota):
            if term in generic or term in STOPWORDS:
                continue
            for alias in term_aliases(term):
                if alias and alias not in generic and alias not in STOPWORDS and len(alias) >= 3:
                    terms.append(alias)
                    if len(dedupe(terms)) >= limit:
                        return dedupe(terms)[:limit]
    return dedupe(terms)[:limit]


def ranked_field_terms(text: str, *, field: str, limit: int) -> list[str]:
    if not text:
        return []
    text = re.sub(r"\b[A-Za-z][A-Za-z0-9_-]{1,24}:\s+", " ", text)
    scored: dict[str, float] = {}
    for raw in candidate_terms(text):
        value = slug(raw)
        if not value or value in STOPWORDS or len(value) < 3:
            continue
        if value.isdigit():
            continue
        score = 0.0
        if raw.isupper() and len(raw) >= 3:
            score += 6
        if re.search(r"[-_.+]", value):
            score += 4
        if re.search(r"\d", value):
            score += 3
        if len(value) >= 8:
            score += 1
        if field in {"summary", "intent"} and not re.search(r"\d", value):
            score += 2
        if field == "constraints" and re.search(r"\d", value):
            score -= 2
        scored[value] = max(scored.get(value, 0.0), score)
    return [term for term, _score in sorted(scored.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def candidate_terms(text: str) -> list[str]:
    terms: list[str] = []
    terms.extend(re.findall(r"\bv?\d+(?:\.\d+)+\b", text, flags=re.IGNORECASE))
    terms.extend(re.findall(r"\b[A-Z]{2,}[A-Za-z0-9_-]*\b", text))
    terms.extend(re.findall(r"\b[A-Za-z][A-Za-z0-9_+-]+(?:[-_+][A-Za-z0-9]+)+\b", text))
    terms.extend(re.findall(r"\b[A-Za-z][A-Za-z0-9_+-]{2,}\b", text))
    return dedupe(terms)


def term_aliases(term: str) -> list[str]:
    aliases = [term]
    collapsed = term.replace("-", "").replace("_", "")
    if collapsed != term:
        aliases.append(collapsed)
    if term.endswith("s") and len(term) > 4:
        aliases.append(term[:-1])
    if term.endswith("ies") and len(term) > 5:
        aliases.append(term[:-3] + "y")
    return aliases


def metadata_value_rows(
    file_ref: str,
    metadata: dict[str, str],
    field_ids: dict[str, str],
) -> list[tuple[str, str, str, str]]:
    rows = []
    for name, value in metadata.items():
        field_id_value = field_ids.get(name)
        if not field_id_value or value == "":
            continue
        rows.append((file_ref, field_id_value, value, json.dumps(value, ensure_ascii=False)))
    return rows


def lite_fts_text(metadata: dict[str, str]) -> str:
    return "\n".join(
        value
        for key in (
            "title",
            "source_type",
            "doc_type",
            "source_bucket",
            "domain",
            "topic",
            "intent",
            "entities",
            "constraints",
            "summary",
            "repo",
            "project",
            "channel",
            "space",
            "customer",
            "owner",
            "participants",
            "status",
            "year",
            "month",
        )
        for value in [metadata.get(key, "")]
        if value
    )


def preload_folder_paths(conn: sqlite3.Connection) -> set[str]:
    return {row["path"] for row in conn.execute("SELECT path FROM folders").fetchall()}


def ensure_folder(
    conn: sqlite3.Connection,
    folder_cache: set[str],
    path: str,
    *,
    kind: str,
) -> None:
    path = normalize_path(path)
    if path in folder_cache:
        return
    parent = str(Path(path).parent).replace("\\", "/")
    if parent == ".":
        parent = "/"
    ensure_folder(conn, folder_cache, parent, kind=kind)
    name = "/" if path == "/" else path.rsplit("/", 1)[-1]
    parent_id = None if path == "/" else folder_id(parent)
    conn.execute(
        """
        INSERT INTO folders(folder_id, parent_id, name, path, description, kind, metadata_json)
        VALUES (?, ?, ?, ?, '', ?, '{}')
        ON CONFLICT(path) DO NOTHING
        """,
        (folder_id(path), parent_id, name, path, kind),
    )
    folder_cache.add(path)


def project_workspace(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    db_path = workspace / "filesystem.sqlite"
    if not db_path.exists():
        raise SystemExit(f"workspace is not registered: {workspace}")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA temp_store = MEMORY")
        if args.reset_semantic_folders:
            reset_semantic_folders(conn)
        if args.reset_lite_fts:
            conn.execute("DELETE FROM file_fts")
        summary = project_catalog_layers(
            conn,
            batch_size=args.batch_size,
            max_docs=args.max_docs,
            semantic_folders=args.semantic_folders,
            lite_fts=args.lite_fts,
        )
    summary.update({"workspace": str(workspace), "seconds": round(time.time() - started, 3)})
    write_json(workspace.parent / "projection_summary.json", summary)
    return summary


def reset_semantic_folders(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT folder_id FROM folders WHERE path = '/semantic' OR path LIKE '/semantic/%'"
    ).fetchall()
    folder_ids = [row["folder_id"] for row in rows]
    if not folder_ids:
        return
    conn.executemany("DELETE FROM file_folders WHERE folder_id = ?", [(item,) for item in folder_ids])
    conn.executemany("DELETE FROM folders WHERE folder_id = ?", [(item,) for item in folder_ids])
    conn.commit()


def project_catalog_layers(
    conn: sqlite3.Connection,
    *,
    batch_size: int,
    max_docs: int,
    semantic_folders: bool,
    lite_fts: bool,
) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) FROM files WHERE deleted_at IS NULL").fetchone()[0]
    if max_docs > 0:
        total = min(total, max_docs)
    if semantic_folders:
        conn.execute("DROP INDEX IF EXISTS idx_file_folders_folder")
    processed = 0
    folder_counts: collections.Counter[str] = collections.Counter()
    folder_cache = preload_folder_paths(conn)
    last_file_ref = ""
    while True:
        rows = conn.execute(
            """
            SELECT file_ref, source_path, title, metadata_json
            FROM files
            WHERE deleted_at IS NULL AND file_ref > ?
            ORDER BY file_ref
            LIMIT ?
            """,
            (last_file_ref, batch_size),
        ).fetchall()
        if not rows or (max_docs > 0 and processed >= max_docs):
            break
        if max_docs > 0 and processed + len(rows) > max_docs:
            rows = rows[: max_docs - processed]
        last_file_ref = rows[-1]["file_ref"]
        membership_rows = []
        fts_rows = []
        for row in rows:
            metadata = safe_json_obj(row["metadata_json"])
            if lite_fts:
                fts_text = lite_fts_text(metadata)
                fts_rows.append((row["file_ref"], metadata.get("title") or row["title"] or "", fts_text, fts_text))
            if semantic_folders:
                for folder_path in semantic_folder_paths(metadata):
                    ensure_folder(conn, folder_cache, folder_path, kind="semantic")
                    membership_rows.append((row["file_ref"], folder_id(folder_path), "{}"))
                    folder_counts[folder_path] += 1
        if lite_fts and fts_rows:
            conn.executemany(
                "INSERT OR REPLACE INTO file_fts(file_ref, title, body, metadata_text) VALUES (?, ?, ?, ?)",
                fts_rows,
            )
        if semantic_folders and membership_rows:
            conn.executemany(
                """
                INSERT INTO file_folders(file_ref, folder_id, metadata_json)
                VALUES (?, ?, ?)
                ON CONFLICT(file_ref, folder_id) DO UPDATE SET metadata_json = excluded.metadata_json
                """,
                membership_rows,
            )
        processed += len(rows)
        conn.commit()
        LOGGER.info("projected %d/%d files", processed, total)
        if max_docs > 0 and processed >= max_docs:
            break
    if semantic_folders:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_file_folders_folder ON file_folders(folder_id)")
    conn.commit()
    return {
        "processed_files": processed,
        "semantic_folders": semantic_folders,
        "lite_fts": lite_fts,
        "folder_memberships_added": sum(folder_counts.values()),
        "unique_semantic_folders": len(folder_counts),
        "top_semantic_folders": folder_counts.most_common(30),
        "file_fts_total": conn.execute("SELECT COUNT(*) FROM file_fts").fetchone()[0],
        "folders_total": conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0],
        "file_folders_total": conn.execute("SELECT COUNT(*) FROM file_folders").fetchone()[0],
    }


def probe_metadata(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    questions = load_selected_questions(args)
    with sqlite3.connect(workspace / "filesystem.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        field_ids = metadata_field_ids(conn)
        rows = [probe_one_question_metadata(conn, field_ids, question, args.limit_terms) for question in questions]
    summary = probe_summary(rows)
    summary.update({"workspace": str(workspace), "seconds": round(time.time() - started, 3), "questions": rows})
    output = Path(args.output).expanduser().resolve() if args.output else DEFAULT_RESULTS_DIR / f"metadata-probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    write_json(output, summary)
    return {"output": str(output), **{key: value for key, value in summary.items() if key != "questions"}}


def build_term_index(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    db_path = workspace / "filesystem.sqlite"
    if not db_path.exists():
        raise SystemExit(f"workspace is not registered: {workspace}")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA temp_store = MEMORY")
        summary = populate_semantic_terms(conn, batch_size=args.batch_size, max_docs=args.max_docs)
    summary.update({"workspace": str(workspace), "seconds": round(time.time() - started, 3)})
    write_json(workspace.parent / "semantic_terms_summary.json", summary)
    return summary


def populate_semantic_terms(conn: sqlite3.Connection, *, batch_size: int, max_docs: int) -> dict[str, Any]:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS semantic_terms (
            term TEXT NOT NULL,
            file_ref TEXT NOT NULL,
            source_type TEXT NOT NULL,
            field TEXT NOT NULL,
            weight REAL NOT NULL,
            PRIMARY KEY(term, file_ref, field)
        );
        DROP INDEX IF EXISTS idx_semantic_terms_source_term;
        DROP INDEX IF EXISTS idx_semantic_terms_file;
        DELETE FROM semantic_terms;
        """
    )
    total = conn.execute("SELECT COUNT(*) FROM files WHERE deleted_at IS NULL").fetchone()[0]
    if max_docs > 0:
        total = min(total, max_docs)
    processed = 0
    inserted = 0
    source_counts: collections.Counter[str] = collections.Counter()
    last_file_ref = ""
    while True:
        rows = conn.execute(
            """
            SELECT file_ref, source_type, metadata_json
            FROM files
            WHERE deleted_at IS NULL AND file_ref > ?
            ORDER BY file_ref
            LIMIT ?
            """,
            (last_file_ref, batch_size),
        ).fetchall()
        if not rows or (max_docs > 0 and processed >= max_docs):
            break
        if max_docs > 0 and processed + len(rows) > max_docs:
            rows = rows[: max_docs - processed]
        last_file_ref = rows[-1]["file_ref"]
        term_rows = []
        for row in rows:
            metadata = safe_json_obj(row["metadata_json"])
            source_type = metadata.get("source_type") or row["source_type"] or ""
            source_counts[source_type] += 1
            for term, field, weight in semantic_index_terms(metadata):
                term_rows.append((term, row["file_ref"], source_type, field, weight))
        if term_rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO semantic_terms(term, file_ref, source_type, field, weight)
                VALUES (?, ?, ?, ?, ?)
                """,
                term_rows,
            )
            inserted += len(term_rows)
        processed += len(rows)
        conn.commit()
        LOGGER.info("indexed semantic terms %d/%d files", processed, total)
        if max_docs > 0 and processed >= max_docs:
            break
    LOGGER.info("rebuilding semantic term indexes")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_semantic_terms_source_term ON semantic_terms(source_type, term)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_semantic_terms_file ON semantic_terms(file_ref)")
    stats = rebuild_term_stats(conn)
    conn.commit()
    return {
        "processed_files": processed,
        "inserted_term_rows": inserted,
        "semantic_terms_total": conn.execute("SELECT COUNT(*) FROM semantic_terms").fetchone()[0],
        "unique_terms": conn.execute("SELECT COUNT(DISTINCT term) FROM semantic_terms").fetchone()[0],
        "semantic_term_stats_total": stats["semantic_term_stats_total"],
        "source_counts": dict(sorted(source_counts.items())),
    }


def build_term_stats(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    with sqlite3.connect(workspace / "filesystem.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        summary = rebuild_term_stats(conn)
    summary.update({"workspace": str(workspace), "seconds": round(time.time() - started, 3)})
    write_json(workspace.parent / "semantic_term_stats_summary.json", summary)
    return summary


def rebuild_term_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    LOGGER.info("rebuilding semantic term stats")
    conn.executescript(
        """
        DROP TABLE IF EXISTS semantic_term_stats;
        CREATE TABLE semantic_term_stats AS
        SELECT source_type, term, COUNT(DISTINCT file_ref) AS df
        FROM semantic_terms
        GROUP BY source_type, term;
        CREATE UNIQUE INDEX idx_semantic_term_stats_source_term
          ON semantic_term_stats(source_type, term);
        """
    )
    return {
        "semantic_term_stats_total": conn.execute("SELECT COUNT(*) FROM semantic_term_stats").fetchone()[0],
    }


def semantic_index_terms(metadata: dict[str, str]) -> list[tuple[str, str, float]]:
    weighted_fields = {
        "source_type": 10.0,
        "repo": 9.0,
        "project": 9.0,
        "channel": 8.0,
        "customer": 10.0,
        "source_bucket": 8.0,
        "doc_type": 6.0,
        "status": 5.0,
        "domain": 5.0,
        "topic": 8.0,
        "title": 7.0,
        "entities": 7.0,
        "constraints": 7.0,
        "summary": 4.0,
        "intent": 4.0,
    }
    rows: list[tuple[str, str, float]] = []
    for field, weight in weighted_fields.items():
        text = metadata.get(field, "")
        if not text:
            continue
        for raw in candidate_terms(text):
            for alias in term_aliases(slug(raw)):
                if not alias or alias in STOPWORDS or len(alias) < 3:
                    continue
                rows.append((alias, field, weight + term_weight_bonus(alias, raw)))
    dedup: dict[tuple[str, str], float] = {}
    for term, field, weight in rows:
        key = (term, field)
        dedup[key] = max(dedup.get(key, 0.0), weight)
    ranked = sorted(((term, field, weight) for (term, field), weight in dedup.items()), key=lambda item: (-item[2], item[0], item[1]))
    return ranked[:40]


def term_weight_bonus(term: str, raw: str) -> float:
    bonus = 0.0
    if raw.isupper() and len(raw) >= 3:
        bonus += 3
    if re.search(r"\d", term):
        bonus += 2
    if re.search(r"[-_.+]", term):
        bonus += 1
    return bonus


def probe_term_index(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    questions = load_selected_questions(args)
    with sqlite3.connect(workspace / "filesystem.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        rows = [probe_one_question_term_index(conn, question, args.limit_terms) for question in questions]
    summary = probe_summary(rows)
    summary.update({"workspace": str(workspace), "seconds": round(time.time() - started, 3), "questions": rows})
    output = Path(args.output).expanduser().resolve() if args.output else DEFAULT_RESULTS_DIR / f"term-index-probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    write_json(output, summary)
    return {"output": str(output), **{key: value for key, value in summary.items() if key != "questions"}}


def probe_one_question_term_index(conn: sqlite3.Connection, question: dict[str, Any], limit_terms: int) -> dict[str, Any]:
    terms = question_terms(question["question"], limit=limit_terms)
    ids = semantic_term_probe_ids(conn, terms, question["source_types"], limit=200)
    expected = set(question["expected_doc_ids"])
    hit = bool(expected.intersection(ids))
    return {
        "question_id": question["question_id"],
        "source_types": question["source_types"],
        "expected_doc_ids": question["expected_doc_ids"],
        "terms": terms,
        "hit_any": hit,
        "best_hit": {"candidate_count_lte_200": len(ids), "hit": hit} if hit else None,
        "top_document_ids": ids[:20],
    }


def semantic_term_probe_ids(conn: sqlite3.Connection, terms: list[str], source_types: list[str], *, limit: int) -> list[str]:
    terms = [slug(term) for term in terms if slug(term)]
    if not terms:
        return []
    placeholders = ", ".join("?" for _ in terms)
    source_clause = ""
    params: list[Any] = [*terms]
    if source_types:
        source_placeholders = ", ".join("?" for _ in source_types)
        source_clause = f"AND st.source_type IN ({source_placeholders})"
        params.extend(source_types)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT
            f.external_id,
            SUM(st.weight / (1.0 + (CAST(sts.df AS REAL) / 100.0))) AS score
        FROM semantic_terms st
        JOIN semantic_term_stats sts
          ON sts.source_type = st.source_type
         AND sts.term = st.term
        JOIN files f ON f.file_ref = st.file_ref
        WHERE st.term IN ({placeholders})
          {source_clause}
          AND f.deleted_at IS NULL
        GROUP BY f.file_ref
        ORDER BY score DESC, f.source_path
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [row["external_id"] for row in rows if row["external_id"]]


def build_vector_index(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_workspace(workspace)
    index_path = vector_index_path(workspace, args.field_mode, args.index_name)
    embedder = make_embedder(
        args.embedding_provider,
        args.embedding_model,
        dimensions=args.embedding_dimensions,
        timeout=args.embedding_timeout,
    )
    cache = EmbeddingCache(workspace / DEFAULT_VECTOR_INDEX_DIRNAME / "embedding_cache.sqlite")
    cache_model = embedding_cache_model_key(args.embedding_model, args.embedding_dimensions)
    index = SQLiteVecSemanticIndex(index_path)
    source_rows = 0
    indexed_rows = 0
    skipped_empty = 0
    initialized = False
    if index_path.exists() and not args.reset:
        try:
            index.info()
            initialized = True
        except Exception:  # noqa: BLE001 - malformed research indexes are rebuilt.
            initialized = False
    if args.reset and index_path.exists():
        index_path.unlink()

    with sqlite3.connect(workspace / "filesystem.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        for rows in iter_file_batches(conn, batch_size=args.batch_size, max_docs=args.max_docs):
            source_rows += len(rows)
            prepared = []
            for row in rows:
                metadata = safe_json_obj(row["metadata_json"])
                text = vector_text_for_row(
                    row,
                    metadata,
                    field_mode=args.field_mode,
                    max_fulltext_chars=args.max_fulltext_chars,
                )
                if not text:
                    skipped_empty += 1
                    continue
                prepared.append((row, metadata, text))
            if not prepared:
                continue
            texts = [item[2] for item in prepared]
            vectors = cache.embed_texts(
                texts,
                model=cache_model,
                provider=args.embedding_provider,
                embedder=embedder,
                batch_size=args.batch_size,
            )
            if not initialized:
                index.reset(
                    dimension=len(vectors[0]),
                    metadata={
                        "workspace": str(workspace),
                        "field_mode": args.field_mode,
                        "embedding_provider": args.embedding_provider,
                        "embedding_model": args.embedding_model,
                        "embedding_dimensions": args.embedding_dimensions,
                        "max_fulltext_chars": args.max_fulltext_chars,
                    },
                )
                initialized = True
            records = [
                SemanticIndexRecord(
                    file_ref=row["file_ref"],
                    external_id=row["external_id"],
                    source_type=metadata.get("source_type") or row["source_type"] or "",
                    source_path=row["source_path"] or "",
                    title=metadata.get("title") or row["title"] or "",
                    text=text,
                    vector=vector,
                    metadata={
                        "field_mode": args.field_mode,
                        "text_chars": len(text),
                        **index_metadata_projection(metadata),
                    },
                )
                for (row, metadata, text), vector in zip(prepared, vectors)
            ]
            indexed_rows += index.upsert_many(records)
            LOGGER.info(
                "vector indexed mode=%s %d/%s files",
                args.field_mode,
                indexed_rows,
                args.max_docs or "all",
            )
    if not initialized:
        raise SystemExit("no non-empty documents were available for vector indexing")
    summary = {
        "workspace": str(workspace),
        "index_path": str(index_path),
        "field_mode": args.field_mode,
        "embedding_provider": args.embedding_provider,
        "embedding_model": args.embedding_model,
        "embedding_dimensions": args.embedding_dimensions,
        "source_rows": source_rows,
        "indexed_rows": indexed_rows,
        "skipped_empty": skipped_empty,
        "seconds": round(time.time() - started, 3),
        "index_info": index.info(),
    }
    write_json(index_path.with_suffix(".summary.json"), summary)
    return summary


def probe_vector_index(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_workspace(workspace)
    questions = load_selected_questions(args)
    index = SQLiteVecSemanticIndex(vector_index_path(workspace, args.field_mode, args.index_name))
    embedder = make_embedder(
        args.embedding_provider,
        args.embedding_model,
        dimensions=args.embedding_dimensions,
        timeout=args.embedding_timeout,
    )
    cache = EmbeddingCache(workspace / DEFAULT_VECTOR_INDEX_DIRNAME / "embedding_cache.sqlite")
    cache_model = embedding_cache_model_key(args.embedding_model, args.embedding_dimensions)
    rows = []
    for question in questions:
        question_started = time.time()
        vector = cache.embed_texts(
            [question["question"]],
            model=cache_model,
            provider=args.embedding_provider,
            embedder=embedder,
            batch_size=1,
        )[0]
        results = index.search(
            vector,
            limit=args.candidate_limit,
            filters={"source_type": question["source_types"]} if question["source_types"] else None,
            fetch_multiplier=args.fetch_multiplier,
        )
        row = probe_one_question_vector(question, results)
        row["seconds"] = round(time.time() - question_started, 3)
        rows.append(row)
    summary = vector_probe_summary(rows)
    summary.update(
        {
            "workspace": str(workspace),
            "index_path": str(index.db_path),
            "index_info": index.info(),
            "field_mode": args.field_mode,
            "embedding_provider": args.embedding_provider,
            "embedding_model": args.embedding_model,
            "embedding_dimensions": args.embedding_dimensions,
            "candidate_limit": args.candidate_limit,
            "fetch_multiplier": args.fetch_multiplier,
            "seconds": round(time.time() - started, 3),
            "questions": rows,
        }
    )
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else DEFAULT_RESULTS_DIR
        / f"vector-probe-{args.field_mode}-{args.embedding_provider}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    write_json(output, summary)
    return {"output": str(output), **{key: value for key, value in summary.items() if key != "questions"}}


def probe_vector_rerank(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_workspace(workspace)
    questions = load_selected_questions(args)
    index = SQLiteVecSemanticIndex(vector_index_path(workspace, args.field_mode, args.index_name))
    embedder = make_embedder(
        args.embedding_provider,
        args.embedding_model,
        dimensions=args.embedding_dimensions,
        timeout=args.embedding_timeout,
    )
    cache = EmbeddingCache(workspace / DEFAULT_VECTOR_INDEX_DIRNAME / "embedding_cache.sqlite")
    cache_model = embedding_cache_model_key(args.embedding_model, args.embedding_dimensions)
    reranker = make_reranker(
        args.reranker_provider,
        args.reranker_model,
        timeout=args.reranker_timeout,
    )
    rows = []
    with sqlite3.connect(workspace / "filesystem.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        for question in questions:
            LOGGER.info(
                "vector+rerank question=%s candidates=%d rerank_limit=%d",
                question["question_id"],
                args.candidate_limit,
                args.rerank_limit,
            )
            vector = cache.embed_texts(
                [question["question"]],
                model=cache_model,
                provider=args.embedding_provider,
                embedder=embedder,
                batch_size=1,
            )[0]
            vector_results = index.search(
                vector,
                limit=args.candidate_limit,
                filters={"source_type": question["source_types"]} if question["source_types"] else None,
                fetch_multiplier=args.fetch_multiplier,
            )
            candidates = candidate_details_for_results(conn, vector_results[: args.rerank_limit])
            try:
                reranked = reranker.rerank(question["question"], candidates)
                error = None
            except Exception as exc:  # noqa: BLE001 - research runner records failures.
                LOGGER.warning("rerank failed question=%s error=%s", question["question_id"], exc)
                reranked = candidates
                error = f"{type(exc).__name__}: {exc}"
            row = probe_one_question_rerank(question, vector_results, reranked)
            row["rerank_error"] = error
            rows.append(row)
    summary = rerank_probe_summary(rows)
    summary.update(
        {
            "workspace": str(workspace),
            "index_path": str(index.db_path),
            "index_info": index.info(),
            "field_mode": args.field_mode,
            "embedding_provider": args.embedding_provider,
            "embedding_model": args.embedding_model,
            "embedding_dimensions": args.embedding_dimensions,
            "candidate_limit": args.candidate_limit,
            "rerank_limit": args.rerank_limit,
            "fetch_multiplier": args.fetch_multiplier,
            "reranker_provider": args.reranker_provider,
            "reranker_model": args.reranker_model,
            "reranker_timeout": args.reranker_timeout,
            "seconds": round(time.time() - started, 3),
            "questions": rows,
        }
    )
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else DEFAULT_RESULTS_DIR
        / f"vector-rerank-{args.field_mode}-{args.reranker_provider}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    write_json(output, summary)
    return {"output": str(output), **{key: value for key, value in summary.items() if key != "questions"}}


def probe_vector_union(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_workspace(workspace)
    questions = load_selected_questions(args)
    specs = parse_vector_index_specs(args.index_specs)
    indexes = [
        (field_mode, index_name, SQLiteVecSemanticIndex(vector_index_path(workspace, field_mode, index_name)))
        for field_mode, index_name in specs
    ]
    embedder = make_embedder(
        args.embedding_provider,
        args.embedding_model,
        dimensions=args.embedding_dimensions,
        timeout=args.embedding_timeout,
    )
    cache = EmbeddingCache(workspace / DEFAULT_VECTOR_INDEX_DIRNAME / "embedding_cache.sqlite")
    cache_model = embedding_cache_model_key(args.embedding_model, args.embedding_dimensions)
    rows = []
    for question in questions:
        question_started = time.time()
        vector = cache.embed_texts(
            [question["question"]],
            model=cache_model,
            provider=args.embedding_provider,
            embedder=embedder,
            batch_size=1,
        )[0]
        result_sets = {}
        for field_mode, _index_name, index in indexes:
            result_sets[field_mode] = index.search(
                vector,
                limit=args.per_index_limit,
                filters={"source_type": question["source_types"]} if question["source_types"] else None,
                fetch_multiplier=args.fetch_multiplier,
            )
        merged = merge_vector_result_sets(result_sets, limit=args.candidate_limit)
        row = probe_one_question_vector(question, merged)
        row["seconds"] = round(time.time() - question_started, 3)
        row["index_specs"] = [{"field_mode": field, "index_name": name} for field, name in specs]
        rows.append(row)
    summary = vector_probe_summary(rows)
    summary.update(
        {
            "workspace": str(workspace),
            "index_specs": [{"field_mode": field, "index_name": name} for field, name in specs],
            "embedding_provider": args.embedding_provider,
            "embedding_model": args.embedding_model,
            "embedding_dimensions": args.embedding_dimensions,
            "per_index_limit": args.per_index_limit,
            "candidate_limit": args.candidate_limit,
            "fetch_multiplier": args.fetch_multiplier,
            "seconds": round(time.time() - started, 3),
            "questions": rows,
        }
    )
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else DEFAULT_RESULTS_DIR
        / f"vector-union-{args.embedding_provider}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    write_json(output, summary)
    return {"output": str(output), **{key: value for key, value in summary.items() if key != "questions"}}


def probe_vector_groups(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    ensure_workspace(workspace)
    questions = load_selected_questions(args)
    specs = parse_vector_index_specs(args.index_specs)
    group_fields = [item.strip() for item in args.group_fields.split(",") if item.strip()]
    indexes = [
        (field_mode, index_name, SQLiteVecSemanticIndex(vector_index_path(workspace, field_mode, index_name)))
        for field_mode, index_name in specs
    ]
    embedder = make_embedder(
        args.embedding_provider,
        args.embedding_model,
        dimensions=args.embedding_dimensions,
        timeout=args.embedding_timeout,
    )
    cache = EmbeddingCache(workspace / DEFAULT_VECTOR_INDEX_DIRNAME / "embedding_cache.sqlite")
    cache_model = embedding_cache_model_key(args.embedding_model, args.embedding_dimensions)
    rows = []
    with sqlite3.connect(workspace / "filesystem.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        for question in questions:
            question_started = time.time()
            vector = cache.embed_texts(
                [question["question"]],
                model=cache_model,
                provider=args.embedding_provider,
                embedder=embedder,
                batch_size=1,
            )[0]
            result_sets = {}
            for field_mode, _index_name, index in indexes:
                result_sets[field_mode] = index.search(
                    vector,
                    limit=args.per_index_limit,
                    filters={"source_type": question["source_types"]} if question["source_types"] else None,
                    fetch_multiplier=args.fetch_multiplier,
                )
            merged = merge_vector_result_sets(result_sets, limit=args.candidate_limit)
            groups = aggregate_vector_groups(merged, group_fields=group_fields, limit=args.group_limit)
            expected_keys = expected_metadata_group_keys(conn, question["expected_doc_ids"], group_fields)
            group_rank = first_group_rank(groups, expected_keys)
            vector_row = probe_one_question_vector(question, merged)
            rows.append(
                {
                    **vector_row,
                    "group_best_rank": group_rank,
                    "group_hit_any": group_rank is not None,
                    "expected_group_keys": sorted(expected_keys)[:40],
                    "top_groups": groups,
                    "seconds": round(time.time() - question_started, 3),
                }
            )
    summary = vector_group_probe_summary(rows)
    summary.update(
        {
            "workspace": str(workspace),
            "index_specs": [{"field_mode": field, "index_name": name} for field, name in specs],
            "embedding_provider": args.embedding_provider,
            "embedding_model": args.embedding_model,
            "embedding_dimensions": args.embedding_dimensions,
            "per_index_limit": args.per_index_limit,
            "candidate_limit": args.candidate_limit,
            "fetch_multiplier": args.fetch_multiplier,
            "group_fields": group_fields,
            "group_limit": args.group_limit,
            "seconds": round(time.time() - started, 3),
            "questions": rows,
        }
    )
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else DEFAULT_RESULTS_DIR
        / f"vector-groups-{args.embedding_provider}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    write_json(output, summary)
    return {"output": str(output), **{key: value for key, value in summary.items() if key != "questions"}}


def aggregate_vector_groups(
    results: list[SemanticSearchResult],
    *,
    group_fields: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for rank, result in enumerate(results, 1):
        for field in group_fields:
            for value in group_values(result.metadata.get(field)):
                key = f"{field}={value}"
                group = groups.setdefault(
                    key,
                    {
                        "key": key,
                        "field": field,
                        "value": value,
                        "score": 0.0,
                        "candidate_count": 0,
                        "best_rank": rank,
                        "top_document_ids": [],
                    },
                )
                group["score"] += 1.0 / (rank + 60.0)
                group["candidate_count"] += 1
                group["best_rank"] = min(int(group["best_rank"]), rank)
                if result.external_id and result.external_id not in group["top_document_ids"]:
                    group["top_document_ids"].append(result.external_id)
                    group["top_document_ids"] = group["top_document_ids"][:5]
    ranked = sorted(
        groups.values(),
        key=lambda item: (-float(item["score"]), int(item["best_rank"]), -int(item["candidate_count"]), item["key"]),
    )
    return [
        {
            **item,
            "score": round(float(item["score"]), 6),
        }
        for item in ranked[:limit]
    ]


def expected_metadata_group_keys(
    conn: sqlite3.Connection,
    expected_doc_ids: list[str],
    group_fields: list[str],
) -> set[str]:
    if not expected_doc_ids:
        return set()
    placeholders = ", ".join("?" for _ in expected_doc_ids)
    rows = conn.execute(
        f"""
        SELECT metadata_json
        FROM files
        WHERE external_id IN ({placeholders})
          AND deleted_at IS NULL
        """,
        expected_doc_ids,
    ).fetchall()
    keys: set[str] = set()
    for row in rows:
        metadata = safe_json_obj(row["metadata_json"])
        for field in group_fields:
            for value in group_values(metadata.get(field)):
                keys.add(f"{field}={value}")
    return keys


def first_group_rank(groups: list[dict[str, Any]], expected_keys: set[str]) -> int | None:
    if not expected_keys:
        return None
    return next((index for index, group in enumerate(groups, 1) if group["key"] in expected_keys), None)


def vector_group_probe_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answerable_rows = [row for row in rows if row.get("expected_doc_ids")]
    question_seconds = [float(row.get("seconds") or 0) for row in rows]
    return {
        **vector_probe_summary(rows),
        "group_hit_at_1": group_hit_at(rows, 1),
        "group_hit_at_3": group_hit_at(rows, 3),
        "group_hit_at_5": group_hit_at(rows, 5),
        "group_hit_at_10": group_hit_at(rows, 10),
        "group_hit_at_20": group_hit_at(rows, 20),
        "answerable_group_hit_at_1": group_hit_at(answerable_rows, 1),
        "answerable_group_hit_at_3": group_hit_at(answerable_rows, 3),
        "answerable_group_hit_at_5": group_hit_at(answerable_rows, 5),
        "answerable_group_hit_at_10": group_hit_at(answerable_rows, 10),
        "answerable_group_hit_at_20": group_hit_at(answerable_rows, 20),
        "group_misses": [row["question_id"] for row in rows if row.get("expected_doc_ids") and not row["group_hit_any"]],
        "avg_question_seconds": avg(question_seconds),
        "p95_question_seconds": percentile(question_seconds, 0.95),
        "max_question_seconds": max(question_seconds, default=0),
    }


def group_hit_at(rows: list[dict[str, Any]], k: int) -> float:
    if not rows:
        return 0
    return sum(1 for row in rows if row.get("group_best_rank") is not None and row["group_best_rank"] <= k) / len(rows)


def group_values(value: Any) -> list[str]:
    if value is None:
        return []
    raw_values: list[str] = []
    if isinstance(value, list):
        raw_values = [text_value(item) for item in value]
    else:
        text = text_value(value)
        if "," in text or "\n" in text:
            raw_values = re.split(r"[,\n]+", text)
        else:
            raw_values = [text]
    normalized = []
    for item in raw_values:
        compact = re.sub(r"\s+", " ", str(item)).strip().lower()
        if not compact or compact in {"unknown", "n/a", "none", "null"}:
            continue
        if len(compact) > 80:
            continue
        normalized.append(compact)
    return dedupe(normalized)


def parse_vector_index_specs(raw: str) -> list[tuple[str, str]]:
    specs = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            field_mode, index_name = item.split(":", 1)
        else:
            field_mode, index_name = item, item
        specs.append((field_mode.strip(), index_name.strip()))
    if not specs:
        raise SystemExit("--index-specs must include at least one field:index entry")
    return specs


def merge_vector_result_sets(
    result_sets: dict[str, list[SemanticSearchResult]],
    *,
    limit: int,
) -> list[SemanticSearchResult]:
    scored: dict[str, dict[str, Any]] = {}
    for field_mode, results in result_sets.items():
        for rank, result in enumerate(results, 1):
            item = scored.setdefault(
                result.file_ref,
                {
                    "score": 0.0,
                    "best_rank": rank,
                    "result": result,
                    "sources": [],
                },
            )
            item["score"] += 1.0 / (rank + 60.0)
            item["best_rank"] = min(int(item["best_rank"]), rank)
            if result.distance < item["result"].distance:
                item["result"] = result
            item["sources"].append({"field_mode": field_mode, "rank": rank, "distance": result.distance})
    merged = []
    for item in sorted(scored.values(), key=lambda value: (-value["score"], value["best_rank"])):
        result = item["result"]
        metadata = dict(result.metadata)
        metadata["union_score"] = item["score"]
        metadata["union_sources"] = item["sources"]
        merged.append(
            SemanticSearchResult(
                file_ref=result.file_ref,
                distance=-float(item["score"]),
                external_id=result.external_id,
                source_type=result.source_type,
                source_path=result.source_path,
                title=result.title,
                text_hash=result.text_hash,
                metadata=metadata,
            )
        )
        if len(merged) >= limit:
            break
    return merged


def compare_vector_fields(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    modes = [item.strip() for item in args.field_modes.split(",") if item.strip()]
    summaries = {}
    output_dir = (
        Path(args.output).expanduser().resolve()
        if args.output
        else DEFAULT_RESULTS_DIR / f"vector-field-ablation-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    for mode in modes:
        if mode not in {"summary", "metadata", "fulltext", "sampled_fulltext"}:
            raise SystemExit(f"unknown vector field mode: {mode}")
        build_args = argparse.Namespace(**{**vars(args), "field_mode": mode})
        build_summary = build_vector_index(build_args)
        probe_args = argparse.Namespace(
            **{
                **vars(args),
                "field_mode": mode,
                "output": str(output_dir / f"{mode}.probe.json"),
            }
        )
        probe_summary = probe_vector_index(probe_args)
        summaries[mode] = {
            "build": build_summary,
            "probe": probe_summary,
        }
    comparison = {
        "workspace": str(Path(args.workspace).expanduser().resolve()),
        "field_modes": modes,
        "embedding_provider": args.embedding_provider,
        "embedding_model": args.embedding_model,
        "embedding_dimensions": args.embedding_dimensions,
        "max_docs": args.max_docs,
        "candidate_limit": args.candidate_limit,
        "seconds": round(time.time() - started, 3),
        "ranking": sorted(
            [
                {
                    "field_mode": mode,
                    "hit_at_1": value["probe"].get("hit_at_1", 0),
                    "hit_at_5": value["probe"].get("hit_at_5", 0),
                    "hit_at_10": value["probe"].get("hit_at_10", 0),
                    "hit_at_50": value["probe"].get("hit_at_50", 0),
                    "hit_at_limit": value["probe"].get("hit_at_limit", 0),
                    "mean_best_rank": value["probe"].get("mean_best_rank", 0),
                    "indexed_rows": value["build"].get("indexed_rows", 0),
                }
                for mode, value in summaries.items()
            ],
            key=lambda item: (
                -item["hit_at_limit"],
                item["mean_best_rank"] or 10**9,
                -item["hit_at_10"],
                item["field_mode"],
            ),
        ),
        "summaries": summaries,
    }
    write_json(output_dir / "summary.json", comparison)
    write_vector_ablation_summary(output_dir / "summary.md", comparison)
    return {"output_dir": str(output_dir), "ranking": comparison["ranking"]}


def probe_one_question_vector(
    question: dict[str, Any],
    results: list[Any],
) -> dict[str, Any]:
    expected = set(question["expected_doc_ids"])
    ids = [result.external_id for result in results if result.external_id]
    best_rank = next(
        (index for index, doc_id in enumerate(ids, 1) if doc_id in expected),
        None,
    )
    return {
        "question_id": question["question_id"],
        "source_types": question["source_types"],
        "expected_doc_ids": question["expected_doc_ids"],
        "candidate_count": len(results),
        "hit_any": best_rank is not None,
        "best_rank": best_rank,
        "top_document_ids": ids[:20],
        "top_hits": [
            {
                "rank": index,
                "document_id": result.external_id,
                "distance": result.distance,
                "file_ref": result.file_ref,
                "source_type": result.source_type,
                "source_path": result.source_path,
                "title": result.title,
            }
            for index, result in enumerate(results[:20], 1)
        ],
    }


def candidate_details_for_results(
    conn: sqlite3.Connection,
    results: list[Any],
) -> list[dict[str, Any]]:
    if not results:
        return []
    file_refs = [result.file_ref for result in results]
    placeholders = ", ".join("?" for _ in file_refs)
    rows_by_ref = {
        row["file_ref"]: row
        for row in conn.execute(
            f"""
            SELECT file_ref, external_id, source_type, source_path, title, metadata_json
            FROM files
            WHERE file_ref IN ({placeholders})
            """,
            file_refs,
        ).fetchall()
    }
    candidates = []
    for rank, result in enumerate(results, 1):
        row = rows_by_ref.get(result.file_ref)
        metadata = safe_json_obj(row["metadata_json"] if row else "{}")
        candidates.append(
            {
                "rank": rank,
                "file_ref": result.file_ref,
                "document_id": result.external_id,
                "source_type": result.source_type,
                "source_path": result.source_path,
                "title": result.title,
                "vector_distance": result.distance,
                "metadata": {
                    key: metadata.get(key, "")
                    for key in rerank_metadata_fields(metadata)
                    if metadata.get(key)
                },
            }
        )
    return candidates


def rerank_metadata_fields(metadata: dict[str, Any]) -> list[str]:
    if "semantic_summary" in metadata:
        return [
            "semantic_summary",
            "semantic_topics",
            "semantic_entities",
            "semantic_constraints",
            "semantic_problems",
            "semantic_actions",
            "semantic_evidence_terms",
            "semantic_measurements",
            "semantic_time_hints",
        ]
    return [
        "summary",
        "intent",
        "entities",
        "constraints",
        "topic",
        "domain",
        "doc_type",
        "repo",
        "project",
        "channel",
        "space",
        "customer",
        "status",
        "year",
        "month",
    ]


def probe_one_question_rerank(
    question: dict[str, Any],
    vector_results: list[Any],
    reranked: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = set(question["expected_doc_ids"])
    vector_ids = [result.external_id for result in vector_results if result.external_id]
    reranked_ids = [row["document_id"] for row in reranked if row.get("document_id")]
    vector_rank = first_expected_rank(vector_ids, expected)
    rerank_rank = first_expected_rank(reranked_ids, expected)
    return {
        "question_id": question["question_id"],
        "source_types": question["source_types"],
        "expected_doc_ids": question["expected_doc_ids"],
        "vector_best_rank": vector_rank,
        "rerank_best_rank": rerank_rank,
        "vector_hit_any": vector_rank is not None,
        "rerank_hit_any": rerank_rank is not None,
        "vector_top_document_ids": vector_ids[:20],
        "reranked_top_document_ids": reranked_ids[:20],
        "reranked": reranked[:20],
    }


def first_expected_rank(ids: list[str], expected: set[str]) -> int | None:
    return next((index for index, doc_id in enumerate(ids, 1) if doc_id in expected), None)


def rerank_probe_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    vector_ranks = [row["vector_best_rank"] for row in rows if row.get("vector_best_rank") is not None]
    rerank_ranks = [row["rerank_best_rank"] for row in rows if row.get("rerank_best_rank") is not None]
    return {
        "question_count": len(rows),
        "vector_hit_at_1": rank_hit_at(rows, "vector_best_rank", 1),
        "vector_hit_at_3": rank_hit_at(rows, "vector_best_rank", 3),
        "vector_hit_at_5": rank_hit_at(rows, "vector_best_rank", 5),
        "vector_hit_at_10": rank_hit_at(rows, "vector_best_rank", 10),
        "vector_hit_at_50": rank_hit_at(rows, "vector_best_rank", 50),
        "vector_hit_at_limit": len(vector_ranks) / len(rows) if rows else 0,
        "rerank_hit_at_1": rank_hit_at(rows, "rerank_best_rank", 1),
        "rerank_hit_at_3": rank_hit_at(rows, "rerank_best_rank", 3),
        "rerank_hit_at_5": rank_hit_at(rows, "rerank_best_rank", 5),
        "rerank_hit_at_10": rank_hit_at(rows, "rerank_best_rank", 10),
        "rerank_hit_at_limit": len(rerank_ranks) / len(rows) if rows else 0,
        "vector_mean_best_rank": avg([float(rank) for rank in vector_ranks]),
        "rerank_mean_best_rank": avg([float(rank) for rank in rerank_ranks]),
        "vector_misses": [row["question_id"] for row in rows if not row["vector_hit_any"]],
        "rerank_misses": [row["question_id"] for row in rows if not row["rerank_hit_any"]],
    }


def rank_hit_at(rows: list[dict[str, Any]], key: str, k: int) -> float:
    if not rows:
        return 0
    return sum(1 for row in rows if row.get(key) is not None and row[key] <= k) / len(rows)


def vector_probe_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ranks = [row["best_rank"] for row in rows if row.get("best_rank") is not None]
    limit = max((int(row.get("candidate_count") or 0) for row in rows), default=0)
    question_seconds = [float(row.get("seconds") or 0) for row in rows]
    answerable_rows = [row for row in rows if row.get("expected_doc_ids")]
    return {
        "question_count": len(rows),
        "answerable_question_count": len(answerable_rows),
        "hit_at_1": hit_at(rows, 1),
        "hit_at_5": hit_at(rows, 5),
        "hit_at_10": hit_at(rows, 10),
        "hit_at_50": hit_at(rows, 50),
        "hit_at_limit": len(ranks) / len(rows) if rows else 0,
        "answerable_hit_at_1": hit_at(answerable_rows, 1),
        "answerable_hit_at_5": hit_at(answerable_rows, 5),
        "answerable_hit_at_10": hit_at(answerable_rows, 10),
        "answerable_hit_at_50": hit_at(answerable_rows, 50),
        "answerable_hit_at_limit": (
            sum(1 for row in answerable_rows if row.get("best_rank") is not None) / len(answerable_rows)
            if answerable_rows
            else 0
        ),
        "candidate_limit_observed": limit,
        "mean_best_rank": avg([float(rank) for rank in ranks]),
        "avg_question_seconds": avg(question_seconds),
        "p95_question_seconds": percentile(question_seconds, 0.95),
        "max_question_seconds": max(question_seconds, default=0),
        "misses": [row["question_id"] for row in rows if not row["hit_any"]],
    }


def hit_at(rows: list[dict[str, Any]], k: int) -> float:
    if not rows:
        return 0
    return sum(1 for row in rows if row.get("best_rank") is not None and row["best_rank"] <= k) / len(rows)


def write_vector_ablation_summary(path: Path, comparison: dict[str, Any]) -> None:
    lines = [
        "# Semantic Vector Field Ablation",
        "",
        "| field mode | indexed docs | hit@1 | hit@5 | hit@10 | hit@50 | hit@limit | mean best rank |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison["ranking"]:
        lines.append(
            "| {mode} | {docs} | {h1:.3f} | {h5:.3f} | {h10:.3f} | {h50:.3f} | {hl:.3f} | {rank:.1f} |".format(
                mode=row["field_mode"],
                docs=row["indexed_rows"],
                h1=row["hit_at_1"],
                h5=row["hit_at_5"],
                h10=row["hit_at_10"],
                h50=row["hit_at_50"],
                hl=row["hit_at_limit"],
                rank=row["mean_best_rank"],
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation Rule",
            "",
            "- `summary` tests whether one compact generated summary is enough.",
            "- `metadata` tests the current recommended recall text: title, summary, intent, entities, constraints, topic, and semantic equivalents.",
            "- `fulltext` tests raw source-text preview embedding without benchmark question/gold leakage.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class EmbeddingCache:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector_blob BLOB,
                    vector_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(provider, model, text_hash)
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(embedding_cache)").fetchall()
            }
            if "vector_blob" not in columns:
                conn.execute("ALTER TABLE embedding_cache ADD COLUMN vector_blob BLOB")
            if "vector_json" not in columns:
                conn.execute("ALTER TABLE embedding_cache ADD COLUMN vector_json TEXT")
            conn.commit()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def embed_texts(
        self,
        texts: list[str],
        *,
        provider: str,
        model: str,
        embedder: Any,
        batch_size: int,
    ) -> list[list[float]]:
        hashes = [SQLiteVecSemanticIndex.text_hash(text) for text in texts]
        cached: dict[str, list[float]] = {}
        with self.connect() as conn:
            for text_hash in sorted(set(hashes)):
                row = conn.execute(
                    """
                    SELECT vector_blob, vector_json
                    FROM embedding_cache
                    WHERE provider = ? AND model = ? AND text_hash = ?
                    """,
                    (provider, model, text_hash),
                ).fetchone()
                if row is not None:
                    cached[text_hash] = decode_vector(row["vector_blob"], row["vector_json"])
        missing_positions = [index for index, text_hash in enumerate(hashes) if text_hash not in cached]
        for start in range(0, len(missing_positions), batch_size):
            positions = missing_positions[start : start + batch_size]
            batch_texts = [texts[index] for index in positions]
            vectors = embed_with_retry(embedder, batch_texts)
            with self.connect() as conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO embedding_cache(
                        provider, model, text_hash, dimension, vector_blob, vector_json
                    )
                    VALUES (?, ?, ?, ?, ?, '')
                    """,
                    [
                        (
                            provider,
                            model,
                            hashes[index],
                            len(vector),
                            encode_vector(vector),
                        )
                        for index, vector in zip(positions, vectors)
                    ],
                )
                conn.commit()
            for index, vector in zip(positions, vectors):
                cached[hashes[index]] = vector
            LOGGER.info("embedded %d/%d uncached texts", min(start + len(positions), len(missing_positions)), len(missing_positions))
        return [cached[text_hash] for text_hash in hashes]


def embed_with_retry(embedder: Any, texts: list[str], *, max_attempts: int = 8) -> list[list[float]]:
    for attempt in range(1, max_attempts + 1):
        try:
            return embedder.embed(texts)
        except Exception as exc:
            if attempt >= max_attempts:
                raise
            delay = min(120.0, 2.0 ** (attempt - 1))
            LOGGER.warning(
                "embedding batch failed attempt=%d/%d texts=%d delay=%.1fs error=%s",
                attempt,
                max_attempts,
                len(texts),
                delay,
                exc,
            )
            time.sleep(delay)
    raise RuntimeError("unreachable embedding retry state")


class OpenAIEmbeddingClient:
    def __init__(self, model: str, *, dimensions: int, timeout: float):
        from openai import OpenAI

        self.model = model
        self.dimensions = dimensions
        self.client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            timeout=timeout,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        kwargs: dict[str, Any] = {"model": self.model, "input": texts}
        if self.dimensions > 0:
            kwargs["dimensions"] = self.dimensions
        response = self.client.embeddings.create(**kwargs)
        return [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]


class OpenAIRerankerClient:
    def __init__(self, model: str, *, timeout: float):
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            timeout=timeout,
        )

    def rerank(self, question: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return []
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You rerank candidate enterprise documents for retrieval. "
                        "Use only the candidate metadata provided. Do not answer the question. "
                        "Return strict JSON with key ranked, an array of objects: "
                        "{rank:int, document_id:string, score:number, reason:string}. "
                        "Rank the most likely evidence document first."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "candidates": compact_rerank_candidates(candidates),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        text = response.choices[0].message.content or "{}"
        ranked_ids = parse_rerank_ids(text)
        return merge_rerank_order(candidates, ranked_ids)


class HeuristicRerankerClient:
    def rerank(self, question: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        question_terms_set = set(question_terms(question, limit=40))
        rows = []
        for candidate in candidates:
            haystack = json.dumps(candidate, ensure_ascii=False).lower()
            score = sum(1 for term in question_terms_set if term and term.lower() in haystack)
            rows.append(
                {
                    **candidate,
                    "score": score,
                    "reason": "term-overlap heuristic",
                }
            )
        rows.sort(key=lambda item: (-float(item["score"]), item["rank"]))
        return [{**row, "rank": index} for index, row in enumerate(rows, 1)]


class HashEmbeddingClient:
    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for term in keyword_terms(text)[:256]:
            digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = sum(value * value for value in vector) ** 0.5
        if norm:
            vector = [value / norm for value in vector]
        return vector


def make_embedder(provider: str, model: str, *, dimensions: int, timeout: float) -> Any:
    if provider == "openai":
        return OpenAIEmbeddingClient(model, dimensions=dimensions, timeout=timeout)
    if provider == "hash":
        return HashEmbeddingClient(dimensions=dimensions if dimensions > 0 else 256)
    raise SystemExit(f"unknown embedding provider: {provider}")


def embedding_cache_model_key(model: str, dimensions: int) -> str:
    return f"{model}:dimensions={dimensions}" if dimensions > 0 else model


def make_reranker(provider: str, model: str, *, timeout: float) -> Any:
    if provider == "openai":
        return OpenAIRerankerClient(model, timeout=timeout)
    if provider == "heuristic":
        return HeuristicRerankerClient()
    raise SystemExit(f"unknown reranker provider: {provider}")


def compact_rerank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for candidate in candidates:
        metadata = candidate.get("metadata") or {}
        compact.append(
            {
                "candidate_rank": candidate.get("rank"),
                "document_id": candidate.get("document_id"),
                "source_type": candidate.get("source_type"),
                "title": candidate.get("title"),
                "source_path": candidate.get("source_path"),
                "vector_distance": candidate.get("vector_distance"),
                "metadata": {
                    key: truncate_text(text_value(value), 700)
                    for key, value in metadata.items()
                    if text_value(value)
                },
            }
        )
    return compact


def parse_rerank_ids(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        data = json.loads(match.group(0)) if match else {}
    ranked = data.get("ranked", []) if isinstance(data, dict) else []
    ids = []
    if isinstance(ranked, list):
        for item in ranked:
            if isinstance(item, dict) and item.get("document_id"):
                ids.append(str(item["document_id"]))
            elif isinstance(item, str):
                ids.append(item)
    return dedupe(ids)


def merge_rerank_order(
    candidates: list[dict[str, Any]],
    ranked_ids: list[str],
) -> list[dict[str, Any]]:
    by_id = {
        str(candidate.get("document_id")): candidate
        for candidate in candidates
        if candidate.get("document_id")
    }
    ordered = []
    seen = set()
    for doc_id in ranked_ids:
        candidate = by_id.get(doc_id)
        if not candidate or doc_id in seen:
            continue
        seen.add(doc_id)
        ordered.append(
            {
                **candidate,
                "score": len(ranked_ids) - len(ordered),
                "reason": "llm-reranked",
            }
        )
    for candidate in candidates:
        doc_id = str(candidate.get("document_id"))
        if doc_id in seen:
            continue
        ordered.append({**candidate, "score": 0, "reason": "kept-vector-order"})
    return [{**row, "rank": index} for index, row in enumerate(ordered, 1)]


def truncate_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def encode_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def decode_vector(blob: bytes | None, vector_json: str | None) -> list[float]:
    if blob:
        if len(blob) % 4 != 0:
            raise ValueError("invalid cached vector blob length")
        return list(struct.unpack(f"<{len(blob) // 4}f", blob))
    if vector_json:
        value = json.loads(vector_json)
        if isinstance(value, list):
            return [float(item) for item in value]
    raise ValueError("cached embedding row does not contain a vector")


def vector_index_path(workspace: Path, field_mode: str, index_name: str) -> Path:
    name = index_name or f"{field_mode}"
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or field_mode
    return workspace / DEFAULT_VECTOR_INDEX_DIRNAME / f"{safe_name}.sqlite"


def ensure_workspace(workspace: Path) -> None:
    if not (workspace / "filesystem.sqlite").exists():
        raise SystemExit(f"workspace is not registered: {workspace}")


def iter_file_batches(
    conn: sqlite3.Connection,
    *,
    batch_size: int,
    max_docs: int,
) -> Iterable[list[sqlite3.Row]]:
    processed = 0
    last_file_ref = ""
    while True:
        rows = conn.execute(
            """
            SELECT file_ref, external_id, source_type, source_path, title,
                   text_artifact_path, storage_uri, metadata_json
            FROM files
            WHERE deleted_at IS NULL AND file_ref > ?
            ORDER BY file_ref
            LIMIT ?
            """,
            (last_file_ref, batch_size),
        ).fetchall()
        if not rows or (max_docs > 0 and processed >= max_docs):
            break
        if max_docs > 0 and processed + len(rows) > max_docs:
            rows = rows[: max_docs - processed]
        last_file_ref = rows[-1]["file_ref"]
        processed += len(rows)
        yield rows


def vector_text_for_row(
    row: sqlite3.Row,
    metadata: dict[str, Any],
    *,
    field_mode: str,
    max_fulltext_chars: int,
) -> str:
    if field_mode == "summary":
        return compact_vector_text(
            [
                metadata.get("summary"),
                metadata.get("semantic_summary"),
                metadata.get("title") or row["title"],
            ],
            max_chars=2000,
        )
    if field_mode == "metadata":
        return metadata_recall_text(metadata, fallback_title=row["title"])
    if field_mode == "fulltext":
        raw = load_document_json(row)
        return raw_document_text(raw, fallback_title=row["title"], max_chars=max_fulltext_chars)
    if field_mode == "sampled_fulltext":
        raw = load_document_json(row)
        return sampled_document_text(raw, fallback_title=row["title"], max_chars=max_fulltext_chars)
    raise ValueError(f"unknown vector field mode: {field_mode}")


def metadata_recall_text(metadata: dict[str, Any], *, fallback_title: str) -> str:
    fields = [
        "title",
        "summary",
        "intent",
        "entities",
        "constraints",
        "topic",
        "domain",
        "doc_type",
        "source_bucket",
        "repo",
        "project",
        "channel",
        "space",
        "customer",
        "status",
        "semantic_summary",
        "semantic_topics",
        "semantic_entities",
        "semantic_constraints",
        "semantic_problems",
        "semantic_actions",
        "semantic_systems",
        "semantic_aliases",
        "semantic_evidence_terms",
        "semantic_measurements",
        "semantic_events",
        "semantic_time_hints",
    ]
    values = [metadata.get(field) for field in fields]
    if fallback_title:
        values.append(fallback_title)
    return compact_vector_text(values, max_chars=6000)


def raw_document_text(data: dict[str, Any], *, fallback_title: str, max_chars: int) -> str:
    values = []
    title = text_value(
        data.get(data.get("title_field_name") or "")
        or data.get("title")
        or data.get("subject")
        or data.get("summary")
        or data.get("company_name")
        or fallback_title
    )
    if title:
        values.append(title)
    for key in data.get("content_field_names") or []:
        value = text_value(data.get(key))
        if value:
            values.append(value)
    if not values:
        for key, value in sorted(data.items()):
            if key in {"dataset_doc_uuid", "document_id", "external_id", "file_ref", "original_location"}:
                continue
            if key.endswith("_id") or key.endswith("_uuid"):
                continue
            if isinstance(value, (dict, list, str, int, float, bool)):
                values.append(f"{key}: {text_value(value)}")
            if sum(len(item) for item in values) >= max_chars:
                break
    return compact_vector_text(values, max_chars=max_chars)


def sampled_document_text(data: dict[str, Any], *, fallback_title: str, max_chars: int) -> str:
    title = text_value(
        data.get(data.get("title_field_name") or "")
        or data.get("title")
        or data.get("subject")
        or data.get("summary")
        or data.get("company_name")
        or fallback_title
    )
    body_parts = [
        body_text_value(data.get(key))
        for key in data.get("content_field_names") or []
        if body_text_value(data.get(key))
    ]
    if not body_parts:
        return raw_document_text(data, fallback_title=fallback_title, max_chars=max_chars)
    body = normalize_document_body("\n\n".join(body_parts))
    if not body:
        return compact_vector_text([title], max_chars=max_chars)
    title_budget = min(len(title), max_chars // 5)
    body_budget = max(0, max_chars - title_budget - 64)
    salient = salient_document_lines(body, max_chars=int(body_budget * 0.82))
    sampled_budget = max(0, body_budget - len(salient) - 32)
    sampled = sample_text_windows(body, max_chars=sampled_budget)
    return compact_vector_text([title, salient, sampled], max_chars=max_chars)


def normalize_document_body(text: str) -> str:
    normalized = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def body_text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n\n".join(item for item in (body_text_value(item) for item in value) if item)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            text = body_text_value(item)
            if text:
                parts.append(f"{key}: {text}")
        return "\n".join(parts)
    return str(value).strip()


def salient_document_lines(text: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    keyword_re = re.compile(
        r"\b(default|limit|max|min|target|threshold|timeout|retention|window|"
        r"deadline|sla|rpo|rto|p50|p95|p99|must|required|requires|approved|"
        r"owner|decision|rollback|failover|fallback|hierarchy|sequence|order|"
        r"primary|standby|emergency|region|incident|severity|sev\d|permitted|"
        r"allowed|disallowed|encrypted|audit|compliance)\b",
        flags=re.IGNORECASE,
    )
    numeric_re = re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:MiB|GiB|MB|GB|ms|m|sec|seconds|minutes|hours|days|%|tokens?)\b",
        flags=re.IGNORECASE,
    )
    source_lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in re.split(r"[\n\r]+", text)
    ]
    candidates = []
    for position, normalized in enumerate(source_lines):
        if len(normalized) < 12:
            continue
        keyword_matches = keyword_re.findall(normalized)
        numeric_matches = numeric_re.findall(normalized)
        if keyword_matches or numeric_matches:
            score = len(keyword_matches) + (2 * len(numeric_matches))
            if re.search(r"\b(RPO|RTO|SLA|p50|p95|p99|Sev\d)\b", normalized):
                score += 3
            if re.search(r"\b(hierarchy|sequence|order|primary|standby|emergency)\b", normalized, flags=re.IGNORECASE):
                score += 2
            candidates.append((score, position))
    lines = []
    seen_positions = set()
    for _score, position in sorted(candidates, key=lambda item: (-item[0], item[1])):
        for neighbor in (position - 2, position - 1, position, position + 1):
            if neighbor < 0 or neighbor >= len(source_lines) or neighbor in seen_positions:
                continue
            normalized = source_lines[neighbor]
            if len(normalized) < 12:
                continue
            lines.append(normalized)
            seen_positions.add(neighbor)
            if sum(len(item) + 1 for item in lines) >= max_chars:
                break
        if sum(len(item) + 1 for item in lines) >= max_chars:
            break
    return "\n".join(dedupe(lines))[:max_chars]


def sample_text_windows(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    window_count = 4
    window = max(200, max_chars // window_count)
    starts = [
        0,
        max(0, len(text) // 3 - window // 2),
        max(0, (2 * len(text)) // 3 - window // 2),
        max(0, len(text) - window),
    ]
    intervals = sorted((start, min(len(text), start + window)) for start in starts)
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    chunks = [text[start:end].strip() for start, end in merged if text[start:end].strip()]
    return "\n...\n".join(chunks)[:max_chars]


def compact_vector_text(values: Iterable[Any], *, max_chars: int) -> str:
    text = "\n".join(text_value(value) for value in values if text_value(value))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


def index_metadata_projection(metadata: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "source_type",
        "doc_type",
        "source_bucket",
        "domain",
        "topic",
        "repo",
        "project",
        "channel",
        "space",
        "customer",
        "status",
        "year",
        "month",
    ]
    return {key: metadata.get(key, "") for key in keys if metadata.get(key)}


def probe_one_question_metadata(
    conn: sqlite3.Connection,
    field_ids: dict[str, str],
    question: dict[str, Any],
    limit_terms: int,
) -> dict[str, Any]:
    terms = question_terms(question["question"], limit=limit_terms)
    fields = ["entities", "constraints", "topic", "title", "customer", "repo", "project", "channel", "source_bucket", "doc_type", "summary"]
    probes = []
    expected = set(question["expected_doc_ids"])
    for term in terms:
        for field in fields:
            if field not in field_ids:
                continue
            ids = metadata_probe_ids(conn, field_ids, field, term, question["source_types"], limit=200)
            if not ids:
                continue
            hit = bool(expected.intersection(ids))
            probes.append({"field": field, "term": term, "candidate_count_lte_200": len(ids), "hit": hit})
    probes.sort(key=lambda item: (not item["hit"], item["candidate_count_lte_200"], item["field"], item["term"]))
    return {
        "question_id": question["question_id"],
        "source_types": question["source_types"],
        "expected_doc_ids": question["expected_doc_ids"],
        "terms": terms,
        "hit_any": any(item["hit"] for item in probes),
        "best_hit": next((item for item in probes if item["hit"]), None),
        "top_probes": probes[:20],
    }


def metadata_probe_ids(
    conn: sqlite3.Connection,
    field_ids: dict[str, str],
    field: str,
    term: str,
    source_types: list[str],
    *,
    limit: int,
) -> list[str]:
    joins = ""
    params: list[Any] = []
    if source_types and "source_type" in field_ids:
        placeholders = ", ".join("?" for _ in source_types)
        joins = f"""
            JOIN metadata_values smv
              ON smv.file_ref = f.file_ref
             AND smv.field_id = ?
             AND smv.value_text IN ({placeholders})
        """
        params.extend([field_ids["source_type"], *source_types])
    params.extend([field_ids[field], term, limit])
    rows = conn.execute(
        f"""
        SELECT DISTINCT f.external_id
        FROM files f
        {joins}
        JOIN metadata_values mv
          ON mv.file_ref = f.file_ref
         AND mv.field_id = ?
         AND lower(mv.value_text) LIKE '%' || lower(?) || '%'
        WHERE f.deleted_at IS NULL
        ORDER BY f.source_path
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [row["external_id"] for row in rows if row["external_id"]]


def probe_folders(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    questions = load_selected_questions(args)
    with sqlite3.connect(workspace / "filesystem.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        rows = [probe_one_question_folders(conn, question, args.limit_terms) for question in questions]
    summary = probe_summary(rows)
    summary.update({"workspace": str(workspace), "seconds": round(time.time() - started, 3), "questions": rows})
    output = Path(args.output).expanduser().resolve() if args.output else DEFAULT_RESULTS_DIR / f"folder-probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    write_json(output, summary)
    return {"output": str(output), **{key: value for key, value in summary.items() if key != "questions"}}


def probe_fts(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    questions = load_selected_questions(args)
    with sqlite3.connect(workspace / "filesystem.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        rows = [probe_one_question_fts(conn, question, args.limit_terms) for question in questions]
    summary = probe_summary(rows)
    summary.update({"workspace": str(workspace), "seconds": round(time.time() - started, 3), "questions": rows})
    output = Path(args.output).expanduser().resolve() if args.output else DEFAULT_RESULTS_DIR / f"fts-probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    write_json(output, summary)
    return {"output": str(output), **{key: value for key, value in summary.items() if key != "questions"}}


def probe_one_question_fts(conn: sqlite3.Connection, question: dict[str, Any], limit_terms: int) -> dict[str, Any]:
    terms = question_terms(question["question"], limit=limit_terms)
    expected = set(question["expected_doc_ids"])
    probes = []
    for query in fts_queries(terms):
        ids = fts_probe_ids(conn, query, question["source_types"], limit=200)
        if not ids:
            continue
        probes.append({"query": query, "candidate_count_lte_200": len(ids), "hit": bool(expected.intersection(ids))})
    probes.sort(key=lambda item: (not item["hit"], item["candidate_count_lte_200"], item["query"]))
    return {
        "question_id": question["question_id"],
        "source_types": question["source_types"],
        "expected_doc_ids": question["expected_doc_ids"],
        "terms": terms,
        "hit_any": any(item["hit"] for item in probes),
        "best_hit": next((item for item in probes if item["hit"]), None),
        "top_probes": probes[:20],
    }


def fts_probe_ids(conn: sqlite3.Connection, query: str, source_types: list[str], *, limit: int) -> list[str]:
    params: list[Any] = [query]
    source_clause = ""
    if source_types:
        placeholders = ", ".join("?" for _ in source_types)
        source_clause = f"AND f.source_type IN ({placeholders})"
        params.extend(source_types)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT DISTINCT f.external_id
        FROM file_fts
        JOIN files f ON f.file_ref = file_fts.file_ref
        WHERE file_fts MATCH ?
          AND f.deleted_at IS NULL
          {source_clause}
        ORDER BY bm25(file_fts)
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [row["external_id"] for row in rows if row["external_id"]]


def fts_queries(terms: list[str]) -> list[str]:
    queries = []
    for term in terms:
        tokens = [token for token in re.findall(r"[A-Za-z0-9_]+", term.lower()) if len(token) >= 3]
        if not tokens:
            continue
        queries.append(" ".join(tokens))
        if len(tokens) > 1:
            queries.append(" OR ".join(tokens))
    return dedupe(queries)[:24]


def probe_one_question_folders(conn: sqlite3.Connection, question: dict[str, Any], limit_terms: int) -> dict[str, Any]:
    terms = question_terms(question["question"], limit=limit_terms)
    expected = set(question["expected_doc_ids"])
    probes = []
    for term in terms:
        for folder in folder_probe_rows(conn, term, question["source_types"], limit=50):
            probes.append(
                {
                    "term": term,
                    "path": folder["path"],
                    "files": folder["files"],
                    "children": folder["children"],
                    "hit": folder_contains_expected(conn, folder["folder_id"], expected),
                }
            )
    probes.sort(key=lambda item: (not item["hit"], item["files"], item["path"]))
    return {
        "question_id": question["question_id"],
        "source_types": question["source_types"],
        "expected_doc_ids": question["expected_doc_ids"],
        "terms": terms,
        "hit_any": any(item["hit"] for item in probes),
        "best_hit": next((item for item in probes if item["hit"]), None),
        "top_probes": probes[:20],
    }


def folder_probe_rows(conn: sqlite3.Connection, term: str, source_types: list[str], *, limit: int) -> list[dict[str, Any]]:
    source_paths = [f"/semantic/source={slug(item)}%" for item in source_types] if source_types else ["/semantic/%"]
    source_clause = " OR ".join("fo.path LIKE ?" for _ in source_paths)
    exact_paths = []
    for source_type in source_types:
        source = slug(source_type)
        exact_paths.extend(
            [
                f"/semantic/source={source}/topic/{slug(term)}",
                f"/semantic/source={source}/term/{slug(term)}",
                f"/semantic/source={source}/customer/{slug(term)}",
                f"/semantic/source={source}/bucket/{slug(term)}",
            ]
        )
    exact_rows: list[dict[str, Any]] = []
    if exact_paths:
        placeholders = ", ".join("?" for _ in exact_paths)
        rows = conn.execute(
            f"""
            SELECT
                fo.folder_id,
                fo.path,
                (SELECT COUNT(DISTINCT ff.file_ref) FROM file_folders ff WHERE ff.folder_id = fo.folder_id) AS files,
                (SELECT COUNT(*) FROM folders child WHERE child.parent_id = fo.folder_id) AS children
            FROM folders fo
            WHERE fo.path IN ({placeholders})
            ORDER BY files ASC, fo.path
            """,
            exact_paths,
        ).fetchall()
        exact_rows = [dict(row) for row in rows]
    rows = conn.execute(
        f"""
        SELECT
            fo.folder_id,
            fo.path,
            (SELECT COUNT(DISTINCT ff.file_ref) FROM file_folders ff WHERE ff.folder_id = fo.folder_id) AS files,
            (SELECT COUNT(*) FROM folders child WHERE child.parent_id = fo.folder_id) AS children
        FROM folders fo
        WHERE ({source_clause})
          AND lower(fo.path) LIKE '%' || lower(?) || '%'
        ORDER BY files ASC, fo.path
        LIMIT ?
        """,
        [*source_paths, term, limit],
    ).fetchall()
    seen = {row["path"] for row in exact_rows}
    combined = exact_rows + [dict(row) for row in rows if row["path"] not in seen]
    return combined[:limit]


def folder_contains_expected(conn: sqlite3.Connection, folder_id_value: str, expected_doc_ids: set[str]) -> bool:
    if not expected_doc_ids:
        return False
    placeholders = ", ".join("?" for _ in expected_doc_ids)
    row = conn.execute(
        f"""
        SELECT 1
        FROM file_folders ff
        JOIN files f ON f.file_ref = ff.file_ref
        WHERE ff.folder_id = ?
          AND f.external_id IN ({placeholders})
        LIMIT 1
        """,
        [folder_id_value, *expected_doc_ids],
    ).fetchone()
    return row is not None


def probe_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "question_count": len(rows),
        "hit_any_count": sum(1 for row in rows if row["hit_any"]),
        "hit_any_rate": (sum(1 for row in rows if row["hit_any"]) / len(rows)) if rows else 0,
        "best_candidate_count_avg": avg(
            [
                float(row["best_hit"]["candidate_count_lte_200"])
                for row in rows
                if row.get("best_hit") and "candidate_count_lte_200" in row["best_hit"]
            ]
        ),
        "misses": [row["question_id"] for row in rows if not row["hit_any"]],
    }


def load_selected_questions(args: argparse.Namespace) -> list[dict[str, Any]]:
    question_ids = selected_question_ids(args)
    by_id = {row["question_id"]: row for row in read_jsonl(BENCHMARK_DIR / "dataset" / "questions.jsonl")}
    return [by_id[item] for item in question_ids if item in by_id]


def metadata_field_ids(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["name"]: row["field_id"]
        for row in conn.execute("SELECT name, field_id FROM metadata_fields WHERE schema_id = 'default'").fetchall()
    }


def question_terms(question: str, *, limit: int) -> list[str]:
    terms: list[str] = []
    terms.extend(extract_constraint_terms(question))
    raw_tokens = keyword_terms(question)
    terms.extend(raw_tokens)
    for n in (3, 2):
        for i in range(0, max(0, len(raw_tokens) - n + 1)):
            terms.append(" ".join(raw_tokens[i : i + n]))
    terms.extend(re.findall(r"\b[A-Za-z][A-Za-z0-9_./:-]{3,}\b", question))
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        for alias in term_aliases(slug(term)):
            expanded.append(alias)
    return dedupe(expanded)[:limit]


def run_experiments(args: argparse.Namespace) -> dict[str, Any]:
    workspace = Path(args.workspace).expanduser().resolve()
    if not (workspace / "filesystem.sqlite").exists():
        raise SystemExit(f"workspace is not registered: {workspace}")
    question_ids = selected_question_ids(args)
    run_name = args.run_name or time.strftime("full-layer-%Y%m%d-%H%M%S")
    run_dir = DEFAULT_RESULTS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "workspace": str(workspace),
        "question_ids": question_ids,
        "model": args.model,
        "max_seconds": args.max_seconds,
        "max_turns": args.max_turns,
        "agent_retries": args.agent_retries,
        "strategies": [item.strip() for item in args.strategies.split(",") if item.strip()],
    }
    (run_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summaries: dict[str, Any] = {}
    baseline_rows = baseline_for_questions(Path(args.baseline_results), question_ids)
    if baseline_rows:
        summaries["baseline_existing_source_grep"] = summarize_results(baseline_rows)
        write_json(run_dir / "baseline_existing_source_grep.summary.json", summaries["baseline_existing_source_grep"])

    for strategy in config["strategies"]:
        command = strategy_command(
            strategy,
            workspace=workspace,
            run_name=f"{run_name}-{strategy}",
            question_ids=question_ids,
            model=args.model,
            max_seconds=args.max_seconds,
            max_turns=args.max_turns,
            agent_retries=args.agent_retries,
        )
        command_path = run_dir / f"{strategy}.command.txt"
        command_path.write_text(" ".join(command) + "\n", encoding="utf-8")
        LOGGER.info("running strategy=%s", strategy)
        started = time.time()
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "UV_CACHE_DIR": str(REPO_ROOT / ".uv-cache")},
        )
        (run_dir / f"{strategy}.stdout.txt").write_text(completed.stdout, encoding="utf-8")
        (run_dir / f"{strategy}.stderr.txt").write_text(completed.stderr, encoding="utf-8")
        strategy_run_dir = BENCHMARK_DIR / "runs" / f"{run_name}-{strategy}"
        result_rows = read_jsonl(strategy_run_dir / "results.jsonl")
        summary = summarize_results(result_rows)
        summary.update(
            {
                "returncode": completed.returncode,
                "seconds_wall": round(time.time() - started, 3),
                "benchmark_run_dir": str(strategy_run_dir),
            }
        )
        summaries[strategy] = summary
        write_json(run_dir / f"{strategy}.summary.json", summary)

    write_research_summary(run_dir, summaries)
    return {"run_dir": str(run_dir), "summaries": summaries}


def strategy_command(
    strategy: str,
    *,
    workspace: Path,
    run_name: str,
    question_ids: list[str],
    model: str,
    max_seconds: float,
    max_turns: int,
    agent_retries: int,
) -> list[str]:
    prompt_files = {
        "metadata_original": BENCHMARK_DIR / "prompts" / "pifs_question_metadata.md",
        "metadata_source_aware": PROMPTS_DIR / "pifs_question_metadata_source_aware.md",
        "folder_semantic": PROMPTS_DIR / "pifs_question_folder_semantic.md",
    }
    retrieval_modes = {
        "metadata_original": "metadata",
        "metadata_source_aware": "metadata",
        "folder_semantic": "folder",
    }
    if strategy not in prompt_files:
        raise SystemExit(f"unknown strategy: {strategy}")
    return [
        "uv",
        "run",
        "python",
        str(BENCHMARK_DIR / "run_enterprise_rag_pifs_agent.py"),
        "--workspace",
        str(workspace),
        "--run-name",
        run_name,
        "--question-ids",
        ",".join(question_ids),
        "--model",
        model,
        "--retrieval-mode",
        retrieval_modes[strategy],
        "--question-prompt-file",
        str(prompt_files[strategy]),
        "--max-seconds",
        str(max_seconds),
        "--max-turns",
        str(max_turns),
        "--agent-retries",
        str(agent_retries),
        "--stream-mode",
        "off",
        "--reasoning-effort",
        "low",
    ]


def selected_question_ids(args: argparse.Namespace) -> list[str]:
    if getattr(args, "all_questions", False):
        return [row["question_id"] for row in read_jsonl(BENCHMARK_DIR / "dataset" / "questions.jsonl")]
    if args.question_ids:
        return [item.strip() for item in args.question_ids.split(",") if item.strip()]
    return QUESTION_SETS[args.question_set]


def baseline_for_questions(path: Path, question_ids: list[str]) -> list[dict[str, Any]]:
    rows_by_id = {row["question_id"]: row for row in read_jsonl(path)}
    return [rows_by_id[item] for item in question_ids if item in rows_by_id]


def analyze_run_dir(run_dir: Path) -> dict[str, Any]:
    summaries = {}
    for path in sorted(run_dir.glob("*.summary.json")):
        summaries[path.stem.replace(".summary", "")] = json.loads(path.read_text(encoding="utf-8"))
    write_research_summary(run_dir, summaries)
    return summaries


def summarize_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"questions": 0}
    tool_counts = []
    cat_counts = []
    grep_counts = []
    find_counts = []
    output_chars = []
    for row in rows:
        agent_log = row.get("agent_log") or []
        tool_events = [event for event in agent_log if event.get("type") == "tool_call"]
        tool_counts.append(len(tool_events))
        cat_counts.append(sum(1 for event in tool_events if str(event.get("command", "")).startswith("cat ")))
        grep_counts.append(sum(1 for event in tool_events if str(event.get("command", "")).startswith("grep")))
        find_counts.append(sum(1 for event in tool_events if str(event.get("command", "")).startswith("find")))
        output_chars.append(sum(len(str(event.get("result", ""))) for event in tool_events))
    timeout_count = sum(1 for row in rows if row.get("error") and "MaxSecondsExceeded" in str(row.get("error")))
    hit_count = sum(1 for row in rows if row.get("doc_hit"))
    wrong_count = sum(1 for row in rows if not row.get("doc_hit") and not row.get("error"))
    return {
        "questions": len(rows),
        "question_ids": [row["question_id"] for row in rows],
        "hit_count": hit_count,
        "doc_hit_rate": hit_count / len(rows),
        "timeout_count": timeout_count,
        "wrong_count": wrong_count,
        "avg_seconds": avg([float(row.get("seconds") or 0) for row in rows]),
        "avg_tool_calls": avg(tool_counts),
        "avg_cat_calls": avg(cat_counts),
        "avg_grep_calls": avg(grep_counts),
        "avg_find_calls": avg(find_counts),
        "avg_tool_output_chars": avg(output_chars),
        "failures": [
            {
                "question_id": row["question_id"],
                "source_types": row.get("source_types", []),
                "error": row.get("error"),
                "document_ids": row.get("document_ids", []),
                "expected_doc_ids": row.get("expected_doc_ids", []),
            }
            for row in rows
            if not row.get("doc_hit")
        ],
    }


def write_research_summary(run_dir: Path, summaries: dict[str, Any]) -> None:
    lines = ["# Full Workspace Layer Research Summary", ""]
    lines.append("| strategy | hit rate | timeout | wrong | avg sec | tool calls | cat | grep | find | output chars |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, summary in summaries.items():
        if not summary.get("questions"):
            continue
        lines.append(
            "| {name} | {hit:.3f} | {timeout} | {wrong} | {sec:.1f} | {tools:.1f} | {cat:.1f} | {grep:.1f} | {find:.1f} | {chars:.0f} |".format(
                name=name,
                hit=summary.get("doc_hit_rate", 0),
                timeout=summary.get("timeout_count", 0),
                wrong=summary.get("wrong_count", 0),
                sec=summary.get("avg_seconds", 0),
                tools=summary.get("avg_tool_calls", 0),
                cat=summary.get("avg_cat_calls", 0),
                grep=summary.get("avg_grep_calls", 0),
                find=summary.get("avg_find_calls", 0),
                chars=summary.get("avg_tool_output_chars", 0),
            )
        )
    lines.extend(["", "## Failures", ""])
    for name, summary in summaries.items():
        failures = summary.get("failures") or []
        if not failures:
            continue
        lines.append(f"### {name}")
        for failure in failures[:20]:
            lines.append(
                "- {qid} sources={sources} error={error} got={got} expected={expected}".format(
                    qid=failure["question_id"],
                    sources=",".join(failure.get("source_types") or []),
                    error=failure.get("error") or "wrong_doc",
                    got=",".join(failure.get("document_ids") or []),
                    expected=",".join(failure.get("expected_doc_ids") or []),
                )
            )
        lines.append("")
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(run_dir / "summary.json", summaries)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def avg(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def percentile(values: Iterable[float], quantile: float) -> float:
    sorted_values = sorted(values)
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, int(round((len(sorted_values) - 1) * quantile))))
    return sorted_values[index]


def field_id(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    return f"field_{digest}"


def folder_id(path: str) -> str:
    normalized = normalize_path(path)
    if normalized == "/":
        return "folder_root"
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
    return f"folder_{digest}"


def normalize_path(path: str | Path | None) -> str:
    if path is None:
        return "/"
    parts = [part for part in str(path).replace("\\", "/").split("/") if part and part != "."]
    return "/" + "/".join(parts) if parts else "/"


def safe_json_obj(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return compact_join(value)
    if isinstance(value, dict):
        return " ".join(f"{key} {text_value(item)}" for key, item in value.items())
    return re.sub(r"\s+", " ", str(value)).strip()


def values_from_keys(data: dict[str, Any], keys: list[str]) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            values.extend(text_value(item) for item in value)
        else:
            values.append(text_value(value))
    return [value for value in values if value]


def first_date(data: dict[str, Any]) -> str:
    for key in (
        "created_at",
        "updated_at",
        "last_updated",
        "first_email_at",
        "last_email_at",
        "recorded_at",
        "last_modified",
        "due_date",
        "forecast_close_month",
    ):
        value = text_value(data.get(key))
        match = re.search(r"\b20\d{2}(?:-\d{2})?(?:-\d{2})?\b", value)
        if match:
            return match.group(0)
    return ""


def keyword_terms(text: str) -> list[str]:
    terms = []
    for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower()):
        term = term.replace("_", "-")
        if term in STOPWORDS or len(term) < 3:
            continue
        terms.append(term)
    return dedupe(terms)[:20]


def phrase(value: Any) -> str:
    text = text_value(value).lower()
    text = re.sub(r"[_/]+", " ", text)
    text = re.sub(r"[^a-z0-9.+ -]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    terms = [term for term in text.split() if term not in STOPWORDS]
    return " ".join(terms[:6])


def slug(value: Any) -> str:
    text = phrase(value)
    text = re.sub(r"[^a-z0-9.+-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80]


def compact_join(values: Iterable[Any], *, limit: int = 12) -> str:
    return ", ".join(dedupe(text_value(value) for value in values if text_value(value))[:limit])


def compact_summary(title: str, preview: str) -> str:
    text = " ".join(part for part in [title, preview] if part)
    return text[:700]


def clean_metadata(metadata: dict[str, str]) -> dict[str, str]:
    return {key: text_value(value)[:1200] for key, value in metadata.items() if key in SCHEMA_FIELDS}


def dedupe(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        normalized = re.sub(r"\s+", " ", str(value)).strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
