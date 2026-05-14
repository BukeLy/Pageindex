from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .types import FileEntry, MetadataField

SCHEMA_VERSION = 1


class SQLiteFileSystemStore:
    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).expanduser()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.db_path = self.workspace / "filesystem.sqlite"
        self.text_dir = self.workspace / "artifacts" / "text"
        self.raw_dir = self.workspace / "artifacts" / "raw"
        self.pageindex_tree_dir = self.workspace / "artifacts" / "pageindex_trees"
        for path in (self.text_dir, self.raw_dir, self.pageindex_tree_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def migrate(self) -> None:
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                self._migrate_to_v1(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _migrate_to_v1(self, conn: sqlite3.Connection) -> None:
        self._migrate_legacy_tables(conn)
        conn.executescript(
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
                raw_artifact_path TEXT,
                pageindex_doc_id TEXT,
                pageindex_tree_status TEXT NOT NULL DEFAULT 'not_built',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                deleted_at TEXT
            );

            CREATE TABLE IF NOT EXISTS folders (
                folder_id TEXT PRIMARY KEY,
                parent_id TEXT,
                name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL DEFAULT 'physical',
                source TEXT NOT NULL DEFAULT 'source',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(parent_id) REFERENCES folders(folder_id)
            );

            CREATE TABLE IF NOT EXISTS file_folders (
                file_ref TEXT NOT NULL,
                folder_id TEXT NOT NULL,
                membership_kind TEXT NOT NULL DEFAULT 'primary',
                reason TEXT,
                confidence REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (file_ref, folder_id, membership_kind),
                FOREIGN KEY(file_ref) REFERENCES files(file_ref) ON DELETE CASCADE,
                FOREIGN KEY(folder_id) REFERENCES folders(folder_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS metadata_schema (
                schema_id TEXT PRIMARY KEY,
                scope_path TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS metadata_fields (
                field_id TEXT PRIMARY KEY,
                schema_id TEXT NOT NULL DEFAULT 'default',
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                indexed INTEGER NOT NULL DEFAULT 1,
                faceted INTEGER NOT NULL DEFAULT 0,
                sortable INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'inferred',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(schema_id, name),
                FOREIGN KEY(schema_id) REFERENCES metadata_schema(schema_id)
            );

            CREATE TABLE IF NOT EXISTS metadata_values (
                file_ref TEXT NOT NULL,
                field_id TEXT NOT NULL,
                value_text TEXT,
                value_number REAL,
                value_bool INTEGER,
                value_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(file_ref) REFERENCES files(file_ref) ON DELETE CASCADE,
                FOREIGN KEY(field_id) REFERENCES metadata_fields(field_id) ON DELETE CASCADE
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS file_fts
            USING fts5(file_ref UNINDEXED, title, body, metadata_text);

            CREATE INDEX IF NOT EXISTS idx_files_external_id ON files(external_id);
            CREATE INDEX IF NOT EXISTS idx_files_source_path ON files(source_path);
            CREATE INDEX IF NOT EXISTS idx_files_source_type ON files(source_type);
            CREATE INDEX IF NOT EXISTS idx_folders_path ON folders(path);
            CREATE INDEX IF NOT EXISTS idx_folders_parent_id ON folders(parent_id);
            CREATE INDEX IF NOT EXISTS idx_file_folders_folder ON file_folders(folder_id);
            CREATE INDEX IF NOT EXISTS idx_file_folders_kind ON file_folders(membership_kind);
            CREATE INDEX IF NOT EXISTS idx_metadata_fields_name ON metadata_fields(name);
            CREATE INDEX IF NOT EXISTS idx_metadata_values_field_text ON metadata_values(field_id, value_text);
            CREATE INDEX IF NOT EXISTS idx_metadata_values_field_number ON metadata_values(field_id, value_number);
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO metadata_schema(schema_id, scope_path, version, status)
            VALUES ('default', NULL, 1, 'active')
            """
        )
        self.ensure_folder(conn, "/")
        self._backfill_legacy_memberships(conn)
        self._backfill_metadata_values(conn)

    def _migrate_legacy_tables(self, conn: sqlite3.Connection) -> None:
        tables = self._tables(conn)
        if "folders" in tables and "folder_id" not in self._columns(conn, "folders"):
            conn.execute("ALTER TABLE folders RENAME TO folders_legacy_v0")
        if "files" in tables:
            columns = self._columns(conn, "files")
            for name, ddl in {
                "raw_artifact_path": "ALTER TABLE files ADD COLUMN raw_artifact_path TEXT",
                "pageindex_doc_id": "ALTER TABLE files ADD COLUMN pageindex_doc_id TEXT",
                "pageindex_tree_status": (
                    "ALTER TABLE files ADD COLUMN pageindex_tree_status TEXT "
                    "NOT NULL DEFAULT 'not_built'"
                ),
                "deleted_at": "ALTER TABLE files ADD COLUMN deleted_at TEXT",
            }.items():
                if name not in columns:
                    conn.execute(ddl)

    def _backfill_legacy_memberships(self, conn: sqlite3.Connection) -> None:
        if "files" not in self._tables(conn) or "folder_path" not in self._columns(conn, "files"):
            return
        rows = conn.execute(
            "SELECT file_ref, folder_path FROM files WHERE deleted_at IS NULL"
        ).fetchall()
        for row in rows:
            folder_id = self.ensure_folder(conn, row["folder_path"] or "/")
            conn.execute(
                """
                INSERT OR IGNORE INTO file_folders(
                    file_ref, folder_id, membership_kind, reason, confidence
                ) VALUES (?, ?, 'primary', 'legacy_folder_path', 1.0)
                """,
                (row["file_ref"], folder_id),
            )

    def _backfill_metadata_values(self, conn: sqlite3.Connection) -> None:
        if "files" not in self._tables(conn):
            return
        rows = conn.execute(
            "SELECT file_ref, metadata_json FROM files WHERE deleted_at IS NULL"
        ).fetchall()
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            fields = [
                MetadataField(name=name, field_type=self._infer_metadata_type(value))
                for name, value in metadata.items()
                if self._valid_field_name(name)
            ]
            self.upsert_metadata_fields(fields, conn=conn)
            self.replace_metadata_values(conn, row["file_ref"], metadata)

    @staticmethod
    def _tables(conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')").fetchall()
        return {row["name"] for row in rows}

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def insert_file(self, record: dict[str, Any]) -> None:
        with self.connect() as conn:
            folder_id = self.ensure_folder(conn, record["folder_path"])
            self._insert_file_row(conn, record)
            conn.execute(
                """
                INSERT OR REPLACE INTO file_folders(
                    file_ref, folder_id, membership_kind, reason, confidence
                ) VALUES (?, ?, 'primary', 'source_path', 1.0)
                """,
                (record["file_ref"], folder_id),
            )
            self.replace_metadata_values(conn, record["file_ref"], record["metadata"])
            self.replace_fts(conn, record)

    def _insert_file_row(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        current_timestamp = object()
        columns = [
            "file_ref",
            "external_id",
            "storage_uri",
            "source_path",
            "title",
            "descriptor",
            "content_type",
            "source_type",
            "fingerprint",
            "text_artifact_path",
            "raw_artifact_path",
            "pageindex_doc_id",
            "pageindex_tree_status",
            "metadata_json",
            "deleted_at",
            "updated_at",
        ]
        values: list[Any] = [
            record["file_ref"],
            record["external_id"],
            record["storage_uri"],
            record["source_path"],
            record["title"],
            record["descriptor"],
            record["content_type"],
            record["source_type"],
            record["fingerprint"],
            record["text_artifact_path"],
            record["raw_artifact_path"],
            record.get("pageindex_doc_id"),
            record.get("pageindex_tree_status", "not_built"),
            record["metadata_json"],
            None,
            current_timestamp,
        ]
        if "folder_path" in self._columns(conn, "files"):
            columns.insert(-2, "folder_path")
            values.insert(-2, record["folder_path"])
        placeholders = ", ".join("CURRENT_TIMESTAMP" if value is current_timestamp else "?" for value in values)
        bound_values = [value for value in values if value is not current_timestamp]
        conn.execute(
            f"""
            INSERT OR REPLACE INTO files ({", ".join(columns)})
            VALUES ({placeholders})
            """,
            bound_values,
        )

    def replace_metadata_values(
        self,
        conn: sqlite3.Connection,
        file_ref: str,
        metadata: dict[str, Any],
    ) -> None:
        conn.execute("DELETE FROM metadata_values WHERE file_ref = ?", (file_ref,))
        for name, value in metadata.items():
            if not self._valid_field_name(name):
                continue
            field_id = self.field_id(name)
            for item in self._metadata_value_items(value):
                conn.execute(
                    """
                    INSERT INTO metadata_values(
                        file_ref, field_id, value_text, value_number, value_bool, value_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_ref,
                        field_id,
                        item["value_text"],
                        item["value_number"],
                        item["value_bool"],
                        item["value_json"],
                    ),
                )

    def replace_fts(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        conn.execute("DELETE FROM file_fts WHERE file_ref = ?", (record["file_ref"],))
        conn.execute(
            """
            INSERT INTO file_fts(file_ref, title, body, metadata_text)
            VALUES (?, ?, ?, ?)
            """,
            (
                record["file_ref"],
                record["title"],
                record["content"],
                record["metadata_text"],
            ),
        )

    def upsert_metadata_fields(
        self,
        fields: Iterable[MetadataField],
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        owns_connection = conn is None
        if conn is None:
            conn = self.connect()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO metadata_schema(schema_id, scope_path, version, status)
                VALUES ('default', NULL, 1, 'active')
                """
            )
            for field in fields:
                conn.execute(
                    """
                    INSERT INTO metadata_fields(
                        field_id, schema_id, name, type, description,
                        indexed, faceted, sortable, source, updated_at
                    ) VALUES (?, 'default', ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(schema_id, name) DO UPDATE SET
                        type = excluded.type,
                        source = excluded.source,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        self.field_id(field.name),
                        field.name,
                        field.field_type,
                        field.description,
                        int(field.indexed),
                        int(field.faceted),
                        int(field.sortable),
                        field.source,
                    ),
                )
            if owns_connection:
                conn.commit()
        finally:
            if owns_connection:
                conn.close()

    def metadata_field_exists(self, name: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM metadata_fields WHERE schema_id = 'default' AND name = ?",
                (name,),
            ).fetchone()
        return row is not None

    def list_metadata_fields(self) -> list[MetadataField]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT name, type, description, indexed, faceted, sortable, source
                FROM metadata_fields
                WHERE schema_id = 'default'
                ORDER BY name
                """
            ).fetchall()
        return [
            MetadataField(
                name=row["name"],
                field_type=row["type"],
                description=row["description"],
                indexed=bool(row["indexed"]),
                faceted=bool(row["faceted"]),
                sortable=bool(row["sortable"]),
                source=row["source"],
            )
            for row in rows
        ]

    def list_folder(self, path: str = "/", recursive: bool = False, limit: int = 100) -> dict[str, Any]:
        path = normalize_path(path)
        with self.connect() as conn:
            folder = self._folder_by_path(conn, path)
            if folder is None:
                raise KeyError(f"Unknown folder path: {path}")
            if recursive:
                folder_rows = conn.execute(
                    """
                    SELECT folder_id, parent_id, name, path, kind, source
                    FROM folders
                    WHERE path != ? AND (path LIKE ?)
                    ORDER BY path
                    LIMIT ?
                    """,
                    (path, self._descendant_like(path), limit),
                ).fetchall()
                file_rows = self._file_rows_for_scope(conn, path, True, limit)
            else:
                folder_rows = conn.execute(
                    """
                    SELECT folder_id, parent_id, name, path, kind, source
                    FROM folders
                    WHERE parent_id = ?
                    ORDER BY kind, name
                    LIMIT ?
                    """,
                    (folder["folder_id"], limit),
                ).fetchall()
                file_rows = self._file_rows_for_scope(conn, path, False, limit)
        return {
            "folders": [self._folder_row_to_dict(row) for row in folder_rows],
            "files": [self._file_summary(row) for row in file_rows],
        }

    def search_files(
        self,
        query: str | list[str] | None,
        *,
        scope: Optional[dict[str, Any]] = None,
        metadata_filter: Optional[dict[str, Any]] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        query_text = self._query_text(query)
        match_queries = self._fts_match_queries(query_text) if query_text else [None]
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for match_query in match_queries:
            rows = self._search_once(match_query, scope, metadata_filter, max(limit * 25, limit))
            for row in rows:
                if row["file_ref"] in seen:
                    continue
                seen.add(row["file_ref"])
                results.append(self._search_row_to_dict(row))
                if len(results) >= limit:
                    return results
            if results:
                return results
        return results

    def _search_once(
        self,
        match_query: str | None,
        scope: Optional[dict[str, Any]],
        metadata_filter: Optional[dict[str, Any]],
        limit: int,
    ) -> list[sqlite3.Row]:
        joins = [
            "JOIN file_folders ff ON ff.file_ref = f.file_ref AND ff.membership_kind = 'primary'",
            "JOIN folders pf ON pf.folder_id = ff.folder_id",
        ]
        selects = [
            "f.file_ref",
            "f.external_id",
            "f.source_path",
            "f.title",
            "f.metadata_json",
            "pf.path AS folder_path",
        ]
        where = ["f.deleted_at IS NULL"]
        params: list[Any] = []
        if match_query:
            joins.append("JOIN file_fts ON file_fts.file_ref = f.file_ref")
            selects.append("snippet(file_fts, 2, '', '', '...', 16) AS snippet")
            selects.append("bm25(file_fts) AS rank")
            where.append("file_fts MATCH ?")
            params.append(match_query)
            order_by = "rank"
        else:
            selects.append("f.descriptor AS snippet")
            selects.append("0 AS rank")
            order_by = "f.title"
        scope_sql, scope_params = self._scope_sql(scope)
        if scope_sql:
            where.append(scope_sql)
            params.extend(scope_params)
        metadata_sql, metadata_params = self._metadata_filter_sql(metadata_filter)
        where.extend(metadata_sql)
        params.extend(metadata_params)
        sql = f"""
            SELECT {", ".join(selects)}
            FROM files f
            {" ".join(joins)}
            WHERE {" AND ".join(where)}
            ORDER BY {order_by}
            LIMIT ?
        """
        params.append(limit)
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def _metadata_filter_sql(self, metadata_filter: Optional[dict[str, Any]]) -> tuple[list[str], list[Any]]:
        if not metadata_filter:
            return [], []
        clauses = []
        params: list[Any] = []
        for field, condition in metadata_filter.items():
            if not isinstance(condition, dict):
                condition = {"$eq": condition}
            for operator, expected in condition.items():
                field_id = self.field_id(field)
                if operator == "$eq":
                    clauses.append(
                        """
                        EXISTS (
                            SELECT 1 FROM metadata_values mv
                            WHERE mv.file_ref = f.file_ref
                              AND mv.field_id = ?
                              AND mv.value_text = ?
                        )
                        """
                    )
                    params.extend([field_id, self._metadata_compare_text(expected)])
                elif operator == "$ne":
                    clauses.append(
                        """
                        NOT EXISTS (
                            SELECT 1 FROM metadata_values mv
                            WHERE mv.file_ref = f.file_ref
                              AND mv.field_id = ?
                              AND mv.value_text = ?
                        )
                        """
                    )
                    params.extend([field_id, self._metadata_compare_text(expected)])
                elif operator == "$contains":
                    clauses.append(
                        """
                        EXISTS (
                            SELECT 1 FROM metadata_values mv
                            WHERE mv.file_ref = f.file_ref
                              AND mv.field_id = ?
                              AND (mv.value_text = ? OR mv.value_text LIKE ?)
                        )
                        """
                    )
                    text = self._metadata_compare_text(expected)
                    params.extend([field_id, text, f"%{text}%"])
                elif operator == "$in":
                    values = [self._metadata_compare_text(item) for item in expected]
                    if not values:
                        clauses.append("0")
                    else:
                        placeholders = ", ".join("?" for _ in values)
                        clauses.append(
                            f"""
                            EXISTS (
                                SELECT 1 FROM metadata_values mv
                                WHERE mv.file_ref = f.file_ref
                                  AND mv.field_id = ?
                                  AND mv.value_text IN ({placeholders})
                            )
                            """
                        )
                        params.extend([field_id, *values])
                elif operator in {"$>", "$>=", "$<", "$<="}:
                    comparator = operator[1:]
                    clauses.append(
                        f"""
                        EXISTS (
                            SELECT 1 FROM metadata_values mv
                            WHERE mv.file_ref = f.file_ref
                              AND mv.field_id = ?
                              AND mv.value_number IS NOT NULL
                              AND mv.value_number {comparator} ?
                        )
                        """
                    )
                    params.extend([field_id, float(expected)])
        return clauses, params

    def get_file(self, file_ref: str) -> FileEntry:
        with self.connect() as conn:
            row = self._file_entry_row(conn, file_ref)
        if row is None:
            raise KeyError(f"Unknown file_ref: {file_ref}")
        return self._file_entry(row)

    def resolve_file_ref(self, target: str) -> str:
        target = str(target).strip()
        if not target:
            raise KeyError("Empty file target")
        with self.connect() as conn:
            row = conn.execute(
                "SELECT file_ref FROM files WHERE file_ref = ? AND deleted_at IS NULL",
                (target,),
            ).fetchone()
            if row:
                return row["file_ref"]
            row = conn.execute(
                "SELECT file_ref FROM files WHERE external_id = ? AND deleted_at IS NULL",
                (target,),
            ).fetchone()
            if row:
                return row["file_ref"]
            stripped = target.strip("/")
            row = conn.execute(
                "SELECT file_ref FROM files WHERE source_path = ? AND deleted_at IS NULL",
                (stripped,),
            ).fetchone()
            if row:
                return row["file_ref"]
            row = conn.execute(
                """
                SELECT f.file_ref
                FROM files f
                JOIN file_folders ff ON ff.file_ref = f.file_ref AND ff.membership_kind = 'primary'
                JOIN folders pf ON pf.folder_id = ff.folder_id
                WHERE (pf.path || '/' || f.title) = ?
                   OR (pf.path || '/' || f.source_path) = ?
                LIMIT 1
                """,
                (target, target),
            ).fetchone()
            if row:
                return row["file_ref"]
        raise KeyError(f"Unknown file target: {target}")

    def ensure_folder(
        self,
        conn: sqlite3.Connection | None,
        path: str,
        *,
        kind: str = "physical",
        source: str = "source",
    ) -> str:
        owns_connection = conn is None
        if conn is None:
            conn = self.connect()
        try:
            normalized = normalize_path(path)
            if normalized == "/":
                folder_id = self.folder_id("/")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO folders(folder_id, parent_id, name, path, kind, source)
                    VALUES (?, NULL, '/', '/', ?, ?)
                    """,
                    (folder_id, kind, source),
                )
                if owns_connection:
                    conn.commit()
                return folder_id
            parent_id = self.ensure_folder(conn, str(Path(normalized).parent), kind=kind, source=source)
            name = normalized.rsplit("/", 1)[-1]
            folder_id = self.folder_id(normalized)
            conn.execute(
                """
                INSERT OR IGNORE INTO folders(folder_id, parent_id, name, path, kind, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (folder_id, parent_id, name, normalized, kind, source),
            )
            if owns_connection:
                conn.commit()
            return folder_id
        finally:
            if owns_connection:
                conn.close()

    def read_text(self, file_ref: str) -> str:
        entry = self.get_file(file_ref)
        return Path(entry.text_artifact_path).read_text(encoding="utf-8")

    def write_text_artifact(self, file_ref: str, content: str) -> Path:
        path = self.text_dir / f"{file_ref}.txt"
        path.write_text(content, encoding="utf-8")
        return path

    def write_raw_artifact(self, file_ref: str, metadata: dict[str, Any]) -> Path:
        path = self.raw_dir / f"{file_ref}.json"
        path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def file_info(self, target: str) -> dict[str, Any]:
        return self._file_entry_to_dict(self.get_file(self.resolve_file_ref(target)))

    def _file_entry_row(self, conn: sqlite3.Connection, file_ref: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT
                f.file_ref,
                f.external_id,
                f.storage_uri,
                f.source_path,
                f.title,
                f.descriptor,
                f.content_type,
                f.source_type,
                f.fingerprint,
                f.text_artifact_path,
                f.raw_artifact_path,
                f.pageindex_doc_id,
                f.pageindex_tree_status,
                f.metadata_json,
                pf.path AS folder_path
            FROM files f
            JOIN file_folders ff ON ff.file_ref = f.file_ref AND ff.membership_kind = 'primary'
            JOIN folders pf ON pf.folder_id = ff.folder_id
            WHERE f.file_ref = ? AND f.deleted_at IS NULL
            """,
            (file_ref,),
        ).fetchone()

    def _file_rows_for_scope(
        self,
        conn: sqlite3.Connection,
        path: str,
        recursive: bool,
        limit: int,
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT f.file_ref, f.external_id, f.title, f.source_path, f.metadata_json, pf.path AS folder_path
            FROM files f
            JOIN file_folders ff ON ff.file_ref = f.file_ref AND ff.membership_kind = 'primary'
            JOIN folders pf ON pf.folder_id = ff.folder_id
            WHERE f.deleted_at IS NULL
        """
        params: list[Any]
        if recursive:
            sql += " AND (pf.path = ? OR pf.path LIKE ?)"
            params = [path, self._descendant_like(path)]
        else:
            sql += " AND pf.path = ?"
            params = [path]
        sql += " ORDER BY f.title LIMIT ?"
        params.append(limit)
        return conn.execute(sql, params).fetchall()

    def _scope_sql(self, scope: Optional[dict[str, Any]]) -> tuple[str, list[Any]]:
        if not scope or not scope.get("folder_path"):
            return "", []
        folder_path = normalize_path(scope["folder_path"])
        if scope.get("recursive", True):
            return "(pf.path = ? OR pf.path LIKE ?)", [folder_path, self._descendant_like(folder_path)]
        return "pf.path = ?", [folder_path]

    def _folder_by_path(self, conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT folder_id, parent_id, name, path, kind, source
            FROM folders
            WHERE path = ?
            """,
            (path,),
        ).fetchone()

    @staticmethod
    def _descendant_like(path: str) -> str:
        return "/%" if path == "/" else f"{path}/%"

    @staticmethod
    def _folder_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "folder_id": row["folder_id"],
            "parent_id": row["parent_id"],
            "name": row["name"],
            "path": row["path"],
            "kind": row["kind"],
            "source": row["source"],
        }

    @staticmethod
    def _file_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "file_ref": row["file_ref"],
            "external_id": row["external_id"],
            "title": row["title"],
            "source_path": row["source_path"],
            "folder_path": row["folder_path"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    @staticmethod
    def _search_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "file_ref": row["file_ref"],
            "external_id": row["external_id"],
            "title": row["title"],
            "source_path": row["source_path"],
            "snippet": row["snippet"] or row["title"],
            "folder_path": row["folder_path"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    @staticmethod
    def _file_entry(row: sqlite3.Row) -> FileEntry:
        return FileEntry(
            file_ref=row["file_ref"],
            external_id=row["external_id"],
            storage_uri=row["storage_uri"],
            source_path=row["source_path"],
            title=row["title"],
            descriptor=row["descriptor"],
            content_type=row["content_type"],
            source_type=row["source_type"],
            fingerprint=row["fingerprint"],
            text_artifact_path=row["text_artifact_path"],
            raw_artifact_path=row["raw_artifact_path"],
            pageindex_doc_id=row["pageindex_doc_id"],
            pageindex_tree_status=row["pageindex_tree_status"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            folder_path=row["folder_path"],
        )

    @classmethod
    def _file_entry_to_dict(cls, entry: FileEntry) -> dict[str, Any]:
        return {
            "file_ref": entry.file_ref,
            "external_id": entry.external_id,
            "storage_uri": entry.storage_uri,
            "source_path": entry.source_path,
            "title": entry.title,
            "descriptor": entry.descriptor,
            "content_type": entry.content_type,
            "source_type": entry.source_type,
            "fingerprint": entry.fingerprint,
            "text_artifact_path": entry.text_artifact_path,
            "raw_artifact_path": entry.raw_artifact_path,
            "pageindex_doc_id": entry.pageindex_doc_id,
            "pageindex_tree_status": entry.pageindex_tree_status,
            "metadata": entry.metadata,
            "folder_path": entry.folder_path,
        }

    @staticmethod
    def _query_text(query: str | list[str] | None) -> str:
        if query is None:
            return ""
        if isinstance(query, list):
            return " ".join(str(item) for item in query)
        return str(query)

    @classmethod
    def _fts_match_queries(cls, query: str) -> list[str]:
        terms = cls._fts_terms(query)
        if not terms:
            return []
        queries = [" ".join(terms)]
        if len(terms) > 1:
            queries.append(" OR ".join(terms))
        return queries

    @staticmethod
    def _fts_terms(query: str) -> list[str]:
        stopwords = {
            "a",
            "an",
            "and",
            "are",
            "as",
            "at",
            "be",
            "by",
            "did",
            "do",
            "does",
            "for",
            "from",
            "how",
            "in",
            "is",
            "it",
            "of",
            "on",
            "or",
            "that",
            "the",
            "to",
            "was",
            "were",
            "what",
            "when",
            "where",
            "which",
            "who",
            "why",
            "with",
        }
        terms = re.findall(r"[A-Za-z0-9_]+", query.lower())
        unique_terms = []
        seen = set()
        for term in terms:
            if term in stopwords or term in seen:
                continue
            seen.add(term)
            unique_terms.append(term)
        return unique_terms

    @staticmethod
    def _metadata_value_items(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            items = []
            for item in value:
                items.extend(SQLiteFileSystemStore._metadata_value_items(item))
            return items
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
        value_text = SQLiteFileSystemStore._metadata_compare_text(value)
        return [
            {
                "value_text": value_text,
                "value_number": float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None,
                "value_bool": int(value) if isinstance(value, bool) else None,
                "value_json": value_json,
            }
        ]

    @staticmethod
    def _metadata_compare_text(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return "" if value is None else str(value)

    @staticmethod
    def _infer_metadata_type(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return "number"
        if isinstance(value, list):
            return "array"
        return "string"

    @staticmethod
    def _valid_field_name(name: str) -> bool:
        return re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(name)) is not None

    @staticmethod
    def folder_id(path: str) -> str:
        normalized = normalize_path(path)
        if normalized == "/":
            return "folder_root"
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
        return f"folder_{digest}"

    @staticmethod
    def field_id(name: str) -> str:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
        return f"field_{digest}"


def normalize_path(path: str | Path | None) -> str:
    if path is None:
        return "/"
    parts = [part for part in str(path).replace("\\", "/").split("/") if part and part != "."]
    return "/" + "/".join(parts) if parts else "/"


def make_file_ref(seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"file_{digest}"


def fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def metadata_text(metadata: dict[str, Any]) -> str:
    values = []
    for value in metadata.values():
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif isinstance(value, dict):
            values.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
        elif value is not None:
            values.append(str(value))
    return " ".join(values)
