from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Union

from .metadata import MetadataQueryEngine
from .store import (
    SQLiteFileSystemStore,
    fingerprint,
    make_file_ref,
    metadata_text,
    normalize_path,
)
from .types import OpenResult, SearchResult


SEMANTIC_FOLDER_ROOT = "/semantic"
SEMANTIC_FOLDER_BASE_FIELDS = {"doc_type", "domain", "topic"}
SEMANTIC_FOLDER_SYSTEM_FIELDS = {"source_type"}
SEMANTIC_FOLDER_FORBIDDEN_FIELDS = {
    "summary",
    "entities",
    "relations",
    "constraints",
    "retrieval_cues",
    "dataset_doc_uuid",
    "path",
    "uri",
    "source_path",
    "storage_uri",
    "title",
    "content_type",
    "created_at",
    "updated_at",
}


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
                }
            ]
        )[0]

    def register_files(self, files: list[dict[str, Any]]) -> list[str]:
        records = [self._prepare_file_record(file) for file in files]
        self.store.insert_files(records)
        return [record["file_ref"] for record in records]

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
        allowed_extension_fields = {
            str(field)
            for field in policy.get("allowed_extension_fields", [])
            if str(field)
        }
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
        if self.semantic_retrieval_backend is None or not self._query_text(query):
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
            "metadata_text": metadata_text(metadata),
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
        explicit_field = str(item.get("field") or "")
        if explicit_field:
            fields.append(explicit_field)
        fields.extend(cls._semantic_folder_projection_fields_from_path(str(path)))
        for payload_key in ("metadata", "folder_metadata"):
            cls._validate_semantic_folder_projection_metadata_payload(
                item.get(payload_key),
                allowed_fields,
            )
        for field in fields:
            if field in SEMANTIC_FOLDER_FORBIDDEN_FIELDS or field not in allowed_fields:
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
            field = segment.split("=", 1)[0].strip()
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
                if key_text in SEMANTIC_FOLDER_FORBIDDEN_FIELDS:
                    raise ValueError(
                        "Forbidden metadata field in Semantic Folder Projection payload: "
                        f"{key_text}"
                    )
                if key_text in {"field", "source_field", "metadata_field"}:
                    field = str(value or "")
                    if field and (
                        field in SEMANTIC_FOLDER_FORBIDDEN_FIELDS
                        or field not in allowed_fields
                    ):
                        raise ValueError(
                            f"Field is not allowed for Semantic Folder Projection: {field}"
                        )
                cls._validate_semantic_folder_projection_metadata_payload(value, allowed_fields)
        elif isinstance(payload, list):
            for item in payload:
                cls._validate_semantic_folder_projection_metadata_payload(item, allowed_fields)

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
