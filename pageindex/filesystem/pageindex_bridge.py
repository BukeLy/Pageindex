from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PAGEINDEX_CORE_INPUT_KINDS = {"pdf", "markdown"}
TEXT_ARTIFACT_INPUT_KINDS = {"text", "json", "jsonl"}
SUPPORTED_INPUT_KINDS = PAGEINDEX_CORE_INPUT_KINDS | TEXT_ARTIFACT_INPUT_KINDS


@dataclass(frozen=True)
class PageIndexInputDetection:
    path: str
    kind: str
    suffix: str
    content_type: str | None = None
    supported: bool = True
    requires_pageindex_core: bool = False
    reason: str = ""

    @property
    def can_register_text_directly(self) -> bool:
        return self.kind in TEXT_ARTIFACT_INPUT_KINDS


EXTENSION_INPUT_KINDS = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".text": "text",
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
}

CONTENT_TYPE_INPUT_KINDS = {
    "application/pdf": "pdf",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    "application/markdown": "markdown",
    "text/plain": "text",
    "application/json": "json",
    "application/x-jsonlines": "jsonl",
    "application/jsonl": "jsonl",
    "application/x-ndjson": "jsonl",
}


def detect_pageindex_input(
    path: str | Path,
    content_type: str | None = None,
) -> PageIndexInputDetection:
    """Classify a candidate PIFS registration input without indexing it.

    This is deliberately side-effect free: it does not read the file, call
    PageIndex Core, write artifacts, or alter the filesystem catalog.
    """

    path_text = str(path)
    suffix = Path(path_text).suffix.lower()
    normalized_content_type = _normalize_content_type(content_type)
    kind = (
        CONTENT_TYPE_INPUT_KINDS.get(normalized_content_type or "")
        or EXTENSION_INPUT_KINDS.get(suffix)
    )
    if kind is None:
        return PageIndexInputDetection(
            path=path_text,
            kind="unsupported",
            suffix=suffix,
            content_type=normalized_content_type,
            supported=False,
            requires_pageindex_core=False,
            reason=(
                "Unsupported input format. Supported inputs are PDF, Markdown, "
                "plain text, JSON, and JSONL."
            ),
        )
    return PageIndexInputDetection(
        path=path_text,
        kind=kind,
        suffix=suffix,
        content_type=normalized_content_type,
        supported=kind in SUPPORTED_INPUT_KINDS,
        requires_pageindex_core=kind in PAGEINDEX_CORE_INPUT_KINDS,
        reason="",
    )


def _normalize_content_type(content_type: str | None) -> str | None:
    if content_type is None:
        return None
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type or None
