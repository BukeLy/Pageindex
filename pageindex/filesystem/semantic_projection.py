from __future__ import annotations

import sqlite3
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .core import DEFAULT_EMBEDDING_DIMENSIONS
from .semantic_index import (
    SCHEMA_VERSION,
    SQLiteVecSemanticIndex,
    SemanticIndexRecord,
)


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
SUMMARY_INDEX_NAME = "summary"


def normalize_base_url(value: str | None) -> str:
    raw = str(value or DEFAULT_OPENAI_BASE_URL).strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("embedding base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "embedding base URL must not contain credentials, query parameters, or a fragment"
        )
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            "",
        )
    )


@dataclass(frozen=True)
class SummaryEmbeddingProfile:
    base_url: str | None = DEFAULT_OPENAI_BASE_URL
    model: str = "text-embedding-3-small"
    dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS
    timeout: float = 60
    api_key: str | None = None

    def __post_init__(self) -> None:
        model = str(self.model or "").strip()
        if not model:
            raise ValueError("embedding model must not be empty")
        if int(self.dimensions) <= 0:
            raise ValueError("embedding dimensions must be positive")
        if float(self.timeout) <= 0:
            raise ValueError("embedding timeout must be positive")
        object.__setattr__(self, "base_url", normalize_base_url(self.base_url))
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "dimensions", int(self.dimensions))
        object.__setattr__(self, "timeout", float(self.timeout))

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "dimensions": self.dimensions,
        }


@dataclass(frozen=True)
class SummaryProjectionCandidate:
    file_ref: str
    distance: float
    source_type: str
    title: str
    metadata: dict[str, Any]

    @property
    def similarity(self) -> float:
        return 1.0 / (1.0 + max(0.0, self.distance))


class SummaryProjection:
    """Own the complete PIFS Summary Projection lifecycle."""

    def __init__(
        self,
        index_dir: str | Path,
        *,
        profile: SummaryEmbeddingProfile,
        embedder: Any | None = None,
        create: bool = False,
        fetch_multiplier: int = 100,
    ) -> None:
        self.index_dir = Path(index_dir).expanduser()
        self.profile = profile
        self._embedder_instance = embedder
        self.fetch_multiplier = fetch_multiplier
        self.index = SQLiteVecSemanticIndex(
            self.index_dir / f"{SUMMARY_INDEX_NAME}.sqlite"
        )
        summary_exists = self.index.db_path.exists()
        cache_path = self.index_dir / "embedding_cache.sqlite"
        cache_exists = cache_path.exists()
        if summary_exists and cache_exists:
            self.index.validate(self.profile.identity)
            self.embedding_cache = EmbeddingCache(cache_path, create=False)
        elif summary_exists or cache_exists:
            raise RuntimeError(
                "PIFS Summary Projection topology is incomplete; migrate this workspace with "
                "pifs-data/scripts/migrate_pifs_workspace.py before opening it."
            )
        elif create:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            self.index.reset(
                dimension=self.profile.dimensions,
                metadata=self.profile.identity,
            )
            self.embedding_cache = EmbeddingCache(cache_path, create=True)
        else:
            raise RuntimeError("PIFS Summary Projection is not available")

    def upsert_summary(self, record: dict[str, Any]) -> dict[str, Any]:
        summary = str((record.get("metadata") or {}).get("summary") or "").strip()
        if not summary:
            return {"status": "skipped", "reason": "missing_summary"}
        vector = self.embedding_cache.embed_texts(
            [summary],
            profile=self.profile,
            embedder=self._embedder(),
            batch_size=1,
        )[0]
        count = self.index.upsert_many(
            [
                SemanticIndexRecord(
                    file_ref=str(record["file_ref"]),
                    vector=vector,
                    text=summary,
                    external_id=record.get("external_id"),
                    source_type=str(record.get("source_type") or ""),
                    title=str(record.get("title") or ""),
                    metadata=dict(record.get("metadata") or {}),
                )
            ]
        )
        return {
            "status": "ready",
            "indexed_rows": count,
            "index_path": str(self.index.db_path),
            **self.profile.identity,
        }

    def delete_summary(self, file_ref: str) -> int:
        return self.index.delete_file_refs([file_ref])

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        file_refs: list[str] | None = None,
    ) -> list[SummaryProjectionCandidate]:
        query = normalize_text(query)
        if not query or not self.available:
            return []
        vector = self.embedding_cache.embed_texts(
            [query],
            profile=self.profile,
            embedder=self._embedder(),
            batch_size=1,
        )[0]
        filters = {"file_ref": file_refs} if file_refs is not None else None
        return [
            SummaryProjectionCandidate(
                file_ref=result.file_ref,
                distance=result.distance,
                source_type=result.source_type,
                title=result.title,
                metadata=result.metadata,
            )
            for result in self.index.search(
                vector,
                limit=limit,
                filters=filters,
                fetch_multiplier=self.fetch_multiplier,
            )
        ]

    @property
    def available(self) -> bool:
        return int(self.index.info().get("document_count") or 0) > 0

    def info(self) -> dict[str, Any]:
        return {
            **self.index.info(),
            "embedding_identity": self.profile.identity,
            "available": self.available,
        }

    def _embedder(self) -> Any:
        if self._embedder_instance is None:
            self._embedder_instance = EmbeddingClient(self.profile)
        return self._embedder_instance


class EmbeddingCache:
    def __init__(self, db_path: Path, *, create: bool) -> None:
        self.db_path = db_path
        exists = self.db_path.exists()
        if exists:
            self._validate()
        elif create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as connection:
                self._create_schema(connection)
        else:
            raise RuntimeError(
                "PIFS embedding cache is missing; migrate this workspace with "
                "pifs-data/scripts/migrate_pifs_workspace.py before opening it."
            )

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            connection = sqlite3.connect(
                f"{self.db_path.resolve().as_uri()}?mode=ro",
                uri=True,
            )
        else:
            connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def embed_texts(
        self,
        texts: list[str],
        *,
        profile: SummaryEmbeddingProfile,
        embedder: Any,
        batch_size: int,
    ) -> list[list[float]]:
        hashes = [SQLiteVecSemanticIndex.text_hash(text) for text in texts]
        cached: dict[str, list[float]] = {}
        with self.connect() as connection:
            for text_hash in sorted(set(hashes)):
                row = connection.execute(
                    """
                    SELECT vector_blob
                    FROM embedding_cache
                    WHERE base_url = ? AND model = ? AND dimensions = ? AND text_hash = ?
                    """,
                    (
                        profile.base_url,
                        profile.model,
                        profile.dimensions,
                        text_hash,
                    ),
                ).fetchone()
                if row is not None:
                    cached[text_hash] = decode_vector(
                        bytes(row["vector_blob"]), profile.dimensions
                    )
        missing_positions = [
            index for index, text_hash in enumerate(hashes) if text_hash not in cached
        ]
        for start in range(0, len(missing_positions), max(1, batch_size)):
            positions = missing_positions[start : start + max(1, batch_size)]
            batch_texts = [texts[index] for index in positions]
            vectors = embed_with_retry(embedder, batch_texts)
            if len(vectors) != len(positions):
                raise ValueError(
                    "embedding response length mismatch: "
                    f"requested {len(positions)}, received {len(vectors)}"
                )
            for vector in vectors:
                if len(vector) != profile.dimensions:
                    raise ValueError(
                        "embedding dimension mismatch: "
                        f"expected {profile.dimensions}, received {len(vector)}"
                    )
            with self.connect() as connection:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO embedding_cache(
                        base_url, model, dimensions, text_hash, vector_blob
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            profile.base_url,
                            profile.model,
                            profile.dimensions,
                            hashes[index],
                            encode_vector(vector),
                        )
                        for index, vector in zip(positions, vectors)
                    ],
                )
            for index, vector in zip(positions, vectors):
                cached[hashes[index]] = vector
        return [cached[text_hash] for text_hash in hashes]

    def _validate(self) -> None:
        try:
            with self.connect(read_only=True) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                actual = self._schema_signature(connection)
                invalid_rows = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM embedding_cache "
                        "WHERE dimensions <= 0 OR length(vector_blob) != dimensions * 4 "
                        "OR trim(base_url) = '' OR trim(model) = '' OR trim(text_hash) = ''"
                    ).fetchone()[0]
                )
            with sqlite3.connect(":memory:") as expected_connection:
                expected_connection.row_factory = sqlite3.Row
                self._create_schema(expected_connection)
                expected = self._schema_signature(expected_connection)
        except sqlite3.Error as exc:
            raise self._incompatible_schema_error() from exc
        if version != SCHEMA_VERSION or actual != expected or invalid_rows:
            raise self._incompatible_schema_error()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            f"""
            CREATE TABLE embedding_cache (
                base_url TEXT NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL CHECK(dimensions > 0),
                text_hash TEXT NOT NULL,
                vector_blob BLOB NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(base_url, model, dimensions, text_hash)
            );
            PRAGMA user_version = {SCHEMA_VERSION};
            """
        )

    @staticmethod
    def _schema_signature(connection: sqlite3.Connection) -> dict[str, Any]:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != {"embedding_cache"}:
            return {"tables": tables}
        columns = tuple(
            tuple(row)
            for row in connection.execute('PRAGMA table_xinfo("embedding_cache")')
        )
        indexes = []
        for row in connection.execute('PRAGMA index_list("embedding_cache")'):
            name = str(row[1])
            origin = str(row[3])
            index_columns = tuple(
                str(column[2])
                for column in connection.execute(f'PRAGMA index_info("{name}")')
            )
            indexes.append(
                (
                    name if origin == "c" else None,
                    int(row[2]),
                    origin,
                    int(row[4]),
                    index_columns,
                )
            )
        foreign_keys = tuple(
            sorted(
                (
                    str(row[2]),
                    str(row[3]),
                    str(row[4]),
                    str(row[5]),
                    str(row[6]),
                    str(row[7]),
                )
                for row in connection.execute(
                    'PRAGMA foreign_key_list("embedding_cache")'
                )
            )
        )
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'embedding_cache'"
        ).fetchone()[0]
        return {
            "tables": tables,
            "columns": columns,
            "indexes": tuple(sorted(indexes, key=repr)),
            "foreign_keys": foreign_keys,
            "sql": " ".join(str(sql).lower().split()),
        }

    @staticmethod
    def _incompatible_schema_error() -> RuntimeError:
        return RuntimeError(
            "Incompatible PIFS embedding cache schema; migrate this workspace with "
            "pifs-data/scripts/migrate_pifs_workspace.py before opening it."
        )


def validate_projection_topology(index_dir: str | Path) -> bool:
    index_dir = Path(index_dir).expanduser()
    summary_path = index_dir / f"{SUMMARY_INDEX_NAME}.sqlite"
    cache_path = index_dir / "embedding_cache.sqlite"
    summary_exists = summary_path.exists()
    cache_exists = cache_path.exists()
    if not summary_exists and not cache_exists:
        return False
    if not summary_exists or not cache_exists:
        raise RuntimeError(
            "PIFS Summary Projection topology is incomplete; migrate this workspace with "
            "pifs-data/scripts/migrate_pifs_workspace.py before opening it."
        )
    SQLiteVecSemanticIndex(summary_path).validate_schema()
    EmbeddingCache(cache_path, create=False)
    return True


class EmbeddingClient:
    def __init__(self, profile: SummaryEmbeddingProfile) -> None:
        from openai import OpenAI

        if not profile.api_key:
            raise ValueError("embedding_api_key is required for PIFS embeddings")
        self.profile = profile
        self.client = OpenAI(
            api_key=profile.api_key,
            base_url=profile.base_url,
            timeout=profile.timeout,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(
            model=self.profile.model,
            input=texts,
            dimensions=self.profile.dimensions,
        )
        return [
            list(item.embedding)
            for item in sorted(response.data, key=lambda item: item.index)
        ]


def normalize_text(text: str) -> str:
    return " ".join(str(text or "").split())


def embed_with_retry(
    embedder: Any,
    texts: list[str],
    *,
    max_attempts: int = 8,
) -> list[list[float]]:
    for attempt in range(1, max_attempts + 1):
        try:
            return embedder.embed(texts)
        except Exception as exc:
            if attempt >= max_attempts or not is_retryable_embedding_error(exc):
                raise
            time.sleep(min(120.0, 2.0 ** (attempt - 1)))
    raise RuntimeError("unreachable embedding retry state")


def is_retryable_embedding_error(exc: Exception) -> bool:
    retryable = getattr(exc, "retryable", None)
    if isinstance(retryable, bool):
        return retryable
    status_code = getattr(exc, "status_code", None)
    try:
        status = int(status_code)
    except (TypeError, ValueError):
        status = None
    if status is not None:
        if status in {408, 409, 429} or status >= 500:
            return True
        if 400 <= status < 500:
            return False
    name = exc.__class__.__name__.lower()
    return any(token in name for token in ("timeout", "connection", "ratelimit"))


def encode_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def decode_vector(blob: bytes, dimensions: int) -> list[float]:
    if len(blob) != dimensions * 4:
        raise ValueError(
            f"cached embedding has {len(blob) // 4} dimensions, expected {dimensions}"
        )
    return list(struct.unpack(f"<{dimensions}f", blob))
