from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[5]
BENCHMARK_DIR = REPO_ROOT / "examples" / "Benchmark" / "enterprise_rag_benchmark"
FULL_WORKSPACE_LAYER_DIR = BENCHMARK_DIR / "auto_gen_research" / "full_workspace_layer"
RESEARCH_DIR = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(FULL_WORKSPACE_LAYER_DIR) not in sys.path:
    sys.path.insert(0, str(FULL_WORKSPACE_LAYER_DIR))

import pyarrow.parquet as pq

from pageindex.filesystem.semantic_index import SQLiteVecSemanticIndex, SemanticSearchResult
from run_full_workspace_layer_research import (  # type: ignore[import-not-found]
    EmbeddingCache,
    compact_join,
    embedding_cache_model_key,
    extract_constraint_terms,
    keyword_terms,
    make_embedder,
    text_value,
)


DEFAULT_DATASET_DIR = BENCHMARK_DIR / "dataset"
DEFAULT_INDEX_DIR = RESEARCH_DIR / "cache" / "projection_indexes" / "enterprise_full_v1"
DEFAULT_OUTPUT_DIR = RESEARCH_DIR / "results" / f"projection-recall-{time.strftime('%Y%m%d-%H%M%S')}"
STRATEGIES = [
    "baseline_metadata_vector",
    "summary_only_vector",
    "entity_constraint_index",
    "entity_relation_index",
    "hybrid_entity_relation_vector",
]
INDEX_BY_CHANNEL = {
    "metadata": "metadata_composite_vector",
    "summary": "summary_only_vector",
    "entity": "entity_vectors",
    "constraint": "constraint_vectors",
    "relation": "relation_vectors",
}
LOGGER = logging.getLogger("probe_projection_recall")


@dataclass(frozen=True)
class QueryProjection:
    entities: list[str]
    relations: list[str]
    constraints: list[str]
    expected_answer_type: str = ""


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BENCHMARK_DIR / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    index_dir = Path(args.index_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    questions = load_questions(
        dataset_dir / "data" / "questions" / "test.parquet",
        start=args.question_start,
        limit=args.question_limit,
        all_questions=args.all_questions,
    )
    embedder = make_embedder(
        args.embedding_provider,
        args.embedding_model,
        dimensions=args.embedding_dimensions,
        timeout=args.embedding_timeout,
    )
    embedding_cache = EmbeddingCache(index_dir / "embedding_cache.sqlite")
    cache_model = embedding_cache_model_key(args.embedding_model, args.embedding_dimensions)
    extractor = QueryExtractor(
        index_dir / "query_projection_cache.sqlite",
        mode=args.query_extractor,
        model=args.query_extractor_model,
        timeout=args.query_extractor_timeout,
    )

    index_by_channel = {
        channel: SQLiteVecSemanticIndex(index_dir / f"{index_name}.sqlite")
        for channel, index_name in INDEX_BY_CHANNEL.items()
    }
    for channel, index in index_by_channel.items():
        info = index.info()
        LOGGER.info("loaded channel=%s docs=%s", channel, info.get("document_count"))

    started = time.time()
    strategy_rows: dict[str, list[dict[str, Any]]] = {strategy: [] for strategy in STRATEGIES}
    for question_number, question in enumerate(questions, 1):
        if question_number == 1 or question_number % 25 == 0:
            LOGGER.info("probing question=%d/%d %s", question_number, len(questions), question["question_id"])
        projection = extractor.extract(question["question"])
        filters = None
        if args.source_type_filter and question.get("source_types"):
            filters = {"source_type": question["source_types"]}
        channel_hits = search_channels(
            question=question["question"],
            projection=projection,
            indexes=index_by_channel,
            embedder=embedder,
            embedding_cache=embedding_cache,
            cache_model=cache_model,
            filters=filters,
            args=args,
        )
        for strategy in STRATEGIES:
            ranked_docs = rank_strategy(strategy, channel_hits, projection)
            strategy_rows[strategy].append(evaluate_question(question, ranked_docs, projection))

    summary = {
        "dataset_dir": str(dataset_dir),
        "index_dir": str(index_dir),
        "query_extractor": args.query_extractor,
        "query_extractor_model": args.query_extractor_model if args.query_extractor == "openai" else "",
        "embedding_provider": args.embedding_provider,
        "embedding_model": args.embedding_model,
        "embedding_dimensions": args.embedding_dimensions,
        "question_start": args.question_start,
        "question_limit": args.question_limit,
        "all_questions": args.all_questions,
        "source_type_filter": args.source_type_filter,
        "candidate_limit": args.candidate_limit,
        "per_channel_limit": args.per_channel_limit,
        "seconds": round(time.time() - started, 3),
        "strategies": {
            strategy: summarize_rows(rows)
            for strategy, rows in strategy_rows.items()
        },
        "questions": strategy_rows,
    }
    write_json(output_dir / "summary.json", summary)
    write_summary_md(output_dir / "summary.md", summary)
    print(json.dumps({"output_dir": str(output_dir), "strategies": summary["strategies"]}, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe EnterpriseRAG projection recall")
    parser.add_argument("--dataset-dir", default=str(DEFAULT_DATASET_DIR))
    parser.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--embedding-provider", default="openai", choices=["openai", "hash"])
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    parser.add_argument("--embedding-dimensions", type=int, default=256)
    parser.add_argument("--embedding-timeout", type=float, default=60)
    parser.add_argument("--query-extractor", default="heuristic", choices=["heuristic", "openai"])
    parser.add_argument("--query-extractor-model", default="gpt-4.1-nano")
    parser.add_argument("--query-extractor-timeout", type=float, default=20)
    parser.add_argument("--question-start", type=int, default=1)
    parser.add_argument("--question-limit", type=int, default=100)
    parser.add_argument("--all-questions", action="store_true")
    parser.add_argument("--candidate-limit", type=int, default=100)
    parser.add_argument("--per-channel-limit", type=int, default=100)
    parser.add_argument("--fetch-multiplier", type=int, default=100)
    parser.add_argument("--source-type-filter", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def search_channels(
    *,
    question: str,
    projection: QueryProjection,
    indexes: dict[str, SQLiteVecSemanticIndex],
    embedder: Any,
    embedding_cache: EmbeddingCache,
    cache_model: str,
    filters: dict[str, Any] | None,
    args: argparse.Namespace,
) -> dict[str, list[SemanticSearchResult]]:
    query_texts = {
        "metadata": question,
        "summary": question,
        "entity": compact_join(projection.entities, limit=24) or question,
        "constraint": compact_join(projection.constraints, limit=24) or question,
        "relation": "\n".join(projection.relations) or question,
    }
    channel_hits = {}
    for channel, query_text in query_texts.items():
        vector = embedding_cache.embed_texts(
            [query_text],
            provider=args.embedding_provider,
            model=cache_model,
            embedder=embedder,
            batch_size=1,
        )[0]
        channel_hits[channel] = indexes[channel].search(
            vector,
            limit=args.per_channel_limit,
            filters=filters,
            fetch_multiplier=args.fetch_multiplier,
        )
    return channel_hits


def rank_strategy(
    strategy: str,
    channel_hits: dict[str, list[SemanticSearchResult]],
    projection: QueryProjection,
) -> list[dict[str, Any]]:
    if strategy == "baseline_metadata_vector":
        return rank_single_channel(channel_hits["metadata"])
    if strategy == "summary_only_vector":
        return rank_single_channel(channel_hits["summary"])
    if strategy == "entity_constraint_index":
        return aggregate_channels(
            {"entity": channel_hits["entity"], "constraint": channel_hits["constraint"]},
            {"entity": 0.55, "constraint": 0.45},
            projection,
        )
    if strategy == "entity_relation_index":
        return aggregate_channels(
            {
                "entity": channel_hits["entity"],
                "relation": channel_hits["relation"],
                "constraint": channel_hits["constraint"],
            },
            {"entity": 0.25, "relation": 0.5, "constraint": 0.25},
            projection,
        )
    if strategy == "hybrid_entity_relation_vector":
        return aggregate_channels(
            {
                "metadata": channel_hits["metadata"],
                "entity": channel_hits["entity"],
                "relation": channel_hits["relation"],
                "constraint": channel_hits["constraint"],
            },
            {"metadata": 0.25, "entity": 0.25, "relation": 0.3, "constraint": 0.2},
            projection,
        )
    raise ValueError(f"unknown strategy: {strategy}")


def rank_single_channel(results: list[SemanticSearchResult]) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for rank, result in enumerate(results, 1):
        doc_id = str(result.external_id or result.file_ref)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        rows.append(
            {
                "document_id": doc_id,
                "score": 1 / (60 + rank),
                "sources": [{"channel": "single", "rank": rank, "distance": result.distance}],
                "source_type": result.source_type,
                "source_path": result.source_path,
                "title": result.title,
            }
        )
    return rows


def aggregate_channels(
    channel_hits: dict[str, list[SemanticSearchResult]],
    weights: dict[str, float],
    projection: QueryProjection,
) -> list[dict[str, Any]]:
    by_doc: dict[str, dict[str, Any]] = {}
    for channel, results in channel_hits.items():
        weight = weights[channel]
        seen_in_channel = set()
        for rank, result in enumerate(results, 1):
            doc_id = str(result.external_id or result.file_ref)
            if doc_id in seen_in_channel:
                continue
            seen_in_channel.add(doc_id)
            item = by_doc.setdefault(
                doc_id,
                {
                    "document_id": doc_id,
                    "score": 0.0,
                    "sources": [],
                    "source_type": result.source_type,
                    "source_path": result.source_path,
                    "title": result.title,
                    "metadata": result.metadata,
                },
            )
            item["score"] += weight * (1 / (60 + rank))
            item["sources"].append({"channel": channel, "rank": rank, "distance": result.distance})
    for item in by_doc.values():
        item["score"] += exact_match_bonus(item, projection)
    rows = sorted(
        by_doc.values(),
        key=lambda item: (-float(item["score"]), min(source["rank"] for source in item["sources"]), item["document_id"]),
    )
    return rows


def exact_match_bonus(item: dict[str, Any], projection: QueryProjection) -> float:
    haystack = json.dumps(
        {
            "title": item.get("title", ""),
            "source_path": item.get("source_path", ""),
            "metadata": item.get("metadata", {}),
        },
        ensure_ascii=False,
    ).lower()
    terms = [*projection.entities[:8], *projection.constraints[:6]]
    matched = 0
    for term in terms:
        normalized = str(term).lower().strip()
        if len(normalized) >= 3 and normalized in haystack:
            matched += 1
    return min(0.02, matched * 0.004)


def evaluate_question(
    question: dict[str, Any],
    ranked_docs: list[dict[str, Any]],
    projection: QueryProjection,
) -> dict[str, Any]:
    expected = {str(item) for item in question.get("expected_doc_ids") or [] if str(item)}
    top_ids = [row["document_id"] for row in ranked_docs]
    best_rank = None
    for rank, document_id in enumerate(top_ids, 1):
        if document_id in expected:
            best_rank = rank
            break
    return {
        "question_id": question["question_id"],
        "question_type": question.get("question_type", ""),
        "source_types": question.get("source_types") or [],
        "expected_doc_ids": sorted(expected),
        "query_projection": {
            "entities": projection.entities,
            "relations": projection.relations,
            "constraints": projection.constraints,
            "expected_answer_type": projection.expected_answer_type,
        },
        "hit_any": best_rank is not None,
        "best_rank": best_rank,
        "candidate_count": len(top_ids),
        "top_document_ids": top_ids[:20],
        "top_candidates": ranked_docs[:5],
    }


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [row for row in rows if row.get("expected_doc_ids")]
    ranks = [int(row["best_rank"]) for row in answerable if row.get("best_rank") is not None]
    return {
        "question_count": len(rows),
        "answerable_question_count": len(answerable),
        "hit_at_1": hit_at(answerable, 1),
        "hit_at_3": hit_at(answerable, 3),
        "hit_at_5": hit_at(answerable, 5),
        "hit_at_10": hit_at(answerable, 10),
        "hit_at_20": hit_at(answerable, 20),
        "hit_at_50": hit_at(answerable, 50),
        "hit_at_100": hit_at(answerable, 100),
        "mrr": round(sum(1 / rank for rank in ranks) / len(answerable), 4) if answerable else 0,
        "avg_candidate_docs": round(sum(int(row.get("candidate_count") or 0) for row in rows) / len(rows), 3) if rows else 0,
        "misses_at_100": [
            row["question_id"]
            for row in answerable
            if row.get("best_rank") is None or int(row["best_rank"]) > 100
        ],
    }


def hit_at(rows: list[dict[str, Any]], k: int) -> float:
    if not rows:
        return 0
    return round(
        sum(1 for row in rows if row.get("best_rank") is not None and int(row["best_rank"]) <= k)
        / len(rows),
        4,
    )


class QueryExtractor:
    def __init__(self, db_path: Path, *, mode: str, model: str, timeout: float):
        self.db_path = db_path
        self.mode = mode
        self.model = model
        self.timeout = timeout
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS query_projection_cache (
                    mode TEXT NOT NULL,
                    model TEXT NOT NULL,
                    question TEXT NOT NULL,
                    projection_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(mode, model, question)
                )
                """
            )
            conn.commit()
        self.client = None
        if mode == "openai":
            from openai import OpenAI

            self.client = OpenAI(
                api_key=os.environ.get("OPENAI_API_KEY"),
                base_url=os.environ.get("OPENAI_BASE_URL") or None,
                timeout=timeout,
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def extract(self, question: str) -> QueryProjection:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT projection_json
                FROM query_projection_cache
                WHERE mode = ? AND model = ? AND question = ?
                """,
                (self.mode, self.model, question),
            ).fetchone()
            if row is not None:
                return projection_from_json(row["projection_json"])
        projection = self._extract_uncached(question)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO query_projection_cache(mode, model, question, projection_json)
                VALUES (?, ?, ?, ?)
                """,
                (self.mode, self.model, question, projection_to_json(projection)),
            )
            conn.commit()
        return projection

    def _extract_uncached(self, question: str) -> QueryProjection:
        if self.mode == "heuristic":
            return heuristic_query_projection(question)
        if self.client is None:
            raise RuntimeError("openai query extractor client is not initialized")
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract retrieval projections from an enterprise search question. "
                        "Return strict JSON with keys entities, relations, constraints, "
                        "expected_answer_type. Keep values short and canonical. Do not answer."
                    ),
                },
                {"role": "user", "content": question},
            ],
        )
        return projection_from_json(response.choices[0].message.content or "{}")


def heuristic_query_projection(question: str) -> QueryProjection:
    entities = dedupe(
        [
            *identifier_terms(question),
            *keyword_terms(question)[:16],
        ]
    )[:16]
    constraints = dedupe(
        [
            *extract_constraint_terms(question),
            *numeric_terms(question),
        ]
    )[:12]
    relations = []
    predicate = infer_query_predicate(question)
    subject = entities[0] if entities else "question"
    relations.append(f"{subject} | {predicate} | {question}")
    return QueryProjection(
        entities=entities,
        relations=relations,
        constraints=constraints,
        expected_answer_type=infer_answer_type(question),
    )


def projection_from_json(text: str) -> QueryProjection:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {}
    return QueryProjection(
        entities=clean_list(data.get("entities")),
        relations=clean_list(data.get("relations")),
        constraints=clean_list(data.get("constraints")),
        expected_answer_type=text_value(data.get("expected_answer_type")),
    )


def projection_to_json(projection: QueryProjection) -> str:
    return json.dumps(
        {
            "entities": projection.entities,
            "relations": projection.relations,
            "constraints": projection.constraints,
            "expected_answer_type": projection.expected_answer_type,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def clean_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return dedupe(text_value(item) for item in value if text_value(item))[:24]
    text = text_value(value)
    if not text:
        return []
    return dedupe(re.split(r"[,;\n]+", text))[:24]


def infer_query_predicate(question: str) -> str:
    lowered = question.lower()
    rules = [
        ("asks_default", ["default", "defaults"]),
        ("asks_limit", ["limit", "maximum", "minimum", "size"]),
        ("asks_cause", ["caused", "cause", "why"]),
        ("asks_owner", ["who", "owner", "assigned"]),
        ("asks_deadline", ["when", "deadline", "date"]),
        ("asks_status", ["status", "state"]),
        ("asks_requirement", ["required", "requirement", "must"]),
    ]
    for predicate, needles in rules:
        if any(needle in lowered for needle in needles):
            return predicate
    return "asks_about"


def infer_answer_type(question: str) -> str:
    lowered = question.lower()
    if "how many" in lowered or "limit" in lowered or "size" in lowered:
        return "number_or_limit"
    if lowered.startswith("who"):
        return "person_or_team"
    if lowered.startswith("when"):
        return "date_or_time"
    if "why" in lowered or "caused" in lowered:
        return "cause"
    return "fact"


def identifier_terms(text: str) -> list[str]:
    patterns = [
        r"\b[A-Z]{2,12}-\d{2,}\b",
        r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b\s*(?:=|:)\s*[A-Za-z0-9_.:/-]+",
        r"\b[A-Za-z][A-Za-z0-9_+-]+(?:[-_+][A-Za-z0-9]+)+\b",
        r"\b[A-Z]{2,}[A-Za-z0-9_-]*\b",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(match.strip() for match in re.findall(pattern, text))
    return found


def numeric_terms(text: str) -> list[str]:
    return re.findall(
        r"\b\d+(?:\.\d+)?\s*(?:MiB|GiB|MB|GB|ms|sec|seconds|minutes|hours|days|%|tokens?|req/s|rps)\b",
        text,
        flags=re.IGNORECASE,
    )


def load_questions(path: Path, *, start: int, limit: int, all_questions: bool) -> list[dict[str, Any]]:
    rows = pq.read_table(path).to_pylist()
    normalized = [
        {
            "question_id": row["question_id"],
            "question_type": row.get("question_type") or "",
            "source_types": [str(item) for item in row.get("source_types") or [] if str(item)],
            "question": row.get("question") or "",
            "expected_doc_ids": [str(item) for item in row.get("expected_doc_ids") or [] if str(item)],
        }
        for row in rows
    ]
    if all_questions:
        return normalized
    offset = max(start - 1, 0)
    return normalized[offset : offset + limit]


def dedupe(values: Any) -> list[str]:
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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_summary_md(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Projection Recall",
        "",
        "Offline retrieval eval only. No agent prompt, no benchmark runner, no fulltext/BM25 strategy.",
        "",
        "## Config",
        "",
        f"- dataset: `{summary['dataset_dir']}`",
        f"- index_dir: `{summary['index_dir']}`",
        f"- query_extractor: `{summary['query_extractor']}`",
        f"- embedding_model: `{summary['embedding_model']}`",
        f"- embedding_dimensions: `{summary['embedding_dimensions']}`",
        f"- question_start: `{summary['question_start']}`",
        f"- question_limit: `{summary['question_limit']}`",
        f"- source_type_filter: `{summary['source_type_filter']}`",
        "",
        "## Results",
        "",
        "| strategy | hit@1 | hit@3 | hit@5 | hit@10 | hit@20 | hit@50 | hit@100 | MRR | avg candidates | misses@100 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy in STRATEGIES:
        row = summary["strategies"][strategy]
        lines.append(
            "| `{strategy}` | {hit1:.4f} | {hit3:.4f} | {hit5:.4f} | {hit10:.4f} | "
            "{hit20:.4f} | {hit50:.4f} | {hit100:.4f} | {mrr:.4f} | {cand:.1f} | {misses} |".format(
                strategy=strategy,
                hit1=row["hit_at_1"],
                hit3=row["hit_at_3"],
                hit5=row["hit_at_5"],
                hit10=row["hit_at_10"],
                hit20=row["hit_at_20"],
                hit50=row["hit_at_50"],
                hit100=row["hit_at_100"],
                mrr=row["mrr"],
                cand=row["avg_candidate_docs"],
                misses=len(row["misses_at_100"]),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
