from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Union

from .metadata import MetadataQueryEngine
from .semantic_folder_policy import (
    SEMANTIC_FOLDER_BASE_FIELDS,
    SEMANTIC_FOLDER_ROOT,
    SEMANTIC_FOLDER_SYSTEM_FIELDS,
    canonical_semantic_folder_field_name,
    is_semantic_folder_forbidden_field,
    semantic_folder_allowed_extension_fields,
)
from .store import (
    SQLiteFileSystemStore,
    fingerprint,
    make_file_ref,
    metadata_text,
    normalize_path,
)
from .types import OpenResult, SearchResult

DEFAULT_METADATA_GENERATION_FIELDS = {
    "summary": True,
    "doc_type": True,
    "domain": True,
    "topic": True,
    "entity": False,
    "relation": False,
}

DEFAULT_DERIVED_METADATA_FIELD_TYPES = {
    "summary": "string",
    "doc_type": "string",
    "domain": "string",
    "topic": "string",
    "entity": "string",
    "relation": "string",
}

METADATA_GENERATION_STATUSES = {
    "pending_submit",
    "pending_generate",
    "generated",
    "failed",
}

PROJECTION_INDEX_STATUSES = {
    "not_indexed",
    "pending_index",
    "generated",
    "ready",
    "failed",
}

SEMANTIC_RETRIEVAL_CHANNELS = ("summary", "entity", "relation")
SEMANTIC_GREP_CHANNELS = ("entity", "relation")


class PageIndexFileSystem:
    def __init__(self, workspace: Union[str, Path], *, semantic_retrieval_backend: Any | None = None):
        self.workspace = Path(workspace).expanduser()
        self.store = SQLiteFileSystemStore(self.workspace)
        self.metadata = MetadataQueryEngine(self.store)
        self._references: dict[str, str] = {}
        self.semantic_retrieval_backend = semantic_retrieval_backend

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
        derived_metadata: Optional[dict[str, Any]] = None,
        metadata_generation_policy: Optional[dict[str, Any]] = None,
        metadata_generation_status: Optional[str] = None,
    ) -> str:
        return self.register_files(
            [
                {
                    "storage_uri": storage_uri,
                    "source_path": source_path,
                    "folder_path": folder_path,
                    "metadata": metadata,
                    "external_id": external_id,
                    "title": title,
                    "content": content,
                    "content_type": content_type,
                    "source_type": source_type,
                    "derived_metadata": derived_metadata,
                    "metadata_generation_policy": metadata_generation_policy,
                    "metadata_generation_status": metadata_generation_status,
                }
            ]
        )[0]

    def register_files(self, files: list[dict[str, Any]]) -> list[str]:
        records = [self._prepare_file_record(file) for file in files]
        self._register_generation_policy_schema(records)
        self.store.insert_files(records)
        return [record["file_ref"] for record in records]

    @classmethod
    def default_metadata_generation_policy(cls) -> dict[str, Any]:
        return {
            "fields": dict(DEFAULT_METADATA_GENERATION_FIELDS),
            "projection_indexes": {"summary": True},
        }

    def browse(
        self,
        path: str = "/",
        recursive: bool = False,
        limit: int = 100,
    ) -> dict[str, list[dict[str, Any]]]:
        return self.store.list_folder(path, recursive=recursive, limit=limit)

    def find_folders(
        self,
        path: str = "/",
        metadata_filter: Optional[dict[str, Any] | str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        parsed_filter = self.metadata.parse_filter(metadata_filter)
        return self.store.find_folders(path, metadata_filter=parsed_filter, limit=limit)

    def create_folder(
        self,
        path: str,
        kind: str = "manual",
        description: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        return self.store.create_folder(
            path,
            kind=kind,
            description=description,
            metadata=metadata,
        )

    def attach_file_to_folder(
        self,
        file_ref: str,
        folder_path_or_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.store.attach_file_to_folder(file_ref, folder_path_or_id, metadata=metadata)

    def attach_files_to_folders(self, items: list[dict[str, Any]]) -> None:
        self.store.attach_files_to_folders(items)

    def apply_semantic_folder_projection(
        self,
        projection_plan: dict[str, Any],
        *,
        file_ref_by_document_id: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Attach registered files to a Semantic Folder Projection.

        Registration remains the explicit folder placement step. This method is
        the separate product API for adding derived `/semantic/...` memberships.
        """
        folders = list(projection_plan.get("folders") or [])
        memberships = list(projection_plan.get("memberships") or [])
        policy_raw = projection_plan.get("policy")
        policy = policy_raw if isinstance(policy_raw, dict) else {}
        allowed_extension_fields = semantic_folder_allowed_extension_fields(
            policy.get("allowed_extension_fields", [])
        )
        for folder in folders:
            self._validate_semantic_folder_projection_item(folder, allowed_extension_fields)
        for membership in memberships:
            self._validate_semantic_folder_projection_item(membership, allowed_extension_fields)

        for folder in folders:
            folder_metadata = folder.get("metadata")
            self.create_folder(
                self._validate_semantic_folder_projection_path(str(folder["path"])),
                kind=str(folder.get("kind") or "semantic_projection"),
                description=str(folder.get("description") or ""),
                metadata=folder_metadata if isinstance(folder_metadata, dict) else {},
            )

        items: list[dict[str, Any]] = []
        file_ref_by_document_id = file_ref_by_document_id or {}
        for membership in memberships:
            document_id = self._semantic_folder_projection_document_id(membership)
            file_ref = file_ref_by_document_id.get(document_id)
            if not file_ref:
                file_ref = self.store.resolve_file_ref(document_id)
            metadata = (
                dict(membership.get("folder_metadata"))
                if isinstance(membership.get("folder_metadata"), dict)
                else {}
            )
            metadata.update(
                {
                    "projection": "Semantic Folder Projection",
                    "field": membership.get("field", ""),
                    "value": membership.get("value", ""),
                    "mount_kind": membership.get(
                        "mount_kind",
                        "semantic_folder_projection",
                    ),
                }
            )
            items.append(
                {
                    "file_ref": file_ref,
                    "folder": self._validate_semantic_folder_projection_path(
                        str(membership["folder_path"])
                    ),
                    "metadata": metadata,
                }
            )
        self.attach_files_to_folders(items)
        return {
            "projection": "Semantic Folder Projection",
            "folders_applied": len(folders),
            "memberships_attached": len(items),
        }

    def search(
        self,
        query: Union[str, list[str], None] = None,
        scope: Optional[dict[str, Any]] = None,
        metadata_filter: Optional[dict[str, Any] | str] = None,
        limit: int = 10,
        semantic: bool = True,
    ) -> list[SearchResult]:
        parsed_filter = self.metadata.parse_filter(metadata_filter)
        if semantic and self._should_use_semantic_retrieval(query, scope):
            semantic_results = self._semantic_search(
                query,
                scope=scope,
                metadata_filter=parsed_filter,
                limit=limit,
            )
            if semantic_results:
                return semantic_results
        rows = self.store.search_files(
            query,
            scope=scope,
            metadata_filter=parsed_filter,
            limit=limit,
        )
        results = []
        scope_path = self._scope_folder_path(scope)
        for row in rows:
            reference_id = self._reference_for(row["file_ref"])
            folder_paths = [
                folder["path"]
                for folder in self.store.folder_memberships(row["file_ref"])
            ]
            folder_path = self._preferred_folder_path(folder_paths, scope_path, row["folder_path"])
            results.append(
                SearchResult(
                    reference_id=reference_id,
                    file_ref=row["file_ref"],
                    external_id=row["external_id"],
                    title=row["title"],
                    snippet=row["snippet"],
                    folder_path=folder_path,
                    folder_paths=folder_paths,
                    metadata=row["metadata"],
                    derived_metadata=row["derived_metadata"],
                    metadata_generation=row["metadata_generation"],
                    source_path=row["source_path"],
                    id=row["id"],
                    document_id=row["document_id"],
                    name=row["name"],
                    description=row["description"],
                    status=row["status"],
                    pageNum=row["pageNum"],
                    createdAt=row["createdAt"],
                    folderId=row["folderId"],
                )
            )
        return results

    def search_semantic_channel(
        self,
        channel: str,
        query: Union[str, list[str], None],
        *,
        scope: Optional[dict[str, Any]] = None,
        metadata_filter: Optional[dict[str, Any] | str] = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        parsed_filter = self.metadata.parse_filter(metadata_filter)
        if (
            self.semantic_retrieval_backend is None
            or not self.has_semantic_channel(channel)
            or not self._query_text(query)
        ):
            return []
        return self._semantic_search(
            query,
            scope=scope,
            metadata_filter=parsed_filter,
            limit=limit,
            channel=channel,
        )

    def configure_hybrid_projection_retrieval(
        self,
        index_dir: Union[str, Path],
        *,
        embedding_provider: str = "openai",
        embedding_model: str = "text-embedding-3-small",
        embedding_dimensions: int = 256,
        embedding_timeout: float = 60,
        per_channel_limit: int = 100,
        fetch_multiplier: int = 100,
    ) -> Any:
        from .hybrid_projection import HybridProjectionSearchBackend

        self.semantic_retrieval_backend = HybridProjectionSearchBackend.from_provider(
            index_dir,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            embedding_timeout=embedding_timeout,
            per_channel_limit=per_channel_limit,
            fetch_multiplier=fetch_multiplier,
        )
        return self.semantic_retrieval_backend

    @property
    def has_semantic_retrieval_backend(self) -> bool:
        return self.semantic_retrieval_backend is not None

    def semantic_retrieval_channels(self) -> tuple[str, ...]:
        backend = self.semantic_retrieval_backend
        if backend is None:
            return ()
        available_channels = getattr(backend, "available_channels", None)
        if callable(available_channels):
            raw_channels = available_channels()
        else:
            raw_channels = getattr(backend, "semantic_tool_channels", ())
        available = set(raw_channels or ())
        return tuple(channel for channel in SEMANTIC_RETRIEVAL_CHANNELS if channel in available)

    def has_semantic_channel(self, channel: str) -> bool:
        return channel in self.semantic_retrieval_channels()

    def retrieval_capabilities(self) -> dict[str, Any]:
        semantic_channels = self.semantic_retrieval_channels()
        semantic_commands = [f"search-{channel}" for channel in semantic_channels]
        semantic_grep_channels = [
            channel for channel in SEMANTIC_GREP_CHANNELS if channel in semantic_channels
        ]
        if semantic_grep_channels:
            semantic_commands.append("semantic-grep")
        return {
            "lexical": {
                "grep_recursive": True,
                "grep_recursive_semantic_prefilter": False,
                "grep_recursive_guard": "bounded broad-folder notice",
            },
            "semantic": {
                "backend_configured": self.semantic_retrieval_backend is not None,
                "channels": list(semantic_channels),
                "commands": semantic_commands,
                "semantic_grep_channels": semantic_grep_channels,
            },
        }

    def find(
        self,
        reference_id: str,
        patterns: Union[str, list[str]],
        limit: int = 20,
    ) -> list[OpenResult]:
        file_ref = self._resolve_reference(reference_id)
        patterns = [patterns] if isinstance(patterns, str) else list(patterns)
        lowered_patterns = [pattern.lower() for pattern in patterns if pattern]
        if not lowered_patterns:
            return []
        text = self.store.read_text(file_ref)
        lines = text.splitlines()
        matches = []
        for i, line in enumerate(lines, 1):
            haystack = line.lower()
            if any(pattern in haystack for pattern in lowered_patterns):
                start = max(1, i - 1)
                end = min(len(lines), i + 1)
                matches.append(self._open_lines(reference_id, file_ref, start, end))
                if len(matches) >= limit:
                    break
        return matches

    def open(self, reference_id: str, location: str = "all") -> OpenResult:
        file_ref = self._resolve_reference(reference_id)
        if str(location).strip().lower() in {"all", "full", "*"}:
            return self._open_all(reference_id, file_ref)
        start, end = self._parse_line_range(location)
        return self._open_lines(reference_id, file_ref, start, end)

    def _stat(self, target: str) -> dict[str, Any]:
        file_ref = self._resolve_reference(target)
        return self.store.file_info(file_ref)

    def _metadata_schema(self) -> dict[str, Any]:
        return self.metadata.export_schema()

    def _register_metadata_schema(self, schema: dict[str, Any]) -> None:
        self.metadata.register_schema(schema)

    def _create_folder(self, path: str) -> str:
        return self.create_folder(path)

    def _prepare_file_record(self, file: dict[str, Any]) -> dict[str, Any]:
        storage_uri = file["storage_uri"]
        source_path = str(file["source_path"]).strip("/")
        metadata = file.get("metadata") or {}
        derived_metadata = file.get("derived_metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a JSON object")
        if not isinstance(derived_metadata, dict):
            raise ValueError("derived_metadata must be a JSON object")
        generation_policy = self._normalize_metadata_generation_policy(
            file.get("metadata_generation_policy"),
            derived_metadata=derived_metadata,
        )
        generation_state = self._metadata_generation_state(
            generation_policy,
            derived_metadata=derived_metadata,
            status=file.get("metadata_generation_status"),
        )
        indexed_metadata = SQLiteFileSystemStore.indexed_metadata_values(
            metadata,
            derived_metadata,
            generation_state,
        )
        searchable_metadata = self._merge_metadata_values(metadata, derived_metadata)
        external_id = file.get("external_id")
        content = file.get("content") or ""
        content_type = file.get("content_type") or "text/plain"
        fts_content = file.get("fts_content", content)
        source_type = file.get("source_type") or self._infer_source_type(source_path)
        folder_path = normalize_path(file.get("folder_path") or "/")
        title = file.get("title") or metadata.get("title") or Path(source_path).stem
        file_ref = make_file_ref(external_id or source_path)
        text_artifact_path = file.get("text_artifact_path") or self.store.write_text_artifact(
            file_ref,
            content,
        )
        raw_artifact_path = file.get("raw_artifact_path")
        if raw_artifact_path is None and file.get("write_raw_artifact", True):
            raw_artifact_path = self.store.write_raw_artifact(
                file_ref,
                {
                    "storage_uri": storage_uri,
                    "source_path": source_path,
                    "folder_path": folder_path,
                    "metadata": metadata,
                    "derived_metadata": derived_metadata,
                    "metadata_generation": generation_state,
                },
            )
        descriptor = self._build_descriptor(title, metadata)
        return {
            "file_ref": file_ref,
            "external_id": external_id,
            "storage_uri": storage_uri,
            "source_path": source_path,
            "title": title,
            "descriptor": descriptor,
            "content_type": content_type,
            "source_type": source_type,
            "fingerprint": fingerprint(content),
            "text_artifact_path": str(text_artifact_path),
            "raw_artifact_path": str(raw_artifact_path) if raw_artifact_path is not None else None,
            "pageindex_doc_id": None,
            "pageindex_tree_status": "not_built",
            "metadata": metadata,
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "derived_metadata": derived_metadata,
            "derived_metadata_json": json.dumps(derived_metadata, ensure_ascii=False),
            "metadata_generation": generation_state,
            "metadata_generation_json": json.dumps(generation_state, ensure_ascii=False),
            "indexed_metadata": indexed_metadata,
            "metadata_text": metadata_text(searchable_metadata),
            "folder_path": folder_path,
            "content": fts_content,
            "skip_fts": bool(file.get("skip_fts", False)),
        }

    def _open_lines(self, reference_id: str, file_ref: str, start: int, end: int) -> OpenResult:
        entry = self.store.get_file(file_ref)
        lines = self.store.read_text(file_ref).splitlines()
        start = max(1, start)
        end = min(max(start, end), len(lines))
        text = "\n".join(lines[start - 1:end])
        return OpenResult(
            reference_id=reference_id,
            file_ref=file_ref,
            start_line=start,
            end_line=end,
            text=text,
            external_id=entry.external_id,
            folder_path=entry.folder_path,
            source_path=entry.source_path,
        )

    def _open_all(self, reference_id: str, file_ref: str) -> OpenResult:
        entry = self.store.get_file(file_ref)
        text = self.store.read_text(file_ref)
        line_count = len(text.splitlines())
        return OpenResult(
            reference_id=reference_id,
            file_ref=file_ref,
            start_line=1,
            end_line=line_count,
            text=text,
            external_id=entry.external_id,
            folder_path=entry.folder_path,
            source_path=entry.source_path,
        )

    def _resolve_reference(self, reference_id: str) -> str:
        if reference_id in self._references:
            return self._references[reference_id]
        return self.store.resolve_file_ref(reference_id)

    def _should_use_semantic_retrieval(
        self,
        query: Union[str, list[str], None],
        scope: Optional[dict[str, Any]],
    ) -> bool:
        if self.semantic_retrieval_backend is None:
            return False
        if not self._query_text(query):
            return False
        if not scope:
            return True
        return bool(scope.get("recursive", True))

    def _semantic_search(
        self,
        query: Union[str, list[str], None],
        *,
        scope: Optional[dict[str, Any]],
        metadata_filter: Optional[dict[str, Any]],
        limit: int,
        channel: str | None = None,
    ) -> list[SearchResult]:
        if self.semantic_retrieval_backend is None:
            return []
        filters = self._semantic_filters_for_scope(scope)
        fetch_limit = max(limit * 10, 50)
        query_text = self._query_text(query)
        if channel:
            search_channel = getattr(self.semantic_retrieval_backend, "search_channel", None)
            if search_channel is None:
                return []
            candidates = search_channel(
                channel,
                query_text,
                limit=fetch_limit,
                filters=filters,
            )
        else:
            candidates = self.semantic_retrieval_backend.search(
                query_text,
                limit=fetch_limit,
                filters=filters,
            )
        results: list[SearchResult] = []
        seen: set[str] = set()
        scope_path = self._scope_folder_path(scope)
        for candidate in candidates:
            try:
                file_ref = self.store.resolve_file_ref(candidate.document_id)
            except KeyError:
                continue
            if file_ref in seen:
                continue
            if not self.store.file_matches(file_ref, scope=scope, metadata_filter=metadata_filter):
                continue
            seen.add(file_ref)
            entry = self.store.get_file(file_ref)
            reference_id = self._reference_for(file_ref)
            folder_paths = [
                folder["path"]
                for folder in self.store.folder_memberships(file_ref)
            ]
            folder_path = self._preferred_folder_path(folder_paths, scope_path, entry.folder_path)
            results.append(
                SearchResult(
                    reference_id=reference_id,
                    file_ref=file_ref,
                    external_id=entry.external_id,
                    title=entry.title,
                    snippet=candidate.snippet or entry.descriptor,
                    folder_path=folder_path,
                    folder_paths=folder_paths,
                    metadata=entry.metadata,
                    derived_metadata=entry.derived_metadata,
                    metadata_generation=entry.metadata_generation,
                    source_path=entry.source_path,
                    id=entry.external_id or file_ref,
                    document_id=entry.external_id,
                    name=entry.title,
                    description=entry.descriptor,
                    status=entry.pageindex_tree_status,
                    pageNum=None,
                    createdAt=None,
                    folderId=None,
                )
            )
            if len(results) >= limit:
                break
        return results

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

    def _register_generation_policy_schema(self, records: list[dict[str, Any]]) -> None:
        fields: dict[str, dict[str, str]] = {}
        for record in records:
            policy_fields = record["metadata_generation"]["policy"]["fields"]
            for name, requested in policy_fields.items():
                if requested:
                    fields[name] = {
                        "type": DEFAULT_DERIVED_METADATA_FIELD_TYPES.get(
                            name,
                            self._infer_metadata_field_type(
                                record.get("derived_metadata", {}).get(name)
                            ),
                        )
                    }
            for name, value in record.get("derived_metadata", {}).items():
                fields.setdefault(name, {"type": self._infer_metadata_field_type(value)})
        if fields:
            self.metadata.register_schema({"fields": fields}, source="derived")

    @classmethod
    def _normalize_metadata_generation_policy(
        cls,
        policy: Optional[dict[str, Any]],
        *,
        derived_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        fields = dict(DEFAULT_METADATA_GENERATION_FIELDS)
        field_statuses: dict[str, str] = {}
        projection_indexes: dict[str, bool] | None = None
        projection_index_statuses: dict[str, str] = {}
        mode = None
        top_level_status = None
        if policy is not None:
            if not isinstance(policy, dict):
                raise ValueError("metadata_generation_policy must be a JSON object")
            raw_fields = policy.get("fields")
            if raw_fields is None:
                raw_fields = {
                    name: declaration
                    for name, declaration in policy.items()
                    if name not in {"mode", "status", "projection_indexes"}
                }
            if not isinstance(raw_fields, dict):
                raise ValueError("metadata_generation_policy fields must be a JSON object")
            for name, declaration in raw_fields.items():
                name = str(name)
                if isinstance(declaration, bool):
                    fields[name] = declaration
                    continue
                if isinstance(declaration, dict):
                    fields[name] = bool(
                        declaration.get("enabled", declaration.get("requested", True))
                    )
                    field_status = declaration.get("status")
                    if field_status is not None:
                        cls._validate_metadata_generation_status(str(field_status))
                        field_statuses[name] = str(field_status)
                    continue
                raise ValueError(f"Invalid metadata generation policy for field: {name}")
            mode = policy.get("mode")
            top_level_status = policy.get("status")
            if top_level_status is not None:
                cls._validate_metadata_generation_status(str(top_level_status))
            if "projection_indexes" in policy:
                projection_indexes, projection_index_statuses = (
                    cls._normalize_projection_index_policy(policy["projection_indexes"])
                )
        for name in derived_metadata:
            fields.setdefault(name, True)
        normalized: dict[str, Any] = {
            "fields": fields,
            "projection_indexes": (
                projection_indexes
                if projection_indexes is not None
                else {"summary": bool(fields.get("summary", False))}
            ),
        }
        if field_statuses:
            normalized["field_statuses"] = field_statuses
        if projection_index_statuses:
            normalized["projection_index_statuses"] = projection_index_statuses
        if mode:
            normalized["mode"] = str(mode)
        if top_level_status:
            normalized["status"] = str(top_level_status)
        return normalized

    @classmethod
    def _metadata_generation_state(
        cls,
        policy: dict[str, Any],
        *,
        derived_metadata: dict[str, Any],
        status: Optional[str],
    ) -> dict[str, Any]:
        explicit_status = status or policy.get("status")
        if explicit_status is not None:
            explicit_status = str(explicit_status)
            cls._validate_metadata_generation_status(explicit_status)
        field_statuses = policy.get("field_statuses", {})
        fields: dict[str, dict[str, Any]] = {}
        for name, requested in policy["fields"].items():
            if not requested:
                fields[name] = {"requested": False}
                continue
            field_status = field_statuses.get(name)
            if field_status is None:
                field_status = explicit_status
            if field_status is None:
                field_status = "generated" if name in derived_metadata else "pending_generate"
            fields[name] = {"requested": True, "status": field_status}

        requested_statuses = [
            item["status"]
            for item in fields.values()
            if item.get("requested") and item.get("status")
        ]
        aggregate_status = explicit_status or cls._aggregate_generation_status(requested_statuses)
        policy_summary = {
            "fields": dict(policy["fields"]),
            "projection_indexes": dict(policy.get("projection_indexes", {})),
        }
        if "mode" in policy:
            policy_summary["mode"] = policy["mode"]
        state = {
            "status": aggregate_status,
            "policy": policy_summary,
            "fields": fields,
            "projection_indexes": {},
        }
        projection_statuses = policy.get("projection_index_statuses", {})
        for name, requested in policy.get("projection_indexes", {}).items():
            if not requested:
                continue
            state["projection_indexes"][name] = {
                "requested": True,
                "status": projection_statuses.get(name, "not_indexed"),
            }
        return state

    @staticmethod
    def _aggregate_generation_status(statuses: list[str]) -> str:
        if not statuses:
            return "generated"
        for status in ("failed", "pending_submit", "pending_generate"):
            if status in statuses:
                return status
        return "generated"

    @staticmethod
    def _validate_metadata_generation_status(status: str) -> None:
        if status not in METADATA_GENERATION_STATUSES:
            raise ValueError(f"Unsupported metadata generation status: {status}")

    @classmethod
    def _normalize_projection_index_policy(
        cls,
        projection_policy: Any,
    ) -> tuple[dict[str, bool], dict[str, str]]:
        if projection_policy is None:
            return {}, {}
        if not isinstance(projection_policy, dict):
            raise ValueError("metadata_generation_policy projection_indexes must be a JSON object")
        projection_indexes: dict[str, bool] = {}
        projection_index_statuses: dict[str, str] = {}
        for name, declaration in projection_policy.items():
            name = str(name)
            if isinstance(declaration, bool):
                projection_indexes[name] = declaration
                continue
            if isinstance(declaration, dict):
                projection_indexes[name] = bool(
                    declaration.get("enabled", declaration.get("requested", True))
                )
                status = declaration.get("status")
                if status is not None:
                    status = str(status)
                    cls._validate_projection_index_status(status)
                    projection_index_statuses[name] = status
                continue
            raise ValueError(f"Invalid projection index policy for index: {name}")
        return projection_indexes, projection_index_statuses

    @staticmethod
    def _validate_projection_index_status(status: str) -> None:
        if status not in PROJECTION_INDEX_STATUSES:
            raise ValueError(f"Unsupported projection index status: {status}")

    @classmethod
    def _merge_metadata_values(
        cls,
        metadata: dict[str, Any],
        derived_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(metadata)
        for name, value in derived_metadata.items():
            if name not in merged:
                merged[name] = value
                continue
            if merged[name] == value:
                continue
            merged[name] = cls._merge_metadata_value(merged[name], value)
        return merged

    @staticmethod
    def _merge_metadata_value(raw_value: Any, derived_value: Any) -> Any:
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        derived_values = derived_value if isinstance(derived_value, list) else [derived_value]
        merged = list(values)
        for item in derived_values:
            if item not in merged:
                merged.append(item)
        return merged

    @staticmethod
    def _infer_metadata_field_type(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        return "string"

    @staticmethod
    def _infer_source_type(source_path: str) -> Optional[str]:
        parts = [part for part in Path(source_path).parts if part not in ("", ".")]
        return parts[0] if parts else None

    @staticmethod
    def _scope_folder_path(scope: Optional[dict[str, Any]]) -> Optional[str]:
        if not scope:
            return None
        path = scope.get("folder_path") or scope.get("path")
        return normalize_path(path) if path else None

    @classmethod
    def _semantic_filters_for_scope(cls, scope: Optional[dict[str, Any]]) -> dict[str, Any]:
        path = cls._scope_folder_path(scope)
        if not path or path == "/":
            return {}
        source_type = cls._source_type_filter_from_path(path)
        return {"source_type": source_type} if source_type else {}

    @staticmethod
    def _source_type_filter_from_path(path: str) -> str:
        segments = [segment for segment in path.strip("/").split("/") if segment]
        if not segments:
            return ""
        if segments[0] == SEMANTIC_FOLDER_ROOT.strip("/"):
            segments = segments[1:]
        if not segments:
            return ""
        first_segment = segments[0]
        if first_segment.startswith("source_type="):
            return first_segment.split("=", 1)[1].replace("-", "_")
        if path.startswith(f"{SEMANTIC_FOLDER_ROOT}/"):
            return ""
        return first_segment

    @classmethod
    def _validate_semantic_folder_projection_item(
        cls,
        item: dict[str, Any],
        allowed_extension_fields: set[str],
    ) -> None:
        path = item.get("folder_path") or item.get("path")
        if not path:
            raise ValueError("Semantic Folder Projection items must include a folder path")
        cls._validate_semantic_folder_projection_path(str(path))
        allowed_fields = (
            SEMANTIC_FOLDER_BASE_FIELDS
            | SEMANTIC_FOLDER_SYSTEM_FIELDS
            | allowed_extension_fields
        )
        if item.get("dataset_doc_uuid"):
            raise ValueError(
                "dataset_doc_uuid is not allowed in Semantic Folder Projection memberships; "
                "use file_key or file_ref"
            )
        fields = []
        explicit_field = cls._canonical_semantic_folder_field_name(item.get("field"))
        if explicit_field:
            fields.append(explicit_field)
        fields.extend(cls._semantic_folder_projection_fields_from_path(str(path)))
        for payload_key in ("metadata", "folder_metadata"):
            cls._validate_semantic_folder_projection_metadata_payload(
                item.get(payload_key),
                allowed_fields,
            )
        for field in fields:
            if is_semantic_folder_forbidden_field(field) or field not in allowed_fields:
                raise ValueError(f"Field is not allowed for Semantic Folder Projection: {field}")

    @staticmethod
    def _validate_semantic_folder_projection_path(path: str) -> str:
        normalized = normalize_path(path)
        if normalized != SEMANTIC_FOLDER_ROOT and not normalized.startswith(
            f"{SEMANTIC_FOLDER_ROOT}/"
        ):
            raise ValueError("Semantic Folder Projection paths must be under /semantic")
        return normalized

    @classmethod
    def _semantic_folder_projection_fields_from_path(cls, path: str) -> list[str]:
        normalized = cls._validate_semantic_folder_projection_path(path)
        fields: list[str] = []
        for segment in normalized.strip("/").split("/")[1:]:
            if "=" not in segment:
                continue
            field = cls._canonical_semantic_folder_field_name(
                segment.split("=", 1)[0]
            )
            if field:
                fields.append(field)
        return fields

    @classmethod
    def _validate_semantic_folder_projection_metadata_payload(
        cls,
        payload: Any,
        allowed_fields: set[str],
    ) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                key_text = str(key)
                key_field = cls._canonical_semantic_folder_field_name(key)
                if is_semantic_folder_forbidden_field(key_field):
                    raise ValueError(
                        "Forbidden metadata field in Semantic Folder Projection payload: "
                        f"{key_text}"
                    )
                if key_field in {"field", "source_field", "metadata_field"}:
                    field = cls._canonical_semantic_folder_field_name(value)
                    if field and (
                        is_semantic_folder_forbidden_field(field)
                        or field not in allowed_fields
                    ):
                        raise ValueError(
                            f"Field is not allowed for Semantic Folder Projection: {field}"
                        )
                cls._validate_semantic_folder_projection_metadata_payload(value, allowed_fields)
        elif isinstance(payload, list):
            for item in payload:
                cls._validate_semantic_folder_projection_metadata_payload(item, allowed_fields)
        elif isinstance(payload, str):
            field = cls._canonical_semantic_folder_field_name(payload)
            if is_semantic_folder_forbidden_field(field):
                raise ValueError(
                    "Forbidden metadata field label in Semantic Folder Projection payload: "
                    f"{payload}"
                )

    @staticmethod
    def _canonical_semantic_folder_field_name(value: Any) -> str:
        return canonical_semantic_folder_field_name(value)

    @staticmethod
    def _semantic_folder_projection_document_id(membership: dict[str, Any]) -> str:
        for key in ("file_key", "file_ref", "document_ref"):
            value = str(membership.get(key) or "").strip()
            if value:
                return value
        raise ValueError("Semantic Folder Projection membership is missing file_key or file_ref")

    @staticmethod
    def _query_text(query: Union[str, list[str], None]) -> str:
        if query is None:
            return ""
        if isinstance(query, list):
            return " ".join(str(item) for item in query)
        return str(query)

    @staticmethod
    def _preferred_folder_path(
        folder_paths: list[str],
        scope_path: Optional[str],
        fallback: str,
    ) -> str:
        if scope_path:
            scoped = [
                path
                for path in folder_paths
                if path == scope_path or path.startswith(f"{scope_path.rstrip('/')}/")
            ]
            if scoped:
                return sorted(scoped, key=lambda item: (len(item), item))[0]
        non_root = [path for path in folder_paths if path != "/"]
        if non_root:
            return sorted(non_root, key=lambda item: (len(item), item))[0]
        return fallback

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
