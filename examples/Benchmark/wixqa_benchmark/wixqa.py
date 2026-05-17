from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Union
from urllib.parse import urlparse

from pageindex.filesystem import PageIndexFileSystem


DATASET_NAME = "Wix/WixQA"


@dataclass(frozen=True)
class WixQAArticle:
    article_id: str
    url: str
    contents: str
    article_type: str


@dataclass(frozen=True)
class WixQAQuestion:
    question_id: str
    question: str
    answer: str
    expected_article_ids: list[str]
    split: str


class WixQABenchmark:
    def __init__(self, filesystem: PageIndexFileSystem):
        self.filesystem = filesystem

    def register_metadata_schema(self) -> None:
        self.filesystem._register_metadata_schema(
            {
                "fields": {
                    "article_id": {"type": "string", "description": "WixQA KB article id"},
                    "article_type": {"type": "string", "description": "WixQA article type"},
                    "url": {"type": "string", "description": "Wix Help Center article URL"},
                    "url_path": {"type": "string", "description": "Normalized URL path"},
                    "category": {"type": "string", "description": "URL-derived help-center category"},
                    "subcategory": {"type": "string", "description": "URL-derived help-center subcategory"},
                    "source_dataset": {"type": "string", "description": "Dataset name"},
                }
            }
        )

    def ingest_articles(self, articles: Iterable[WixQAArticle], batch_size: int = 1000) -> list[str]:
        self.register_metadata_schema()
        refs = []
        batch = []
        for article in articles:
            batch.append(article_spec(article))
            if len(batch) >= batch_size:
                refs.extend(self.filesystem.register_files(batch))
                batch = []
        if batch:
            refs.extend(self.filesystem.register_files(batch))
        return refs


def load_articles(path: Union[str, Path] | None = None, *, dataset_config: str = "wix_kb_corpus") -> list[WixQAArticle]:
    rows = _load_rows(path, dataset_config)
    articles = []
    for row in rows:
        articles.append(
            WixQAArticle(
                article_id=str(row["id"]),
                url=str(row.get("url") or ""),
                contents=str(row.get("contents") or ""),
                article_type=str(row.get("article_type") or "unknown"),
            )
        )
    return articles


def load_questions(
    path: Union[str, Path] | None = None,
    *,
    split: str = "wixqa_expertwritten",
) -> list[WixQAQuestion]:
    rows = _load_rows(path, split)
    questions = []
    for index, row in enumerate(rows, 1):
        article_ids = row.get("article_ids") or []
        if isinstance(article_ids, str):
            article_ids = [article_ids]
        questions.append(
            WixQAQuestion(
                question_id=str(row.get("question_id") or f"{split}_{index:04d}"),
                question=str(row["question"]),
                answer=str(row.get("answer") or ""),
                expected_article_ids=[str(item) for item in article_ids],
                split=split,
            )
        )
    return questions


def write_official_predictions(path: Union[str, Path], rows: Iterable[dict[str, Any]]) -> None:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for row in rows:
            payload = {
                "question": str(row["question"]),
                "answer": str(row.get("answer") or ""),
                "article_ids": [str(item) for item in row.get("article_ids") or []],
            }
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def article_spec(article: WixQAArticle) -> dict[str, Any]:
    parts = url_parts(article.url)
    metadata = {
        "article_id": article.article_id,
        "article_type": article.article_type,
        "url": article.url,
        "url_path": parts["url_path"],
        "category": parts["category"],
        "subcategory": parts["subcategory"],
        "source_dataset": DATASET_NAME,
    }
    return {
        "storage_uri": article.url or f"wixqa://{article.article_id}",
        "source_path": f"wixqa/{article.article_id}.json",
        "folder_path": f"/kb/{slug(article.article_type)}/{slug(parts['category'])}/{slug(parts['subcategory'])}",
        "metadata": metadata,
        "external_id": article.article_id,
        "title": title_from_url(article.url, article.article_id),
        "content": "\n".join(
            [
                title_from_url(article.url, article.article_id),
                article.url,
                article.article_type,
                "",
                article.contents,
            ]
        ),
        "content_type": "text/plain",
        "source_type": "wixqa",
    }


def url_parts(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    segments = [segment for segment in path.split("/") if segment]
    if segments[:2] == ["en", "article"]:
        content_segments = segments[2:]
    else:
        content_segments = segments
    category = content_segments[0] if content_segments else "unknown"
    subcategory = content_segments[1] if len(content_segments) > 1 else "unknown"
    return {
        "url_path": "/" + path if path else "/",
        "category": category or "unknown",
        "subcategory": subcategory or "unknown",
    }


def title_from_url(url: str, fallback: str) -> str:
    parsed = urlparse(url)
    leaf = parsed.path.rstrip("/").split("/")[-1] if parsed.path else ""
    return leaf.replace("-", " ").strip().title() or fallback


def slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() or ch == "_" else "-" for ch in str(value))
    parts = [part for part in text.split("-") if part]
    return "-".join(parts) or "unknown"


def _load_rows(path: Union[str, Path] | None, dataset_config: str) -> list[dict[str, Any]]:
    if path:
        return _load_jsonl(path)
    try:
        from datasets import load_dataset
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Loading WixQA from Hugging Face requires the optional 'datasets' package. "
            "Install it or pass a local JSONL path."
        ) from exc
    dataset = load_dataset(DATASET_NAME, dataset_config, split="train")
    return [dict(row) for row in dataset]


def _load_jsonl(path: Union[str, Path]) -> list[dict[str, Any]]:
    rows = []
    with Path(path).expanduser().open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows
