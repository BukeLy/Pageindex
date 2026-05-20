from __future__ import annotations

import argparse
import collections
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


RESEARCH_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = RESEARCH_DIR.parents[1]
DEFAULT_DATASET_DIR = BENCHMARK_DIR / "dataset"
DEFAULT_DB = RESEARCH_DIR / "cache" / "full_doc_bm25.sqlite"
DEFAULT_OUTPUT = RESEARCH_DIR / "results" / "full-doc-bm25-20260520" / "summary.json"

LOGGER = logging.getLogger("full_doc_bm25")
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_./:-]{1,}|[0-9]+(?:\.[0-9]+)?(?:mi?b|gb|ms|s|%|x)?")
SOURCE_HINTS = {
    "github": {"github", "pull request", "pr", "merge", "reviewer", "runtime", "api", "sdk"},
    "gmail": {"email", "thread", "inbox", "message", "vendor", "contract"},
    "slack": {"slack", "channel", "security team", "incident channel", "thread"},
    "jira": {"jira", "ticket", "issue", "sprint", "epic", "bug", "support"},
    "linear": {"linear", "issue", "ticket", "roadmap", "engineering task"},
    "confluence": {"confluence", "policy", "runbook", "kb", "knowledge base", "page", "docs"},
    "google_drive": {"google drive", "doc", "document", "deck", "spreadsheet", "report", "notes"},
    "fireflies": {"meeting", "call", "transcript", "handoff", "standup"},
    "hubspot": {"hubspot", "customer", "account", "deal", "sales", "founder"},
}


@dataclass(frozen=True)
class Question:
    question_id: str
    question: str
    question_type: str
    source_types: list[str]
    expected_doc_ids: list[str]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    started = time.time()
    dataset_dir = args.dataset_dir.resolve()
    documents_path = dataset_dir / "data" / "documents" / "test.parquet"
    questions_path = dataset_dir / "data" / "questions" / "test.parquet"
    if not documents_path.exists():
        raise SystemExit(f"missing documents parquet: {documents_path}")
    if not questions_path.exists():
        raise SystemExit(f"missing questions parquet: {questions_path}")

    db_path = args.db.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA temp_store = MEMORY")
        if args.rebuild or not has_index(conn):
            build_index(conn, documents_path, batch_size=args.batch_size, max_docs=args.max_docs)
        else:
            LOGGER.info("reuse existing full-doc bm25 index: %s", db_path)
        questions = load_questions(questions_path)
        if args.question_ids:
            keep = {item.strip() for item in args.question_ids.split(",") if item.strip()}
            questions = [question for question in questions if question.question_id in keep]
        if args.max_questions > 0:
            questions = questions[: args.max_questions]
        result = evaluate(conn, questions, limit=args.limit)

    result["config"] = {
        "dataset_dir": str(dataset_dir),
        "documents_path": str(documents_path),
        "questions_path": str(questions_path),
        "db": str(db_path),
        "rebuild": args.rebuild,
        "batch_size": args.batch_size,
        "max_docs": args.max_docs,
        "max_questions": args.max_questions,
        "question_ids": args.question_ids,
        "limit": args.limit,
    }
    result["seconds"] = round(time.time() - started, 3)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(render_summary(result), encoding="utf-8")
    print(json.dumps({"output": str(output), "summary": result["summary"]}, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Full-doc EnterpriseRAG BM25/FTS recall experiment")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--max-docs", type=int, default=0)
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--question-ids", default="")
    parser.add_argument("--limit", type=int, default=100)
    return parser.parse_args()


def has_index(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documents_fts'").fetchone()
    if not row:
        return False
    count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    return count > 0


def build_index(conn: sqlite3.Connection, documents_path: Path, *, batch_size: int, max_docs: int) -> None:
    pa, pq = import_pyarrow()
    LOGGER.info("building full-doc bm25 index from %s", documents_path)
    conn.executescript(
        """
        DROP TABLE IF EXISTS documents;
        DROP TABLE IF EXISTS documents_fts;
        CREATE TABLE documents(
            doc_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE documents_fts USING fts5(
            doc_id UNINDEXED,
            source_type,
            title,
            content,
            tokenize='unicode61'
        );
        """
    )
    parquet = pq.ParquetFile(documents_path)
    processed = 0
    source_counts: collections.Counter[str] = collections.Counter()
    for batch in parquet.iter_batches(batch_size=batch_size):
        rows = batch.to_pylist()
        if max_docs > 0:
            rows = rows[: max(0, max_docs - processed)]
        if not rows:
            break
        doc_rows = []
        fts_rows = []
        for row in rows:
            doc_id = text_value(row.get("doc_id") or row.get("dataset_doc_uuid"))
            source_type = text_value(row.get("source_type"))
            title = text_value(row.get("title"))
            content = text_value(row.get("content"))
            if not doc_id:
                continue
            doc_rows.append((doc_id, source_type, title))
            fts_rows.append((doc_id, source_type, title, content))
            source_counts[source_type] += 1
        conn.executemany(
            "INSERT OR REPLACE INTO documents(doc_id, source_type, title) VALUES (?, ?, ?)",
            doc_rows,
        )
        conn.executemany(
            "INSERT INTO documents_fts(doc_id, source_type, title, content) VALUES (?, ?, ?, ?)",
            fts_rows,
        )
        conn.commit()
        processed += len(rows)
        if processed == len(rows) or processed % 50000 < len(rows):
            LOGGER.info("indexed docs=%s source_counts=%s", processed, dict(source_counts.most_common(5)))
        if max_docs > 0 and processed >= max_docs:
            break
    conn.execute("INSERT INTO documents_fts(documents_fts) VALUES ('optimize')")
    conn.commit()
    LOGGER.info("finished full-doc bm25 index docs=%s", processed)


def import_pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "pyarrow is required for full-doc parquet input. Run with: "
            "uv run --with pyarrow python examples/Benchmark/enterprise_rag_benchmark/"
            "auto_gen_research/entity_relation_research/run_full_doc_bm25.py"
        ) from exc
    return pa, pq


def load_questions(path: Path) -> list[Question]:
    _pa, pq = import_pyarrow()
    table = pq.read_table(path)
    questions = []
    for row in table.to_pylist():
        questions.append(
            Question(
                question_id=text_value(row.get("question_id")),
                question=text_value(row.get("question")),
                question_type=text_value(row.get("question_type")),
                source_types=list_value(row.get("source_types")),
                expected_doc_ids=list_value(row.get("expected_doc_ids")),
            )
        )
    return questions


def evaluate(conn: sqlite3.Connection, questions: list[Question], *, limit: int) -> dict[str, Any]:
    strategies = {
        "content_bm25": lambda q: search_fts(conn, build_match_query(q.question), limit=limit),
        "title_boost_bm25": lambda q: search_fts(conn, build_match_query(q.question), limit=limit, title_boost=True),
        "source_hint_bm25": lambda q: search_fts(
            conn,
            build_match_query(q.question),
            limit=limit,
            source_filter=classify_source_hints(q.question),
        ),
        "query_projection_rrf": lambda q: rrf_fuse(
            [
                search_fts(conn, build_match_query(q.question), limit=limit),
                search_fts(conn, build_match_query(project_query_terms(q.question, mode="entities")), limit=limit),
                search_fts(conn, build_match_query(project_query_terms(q.question, mode="constraints")), limit=limit),
            ],
            limit=limit,
        ),
        "hybrid_title_source_rrf": lambda q: rrf_fuse(
            [
                search_fts(conn, build_match_query(q.question), limit=limit),
                search_fts(conn, build_match_query(q.question), limit=limit, title_boost=True),
                search_fts(
                    conn,
                    build_match_query(project_query_terms(q.question, mode="constraints")),
                    limit=limit,
                    source_filter=classify_source_hints(q.question),
                ),
            ],
            limit=limit,
        ),
    }
    rows = []
    for index, question in enumerate(questions, start=1):
        if index == 1 or index % 50 == 0:
            LOGGER.info("probing question %s/%s %s", index, len(questions), question.question_id)
        row = {
            "question_id": question.question_id,
            "question_type": question.question_type,
            "source_types": question.source_types,
            "expected_doc_ids": question.expected_doc_ids,
            "strategy_results": {},
        }
        for name, fn in strategies.items():
            ranked_ids = fn(question)
            row["strategy_results"][name] = strategy_result(question.expected_doc_ids, ranked_ids)
        rows.append(row)
    return {
        "summary": {name: summarize_strategy(rows, name) for name in strategies},
        "rows": rows,
        "doc_universe": {
            "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "source_counts": {
                row["source_type"]: row["count"]
                for row in conn.execute(
                    "SELECT source_type, COUNT(*) AS count FROM documents GROUP BY source_type ORDER BY count DESC"
                ).fetchall()
            },
        },
    }


def search_fts(
    conn: sqlite3.Connection,
    match_query: str,
    *,
    limit: int,
    title_boost: bool = False,
    source_filter: set[str] | None = None,
) -> list[str]:
    if not match_query:
        return []
    weights = "8.0, 4.0, 1.0" if title_boost else "2.0, 4.0, 1.0"
    params: list[Any] = [match_query]
    where = "documents_fts MATCH ?"
    if source_filter:
        placeholders = ", ".join("?" for _ in source_filter)
        where += f" AND source_type IN ({placeholders})"
        params.extend(sorted(source_filter))
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT doc_id
        FROM documents_fts
        WHERE {where}
        ORDER BY bm25(documents_fts, {weights})
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [row["doc_id"] for row in rows]


def build_match_query(text: str, *, limit: int = 24) -> str:
    values = []
    for token in tokens(text):
        if len(token) < 3:
            continue
        escaped = token.replace('"', '""')
        values.append(f'"{escaped}"')
        if len(values) >= limit:
            break
    return " OR ".join(dedupe(values))


def project_query_terms(question: str, *, mode: str) -> str:
    terms = tokens(question)
    if mode == "entities":
        selected = [term for term in terms if is_identifier(term) or len(term) >= 7]
    elif mode == "constraints":
        selected = [
            term
            for term in terms
            if is_identifier(term)
            or any(
                marker in term
                for marker in ("limit", "default", "quota", "sla", "timeout", "p95", "p99", "rpo", "rto")
            )
        ]
    else:
        selected = terms
    return " ".join(dedupe(selected)[:18])


def rrf_fuse(rankings: list[list[str]], *, limit: int, k: int = 60) -> list[str]:
    scores: dict[str, float] = collections.defaultdict(float)
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += 1.0 / (k + rank)
    return [doc_id for doc_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def classify_source_hints(question_text: str) -> set[str]:
    lowered = question_text.lower()
    hints = set()
    for source_type, terms in SOURCE_HINTS.items():
        if any(term in lowered for term in terms):
            hints.add(source_type)
    return hints


def strategy_result(expected_doc_ids: list[str], ranked_ids: list[str]) -> dict[str, Any]:
    expected = set(expected_doc_ids)
    ranks = [rank for rank, doc_id in enumerate(ranked_ids, start=1) if doc_id in expected]
    return {
        "hit_at_1": bool(ranks and min(ranks) <= 1),
        "hit_at_3": bool(ranks and min(ranks) <= 3),
        "hit_at_5": bool(ranks and min(ranks) <= 5),
        "hit_at_10": bool(ranks and min(ranks) <= 10),
        "hit_at_20": bool(ranks and min(ranks) <= 20),
        "hit_at_50": bool(ranks and min(ranks) <= 50),
        "hit_at_100": bool(ranks and min(ranks) <= 100),
        "best_rank": min(ranks) if ranks else None,
        "top_doc_ids": ranked_ids[:10],
    }


def summarize_strategy(rows: list[dict[str, Any]], strategy_name: str) -> dict[str, Any]:
    answerable = [row for row in rows if row["expected_doc_ids"]]
    total = len(answerable) or 1
    results = [row["strategy_results"][strategy_name] for row in answerable]
    reciprocal_ranks = [1.0 / result["best_rank"] for result in results if result["best_rank"]]
    return {
        "questions": len(answerable),
        "hit@1": round(sum(result["hit_at_1"] for result in results) / total, 4),
        "hit@3": round(sum(result["hit_at_3"] for result in results) / total, 4),
        "hit@5": round(sum(result["hit_at_5"] for result in results) / total, 4),
        "hit@10": round(sum(result["hit_at_10"] for result in results) / total, 4),
        "hit@20": round(sum(result["hit_at_20"] for result in results) / total, 4),
        "hit@50": round(sum(result["hit_at_50"] for result in results) / total, 4),
        "hit@100": round(sum(result["hit_at_100"] for result in results) / total, 4),
        "mrr": round(sum(reciprocal_ranks) / total, 4),
        "misses": [
            row["question_id"]
            for row in answerable
            if not row["strategy_results"][strategy_name]["hit_at_100"]
        ],
    }


def tokens(text: str) -> list[str]:
    return [match.group(0).strip("._-:/").lower() for match in TOKEN_RE.finditer(text or "")]


def is_identifier(token: str) -> bool:
    return any(char.isdigit() for char in token) or "_" in token or "-" in token or "/" in token or "." in token


def dedupe(values: Iterable[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def render_summary(result: dict[str, Any]) -> str:
    lines = [
        "# Full-Doc BM25 Recall",
        "",
        "This evaluates retrieval over the full EnterpriseRAG documents parquet",
        "using local SQLite FTS5/BM25. It does not use metadata generation,",
        "embeddings, or benchmark gold answers during indexing.",
        "",
        "## Document Universe",
        "",
        "```json",
        json.dumps(result["doc_universe"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Summary",
        "",
        "| strategy | hit@1 | hit@3 | hit@5 | hit@10 | hit@20 | hit@50 | hit@100 | MRR | misses@100 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, row in result["summary"].items():
        lines.append(
            f"| `{strategy}` | {row['hit@1']:.4f} | {row['hit@3']:.4f} | "
            f"{row['hit@5']:.4f} | {row['hit@10']:.4f} | {row['hit@20']:.4f} | "
            f"{row['hit@50']:.4f} | {row['hit@100']:.4f} | {row['mrr']:.4f} | "
            f"{len(row['misses'])} |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
