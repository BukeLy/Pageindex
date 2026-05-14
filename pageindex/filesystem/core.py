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


class PageIndexFileSystem:
    def __init__(self, workspace: Union[str, Path]):
        self.workspace = Path(workspace).expanduser()
        self.store = SQLiteFileSystemStore(self.workspace)
        self.metadata = MetadataQueryEngine(self.store)
        self._references: dict[str, str] = {}

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
        for record in records:
            self.metadata.ensure_fields(record["metadata"])
            self.store.insert_file(record)
        return [record["file_ref"] for record in records]

    def browse(
        self,
        path: str = "/",
        recursive: bool = False,
        limit: int = 100,
    ) -> dict[str, list[dict[str, Any]]]:
        return self.store.list_folder(path, recursive=recursive, limit=limit)

    def search(
        self,
        query: Union[str, list[str], None] = None,
        scope: Optional[dict[str, Any]] = None,
        metadata_filter: Optional[dict[str, Any] | str] = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        parsed_filter = self.metadata.parse_filter(metadata_filter)
        rows = self.store.search_files(
            query,
            scope=scope,
            metadata_filter=parsed_filter,
            limit=limit,
        )
        results = []
        for row in rows:
            reference_id = self._reference_for(row["file_ref"])
            results.append(
                SearchResult(
                    reference_id=reference_id,
                    file_ref=row["file_ref"],
                    external_id=row["external_id"],
                    title=row["title"],
                    snippet=row["snippet"],
                    folder_path=row["folder_path"],
                    metadata=row["metadata"],
                    source_path=row["source_path"],
                )
            )
        return results

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

    def _create_folder(self, path: str) -> str:
        return self.store.ensure_folder(None, path, kind="physical", source="user")

    def _prepare_file_record(self, file: dict[str, Any]) -> dict[str, Any]:
        storage_uri = file["storage_uri"]
        source_path = str(file["source_path"]).strip("/")
        metadata = file.get("metadata") or {}
        external_id = file.get("external_id")
        content = file.get("content") or ""
        content_type = file.get("content_type") or "text/plain"
        source_type = file.get("source_type") or self._infer_source_type(source_path)
        folder_path = normalize_path(
            file.get("folder_path") or "/" + str(Path(source_path).parent)
        )
        title = file.get("title") or metadata.get("title") or Path(source_path).stem
        file_ref = make_file_ref(external_id or source_path)
        text_artifact_path = self.store.write_text_artifact(file_ref, content)
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
            "raw_artifact_path": str(raw_artifact_path),
            "pageindex_doc_id": None,
            "pageindex_tree_status": "not_built",
            "metadata": metadata,
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "metadata_text": metadata_text(metadata),
            "folder_path": folder_path,
            "content": content,
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
