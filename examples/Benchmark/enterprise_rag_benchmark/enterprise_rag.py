import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from pageindex.filesystem import PageIndexFileSystem


@dataclass(frozen=True)
class EnterpriseRAGQuestion:
    question_id: str
    question: str
    question_type: str
    source_types: list[str]
    expected_doc_ids: list[str]


class EnterpriseRAGBenchmark:
    def __init__(self, filesystem: PageIndexFileSystem):
        self.filesystem = filesystem

    def ingest_sources(self, source_root: Union[str, Path], batch_size: int = 1000) -> list[str]:
        source_root = Path(source_root).expanduser()
        return self.ingest_paths(source_root, sorted(source_root.rglob("*.json")), batch_size=batch_size)

    def ingest_paths(
        self,
        source_root: Union[str, Path],
        paths: list[Path],
        batch_size: int = 1000,
    ) -> list[str]:
        source_root = Path(source_root).expanduser()
        file_refs = []
        batch = []
        for path in paths:
            data = self._read_json(path)
            batch.append(self._document_spec(source_root, path, data))
            if len(batch) >= batch_size:
                file_refs.extend(self.filesystem.register_files(batch))
                batch = []
        if batch:
            file_refs.extend(self.filesystem.register_files(batch))
        return file_refs

    def load_questions(self, questions_path: Union[str, Path]) -> list[EnterpriseRAGQuestion]:
        questions_path = Path(questions_path).expanduser()
        questions = []
        with questions_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                questions.append(
                    EnterpriseRAGQuestion(
                        question_id=row["question_id"],
                        question=row["question"],
                        question_type=row.get("question_type", ""),
                        source_types=list(row.get("source_types") or []),
                        expected_doc_ids=list(row.get("expected_doc_ids") or []),
                    )
                )
        return questions

    @staticmethod
    def write_answers(answers_path: Union[str, Path], answers: list[dict[str, Any]]):
        answers_path = Path(answers_path).expanduser()
        answers_path.parent.mkdir(parents=True, exist_ok=True)
        with answers_path.open("w", encoding="utf-8") as f:
            for answer in answers:
                row = {
                    "question_id": answer["question_id"],
                    "answer": answer.get("answer", ""),
                    "document_ids": list(answer.get("document_ids") or []),
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _register_document(
        self,
        source_root: Path,
        path: Path,
        data: dict[str, Any],
    ) -> str:
        return self.filesystem.register_file(**self._document_spec(source_root, path, data))

    def _document_spec(
        self,
        source_root: Path,
        path: Path,
        data: dict[str, Any],
    ) -> dict[str, Any]:
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

        return {
            "storage_uri": str(path),
            "source_path": str(relative_path),
            "folder_path": folder_path,
            "metadata": metadata,
            "external_id": data.get("dataset_doc_uuid"),
            "title": title,
            "content": content,
            "content_type": "application/json",
            "source_type": metadata["source_type"],
        }

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
