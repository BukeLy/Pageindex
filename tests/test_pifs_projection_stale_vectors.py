import pytest

from pageindex.filesystem import PageIndexFileSystem
from pageindex.filesystem.hybrid_projection import HybridProjectionSearchBackend
from pageindex.filesystem.projection_indexing import SummaryProjectionIndexer


class ConstantEmbedder:
    def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


class MovingSummaryGenerator:
    def generate(self, request, *, fields):
        values = {}
        for field in fields:
            if field == "summary":
                values[field] = f"alpha report from {request.source_path}"
            else:
                values[field] = "test"
        return values


def test_register_move_evicts_stale_summary_projection_vectors(tmp_path):
    workspace = tmp_path / "workspace"
    index_dir = workspace / "artifacts" / "projection_indexes"
    embedder = ConstantEmbedder()
    indexer = SummaryProjectionIndexer(
        index_dir,
        embedder=embedder,
        embedding_provider="test",
        embedding_model="constant",
        embedding_dimensions=3,
    )
    backend = HybridProjectionSearchBackend(
        index_dir,
        embedder=embedder,
        embedding_provider="test",
        embedding_model="constant",
        embedding_dimensions=3,
    )
    filesystem = PageIndexFileSystem(
        workspace=workspace,
        metadata_generator=MovingSummaryGenerator(),
        summary_projection_indexer=indexer,
        semantic_retrieval_backend=backend,
    )

    first_ref = filesystem.register_file(
        storage_uri="file:///tmp/pifs/report.md",
        source_path="docs/old.md",
        content="alpha report before move",
    )
    assert indexer.index.info()["document_count"] == 1

    second_ref = filesystem.register_file(
        storage_uri="file:///tmp/pifs/report.md",
        source_path="docs/new.md",
        content="alpha report after move",
    )

    assert first_ref != second_ref
    assert indexer.index.info()["document_count"] == 1
    hits = backend.search_channel("summary", "alpha report", limit=5)
    assert [hit.document_id for hit in hits] == [second_ref]
    assert [hit.source_path for hit in hits] == ["docs/new.md"]
    with pytest.raises(KeyError):
        filesystem.store.resolve_file_ref(first_ref)
