import json
from types import SimpleNamespace

import pytest


class SummaryBackend:
    def __init__(self, document_id):
        self.document_id = document_id
        self.calls = []

    def available_channels(self):
        return ("summary",)

    def search_channel(self, channel, query, *, limit=10, filters=None):
        self.calls.append((channel, query, filters))
        return [
            SimpleNamespace(
                document_id=self.document_id,
                snippet=f"summary candidate: {query}",
            )
        ]


def test_semantic_search_scope_keeps_ordinary_folders_out_of_source_type_filters(tmp_path):
    from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem

    filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")
    filesystem.register_file(
        storage_uri="file:///tmp/report.pdf",
        source_path="examples/documents/report.pdf",
        folder_path="/documents",
        external_id="dsid_report",
        title="Annual report",
        metadata={"source_type": "examples-documents"},
        content="Federal Reserve supervision and regulation annual report.",
    )
    backend = SummaryBackend("dsid_report")
    filesystem.semantic_retrieval_backend = backend
    executor = PIFSCommandExecutor(filesystem, json_output=True)

    result = json.loads(
        executor.execute('search-summary "Federal Reserve annual report" /documents')
    )

    assert backend.calls[0][2] == {}
    assert result["data"]["data"][0]["external_id"] == "dsid_report"


def test_semantic_search_scope_filters_explicit_source_type_facets():
    from pageindex.filesystem import PageIndexFileSystem

    assert PageIndexFileSystem._semantic_filters_for_scope(
        {"folder_path": "/source_type=google-drive"}
    ) == {"source_type": "google_drive"}
    assert PageIndexFileSystem._semantic_filters_for_scope(
        {"folder_path": "/semantic/source_type=google-drive"}
    ) == {"source_type": "google_drive"}
    assert PageIndexFileSystem._semantic_filters_for_scope(
        {"folder_path": "/documents"}
    ) == {}


def test_existing_summary_projection_index_configures_retrieval_backend(tmp_path, monkeypatch):
    from pageindex.filesystem import PageIndexFileSystem
    from pageindex.filesystem.semantic_index import SemanticIndexRecord, SQLiteVecSemanticIndex

    workspace = tmp_path / "workspace"
    index_dir = workspace / "artifacts" / "projection_indexes"
    summary_index = SQLiteVecSemanticIndex(index_dir / "summary_only_vector.sqlite")
    summary_index.reset(
        dimension=3,
        metadata={
            "channel": "summary",
            "embedding_provider": "openai",
            "embedding_model": "test-embedding",
            "embedding_dimensions": 3,
        },
    )
    summary_index.upsert_many(
        [
            SemanticIndexRecord(
                file_ref="file_a",
                external_id="doc_a",
                source_type="documents",
                source_path="documents/a.pdf",
                title="A",
                text="summary",
                vector=[1.0, 0.0, 0.0],
            )
        ]
    )
    filesystem = PageIndexFileSystem(workspace)
    calls = []

    def fake_configure(index_dir_arg, **kwargs):
        calls.append((index_dir_arg, kwargs))
        filesystem.semantic_retrieval_backend = SummaryBackend("doc_a")
        return filesystem.semantic_retrieval_backend

    monkeypatch.setattr(
        filesystem,
        "configure_hybrid_projection_retrieval",
        fake_configure,
    )

    assert filesystem.configure_existing_projection_retrieval() is True
    assert calls == [
        (
            filesystem.summary_projection_index_dir,
            {
                "embedding_provider": "openai",
                "embedding_model": "test-embedding",
                "embedding_dimensions": 3,
                "embedding_timeout": 60,
            },
        )
    ]
    assert filesystem.semantic_retrieval_channels() == ("summary",)


def _assert_no_dotdot_folders(filesystem):
    listing = filesystem.browse("/", recursive=True, limit=100)
    folder_paths = {folder["path"] for folder in listing["folders"]}
    assert "/.." not in folder_paths
    assert not any(path.startswith("/../") for path in folder_paths)


def test_register_file_rejects_parent_directory_folder_paths(tmp_path):
    from pageindex.filesystem import PageIndexFileSystem

    source = tmp_path / "source.txt"
    source.write_text("fixture text", encoding="utf-8")
    filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")

    with pytest.raises(ValueError, match=r"\.\."):
        filesystem.register_file(
            storage_uri=source.as_uri(),
            source_path="docs/source.txt",
            folder_path="../tenant_a",
            external_id="doc_dotdot",
            title="Dotdot attempt",
            content=source.read_text(encoding="utf-8"),
        )

    _assert_no_dotdot_folders(filesystem)


def test_create_folder_rejects_parent_directory_segments(tmp_path):
    from pageindex.filesystem import PageIndexFileSystem

    filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")

    with pytest.raises(ValueError, match=r"\.\."):
        filesystem.create_folder("/documents/../tenant_a")

    _assert_no_dotdot_folders(filesystem)


def test_browse_rejects_parent_directory_segments(tmp_path):
    from pageindex.filesystem import PageIndexFileSystem

    filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")
    filesystem.create_folder("/documents")

    with pytest.raises(ValueError, match=r"\.\."):
        filesystem.browse("/documents/..")

    _assert_no_dotdot_folders(filesystem)
