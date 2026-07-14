import json
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


class FakeOpenAI:
    def __init__(self, **_kwargs):
        self.embeddings = self

    def create(self, *, model, input, dimensions):
        assert model == "test-embedding"
        assert dimensions == 3

        def vector(text):
            lowered = str(text).lower()
            if "alpha" in lowered:
                return [1.0, 0.0, 0.0]
            if "beta" in lowered:
                return [0.0, 1.0, 0.0]
            return [0.0, 0.0, 1.0]

        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=vector(text))
                for index, text in enumerate(input)
            ]
        )


def install_network_fakes(monkeypatch):
    import openai
    from pageindex import PageIndexClient

    def fake_index(self, file_path, mode="auto"):
        path = Path(file_path)
        text = (
            path.read_text(encoding="utf-8")
            if path.suffix.lower() in {".md", ".markdown"}
            else "pdf alpha evidence"
        )
        document_id = f"pageindex_{path.stem}"
        document = {
            "id": document_id,
            "type": "pdf" if path.suffix.lower() == ".pdf" else "md",
            "path": str(path.resolve()),
            "doc_name": path.name,
            "doc_description": f"Summary for {path.name}: {text}",
            "line_count": len(text.splitlines()),
            "structure": [
                {
                    "title": path.stem,
                    "node_id": "0001",
                    "line_num": 1,
                    "text": text,
                    "nodes": [],
                }
            ],
            "pages": [{"page": 1, "content": text}],
        }
        self.documents[document_id] = document
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / f"{document_id}.json").write_text(
            json.dumps(document), encoding="utf-8"
        )
        (self.workspace / "_meta.json").write_text(
            json.dumps(
                {
                    document_id: {
                        "type": document["type"],
                        "doc_name": path.name,
                        "doc_description": document["doc_description"],
                        "path": str(path.resolve()),
                        "line_count": document["line_count"],
                    }
                }
            ),
            encoding="utf-8",
        )
        return document_id

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(PageIndexClient, "index", fake_index)


def write_embedding_config(tmp_path, monkeypatch):
    config = tmp_path / "pifs.json"
    config.write_text(
        json.dumps(
            {
                "embedding_base_url": "https://EXAMPLE.invalid/v1/",
                "embedding_model": "test-embedding",
                "embedding_dimensions": 3,
                "embedding_timeout": 12,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PIFS_CONFIG_FILE", str(config))
    monkeypatch.setenv("PIFS_EMBEDDING_API_KEY", "runtime-secret")
    return config


def logical_tables(connection):
    return {
        row[1]
        for row in connection.execute("PRAGMA table_list")
        if row[2] in {"table", "virtual"} and not row[1].startswith("sqlite_")
        and not row[1].startswith("semantic_index_vec_")
    }


def workspace_state(workspace):
    workspace = Path(workspace)
    if not workspace.exists():
        return {}
    return {
        path.relative_to(workspace).as_posix(): (
            "directory"
            if path.is_dir()
            else hashlib.sha256(path.read_bytes()).hexdigest()
        )
        for path in sorted(workspace.rglob("*"))
    }


def create_projection_v2(workspace):
    from pageindex.filesystem.semantic_projection import (
        SummaryEmbeddingProfile,
        SummaryProjection,
    )

    projection_dir = Path(workspace) / "artifacts" / "projection_indexes"
    SummaryProjection(
        projection_dir,
        profile=SummaryEmbeddingProfile(
            base_url="https://example.invalid/v1",
            model="test-embedding",
            dimensions=3,
            api_key="runtime-only",
        ),
        create=True,
    )
    return projection_dir


def catalog_file_count(workspace):
    with sqlite3.connect(Path(workspace) / "filesystem.sqlite") as connection:
        return connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]


def test_cli_rejects_removed_ls_command_with_structured_error(tmp_path, capsys):
    from pageindex.filesystem.cli import main

    status = main(["--workspace", str(tmp_path / "workspace"), "ls", "/"])

    payload = json.loads(capsys.readouterr().out)
    assert status == 2
    assert payload == {
        "success": False,
        "error": {"code": "invalid_command", "message": "Unsupported command: ls"},
        "next_steps": [],
    }


def test_fresh_cli_workspace_uses_the_five_table_catalog_schema_v2(tmp_path, capsys):
    from pageindex.filesystem.cli import main

    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "tree", "/", "-L", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["success"] is True

    with sqlite3.connect(workspace / "filesystem.sqlite") as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    assert version == 2
    assert tables == {
        "files",
        "folders",
        "file_folders",
        "metadata_fields",
        "metadata_values",
    }


def test_cli_rejects_legacy_catalog_without_mutating_it(tmp_path, capsys):
    from pageindex.filesystem.cli import main

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = workspace / "filesystem.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE legacy_sentinel(value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_sentinel(value) VALUES ('preserve me')")
        connection.execute("PRAGMA user_version = 1")
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    status = main(["--workspace", str(workspace), "tree", "/", "-L", "1"])

    assert status == 1
    assert "migrate_pifs_workspace.py" in capsys.readouterr().err
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize(
    "mutation",
    ["extra_column", "missing_index", "missing_primary_and_foreign_keys"],
)
def test_cli_rejects_pseudo_v2_catalog_without_mutating_workspace(
    mutation, tmp_path, capsys
):
    from pageindex.filesystem.cli import main

    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "tree", "/", "-L", "1"]) == 0
    capsys.readouterr()
    database = workspace / "filesystem.sqlite"
    with sqlite3.connect(database) as connection:
        if mutation == "extra_column":
            connection.execute("ALTER TABLE files ADD COLUMN legacy_provider TEXT")
        elif mutation == "missing_index":
            connection.execute("DROP INDEX idx_files_external_id")
        else:
            connection.executescript(
                """
                ALTER TABLE file_folders RENAME TO legacy_file_folders;
                CREATE TABLE file_folders (
                    file_ref TEXT NOT NULL,
                    folder_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                DROP TABLE legacy_file_folders;
                CREATE INDEX idx_file_folders_folder ON file_folders(folder_id);
                """
            )
    before = workspace_state(workspace)

    status = main(["--workspace", str(workspace), "tree", "/", "-L", "1"])

    assert status == 1
    assert "migrate_pifs_workspace.py" in capsys.readouterr().err
    assert workspace_state(workspace) == before


def test_cli_rejects_partial_legacy_projection_without_creating_summary(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "tree", "/", "-L", "1"]) == 0
    capsys.readouterr()
    projection_dir = workspace / "artifacts" / "projection_indexes"
    projection_dir.mkdir(parents=True)
    cache_path = projection_dir / "embedding_cache.sqlite"
    with sqlite3.connect(cache_path) as connection:
        connection.execute(
            "CREATE TABLE embedding_cache(provider TEXT, model TEXT, text_hash TEXT)"
        )
        connection.execute("PRAGMA user_version = 1")
    before = hashlib.sha256(cache_path.read_bytes()).hexdigest()
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")

    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 1

    assert "migrate_pifs_workspace.py" in capsys.readouterr().err
    assert hashlib.sha256(cache_path.read_bytes()).hexdigest() == before
    assert not (projection_dir / "summary.sqlite").exists()
    assert catalog_file_count(workspace) == 0


def test_cli_preflights_partial_projection_before_creating_catalog(tmp_path, capsys):
    from pageindex.filesystem.cli import main

    workspace = tmp_path / "workspace"
    projection_dir = workspace / "artifacts" / "projection_indexes"
    projection_dir.mkdir(parents=True)
    cache_path = projection_dir / "embedding_cache.sqlite"
    with sqlite3.connect(cache_path) as connection:
        connection.execute(
            "CREATE TABLE embedding_cache(provider TEXT, model TEXT, text_hash TEXT)"
        )
        connection.execute("PRAGMA user_version = 1")
    before = workspace_state(workspace)

    status = main(["--workspace", str(workspace), "tree", "/", "-L", "1"])

    assert status == 1
    assert "migrate_pifs_workspace.py" in capsys.readouterr().err
    assert workspace_state(workspace) == before
    assert not (workspace / "filesystem.sqlite").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "summary_extra_column",
        "summary_missing_primary_and_unique",
        "summary_missing_index",
        "summary_extra_config_key",
        "summary_vec_dimension_mismatch",
        "cache_extra_column",
        "cache_missing_primary_key",
    ],
)
def test_cli_rejects_pseudo_v2_projection_without_mutating_workspace(
    mutation, tmp_path, capsys
):
    from pageindex.filesystem.cli import main

    workspace = tmp_path / "workspace"
    projection_dir = create_projection_v2(workspace)
    summary_path = projection_dir / "summary.sqlite"
    cache_path = projection_dir / "embedding_cache.sqlite"
    if mutation.startswith("summary_"):
        with sqlite3.connect(summary_path) as connection:
            if mutation == "summary_extra_column":
                connection.execute(
                    "ALTER TABLE semantic_index_docs ADD COLUMN legacy_provider TEXT"
                )
            elif mutation == "summary_missing_primary_and_unique":
                connection.executescript(
                    """
                    DROP INDEX idx_semantic_index_docs_external_id;
                    DROP INDEX idx_semantic_index_docs_source_type;
                    ALTER TABLE semantic_index_docs RENAME TO legacy_semantic_index_docs;
                    CREATE TABLE semantic_index_docs (
                        rowid INTEGER,
                        file_ref TEXT NOT NULL,
                        external_id TEXT,
                        source_type TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        text_hash TEXT NOT NULL,
                        text_chars INTEGER NOT NULL DEFAULT 0,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );
                    DROP TABLE legacy_semantic_index_docs;
                    CREATE INDEX idx_semantic_index_docs_external_id
                      ON semantic_index_docs(external_id);
                    CREATE INDEX idx_semantic_index_docs_source_type
                      ON semantic_index_docs(source_type);
                    """
                )
            elif mutation == "summary_missing_index":
                connection.execute("DROP INDEX idx_semantic_index_docs_external_id")
            elif mutation == "summary_extra_config_key":
                connection.execute(
                    "INSERT INTO semantic_index_config(key, value) "
                    "VALUES ('legacy_provider', 'openai')"
                )
            else:
                connection.execute(
                    "UPDATE semantic_index_config SET value = '4' WHERE key = 'dimension'"
                )
                connection.execute(
                    "UPDATE semantic_index_config SET value = ? WHERE key = 'metadata'",
                    (
                        json.dumps(
                            {
                                "base_url": "https://example.invalid/v1",
                                "model": "test-embedding",
                                "dimensions": 4,
                            }
                        ),
                    ),
                )
    else:
        with sqlite3.connect(cache_path) as connection:
            if mutation == "cache_extra_column":
                connection.execute(
                    "ALTER TABLE embedding_cache ADD COLUMN provider TEXT"
                )
            else:
                connection.executescript(
                    """
                    ALTER TABLE embedding_cache RENAME TO legacy_embedding_cache;
                    CREATE TABLE embedding_cache (
                        base_url TEXT NOT NULL,
                        model TEXT NOT NULL,
                        dimensions INTEGER NOT NULL CHECK(dimensions > 0),
                        text_hash TEXT NOT NULL,
                        vector_blob BLOB NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );
                    DROP TABLE legacy_embedding_cache;
                    """
                )
    before = workspace_state(workspace)

    status = main(["--workspace", str(workspace), "tree", "/", "-L", "1"])

    assert status == 1
    assert "migrate_pifs_workspace.py" in capsys.readouterr().err
    assert workspace_state(workspace) == before
    assert not (workspace / "filesystem.sqlite").exists()


def test_cli_allows_fresh_listing_when_projection_is_completely_absent(tmp_path, capsys):
    from pageindex.filesystem.cli import main

    workspace = tmp_path / "workspace"

    assert main(["--workspace", str(workspace), "tree", "/", "-L", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["success"] is True
    assert (workspace / "filesystem.sqlite").is_file()
    assert not (workspace / "artifacts" / "projection_indexes").exists()


def test_pageindex_is_the_only_public_python_entry_point_for_pifs():
    import pageindex
    import pageindex.filesystem as filesystem_module

    assert pageindex.PageIndexFileSystem is filesystem_module.PageIndexFileSystem
    for internal_name in (
        "PIFSCommandExecutor",
        "OpenResult",
        "SearchResult",
        "SummaryProjectionIndexer",
        "SemanticProjectionSearchBackend",
        "SQLiteVecSemanticIndex",
        "SemanticIndexRecord",
        "SemanticSearchResult",
    ):
        assert not hasattr(filesystem_module, internal_name)
    assert not hasattr(filesystem_module, "_LAZY_EXPORTS")


def test_cli_add_creates_migration_compatible_summary_and_cache_v2(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")
    workspace = tmp_path / "workspace"

    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 0
    capsys.readouterr()

    projection_dir = workspace / "artifacts" / "projection_indexes"
    with sqlite3.connect(projection_dir / "summary.sqlite") as summary:
        summary_version = summary.execute("PRAGMA user_version").fetchone()[0]
        summary_tables = logical_tables(summary)
        metadata = json.loads(
            summary.execute(
                "SELECT value FROM semantic_index_config WHERE key = 'metadata'"
            ).fetchone()[0]
        )
    with sqlite3.connect(projection_dir / "embedding_cache.sqlite") as cache:
        cache_version = cache.execute("PRAGMA user_version").fetchone()[0]
        cache_tables = logical_tables(cache)
        cache_columns = {
            row[1] for row in cache.execute("PRAGMA table_info(embedding_cache)")
        }

    assert summary_version == cache_version == 2
    assert summary_tables == {
        "semantic_index_config",
        "semantic_index_docs",
        "semantic_index_vec",
    }
    assert cache_tables == {"embedding_cache"}
    assert cache_columns == {
        "base_url",
        "model",
        "dimensions",
        "text_hash",
        "vector_blob",
        "created_at",
    }
    assert metadata == {
        "base_url": "https://example.invalid/v1",
        "model": "test-embedding",
        "dimensions": 3,
    }
    for database in (
        workspace / "filesystem.sqlite",
        projection_dir / "summary.sqlite",
        projection_dir / "embedding_cache.sqlite",
    ):
        assert b"runtime-secret" not in database.read_bytes()


def test_cli_add_reports_path_without_persistence_identity(tmp_path, monkeypatch, capsys):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")

    assert (
        main(
            [
                "--workspace",
                str(tmp_path / "workspace"),
                "add",
                str(source),
                "/documents",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert output == "added: /documents/notes.md\n"


def test_cli_browse_reopens_owned_file_with_canonical_result_fields(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")
    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 0
    capsys.readouterr()
    source.unlink()

    assert (
        main(
            [
                "--workspace",
                str(workspace),
                "browse",
                "/documents",
                "alpha",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    document = payload["data"]["documents"][0]
    assert set(document) == {
        "path",
        "document_id",
        "title",
        "status",
        "rank",
        "similarity",
        "summary",
        "metadata",
        "folder_path",
        "folder_paths",
    }
    assert document["path"] == "/documents/notes.md"
    assert document["title"] == "notes.md"
    assert document["status"] == "built"
    assert document["rank"] == 1
    assert document["summary"].startswith("Summary for notes.md")
    assert document["folder_path"] == "/documents"


def test_cli_stat_translates_persistence_identity_once(tmp_path, monkeypatch, capsys):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")
    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 0
    capsys.readouterr()

    assert main(["--workspace", str(workspace), "stat", "/documents/notes.md"]) == 0

    document = json.loads(capsys.readouterr().out)["data"]["document"]
    assert set(document) == {
        "path",
        "document_id",
        "title",
        "status",
        "content_type",
        "metadata",
        "metadata_status",
        "folder_paths",
    }
    assert document["path"] == "/documents/notes.md"
    assert document["title"] == "notes.md"
    assert document["status"] == "built"
    assert document["folder_paths"] == ["/documents"]


def test_cli_cat_reads_structure_without_internal_identity(tmp_path, monkeypatch, capsys):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")
    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 0
    capsys.readouterr()
    source.unlink()

    assert (
        main(
            [
                "--workspace",
                str(workspace),
                "cat",
                "/documents/notes.md",
                "--structure",
            ]
        )
        == 0
    )

    data = json.loads(capsys.readouterr().out)["data"]
    assert set(data["document"]) == {
        "path",
        "document_id",
        "title",
        "status",
        "content_type",
        "metadata",
        "metadata_status",
        "folder_paths",
        "available",
    }
    assert data["structure"] == [
        {"title": "notes", "node_id": "0001", "line_num": 1, "nodes": []}
    ]


def test_tree_virtual_value_uses_one_axis_equals_value_segment(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")
    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--workspace",
                str(workspace),
                "setmeta",
                "/documents/notes.md",
                '{"year": 2024}',
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["--workspace", str(workspace), "tree", "/documents/@year"]) == 0
    values = json.loads(capsys.readouterr().out)["data"]["tree"]["folders"]
    assert values[0]["path"] == "/documents/@year=2024"

    assert (
        main(
            [
                "--workspace",
                str(workspace),
                "tree",
                "/documents/@year=2024",
                "-L",
                "1",
            ]
        )
        == 0
    )
    files = json.loads(capsys.readouterr().out)["data"]["tree"]["files"]
    assert files[0]["path"] == "/documents/@year=2024/notes.md"


def test_setmeta_translates_identity_at_the_cli_boundary(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")
    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 0
    capsys.readouterr()

    assert main(
        [
            "--workspace",
            str(workspace),
            "setmeta",
            "/documents/notes.md",
            '{"year": 2024}',
        ]
    ) == 0
    document = json.loads(capsys.readouterr().out)

    assert set(document) == {
        "path",
        "document_id",
        "title",
        "status",
        "metadata",
        "metadata_status",
    }
    assert document["path"] == "/documents/notes.md"
    assert document["metadata"]["year"] == 2024


def test_tree_file_records_use_only_command_identity_names(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")
    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 0
    capsys.readouterr()

    assert main(["--workspace", str(workspace), "tree", "/documents", "-L", "1"]) == 0
    file_record = json.loads(capsys.readouterr().out)["data"]["tree"]["files"][0]

    assert set(file_record) == {
        "path",
        "document_id",
        "title",
        "status",
        "type",
        "metadata",
    }


def test_duplicate_virtual_leaves_use_actionable_paths_without_internal_ids(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    for folder_name in ("a", "b"):
        source_dir = tmp_path / folder_name
        source_dir.mkdir()
        source = source_dir / "notes.md"
        source.write_text(f"alpha evidence {folder_name}", encoding="utf-8")
        folder = f"/documents/{folder_name}"
        assert main(["--workspace", str(workspace), "add", str(source), folder]) == 0
        capsys.readouterr()
        assert main(
            [
                "--workspace",
                str(workspace),
                "setmeta",
                f"{folder}/notes.md",
                '{"year": 2024}',
            ]
        ) == 0
        capsys.readouterr()

    assert main(
        ["--workspace", str(workspace), "tree", "/documents/@year=2024", "-L", "1"]
    ) == 0
    files = json.loads(capsys.readouterr().out)["data"]["tree"]["files"]
    paths = [row["path"] for row in files]

    assert len(paths) == len(set(paths)) == 2
    assert all("file_" not in path for path in paths)
    for path in paths:
        assert main(["--workspace", str(workspace), "stat", path]) == 0
        assert json.loads(capsys.readouterr().out)["data"]["document"]["path"] == path


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("notes.md", b"markdown alpha evidence"),
        ("report.pdf", b"%PDF-1.4 fake fixture"),
    ],
)
def test_cli_add_owns_supported_documents_and_keeps_evidence_readable(
    filename, content, tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    source = tmp_path / filename
    source.write_bytes(content)
    workspace = tmp_path / "workspace"

    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 0
    capsys.readouterr()
    source.unlink()

    owned = list((workspace / "artifacts" / "uploads").glob(f"*/{filename}"))
    assert len(owned) == 1
    assert owned[0].read_bytes() == content
    assert main(
        ["--workspace", str(workspace), "grep", "alpha", f"/documents/{filename}"]
    ) == 0
    matches = json.loads(capsys.readouterr().out)["data"]["matches"]
    assert matches[0]["text"].endswith("alpha evidence")


def test_cli_add_rejects_unsupported_type_before_registration(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    write_embedding_config(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("unsupported", encoding="utf-8")
    workspace = tmp_path / "workspace"

    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 1

    assert "Unsupported file type" in capsys.readouterr().err
    assert catalog_file_count(workspace) == 0
    assert not list((workspace / "artifacts" / "uploads").glob("**/*"))


def test_cli_add_rolls_back_when_pageindex_fails(
    tmp_path, monkeypatch, capsys
):
    from pageindex import PageIndexClient
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)

    def fail_index(self, file_path, mode="auto"):
        raise RuntimeError("pageindex unavailable")

    monkeypatch.setattr(PageIndexClient, "index", fail_index)
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")
    workspace = tmp_path / "workspace"

    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 1

    assert "pageindex unavailable" in capsys.readouterr().err
    assert catalog_file_count(workspace) == 0
    assert not list((workspace / "artifacts" / "uploads").glob("**/*"))
    pageindex_cache = workspace / "artifacts" / "pageindex_client"
    assert json.loads((pageindex_cache / "_meta.json").read_text(encoding="utf-8")) == {}
    assert not [path for path in pageindex_cache.glob("*.json") if path.name != "_meta.json"]


def test_cli_add_rolls_back_when_summary_projection_fails(
    tmp_path, monkeypatch, capsys
):
    import openai
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)

    class FailingOpenAI(FakeOpenAI):
        def create(self, *, model, input, dimensions):
            raise RuntimeError("embedding unavailable")

    monkeypatch.setattr(openai, "OpenAI", FailingOpenAI)
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")
    workspace = tmp_path / "workspace"

    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 1

    assert "summary projection" in capsys.readouterr().err.lower()
    assert catalog_file_count(workspace) == 0
    assert not list((workspace / "artifacts" / "uploads").glob("**/*"))
    with sqlite3.connect(
        workspace / "artifacts" / "projection_indexes" / "summary.sqlite"
    ) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM semantic_index_docs"
        ).fetchone()[0] == 0


def test_cli_add_rejects_duplicate_target_without_changing_owned_document(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "notes.md"
    second = second_dir / "notes.md"
    first.write_text("first alpha evidence", encoding="utf-8")
    second.write_text("second beta evidence", encoding="utf-8")
    workspace = tmp_path / "workspace"
    command = ["--workspace", str(workspace), "add"]

    assert main([*command, str(first), "/documents"]) == 0
    capsys.readouterr()
    assert main([*command, str(second), "/documents"]) == 1

    assert "already exists" in capsys.readouterr().err
    assert catalog_file_count(workspace) == 1
    owned = list((workspace / "artifacts" / "uploads").glob("*/notes.md"))
    assert len(owned) == 1
    assert owned[0].read_text(encoding="utf-8") == "first alpha evidence"


def test_browse_recursively_returns_one_global_ranked_page_of_ten(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    for index in range(12):
        source = tmp_path / f"doc-{index:02d}.md"
        relevance = "alpha" if index in {1, 10} else "beta"
        source.write_text(f"{relevance} evidence {index}", encoding="utf-8")
        folder = "/documents/a" if index % 2 == 0 else "/documents/b"
        assert main(["--workspace", str(workspace), "add", str(source), folder]) == 0
        capsys.readouterr()

    command = [
        "--workspace",
        str(workspace),
        "browse",
        "/documents",
        "alpha",
        "--recursive",
    ]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)["data"]
    assert len(first["documents"]) == 10
    assert first["pagination"] == {
        "page": 1,
        "page_size": 10,
        "has_more": True,
        "next_page": 2,
    }
    assert {row["title"] for row in first["documents"][:2]} == {
        "doc-01.md",
        "doc-10.md",
    }
    assert {row["folder_path"] for row in first["documents"]} == {
        "/documents/a",
        "/documents/b",
    }

    assert main([*command, "--page", "2"]) == 0
    second = json.loads(capsys.readouterr().out)["data"]
    assert len(second["documents"]) == 2
    assert second["pagination"] == {
        "page": 2,
        "page_size": 10,
        "has_more": False,
        "next_page": None,
    }
    assert not {
        row["path"] for row in first["documents"]
    }.intersection(row["path"] for row in second["documents"])


def test_browse_respects_physical_and_virtual_scope(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    records = [
        ("current.md", "alpha current", "/documents/current", 2024),
        ("old.md", "alpha old", "/documents/archive", 2023),
    ]
    for filename, content, folder, year in records:
        source = tmp_path / filename
        source.write_text(content, encoding="utf-8")
        assert main(["--workspace", str(workspace), "add", str(source), folder]) == 0
        capsys.readouterr()
        assert main(
            [
                "--workspace",
                str(workspace),
                "setmeta",
                f"{folder}/{filename}",
                json.dumps({"year": year}),
            ]
        ) == 0
        capsys.readouterr()

    assert main(
        [
            "--workspace",
            str(workspace),
            "browse",
            "/documents/current",
            "alpha",
        ]
    ) == 0
    physical = json.loads(capsys.readouterr().out)["data"]["documents"]
    assert [row["title"] for row in physical] == ["current.md"]

    assert main(
        [
            "--workspace",
            str(workspace),
            "browse",
            "/documents/@year=2024",
            "alpha",
        ]
    ) == 0
    virtual = json.loads(capsys.readouterr().out)["data"]["documents"]
    assert [row["title"] for row in virtual] == ["current.md"]
    assert virtual[0]["path"] == "/documents/@year=2024/current.md"


def test_browse_requires_query_and_an_existing_summary_projection(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    write_embedding_config(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "tree", "/", "-L", "1"]) == 0
    capsys.readouterr()

    assert main(["--workspace", str(workspace), "browse", "/documents"]) == 2
    missing_query = json.loads(capsys.readouterr().out)
    assert "requires a query" in missing_query["error"]["message"]

    assert main(["--workspace", str(workspace), "browse", "/", "alpha"]) == 2
    missing_projection = json.loads(capsys.readouterr().out)
    assert "Summary Projection is not available" in missing_projection["error"]["message"]


def test_browse_rejects_incompatible_embedding_identity_without_mutation(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    config = write_embedding_config(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    source = tmp_path / "notes.md"
    source.write_text("alpha evidence", encoding="utf-8")
    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 0
    capsys.readouterr()
    databases = [
        workspace / "filesystem.sqlite",
        workspace / "artifacts" / "projection_indexes" / "summary.sqlite",
        workspace / "artifacts" / "projection_indexes" / "embedding_cache.sqlite",
    ]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in databases}
    config.write_text(
        json.dumps(
            {
                "embedding_base_url": "https://example.invalid/v1",
                "embedding_model": "different-model",
                "embedding_dimensions": 3,
            }
        ),
        encoding="utf-8",
    )

    assert main(["--workspace", str(workspace), "browse", "/documents", "alpha"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert "Incompatible PIFS Summary Embedding Profile" in payload["error"]["message"]
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in databases
    } == before


def test_agent_prompts_describe_only_the_retained_command_surface():
    from pageindex.filesystem.agent import (
        AGENT_SYSTEM_PROMPT,
        AGENT_TOOL_POLICY,
        BASH_TOOL_DESCRIPTION,
    )

    prompts = "\n".join(
        [AGENT_SYSTEM_PROMPT, BASH_TOOL_DESCRIPTION, AGENT_TOOL_POLICY]
    )
    for command in ("tree", "browse", "stat", "cat", "grep"):
        assert command in prompts
    for retired_contract in (
        "ls as an alias",
        "file_ref",
        "--space",
        "Do not use find",
        "recursive grep",
    ):
        assert retired_contract not in prompts


def test_cat_page_reads_are_bounded_and_grep_is_single_document(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    install_network_fakes(monkeypatch)
    write_embedding_config(tmp_path, monkeypatch)
    source = tmp_path / "notes.md"
    source.write_text("line one\nalpha evidence", encoding="utf-8")
    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "add", str(source), "/documents"]) == 0
    capsys.readouterr()

    assert main(
        [
            "--workspace",
            str(workspace),
            "cat",
            "/documents/notes.md",
            "--page",
            "1",
        ]
    ) == 0
    page = json.loads(capsys.readouterr().out)["data"]
    assert page["requested_pages"] == "1"
    assert page["content"]["text"] == "line one\nalpha evidence"

    assert main(
        [
            "--workspace",
            str(workspace),
            "cat",
            "/documents/notes.md",
            "--page",
            "1-6",
        ]
    ) == 2
    too_wide = json.loads(capsys.readouterr().out)
    assert "at most 5 pages" in too_wide["error"]["message"]

    assert main(
        ["--workspace", str(workspace), "grep", "alpha", "/documents"]
    ) == 2
    folder_grep = json.loads(capsys.readouterr().out)
    assert "resolved file locator" in folder_grep["error"]["message"]


@pytest.mark.parametrize(
    "command",
    [
        ["ls", "/"],
        ["find", "/"],
        ["browse", "/", "alpha", "--space", "entity"],
        ["browse", "/", "alpha", "--limit", "2"],
        ["stat", "/", "--schema"],
        ["stat", "/", "--field", "year"],
        ["stat", "/one", "/two"],
        ["cat", "/document"],
        ["cat", "/document", "--all"],
        ["cat", "/document", "--range", "1-2"],
        ["grep", "alpha", "/document", "--recursive"],
    ],
)
def test_retired_command_forms_return_structured_errors(command, tmp_path, capsys):
    from pageindex.filesystem.cli import main

    status = main(["--workspace", str(tmp_path / "workspace"), *command])

    payload = json.loads(capsys.readouterr().out)
    assert status == 2
    assert payload["success"] is False
    assert payload["error"]["code"] == "invalid_command"


def test_set_workspace_persists_runtime_config_without_credentials_or_provider(
    tmp_path, monkeypatch, capsys
):
    from pageindex.filesystem.cli import main

    config = tmp_path / "pifs.json"
    config.write_text(
        json.dumps(
            {
                "embedding_base_url": "https://example.invalid/v1",
                "embedding_model": "test-embedding",
                "embedding_dimensions": 3,
                "embedding_timeout": 12,
                "embedding_api_key": "must-not-survive",
                "embedding_provider": "legacy-provider",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PIFS_CONFIG_FILE", str(config))
    workspace = tmp_path / "workspace"

    assert main(["set", "workspace", str(workspace)]) == 0
    capsys.readouterr()

    assert json.loads(config.read_text(encoding="utf-8")) == {
        "embedding_base_url": "https://example.invalid/v1",
        "embedding_dimensions": "3",
        "embedding_model": "test-embedding",
        "embedding_timeout": "12",
        "workspace": str(workspace),
    }


def test_public_example_is_a_short_supported_cli_walkthrough():
    example = Path(__file__).parents[1] / "examples" / "pifs_demo.py"
    source = example.read_text(encoding="utf-8")

    assert len(source.splitlines()) <= 60
    for command in ("add", "tree", "browse", "stat", "cat", "grep"):
        assert f'"{command}"' in source
    for retired_surface in (
        "embedding_provider",
        "metadata_provider",
        "file_ref",
        "SummaryProjectionIndexer",
        "shutil.rmtree",
        '"ls"',
    ):
        assert retired_surface not in source
