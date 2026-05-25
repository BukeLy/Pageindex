from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

PIPELINE_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = PIPELINE_DIR.parent
REPO_ROOT = PIPELINE_DIR.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pageindex.filesystem.semantic_folder_policy import (
    SEMANTIC_FOLDER_FORBIDDEN_FIELDS,
    SEMANTIC_FOLDER_ROOT,
    canonical_semantic_folder_field_name,
    is_semantic_folder_forbidden_field,
    semantic_folder_allowed_extension_fields,
    semantic_folder_field_identity_keys,
    semantic_folder_field_identity_set,
)

DEFAULT_DATASET_DIR = BENCHMARK_DIR / "dataset"
DEFAULT_RESULTS_DIR = PIPELINE_DIR / "results"
DEFAULT_DOC_PROFILES = BENCHMARK_DIR / "auto_gen_research" / "generated" / "doc_profiles.json"

SYSTEM_PROVENANCE_FIELDS = {
    "dataset_doc_uuid",
    "source_type",
    "source_path",
    "title",
    "content_type",
    "storage_uri",
    "created_at",
    "updated_at",
}
METADATA_BASE_FIELDS = {
    "doc_type",
    "domain",
    "topic",
    "summary",
    "entities",
    "relations",
    "constraints",
    "retrieval_cues",
}
FOLDER_BASE_FIELDS = {"doc_type", "domain", "topic"}
TEXT_HEAVY_FIELDS = {"summary", "entities", "relations", "constraints", "retrieval_cues"}
FORBIDDEN_EXTENSION_FIELDS = {
    *SYSTEM_PROVENANCE_FIELDS,
    *TEXT_HEAVY_FIELDS,
    "id",
    "doc_id",
    "document_id",
    "external_id",
    "file_ref",
    "question_id",
    "benchmark_id",
    "expected_doc_ids",
    "gold_answer",
    "answer",
    "path",
    "file_path",
    "filename",
    "url",
    "uri",
}
FORBIDDEN_FIELD_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r"(^|_)id(s)?$",
        r"(^|_)uuid$",
        r"(^|_)gold(_|$)",
        r"(^|_)expected(_|$)",
        r"(^|_)benchmark(_|$)",
        r"(^|_)dataset(_|$)",
        r"(^|_)path(_|$)",
        r"(^|_)uri(_|$)",
        r"(^|_)url(_|$)",
        r"(^|_)filename(_|$)",
    ]
]
FORBIDDEN_EXTENSION_FIELD_IDENTITIES = semantic_folder_field_identity_set(
    FORBIDDEN_EXTENSION_FIELDS | METADATA_BASE_FIELDS
)
SAFE_GENERATION_METHODS = {
    "llm",
    "normalized_from_llm",
    "source_metadata",
    "generated_incrementally",
}
UNSAFE_LEGACY_METHODS = {
    "compact_summary",
    "rule_compact_summary",
    "title_preview_summary",
    "keyword_terms",
    "rule_keyword_terms",
    "infer_predicate",
    "rule_infer_predicate",
    "fulltext_preview_embedding",
}


@dataclass(frozen=True)
class DatasetDocument:
    dataset_doc_uuid: str
    source_type: str
    title: str
    content: str
    content_type: str
    source_path: str
    storage_uri: str
    created_at: str = ""
    updated_at: str = ""


def now_run_name(prefix: str = "run") -> str:
    return time.strftime(f"%Y%m%d-%H%M%S-{prefix}")


def ensure_run_dir(run_name: str | None) -> Path:
    run_dir = DEFAULT_RESULTS_DIR / (run_name or now_run_name("semantic-metadata"))
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp_path, path)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def update_config(run_dir: str | Path, section: str, values: dict[str, Any]) -> None:
    path = Path(run_dir) / "config.json"
    config = read_json(path) if path.exists() else {}
    config.setdefault("pipeline", "semantic_metadata_pipeline")
    config.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    config[section] = values
    write_json(path, config)


def write_summary(run_dir: str | Path, sections: dict[str, Any]) -> None:
    lines = ["# Semantic Metadata Pipeline Summary", ""]
    config_path = Path(run_dir) / "config.json"
    if config_path.exists():
        config = read_json(config_path)
        lines.append(f"- Run directory: `{Path(run_dir).name}`")
        lines.append(f"- Pipeline: `{config.get('pipeline', 'semantic_metadata_pipeline')}`")
        lines.append("")
    for name, payload in sections.items():
        lines.append(f"## {name}")
        if isinstance(payload, dict):
            for key, value in payload.items():
                lines.append(f"- {key}: `{value}`")
        else:
            lines.append(str(payload))
        lines.append("")
    Path(run_dir, "summary.md").write_text("\n".join(lines), encoding="utf-8")


def load_dataset_documents(
    dataset: str | Path = DEFAULT_DATASET_DIR,
    *,
    max_docs: int = 0,
    target_doc_ids: set[str] | None = None,
) -> list[DatasetDocument]:
    dataset_path = Path(dataset).expanduser()
    if dataset_path.is_dir():
        parquet_path = dataset_path / "data" / "documents" / "test.parquet"
        if parquet_path.exists():
            return load_parquet_documents(parquet_path, max_docs=max_docs, target_doc_ids=target_doc_ids)
        jsonl_candidates = sorted(dataset_path.rglob("*.jsonl"))
        if jsonl_candidates:
            return load_jsonl_documents(jsonl_candidates[0], max_docs=max_docs, target_doc_ids=target_doc_ids)
    if dataset_path.suffix == ".parquet":
        return load_parquet_documents(dataset_path, max_docs=max_docs, target_doc_ids=target_doc_ids)
    if dataset_path.suffix == ".jsonl":
        return load_jsonl_documents(dataset_path, max_docs=max_docs, target_doc_ids=target_doc_ids)
    if dataset_path.suffix == ".json":
        return load_json_documents(dataset_path, max_docs=max_docs, target_doc_ids=target_doc_ids)
    raise FileNotFoundError(f"Unsupported dataset path: {dataset_path}")


def load_parquet_documents(
    path: Path,
    *,
    max_docs: int = 0,
    target_doc_ids: set[str] | None = None,
) -> list[DatasetDocument]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - depends on local environment.
        raise RuntimeError("pyarrow is required to read EnterpriseRAG parquet data") from exc

    docs: list[DatasetDocument] = []
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=512):
        for row in batch.to_pylist():
            doc = document_from_row(row, storage_base=path)
            if target_doc_ids is not None and doc.dataset_doc_uuid not in target_doc_ids:
                continue
            docs.append(doc)
            if max_docs > 0 and len(docs) >= max_docs:
                return docs
            if target_doc_ids is not None and len(docs) >= len(target_doc_ids):
                return docs
    return docs


def load_jsonl_documents(
    path: Path,
    *,
    max_docs: int = 0,
    target_doc_ids: set[str] | None = None,
) -> list[DatasetDocument]:
    docs: list[DatasetDocument] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            doc = document_from_row(json.loads(line), storage_base=path)
            if target_doc_ids is not None and doc.dataset_doc_uuid not in target_doc_ids:
                continue
            docs.append(doc)
            if max_docs > 0 and len(docs) >= max_docs:
                break
            if target_doc_ids is not None and len(docs) >= len(target_doc_ids):
                break
    return docs


def load_json_documents(
    path: Path,
    *,
    max_docs: int = 0,
    target_doc_ids: set[str] | None = None,
) -> list[DatasetDocument]:
    data = read_json(path)
    if isinstance(data, dict):
        values = data.values()
    else:
        values = data
    docs = []
    for row in values:
        doc = document_from_row(row, storage_base=path)
        if target_doc_ids is not None and doc.dataset_doc_uuid not in target_doc_ids:
            continue
        docs.append(doc)
    return docs[:max_docs] if max_docs > 0 else docs


def document_from_row(row: dict[str, Any], *, storage_base: Path) -> DatasetDocument:
    doc_id = text_value(row.get("doc_id") or row.get("dataset_doc_uuid") or row.get("id"))
    if not doc_id:
        doc_id = "doc_" + hashlib.sha1(json.dumps(row, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    source_type = text_value(row.get("source_type") or row.get("source") or "unknown")
    source_path = text_value(row.get("source_path"))
    if not source_path:
        source_path = f"{source_type}/{doc_id}.json" if source_type else f"{doc_id}.json"
    storage_uri = text_value(row.get("storage_uri"))
    if not storage_uri:
        storage_uri = f"{storage_base}#{doc_id}"
    return DatasetDocument(
        dataset_doc_uuid=doc_id,
        source_type=source_type,
        title=text_value(row.get("title")),
        content=normalize_text(text_value(row.get("content") or row.get("text"))),
        content_type=text_value(row.get("content_type") or "text/plain"),
        source_path=source_path,
        storage_uri=storage_uri,
        created_at=text_value(row.get("created_at")),
        updated_at=text_value(row.get("updated_at")),
    )


def load_metadata_sources(paths: list[str | Path]) -> dict[str, list[dict[str, Any]]]:
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(path)
        data = read_jsonl(path) if path.suffix == ".jsonl" else read_json(path)
        source_records = normalize_metadata_source(path, data)
        for doc_id, payload in source_records:
            by_doc[doc_id].append(payload)
    return by_doc


def normalize_metadata_source(path: Path, data: Any) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(data, dict) and all(str(key).startswith("dsid_") for key in data.keys()):
        return [
            (
                str(doc_id),
                {
                    "source_file": str(path),
                    "source_kind": "doc_profiles",
                    "payload": payload,
                },
            )
            for doc_id, payload in data.items()
            if isinstance(payload, dict)
        ]
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = data.get("files") or data.get("records") or data.get("documents") or []
    else:
        records = []
    normalized: list[tuple[str, dict[str, Any]]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if isinstance(record.get("metadata_base"), dict):
            doc_id = text_value(record.get("dataset_doc_uuid") or (record.get("system") or {}).get("dataset_doc_uuid"))
            if not doc_id:
                continue
            normalized.append(
                (
                    doc_id,
                    {
                        "source_file": str(path),
                        "source_kind": "normalized_metadata",
                        "payload": record,
                        "metadata": record.get("metadata_base") or {},
                    },
                )
            )
            continue
        doc_id = text_value(record.get("external_id") or record.get("dataset_doc_uuid") or record.get("doc_id"))
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else record
        if not doc_id and isinstance(metadata, dict):
            doc_id = text_value(metadata.get("dataset_doc_uuid"))
        if not doc_id:
            continue
        normalized.append(
            (
                doc_id,
                {
                    "source_file": str(path),
                    "source_kind": "registered_files",
                    "payload": record,
                    "metadata": metadata,
                },
            )
        )
    return normalized


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value).strip()


def normalize_text(text: str) -> str:
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def ensure_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        if "|" in value:
            return [item.strip() for item in value.split("|") if item.strip()]
        return [value.strip()] if value.strip() else []
    return [value]


def dedupe_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", " ", text_value(value)).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def slug(value: Any, *, max_len: int = 80) -> str:
    text = text_value(value).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    if not text:
        text = "unknown"
    return text[:max_len].strip("-") or "unknown"


def is_forbidden_extension_field(name: Any) -> bool:
    key = canonical_field_name(name)
    if semantic_folder_field_identity_keys(name) & FORBIDDEN_EXTENSION_FIELD_IDENTITIES:
        return True
    return any(pattern.search(key) for pattern in FORBIDDEN_FIELD_PATTERNS)


def canonical_field_name(name: Any) -> str:
    value = canonical_semantic_folder_field_name(name)
    return value or "field"


def compact_json(value: Any, *, max_chars: int = 400) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
    rendered = re.sub(r"\s+", " ", rendered).strip()
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max_chars - 3].rstrip() + "..."


def is_structured_entities(value: Any) -> bool:
    items = ensure_list(value)
    return bool(items) and all(isinstance(item, dict) and text_value(item.get("name")) for item in items)


def is_structured_relations(value: Any) -> bool:
    items = ensure_list(value)
    return bool(
        items
        and all(
            isinstance(item, dict)
            and text_value(item.get("subject"))
            and text_value(item.get("relation"))
            and text_value(item.get("object"))
            for item in items
        )
    )


def normalize_entity(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": text_value(item.get("name")),
        "type": text_value(item.get("type")),
        "aliases": dedupe_strings(ensure_list(item.get("aliases"))),
        "salience": safe_float(item.get("salience"), default=0.0),
    }


def normalize_relation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject": text_value(item.get("subject")),
        "relation": text_value(item.get("relation")),
        "object": text_value(item.get("object")),
        "evidence_terms": dedupe_strings(ensure_list(item.get("evidence_terms"))),
    }


def safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def provenance(
    *,
    source_file: str,
    source_field: str,
    generation_method: str,
    confidence: float,
    notes: str,
) -> dict[str, Any]:
    return {
        "source_file": source_file,
        "source_field": source_field,
        "generation_method": generation_method,
        "confidence": confidence,
        "notes": notes,
    }


def source_provenance(doc: DatasetDocument, field: str) -> dict[str, Any]:
    return provenance(
        source_file=doc.storage_uri,
        source_field=field,
        generation_method="source_metadata",
        confidence=1.0,
        notes="System/provenance field; excluded from schema discovery, folder fields, and semantic embeddings.",
    )


def has_unsafe_generation(prov: dict[str, Any] | None) -> bool:
    if not prov:
        return False
    method = str(prov.get("generation_method") or "").lower()
    field = str(prov.get("source_field") or "").lower()
    notes = str(prov.get("notes") or "").lower()
    if method in UNSAFE_LEGACY_METHODS:
        return True
    return any(marker in field or marker in notes for marker in UNSAFE_LEGACY_METHODS)


def load_normalized_metadata(path: str | Path) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    required = {"dataset_doc_uuid", "system", "metadata_base", "provenance"}
    missing = [index for index, row in enumerate(rows, 1) if not required.issubset(row)]
    if missing:
        raise ValueError(f"metadata file has invalid rows at positions: {missing[:5]}")
    return rows


def metadata_by_doc(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["dataset_doc_uuid"]): row for row in rows}


def field_values_for_doc(row: dict[str, Any], field: str) -> list[str]:
    base = row.get("metadata_base") or {}
    candidates = row.get("extension_candidates") or {}
    system = row.get("system") or {}
    field_keys = semantic_folder_field_identity_keys(field)
    for payload in (base, candidates, system):
        if field in payload:
            return dedupe_strings(ensure_list(payload.get(field)))
        for key, value in payload.items():
            if semantic_folder_field_identity_keys(key) & field_keys:
                return dedupe_strings(ensure_list(value))
    return []


def value_key(value: Any) -> str:
    return slug(value, max_len=120)


def semantic_folder_file_key(doc_id: str) -> str:
    digest = hashlib.sha1(str(doc_id).encode("utf-8")).hexdigest()[:16]
    return f"file_{digest}"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def encode_vector(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *[float(item) for item in vector])


def decode_vector(blob: bytes | None, vector_json: str | None = None) -> list[float]:
    if blob:
        return list(struct.unpack(f"{len(blob) // 4}f", blob))
    if vector_json:
        data = json.loads(vector_json)
        return [float(item) for item in data]
    return []


class BatchEmbeddingCache:
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
            conn.commit()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_many(self, *, provider: str, model: str, texts: list[str]) -> dict[int, list[float]]:
        hashes = [text_hash(text) for text in texts]
        result: dict[int, list[float]] = {}
        with self.connect() as conn:
            for index, hash_value in enumerate(hashes):
                row = conn.execute(
                    """
                    SELECT vector_blob, vector_json
                    FROM embedding_cache
                    WHERE provider = ? AND model = ? AND text_hash = ?
                    """,
                    (provider, model, hash_value),
                ).fetchone()
                if row is not None:
                    result[index] = decode_vector(row["vector_blob"], row["vector_json"])
        return result

    def set_many(
        self,
        *,
        provider: str,
        model: str,
        texts: list[str],
        vectors: list[list[float]],
    ) -> None:
        if len(texts) != len(vectors):
            raise ValueError("texts and vectors length mismatch")
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
                        text_hash(text),
                        len(vector),
                        encode_vector(vector),
                    )
                    for text, vector in zip(texts, vectors)
                ],
            )
            conn.commit()


class HashEmbedder:
    def __init__(self, dimensions: int):
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            values: list[float] = []
            seed = hashlib.sha256(text.encode("utf-8")).digest()
            counter = 0
            while len(values) < self.dimensions:
                digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
                for index in range(0, len(digest), 4):
                    chunk = digest[index:index + 4]
                    raw = int.from_bytes(chunk, "big") / 0xFFFFFFFF
                    values.append((raw * 2.0) - 1.0)
                    if len(values) >= self.dimensions:
                        break
                counter += 1
            norm = math.sqrt(sum(item * item for item in values)) or 1.0
            vectors.append([item / norm for item in values])
        return vectors


class OpenAIEmbedder:
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
        return [list(item.embedding) for item in response.data]


def make_embedder(provider: str, model: str, *, dimensions: int, timeout: float) -> Any:
    if provider == "hash":
        return HashEmbedder(dimensions)
    if provider == "openai":
        return OpenAIEmbedder(model, dimensions=dimensions, timeout=timeout)
    raise ValueError(f"unsupported embedding provider: {provider}")


def batch_embed_with_cache(
    *,
    texts: list[str],
    cache: BatchEmbeddingCache,
    provider: str,
    model: str,
    embedder: Any,
    batch_size: int,
) -> tuple[list[list[float]], dict[str, int]]:
    cached = cache.get_many(provider=provider, model=model, texts=texts)
    vectors: list[list[float] | None] = [cached.get(index) for index in range(len(texts))]
    missing_positions = [index for index, vector in enumerate(vectors) if vector is None]
    for start in range(0, len(missing_positions), max(1, batch_size)):
        positions = missing_positions[start:start + max(1, batch_size)]
        batch_texts = [texts[index] for index in positions]
        batch_vectors = embedder.embed(batch_texts)
        cache.set_many(provider=provider, model=model, texts=batch_texts, vectors=batch_vectors)
        for index, vector in zip(positions, batch_vectors):
            vectors[index] = vector
    return [vector or [] for vector in vectors], {
        "cache_hits": len(cached),
        "cache_misses": len(missing_positions),
    }


def sample_rows(rows: list[Any], *, limit: int) -> list[Any]:
    return rows[: max(0, limit)]


def field_distribution(values_by_doc: dict[str, list[str]]) -> dict[str, Any]:
    counts = Counter()
    docs_with_value = 0
    for values in values_by_doc.values():
        keys = {value_key(value) for value in values if value}
        if keys:
            docs_with_value += 1
        counts.update(keys)
    folder_sizes = list(counts.values())
    singleton = sum(1 for count in folder_sizes if count == 1)
    return {
        "docs_with_value": docs_with_value,
        "unique_values": len(counts),
        "value_counts": dict(counts.most_common(50)),
        "min_folder_size": min(folder_sizes) if folder_sizes else 0,
        "max_folder_size": max(folder_sizes) if folder_sizes else 0,
        "singleton_rate": singleton / len(folder_sizes) if folder_sizes else 0.0,
    }


def cardinality_expectation(unique_values: int, doc_count: int) -> str:
    rate = unique_values / max(1, doc_count)
    if unique_values <= 12 and rate <= 0.25:
        return "low"
    if unique_values <= 80 and rate <= 0.7:
        return "medium"
    return "high"
