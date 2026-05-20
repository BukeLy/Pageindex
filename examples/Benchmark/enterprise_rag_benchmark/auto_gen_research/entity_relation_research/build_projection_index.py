from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

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

from pageindex.filesystem.semantic_index import SemanticIndexRecord, SQLiteVecSemanticIndex
from run_full_workspace_layer_research import (  # type: ignore[import-not-found]
    EmbeddingCache,
    compact_join,
    compact_summary,
    compact_vector_text,
    embedding_cache_model_key,
    extract_constraint_terms,
    keyword_terms,
    make_embedder,
    text_value,
)


DEFAULT_DATASET_DIR = BENCHMARK_DIR / "dataset"
DEFAULT_INDEX_DIR = RESEARCH_DIR / "cache" / "projection_indexes" / "enterprise_full_v1"
PROJECTION_INDEXES = [
    "metadata_composite_vector",
    "summary_only_vector",
    "entity_vectors",
    "constraint_vectors",
    "relation_vectors",
]
LOGGER = logging.getLogger("build_projection_index")


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BENCHMARK_DIR / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    index_dir = Path(args.index_dir).expanduser().resolve()
    index_dir.mkdir(parents=True, exist_ok=True)

    embedder = make_embedder(
        args.embedding_provider,
        args.embedding_model,
        dimensions=args.embedding_dimensions,
        timeout=args.embedding_timeout,
    )
    embedding_cache = EmbeddingCache(index_dir / "embedding_cache.sqlite")
    cache_model = embedding_cache_model_key(args.embedding_model, args.embedding_dimensions)

    requested = [item.strip() for item in args.indexes.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(PROJECTION_INDEXES))
    if unknown:
        raise SystemExit(f"unknown projection indexes: {', '.join(unknown)}")

    started = time.time()
    summaries = {}
    for index_name in requested:
        summaries[index_name] = build_index(
            dataset_dir=dataset_dir,
            index_dir=index_dir,
            index_name=index_name,
            embedder=embedder,
            embedding_cache=embedding_cache,
            cache_model=cache_model,
            args=args,
        )

    manifest = {
        "dataset_dir": str(dataset_dir),
        "index_dir": str(index_dir),
        "indexes": summaries,
        "embedding_provider": args.embedding_provider,
        "embedding_model": args.embedding_model,
        "embedding_dimensions": args.embedding_dimensions,
        "max_docs": args.max_docs,
        "seconds": round(time.time() - started, 3),
    }
    write_json(index_dir / "projection_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build EnterpriseRAG projection semantic indexes")
    parser.add_argument("--dataset-dir", default=str(DEFAULT_DATASET_DIR))
    parser.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    parser.add_argument("--indexes", default=",".join(PROJECTION_INDEXES))
    parser.add_argument("--embedding-provider", default="openai", choices=["openai", "hash"])
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    parser.add_argument("--embedding-dimensions", type=int, default=256)
    parser.add_argument("--embedding-timeout", type=float, default=60)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--max-docs", type=int, default=0)
    parser.add_argument("--max-entities-per-doc", type=int, default=4)
    parser.add_argument("--max-constraints-per-doc", type=int, default=4)
    parser.add_argument("--max-relations-per-doc", type=int, default=4)
    parser.add_argument("--reset", action="store_true")
    return parser.parse_args()


def build_index(
    *,
    dataset_dir: Path,
    index_dir: Path,
    index_name: str,
    embedder: Any,
    embedding_cache: EmbeddingCache,
    cache_model: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    documents_path = dataset_dir / "data" / "documents" / "test.parquet"
    db_path = index_dir / f"{index_name}.sqlite"
    index = SQLiteVecSemanticIndex(db_path)
    expected_docs = args.max_docs or pq.ParquetFile(documents_path).metadata.num_rows
    if db_path.exists() and not args.reset:
        try:
            info = index.info()
            if int(info.get("document_count") or 0) >= expected_docs:
                LOGGER.info("reuse index=%s rows=%s", index_name, info["document_count"])
                return {"index_path": str(db_path), "reused": True, "index_info": info}
        except Exception:  # noqa: BLE001 - research cache may be partial.
            pass
    if db_path.exists():
        db_path.unlink()

    started = time.time()
    initialized = False
    source_docs = 0
    indexed_rows = 0
    skipped_empty = 0
    for docs in iter_docs(documents_path, batch_size=args.batch_size, max_docs=args.max_docs):
        source_docs += len(docs)
        prepared = []
        for doc in docs:
            metadata = metadata_for_doc(doc)
            for projection_index, text in enumerate(projection_texts(index_name, metadata, args=args)):
                if not text:
                    skipped_empty += 1
                    continue
                prepared.append((doc, metadata, projection_index, text))
        if not prepared:
            continue
        texts = [item[3] for item in prepared]
        vectors = embedding_cache.embed_texts(
            texts,
            provider=args.embedding_provider,
            model=cache_model,
            embedder=embedder,
            batch_size=args.batch_size,
        )
        if not initialized:
            index.reset(
                dimension=len(vectors[0]),
                metadata={
                    "projection_index": index_name,
                    "dataset_dir": str(dataset_dir),
                    "embedding_provider": args.embedding_provider,
                    "embedding_model": args.embedding_model,
                    "embedding_dimensions": args.embedding_dimensions,
                    "max_entities_per_doc": args.max_entities_per_doc,
                    "max_constraints_per_doc": args.max_constraints_per_doc,
                    "max_relations_per_doc": args.max_relations_per_doc,
                },
            )
            initialized = True
        records = []
        for (doc, metadata, projection_index, text), vector in zip(prepared, vectors):
            doc_id = str(doc["doc_id"])
            records.append(
                SemanticIndexRecord(
                    file_ref=projection_file_ref(doc_id, index_name, projection_index, text),
                    external_id=doc_id,
                    source_type=metadata["source_type"],
                    source_path=metadata["source_path"],
                    title=metadata["title"],
                    text=text,
                    vector=vector,
                    metadata={
                        "projection_index": index_name,
                        "projection_ordinal": projection_index,
                        "text_chars": len(text),
                        "source_type": metadata["source_type"],
                        "title": metadata["title"],
                        "topic": metadata["topic"],
                        "entities": metadata["entities_text"],
                        "constraints": metadata["constraints_text"],
                    },
                )
            )
        indexed_rows += index.upsert_many(records)
        LOGGER.info(
            "indexed index=%s docs=%d rows=%d",
            index_name,
            source_docs,
            indexed_rows,
        )

    if not initialized:
        raise SystemExit(f"no records indexed for {index_name}")
    summary = {
        "index_name": index_name,
        "index_path": str(db_path),
        "source_docs": source_docs,
        "indexed_rows": indexed_rows,
        "skipped_empty": skipped_empty,
        "seconds": round(time.time() - started, 3),
        "index_info": index.info(),
    }
    write_json(db_path.with_suffix(".summary.json"), summary)
    return summary


def metadata_for_doc(doc: dict[str, Any]) -> dict[str, Any]:
    doc_id = text_value(doc.get("doc_id"))
    source_type = text_value(doc.get("source_type"))
    title = text_value(doc.get("title"))
    content = normalize_text(text_value(doc.get("content")))
    preview = content[:1200]
    summary = compact_summary(title, preview)
    topic_terms = keyword_terms(title)[:6] or keyword_terms(preview)[:6]
    topic = " ".join(topic_terms)
    entity_values = dedupe(
        [
            source_type,
            *identifier_terms(title),
            *identifier_terms(preview),
            *keyword_terms(title)[:10],
            *keyword_terms(preview)[:14],
        ]
    )
    constraint_values = dedupe(
        [
            *extract_constraint_terms(title + "\n" + preview),
            *numeric_terms(preview),
        ]
    )
    relation_values = relation_lines(
        title=title,
        content=content,
        topic=topic,
        entities=entity_values,
        constraints=constraint_values,
    )
    metadata_text = compact_vector_text(
        [
            f"source_type: {source_type}",
            f"title: {title}",
            f"topic: {topic}",
            f"entities: {compact_join(entity_values, limit=24)}",
            f"constraints: {compact_join(constraint_values, limit=24)}",
            f"summary: {summary}",
        ],
        max_chars=2600,
    )
    return {
        "doc_id": doc_id,
        "source_type": source_type,
        "source_path": f"{source_type}/{doc_id}" if source_type else doc_id,
        "title": title,
        "topic": topic,
        "summary": summary,
        "entities": entity_values,
        "constraints": constraint_values,
        "relations": relation_values,
        "entities_text": compact_join(entity_values, limit=24),
        "constraints_text": compact_join(constraint_values, limit=24),
        "metadata_text": metadata_text,
    }


def projection_texts(index_name: str, metadata: dict[str, Any], *, args: argparse.Namespace) -> list[str]:
    if index_name == "metadata_composite_vector":
        return [metadata["metadata_text"]]
    if index_name == "summary_only_vector":
        return [metadata["summary"]]
    if index_name == "entity_vectors":
        return [
            compact_vector_text(
                [
                    f"entity: {entity}",
                    f"topic: {metadata['topic']}",
                    f"title: {metadata['title']}",
                ],
                max_chars=700,
            )
            for entity in metadata["entities"][: args.max_entities_per_doc]
        ]
    if index_name == "constraint_vectors":
        return [
            compact_vector_text(
                [
                    f"constraint: {constraint}",
                    f"topic: {metadata['topic']}",
                    f"title: {metadata['title']}",
                ],
                max_chars=900,
            )
            for constraint in metadata["constraints"][: args.max_constraints_per_doc]
        ]
    if index_name == "relation_vectors":
        return metadata["relations"][: args.max_relations_per_doc]
    raise ValueError(f"unknown projection index: {index_name}")


def relation_lines(
    *,
    title: str,
    content: str,
    topic: str,
    entities: list[str],
    constraints: list[str],
) -> list[str]:
    subject = (entities[0] if entities else topic or title)[:120]
    candidates = split_sentences((title + "\n" + content)[:6000])
    lines: list[str] = []
    for sentence in candidates:
        predicate = infer_predicate(sentence)
        has_constraint = bool(numeric_terms(sentence) or extract_constraint_terms(sentence))
        if predicate == "relates_to" and not has_constraint:
            continue
        object_text = re.sub(r"\s+", " ", sentence).strip()[:220]
        if not object_text:
            continue
        lines.append(f"{subject} | {predicate} | {object_text}")
        if len(lines) >= 8:
            break
    if not lines and title:
        lines.append(f"{subject} | describes | {title}")
    if constraints:
        for constraint in constraints[:4]:
            lines.append(f"{subject} | has_constraint | {constraint}")
    return dedupe(lines)


def infer_predicate(text: str) -> str:
    lowered = text.lower()
    rules = [
        ("has_default", ["default", "defaults to"]),
        ("has_limit", ["limit", "max", "maximum", "minimum", "quota", "cap"]),
        ("requires", ["requires", "required", "must", "needs to"]),
        ("caused_by", ["caused", "root cause", "because of", "due to"]),
        ("mitigated_by", ["mitigation", "mitigated", "rollback", "fallback", "failover"]),
        ("owned_by", ["owner", "assigned", "assignee"]),
        ("has_deadline", ["deadline", "due date", "by end", "eta"]),
        ("has_status", ["status", "state", "stage", "priority", "severity"]),
        ("enforces", ["enforce", "validated", "validation", "policy"]),
    ]
    for predicate, needles in rules:
        if any(needle in lowered for needle in needles):
            return predicate
    return "relates_to"


def split_sentences(text: str) -> list[str]:
    raw = re.split(r"(?<=[.!?])\s+|[\n\r]+|; ", text)
    return [item.strip() for item in raw if len(item.strip()) >= 24]


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


def projection_file_ref(doc_id: str, index_name: str, ordinal: int, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"{doc_id}::{index_name}:{ordinal}:{digest}"


def iter_docs(path: Path, *, batch_size: int, max_docs: int) -> Iterable[list[dict[str, Any]]]:
    parquet = pq.ParquetFile(path)
    seen = 0
    for batch in parquet.iter_batches(batch_size=batch_size):
        rows = batch.to_pylist()
        if max_docs > 0 and seen + len(rows) > max_docs:
            rows = rows[: max_docs - seen]
        seen += len(rows)
        yield rows
        if max_docs > 0 and seen >= max_docs:
            break


def normalize_text(text: str) -> str:
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
