from pathlib import Path

from pageindex.filesystem.pageindex_bridge import detect_pageindex_input


def test_detects_pdf_and_markdown_as_pageindex_core_inputs():
    pdf = detect_pageindex_input("/docs/report.PDF")
    markdown = detect_pageindex_input(Path("/docs/readme.markdown"))

    assert pdf.kind == "pdf"
    assert pdf.supported
    assert pdf.requires_pageindex_core
    assert not pdf.can_register_text_directly

    assert markdown.kind == "markdown"
    assert markdown.supported
    assert markdown.requires_pageindex_core


def test_detects_text_json_and_jsonl_as_direct_text_artifact_inputs():
    text = detect_pageindex_input("/docs/notes.txt")
    json_doc = detect_pageindex_input("/docs/record.json")
    jsonl_doc = detect_pageindex_input("/docs/export.ndjson")

    assert text.kind == "text"
    assert text.can_register_text_directly
    assert not text.requires_pageindex_core

    assert json_doc.kind == "json"
    assert json_doc.can_register_text_directly
    assert not json_doc.requires_pageindex_core

    assert jsonl_doc.kind == "jsonl"
    assert jsonl_doc.can_register_text_directly
    assert not jsonl_doc.requires_pageindex_core


def test_content_type_can_classify_paths_without_supported_extensions():
    markdown = detect_pageindex_input("/tmp/upload", content_type="text/markdown; charset=utf-8")
    jsonl_doc = detect_pageindex_input("/tmp/export", content_type="application/x-ndjson")

    assert markdown.kind == "markdown"
    assert markdown.requires_pageindex_core
    assert markdown.content_type == "text/markdown"

    assert jsonl_doc.kind == "jsonl"
    assert jsonl_doc.can_register_text_directly
    assert jsonl_doc.content_type == "application/x-ndjson"


def test_unsupported_format_reports_clear_reason():
    detected = detect_pageindex_input("/docs/archive.zip")

    assert detected.kind == "unsupported"
    assert not detected.supported
    assert not detected.requires_pageindex_core
    assert "Unsupported input format" in detected.reason
