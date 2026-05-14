import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union


@dataclass(frozen=True)
class SearchResult:
    reference_id: str
    file_ref: str
    external_id: Optional[str]
    title: str
    snippet: str
    folder_path: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OpenResult:
    reference_id: str
    file_ref: str
    start_line: int
    end_line: int
    text: str


class PageIndexFileSystem:
    def __init__(self, workspace: Union[str, Path]):
        self.workspace = Path(workspace).expanduser()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = self.workspace / "artifacts" / "text"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.workspace / "filesystem.sqlite"
        self._references: dict[str, str] = {}
        self._init_db()

    def register_file(
        self,
        *,
        storage_uri: str,
        source_path: str,
        folder_path: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        external_id: Optional[str] = None,
        title: Optional[str] = None,
        content: str = "",
        content_type: str = "text/plain",
        source_type: Optional[str] = None,
    ) -> str:
        metadata = metadata or {}
        source_path = source_path.strip("/")
        folder_path = self._normalize_path(folder_path or "/" + str(Path(source_path).parent))
        source_type = source_type or self._infer_source_type(source_path)
        title = title or metadata.get("title") or Path(source_path).stem
        file_ref = self._make_file_ref(external_id or source_path)
        text_artifact_path = self._write_text_artifact(file_ref, content)
        descriptor = self._build_descriptor(title, metadata)

        with self._connect() as conn:
            self._ensure_folder(conn, folder_path)
            conn.execute(
                """
                INSERT OR REPLACE INTO files (
                    file_ref, external_id, storage_uri, source_path, title,
                    descriptor, content_type, source_type, fingerprint,
                    text_artifact_path, metadata_json, folder_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_ref,
                    external_id,
                    storage_uri,
                    source_path,
                    title,
                    descriptor,
                    content_type,
                    source_type,
                    self._fingerprint(content),
                    str(text_artifact_path),
                    json.dumps(metadata, ensure_ascii=False),
                    folder_path,
                ),
            )
            conn.execute("DELETE FROM file_fts WHERE file_ref = ?", (file_ref,))
            conn.execute(
                """
                INSERT INTO file_fts(file_ref, title, body, metadata_text)
                VALUES (?, ?, ?, ?)
                """,
                (file_ref, title, content, self._metadata_text(metadata)),
            )
        return file_ref

    def browse(self, path: str = "/", limit: int = 100) -> dict[str, list[dict[str, Any]]]:
        path = self._normalize_path(path)
        with self._connect() as conn:
            folders = [
                dict(row)
                for row in conn.execute(
                    "SELECT path, kind, source FROM folders WHERE path LIKE ? ORDER BY path LIMIT ?",
                    (self._child_like(path), limit),
                )
            ]
            files = [
                self._row_to_file_summary(row)
                for row in conn.execute(
                    """
                    SELECT file_ref, external_id, title, folder_path, metadata_json
                    FROM files
                    WHERE folder_path = ?
                    ORDER BY title
                    LIMIT ?
                    """,
                    (path, limit),
                )
            ]
        return {"folders": folders, "files": files}

    def search(
        self,
        query: Union[str, list[str]],
        scope: Optional[dict[str, Any]] = None,
        metadata_filter: Optional[dict[str, Any]] = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        queries = [query] if isinstance(query, str) else list(query)
        match_query = self._fts_query(" ".join(queries))
        if not match_query:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    f.file_ref,
                    f.external_id,
                    f.title,
                    f.folder_path,
                    f.metadata_json,
                    snippet(file_fts, 2, '', '', '...', 16) AS snippet,
                    bm25(file_fts) AS rank
                FROM file_fts
                JOIN files f ON f.file_ref = file_fts.file_ref
                WHERE file_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match_query, max(limit * 10, limit)),
            ).fetchall()
        results = []
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            if not self._matches_scope(row["folder_path"], scope):
                continue
            if not self._matches_filter(metadata, metadata_filter):
                continue
            reference_id = self._reference_for(row["file_ref"])
            results.append(
                SearchResult(
                    reference_id=reference_id,
                    file_ref=row["file_ref"],
                    external_id=row["external_id"],
                    title=row["title"],
                    snippet=row["snippet"] or row["title"],
                    folder_path=row["folder_path"],
                    metadata=metadata,
                )
            )
            if len(results) >= limit:
                break
        return results

    def find(self, reference_id: str, patterns: Union[str, list[str]]) -> list[OpenResult]:
        file_ref = self._resolve_reference(reference_id)
        patterns = [patterns] if isinstance(patterns, str) else patterns
        text = self._read_text(file_ref)
        lines = text.splitlines()
        matches = []
        for i, line in enumerate(lines, 1):
            haystack = line.lower()
            if any(pattern.lower() in haystack for pattern in patterns):
                start = max(1, i - 1)
                end = min(len(lines), i + 1)
                matches.append(self._open_lines(reference_id, file_ref, start, end))
        return matches

    def open(self, reference_id: str, location: str) -> OpenResult:
        file_ref = self._resolve_reference(reference_id)
        start, end = self._parse_line_range(location)
        return self._open_lines(reference_id, file_ref, start, end)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    file_ref TEXT PRIMARY KEY,
                    external_id TEXT,
                    storage_uri TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    descriptor TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    source_type TEXT,
                    fingerprint TEXT NOT NULL,
                    text_artifact_path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    folder_path TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS folders (
                    path TEXT PRIMARY KEY,
                    kind TEXT NOT NULL DEFAULT 'physical',
                    source TEXT NOT NULL DEFAULT 'source'
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS file_fts
                USING fts5(file_ref UNINDEXED, title, body, metadata_text)
                """
            )

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_folder(self, conn: sqlite3.Connection, folder_path: str):
        parts = [part for part in folder_path.strip("/").split("/") if part]
        current = ""
        conn.execute("INSERT OR IGNORE INTO folders(path) VALUES ('/')")
        for part in parts:
            current = f"{current}/{part}"
            conn.execute("INSERT OR IGNORE INTO folders(path) VALUES (?)", (current,))

    def _write_text_artifact(self, file_ref: str, content: str) -> Path:
        path = self.artifacts_dir / f"{file_ref}.txt"
        path.write_text(content, encoding="utf-8")
        return path

    def _read_text(self, file_ref: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT text_artifact_path FROM files WHERE file_ref = ?",
                (file_ref,),
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown file_ref: {file_ref}")
        return Path(row["text_artifact_path"]).read_text(encoding="utf-8")

    def _open_lines(self, reference_id: str, file_ref: str, start: int, end: int) -> OpenResult:
        lines = self._read_text(file_ref).splitlines()
        start = max(1, start)
        end = min(max(start, end), len(lines))
        text = "\n".join(lines[start - 1:end])
        return OpenResult(
            reference_id=reference_id,
            file_ref=file_ref,
            start_line=start,
            end_line=end,
            text=text,
        )

    def _resolve_reference(self, reference_id: str) -> str:
        if reference_id.startswith("file_"):
            return reference_id
        try:
            return self._references[reference_id]
        except KeyError as exc:
            raise KeyError(f"Unknown reference_id: {reference_id}") from exc

    def _reference_for(self, file_ref: str) -> str:
        for reference_id, existing in self._references.items():
            if existing == file_ref:
                return reference_id
        reference_id = f"ref_{len(self._references) + 1}"
        self._references[reference_id] = file_ref
        return reference_id

    @staticmethod
    def _build_descriptor(title: str, metadata: dict[str, Any]) -> str:
        source = metadata.get("source_type") or metadata.get("repo") or metadata.get("channel")
        return f"{title} ({source})" if source else title

    @staticmethod
    def _child_like(path: str) -> str:
        return "/%" if path == "/" else f"{path}/%"

    @staticmethod
    def _fingerprint(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _fts_query(query: str) -> str:
        terms = re.findall(r"[\w.-]+", query)
        return " ".join(terms)

    @staticmethod
    def _infer_source_type(source_path: str) -> Optional[str]:
        parts = [part for part in Path(source_path).parts if part not in ("", ".")]
        return parts[0] if parts else None

    @staticmethod
    def _make_file_ref(seed: str) -> str:
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        return f"file_{digest}"

    @staticmethod
    def _metadata_text(metadata: dict[str, Any]) -> str:
        values = []
        for value in metadata.values():
            if isinstance(value, list):
                values.extend(str(item) for item in value)
            elif isinstance(value, dict):
                values.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
            elif value is not None:
                values.append(str(value))
        return " ".join(values)

    @classmethod
    def _matches_filter(
        cls,
        metadata: dict[str, Any],
        metadata_filter: Optional[dict[str, Any]],
    ) -> bool:
        if not metadata_filter:
            return True
        for key, condition in metadata_filter.items():
            value = metadata.get(key)
            if isinstance(condition, dict):
                if not cls._matches_condition(value, condition):
                    return False
            elif value != condition:
                return False
        return True

    @staticmethod
    def _matches_condition(value: Any, condition: dict[str, Any]) -> bool:
        for operator, expected in condition.items():
            if operator == "$eq" and value != expected:
                return False
            if operator == "$contains":
                if isinstance(value, list):
                    if expected not in value:
                        return False
                elif expected not in str(value):
                    return False
            if operator == "$in" and value not in expected:
                return False
        return True

    @classmethod
    def _matches_scope(cls, folder_path: str, scope: Optional[dict[str, Any]]) -> bool:
        if not scope:
            return True
        requested = scope.get("folder_path")
        if not requested:
            return True
        requested = cls._normalize_path(requested)
        folder_path = cls._normalize_path(folder_path)
        if scope.get("recursive", True):
            return folder_path == requested or folder_path.startswith(f"{requested}/")
        return folder_path == requested

    @staticmethod
    def _normalize_path(path: str) -> str:
        parts = [part for part in str(path).split("/") if part and part != "."]
        return "/" + "/".join(parts) if parts else "/"

    @staticmethod
    def _parse_line_range(location: str) -> tuple[int, int]:
        value = str(location).strip()
        if "-" in value:
            left, right = value.split("-", 1)
            start, end = int(left), int(right)
        else:
            start = end = int(value)
        if start < 1 or end < start:
            raise ValueError(f"Invalid line range: {location}")
        return start, end

    @staticmethod
    def _row_to_file_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "file_ref": row["file_ref"],
            "external_id": row["external_id"],
            "title": row["title"],
            "folder_path": row["folder_path"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }
