from types import SimpleNamespace


class RecordingMetadataGenerator:
    def __init__(self):
        self.calls = []

    def generate(self, request, *, fields):
        self.calls.append((request, list(fields)))
        return {field: f"{field} generated" for field in fields}


class RecordingProjectionIndexer:
    def __init__(self):
        self.calls = []

    def upsert_summary(self, record):
        self.calls.append((record["file_ref"], record["metadata"].get("summary")))
        return {
            "status": "ready",
            "indexed_rows": 1,
            "index_path": "/tmp/pifs-test-summary-index.sqlite",
        }


def _summary_backend():
    return SimpleNamespace(available_channels=lambda: ("summary",))


def _filesystem_with_patched_sync_defaults(tmp_path, monkeypatch):
    from pageindex.filesystem import core

    backend = _summary_backend()
    generator = RecordingMetadataGenerator()
    indexer = RecordingProjectionIndexer()

    monkeypatch.setattr(core, "MetadataGenerator", lambda **kwargs: generator)
    monkeypatch.setattr(
        core.SummaryProjectionIndexer,
        "from_provider",
        classmethod(lambda cls, *args, **kwargs: indexer),
    )

    def fake_configure_projection_retrieval(self, *args, **kwargs):
        self.semantic_retrieval_backend = backend
        return backend

    monkeypatch.setattr(
        core.PageIndexFileSystem,
        "configure_hybrid_projection_retrieval",
        fake_configure_projection_retrieval,
    )
    filesystem = core.PageIndexFileSystem(workspace=tmp_path / "workspace")
    return filesystem, generator, indexer


def test_register_file_default_sync_generates_metadata_and_projection(
    tmp_path,
    monkeypatch,
):
    filesystem, generator, indexer = _filesystem_with_patched_sync_defaults(
        tmp_path,
        monkeypatch,
    )

    file_ref = filesystem.register_file(
        storage_uri="file:///tmp/report.txt",
        source_path="docs/report.txt",
        external_id="doc_sync",
        title="Synchronous document",
        content="Sync registration must complete generated metadata.",
    )

    entry = filesystem.store.get_file(file_ref)
    assert generator.calls[0][1] == ["summary", "doc_type", "domain", "topic"]
    assert entry.metadata["summary"] == "summary generated"
    assert entry.metadata["doc_type"] == "doc_type generated"
    assert entry.metadata_status["status"] == "generated"
    assert entry.metadata_status["fields"]["summary"]["status"] == "generated"
    assert entry.metadata_status["projection_indexes"]["summary"]["status"] == "ready"
    assert "pending_generate" not in str(entry.metadata_status)
    assert indexer.calls == [(file_ref, "summary generated")]
    assert filesystem.metadata_generator is generator
    assert filesystem.summary_projection_indexer is indexer


def test_register_files_default_sync_generates_each_file(tmp_path, monkeypatch):
    filesystem, generator, indexer = _filesystem_with_patched_sync_defaults(
        tmp_path,
        monkeypatch,
    )

    refs = filesystem.register_files(
        [
            {
                "storage_uri": "file:///tmp/one.txt",
                "source_path": "docs/one.txt",
                "external_id": "doc_one",
                "title": "One",
                "content": "First file",
            },
            {
                "storage_uri": "file:///tmp/two.txt",
                "source_path": "docs/two.txt",
                "external_id": "doc_two",
                "title": "Two",
                "content": "Second file",
            },
        ]
    )

    assert len(refs) == 2
    assert [call[0].external_id for call in generator.calls] == ["doc_one", "doc_two"]
    assert [call[0] for call in indexer.calls] == refs
    for file_ref in refs:
        entry = filesystem.store.get_file(file_ref)
        assert entry.metadata_status["status"] == "generated"
        assert entry.metadata_status["projection_indexes"]["summary"]["status"] == "ready"
        assert "pending_generate" not in str(entry.metadata_status)


def test_register_files_batch_policy_remains_deferred(tmp_path, monkeypatch):
    from pageindex.filesystem import PageIndexFileSystem

    filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")

    def fail_if_sync_defaults_are_loaded():
        raise AssertionError("batch registration must not load sync defaults")

    monkeypatch.setattr(
        filesystem,
        "_ensure_register_completion_defaults",
        fail_if_sync_defaults_are_loaded,
    )

    refs = filesystem.register_files(
        [
            {
                "storage_uri": "file:///tmp/batch.txt",
                "source_path": "docs/batch.txt",
                "external_id": "doc_batch",
                "title": "Batch",
                "content": "Batch registration should remain deferred.",
                "metadata_policy": {"batch": True},
            }
        ]
    )

    entry = filesystem.store.get_file(refs[0])
    assert "summary" not in entry.metadata
    assert entry.metadata_status["status"] == "pending_submit"
    assert entry.metadata_status["fields"]["summary"]["status"] == "pending_submit"
    assert filesystem.metadata_generator is None
    assert filesystem.summary_projection_indexer is None
