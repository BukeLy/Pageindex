import json
from pathlib import Path
from typing import Any, Union

from .core import PageIndexFileSystem


class EnterpriseRAGBenchmark:
    def __init__(self, filesystem: PageIndexFileSystem):
        self.filesystem = filesystem

    def ingest_sources(self, source_root: Union[str, Path]) -> list[str]:
        source_root = Path(source_root).expanduser()
        file_refs = []
        for path in sorted(source_root.rglob("*.json")):
            data = self._read_json(path)
            file_refs.append(self._register_document(source_root, path, data))
        return file_refs

    def _register_document(
        self,
        source_root: Path,
        path: Path,
        data: dict[str, Any],
    ) -> str:
        relative_path = path.relative_to(source_root)
        title = self._title(data)
        content = self._text_content(data, title)
        content_fields = set(data.get("content_field_names") or [])
        metadata = {
            key: value
            for key, value in data.items()
            if key not in content_fields and key not in {"content_field_names", "title_field_name"}
        }
        metadata["source_type"] = relative_path.parts[0] if relative_path.parts else None
        folder_path = "/" + "/".join(relative_path.parent.parts)

        return self.filesystem.register_file(
            storage_uri=str(path),
            source_path=str(relative_path),
            folder_path=folder_path,
            metadata=metadata,
            external_id=data.get("dataset_doc_uuid"),
            title=title,
            content=content,
            content_type="application/json",
            source_type=metadata["source_type"],
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"EnterpriseRAG document must be a JSON object: {path}")
        return data

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, list):
            return "\n".join(str(item) for item in value)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return "" if value is None else str(value)

    def _text_content(self, data: dict[str, Any], title: str) -> str:
        contents = [title, ""]
        for field_name in data.get("content_field_names") or []:
            if field_name in data:
                contents.append(self._stringify(data[field_name]))
        return "\n".join(contents)

    @staticmethod
    def _title(data: dict[str, Any]) -> str:
        field_name = data.get("title_field_name") or "title"
        title = data.get(field_name)
        return str(title) if title else str(data.get("dataset_doc_uuid") or "Untitled")
