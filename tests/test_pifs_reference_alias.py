from pathlib import Path


def _register_text_file(tmp_path: Path):
    from pageindex.filesystem import PageIndexFileSystem

    source = tmp_path / "source.txt"
    source.write_text("first line\nsecond line\nthird line\n", encoding="utf-8")
    filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")
    file_ref = filesystem.register_file(
        storage_uri=source.as_uri(),
        source_path="docs/source.txt",
        folder_path="/documents",
        external_id="doc_alias",
        title="Alias document",
        content=source.read_text(encoding="utf-8"),
        metadata={"department": "ops"},
    )
    return filesystem, file_ref


def test_search_result_reference_id_is_stable_document_target(tmp_path):
    filesystem, _ = _register_text_file(tmp_path)

    result = filesystem.search("second", limit=1)[0]

    assert result.reference_id == "doc_alias"
    assert result.reference_id == result.external_id


def test_open_result_reference_id_alias_and_open_reference_id_kwarg(tmp_path):
    filesystem, file_ref = _register_text_file(tmp_path)

    opened = filesystem.open(reference_id="doc_alias", location="2-2")

    assert opened.file_ref == file_ref
    assert opened.reference_id == "doc_alias"
    assert opened.text == "second line"
