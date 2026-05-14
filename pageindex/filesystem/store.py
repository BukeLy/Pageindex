from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .types import FileEntry, MetadataField

SCHEMA_VERSION = 2


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
                conn.execute("PRAGMA user_version = 1")
                version = 1
            if version < 2:
                self._migrate_to_v2(conn)
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
                description TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'physical',
                source TEXT NOT NULL DEFAULT 'source',
                sort_order INTEGER NOT NULL DEFAULT 0,
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

    def _migrate_to_v2(self, conn: sqlite3.Connection) -> None:
        if "folders" in self._tables(conn):
            columns = self._columns(conn, "folders")
            if "description" not in columns:
                conn.execute("ALTER TABLE folders ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            if "sort_order" not in columns:
                conn.execute("ALTER TABLE folders ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        if "metadata_fields" in self._tables(conn):
            conn.execute(
                """
                UPDATE metadata_fields
                SET type = 'string'
                WHERE type NOT IN ('string', 'number', 'boolean')
                """
            )

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
            fields = []
            for name, value in metadata.items():
                field_type = self._infer_metadata_type(value)
                if self._valid_field_name(name) and field_type is not None:
                    fields.append(MetadataField(name=name, field_type=field_type))
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
                    SELECT
                        fo.folder_id,
                        fo.parent_id,
                        fo.name,
                        fo.path,
                        fo.description,
                        fo.kind,
                        fo.source,
                        fo.sort_order,
                        fo.created_at,
                        fo.updated_at,
                        (
                            SELECT COUNT(*)
                            FROM file_folders child_ff
                            JOIN files child_file
                              ON child_file.file_ref = child_ff.file_ref
                             AND child_file.deleted_at IS NULL
                            WHERE child_ff.folder_id = fo.folder_id
                              AND child_ff.membership_kind = 'primary'
                        ) AS file_count,
                        (
                            SELECT COUNT(*)
                            FROM folders child_folder
                            WHERE child_folder.parent_id = fo.folder_id
                        ) AS children_count
                    FROM folders fo
                    WHERE fo.path != ? AND (fo.path LIKE ?)
                    ORDER BY fo.path
                    LIMIT ?
                    """,
                    (path, self._descendant_like(path), limit),
                ).fetchall()
                file_rows = self._file_rows_for_scope(conn, path, True, limit)
            else:
                folder_rows = conn.execute(
                    """
                    SELECT
                        fo.folder_id,
                        fo.parent_id,
                        fo.name,
                        fo.path,
                        fo.description,
                        fo.kind,
                        fo.source,
                        fo.sort_order,
                        fo.created_at,
                        fo.updated_at,
                        (
                            SELECT COUNT(*)
                            FROM file_folders child_ff
                            JOIN files child_file
                              ON child_file.file_ref = child_ff.file_ref
                             AND child_file.deleted_at IS NULL
                            WHERE child_ff.folder_id = fo.folder_id
                              AND child_ff.membership_kind = 'primary'
                        ) AS file_count,
                        (
                            SELECT COUNT(*)
                            FROM folders child_folder
                            WHERE child_folder.parent_id = fo.folder_id
                        ) AS children_count
                    FROM folders fo
                    WHERE fo.parent_id = ?
                    ORDER BY fo.sort_order, fo.kind, fo.name
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
            "f.descriptor",
            "f.pageindex_tree_status",
            "f.metadata_json",
            "f.created_at",
            "pf.folder_id",
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
            order_by = "f.created_at DESC, f.title"
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
        clause, params = self._compile_metadata_filter(metadata_filter)
        return [clause] if clause else [], params

    def _compile_metadata_filter(self, metadata_filter: dict[str, Any]) -> tuple[str, list[Any]]:
        clauses = []
        params: list[Any] = []
        for key, condition in metadata_filter.items():
            if key in {"$and", "$or"}:
                child_clauses = []
                child_params: list[Any] = []
                for item in condition:
                    child_clause, item_params = self._compile_metadata_filter(item)
                    if child_clause:
                        child_clauses.append(f"({child_clause})")
                        child_params.extend(item_params)
                if child_clauses:
                    joiner = " AND " if key == "$and" else " OR "
                    clauses.append(joiner.join(child_clauses))
                    params.extend(child_params)
                continue
            field_clause, field_params = self._compile_metadata_field_filter(key, condition)
            clauses.append(field_clause)
            params.extend(field_params)
        return " AND ".join(f"({clause})" for clause in clauses), params

    def _compile_metadata_field_filter(self, field: str, condition: Any) -> tuple[str, list[Any]]:
        if not isinstance(condition, dict) or not any(str(key).startswith("$") for key in condition):
            condition = {"$eq": condition}
        operator, expected = next(iter(condition.items()))
        field_id = self.field_id(field)
        if operator == "$eq":
            return (
                """
                EXISTS (
                    SELECT 1 FROM metadata_values mv
                    WHERE mv.file_ref = f.file_ref
                      AND mv.field_id = ?
                      AND mv.value_text = ?
                )
                """,
                [field_id, self._metadata_compare_text(expected)],
            )
        if operator == "$ne":
            return (
                """
                NOT EXISTS (
                    SELECT 1 FROM metadata_values mv
                    WHERE mv.file_ref = f.file_ref
                      AND mv.field_id = ?
                      AND mv.value_text = ?
                )
                """,
                [field_id, self._metadata_compare_text(expected)],
            )
        if operator == "$in":
            values = [self._metadata_compare_text(item) for item in expected]
            if not values:
                return "0", []
            placeholders = ", ".join("?" for _ in values)
            return (
                f"""
                EXISTS (
                    SELECT 1 FROM metadata_values mv
                    WHERE mv.file_ref = f.file_ref
                      AND mv.field_id = ?
                      AND mv.value_text IN ({placeholders})
                )
                """,
                [field_id, *values],
            )
        if operator in {"$gt", "$gte", "$lt", "$lte"}:
            comparator = {
                "$gt": ">",
                "$gte": ">=",
                "$lt": "<",
                "$lte": "<=",
            }[operator]
            if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                return (
                    f"""
                    EXISTS (
                        SELECT 1 FROM metadata_values mv
                        WHERE mv.file_ref = f.file_ref
                          AND mv.field_id = ?
                          AND mv.value_number IS NOT NULL
                          AND mv.value_number {comparator} ?
                    )
                    """,
                    [field_id, float(expected)],
                )
            return (
                f"""
                EXISTS (
                    SELECT 1 FROM metadata_values mv
                    WHERE mv.file_ref = f.file_ref
                      AND mv.field_id = ?
                      AND mv.value_text {comparator} ?
                )
                """,
                [field_id, self._metadata_compare_text(expected)],
            )
        raise ValueError(f"Unsupported metadata operator: {operator}")

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
                    INSERT OR IGNORE INTO folders(
                        folder_id, parent_id, name, path, description, kind, source, sort_order
                    )
                    VALUES (?, NULL, '/', '/', '', ?, ?, 0)
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
                INSERT OR IGNORE INTO folders(
                    folder_id, parent_id, name, path, description, kind, source, sort_order
                )
                VALUES (?, ?, ?, ?, '', ?, ?, 0)
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
            SELECT
                f.file_ref,
                f.external_id,
                f.title,
                f.descriptor,
                f.source_path,
                f.pageindex_tree_status,
                f.metadata_json,
                f.created_at,
                pf.folder_id,
                pf.path AS folder_path
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
        sql += " ORDER BY f.created_at DESC, f.title LIMIT ?"
        params.append(limit)
        return conn.execute(sql, params).fetchall()

    def _scope_sql(self, scope: Optional[dict[str, Any]]) -> tuple[str, list[Any]]:
        if not scope:
            return "", []
        recursive = scope.get("recursive", True)
        folder_id = scope.get("folder_id")
        if folder_id:
            if folder_id == "root":
                folder_path = "/"
            elif recursive:
                return (
                    """
                    (
                        pf.folder_id = ?
                        OR pf.path LIKE (
                            SELECT CASE
                                WHEN base.path = '/' THEN '/%'
                                ELSE base.path || '/%'
                            END
                            FROM folders base
                            WHERE base.folder_id = ?
                        )
                    )
                    """,
                    [folder_id, folder_id],
                )
            else:
                return "pf.folder_id = ?", [folder_id]
        elif scope.get("folder_path") or scope.get("path"):
            folder_path = normalize_path(scope.get("folder_path") or scope.get("path"))
        else:
            return "", []
        if recursive:
            return "(pf.path = ? OR pf.path LIKE ?)", [folder_path, self._descendant_like(folder_path)]
        return "pf.path = ?", [folder_path]

    def _folder_by_path(self, conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT
                folder_id,
                parent_id,
                name,
                path,
                description,
                kind,
                source,
                sort_order,
                created_at,
                updated_at
            FROM folders
            WHERE path = ?
            """,
            (path,),
        ).fetchone()

    @staticmethod
    def _descendant_like(path: str) -> str:
        return "/%" if path == "/" else f"{path}/%"

    @classmethod
    def _folder_row_to_dict(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "folder_id": row["folder_id"],
            "id": row["folder_id"],
            "parent_id": row["parent_id"],
            "parent_folder_id": row["parent_id"],
            "name": row["name"],
            "description": cls._row_value(row, "description", ""),
            "path": row["path"],
            "kind": row["kind"],
            "source": row["source"],
            "sort_order": cls._row_value(row, "sort_order", 0),
            "created_at": cls._row_value(row, "created_at"),
            "updated_at": cls._row_value(row, "updated_at"),
            "file_count": cls._row_value(row, "file_count", 0),
            "children_count": cls._row_value(row, "children_count", 0),
        }

    @classmethod
    def _file_summary(cls, row: sqlite3.Row) -> dict[str, Any]:
        external_id = row["external_id"]
        return {
            "file_ref": row["file_ref"],
            "id": external_id or row["file_ref"],
            "document_id": external_id,
            "external_id": external_id,
            "name": row["title"],
            "title": row["title"],
            "description": cls._row_value(row, "descriptor", row["title"]),
            "status": cls._row_value(row, "pageindex_tree_status", "not_built"),
            "pageNum": None,
            "createdAt": cls._row_value(row, "created_at"),
            "folderId": cls._row_value(row, "folder_id"),
            "source_path": row["source_path"],
            "folder_path": row["folder_path"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    @classmethod
    def _search_row_to_dict(cls, row: sqlite3.Row) -> dict[str, Any]:
        external_id = row["external_id"]
        return {
            "file_ref": row["file_ref"],
            "id": external_id or row["file_ref"],
            "document_id": external_id,
            "external_id": external_id,
            "name": row["title"],
            "title": row["title"],
            "description": cls._row_value(row, "descriptor", row["title"]),
            "status": cls._row_value(row, "pageindex_tree_status", "not_built"),
            "pageNum": None,
            "createdAt": cls._row_value(row, "created_at"),
            "folderId": cls._row_value(row, "folder_id"),
            "source_path": row["source_path"],
            "snippet": row["snippet"] or row["title"],
            "folder_path": row["folder_path"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    @staticmethod
    def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
        return row[key] if key in row.keys() else default

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
            "id": entry.external_id or entry.file_ref,
            "document_id": entry.external_id,
            "external_id": entry.external_id,
            "name": entry.title,
            "storage_uri": entry.storage_uri,
            "source_path": entry.source_path,
            "title": entry.title,
            "description": entry.descriptor,
            "status": entry.pageindex_tree_status,
            "pageNum": None,
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
        if value is None:
            return []
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
    def _infer_metadata_type(value: Any) -> str | None:
        if isinstance(value, list):
            for item in value:
                inferred = SQLiteFileSystemStore._infer_metadata_type(item)
                if inferred is not None:
                    return inferred
            return None
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return "number"
        if value is None:
            return None
        return "string"

    @staticmethod
    def _valid_field_name(name: str) -> bool:
        return re.match(r"^[A-Za-z][A-Za-z0-9_]*$", str(name)) is not None

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
    if str(path).strip().lower() == "root":
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
