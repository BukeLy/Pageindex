import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def register_metadata_schema(filesystem, fields):
    filesystem._register_metadata_schema({"fields": fields})


class EnterpriseRAGFileSystemTest(unittest.TestCase):
    def test_filesystem_import_does_not_require_core_dependencies(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = (
            "import pageindex.filesystem as filesystem; "
            "from pageindex.filesystem import PageIndexFileSystem; "
            "print(PageIndexFileSystem.__name__, hasattr(filesystem, 'EnterpriseRAGBenchmark'))"
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            text=True,
            capture_output=True,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("PageIndexFileSystem", result.stdout)
        self.assertIn("False", result.stdout)

    def test_enterprise_rag_json_ingests_with_folder_metadata_search_and_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_root = tmp_path / "generated_data" / "sources"
            source_file = source_root / "github" / "redwood" / "pr-56247-audit.json"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                json.dumps(
                    {
                        "repo": "redwood",
                        "pr_number": "56247",
                        "title": "Emit audit event on successful offline bundle verification",
                        "author": "Logan Wright",
                        "state": "merged",
                        "labels": ["security", "audit-logging", "private"],
                        "description": (
                            "Add private.bundle_verification.succeeded so offline "
                            "bundle verification has a positive audit trail."
                        ),
                        "review_conversation": (
                            "Reviewers asked to avoid absolute bundle paths and "
                            "require signer_key_id."
                        ),
                        "title_field_name": "title",
                        "content_field_names": ["description", "review_conversation"],
                        "dataset_doc_uuid": "dsid_37f88dfdbafb44ec9bf65db178c33dc4",
                    }
                ),
                encoding="utf-8",
            )

            from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import EnterpriseRAGBenchmark
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")
            benchmark = EnterpriseRAGBenchmark(filesystem)

            refs = benchmark.ingest_sources(source_root)

            self.assertEqual(len(refs), 1)
            file_ref = refs[0]
            self.assertTrue(file_ref.startswith("file_"))

            results = filesystem.search("bundle verification audit", limit=5)

            self.assertEqual(len(results), 1)
            result = results[0]
            self.assertEqual(result.file_ref, file_ref)
            self.assertEqual(result.external_id, "dsid_37f88dfdbafb44ec9bf65db178c33dc4")
            self.assertEqual(result.folder_path, "/github/redwood")
            self.assertEqual(result.metadata["repo"], "redwood")
            self.assertEqual(result.metadata["state"], "merged")
            self.assertIn("audit-logging", result.metadata["labels"])

            opened = filesystem.open(result.reference_id, "1-4")

            self.assertEqual(opened.file_ref, file_ref)
            self.assertEqual(opened.start_line, 1)
            self.assertEqual(opened.end_line, 4)
            self.assertIn("private.bundle_verification.succeeded", opened.text)

    def test_register_file_does_not_infer_metadata_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem
            from pageindex.filesystem.metadata import MetadataQueryError

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            filesystem.register_file(
                storage_uri="file:///tmp/doc.json",
                source_path="github/redwood/doc.json",
                folder_path="/github/redwood",
                external_id="dsid_no_infer",
                title="No inferred schema",
                metadata={"repo": "redwood", "state": "merged"},
                content="raw metadata remains available in stat and search text",
            )

            schema = json.loads(
                PIFSCommandExecutor(filesystem, json_output=True).execute("stat --schema /")
            )["data"]
            stat = json.loads(
                PIFSCommandExecutor(filesystem, json_output=True).execute("stat dsid_no_infer")
            )["data"]

            self.assertEqual(schema["fields"], {})
            self.assertEqual(stat["metadata"]["repo"], "redwood")
            with self.assertRaises(MetadataQueryError):
                filesystem.search(None, metadata_filter={"repo": "redwood"})

    def test_search_filters_by_folder_scope_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_root = tmp_path / "generated_data" / "sources"
            github_doc = source_root / "github" / "redwood" / "pr-audit.json"
            slack_doc = source_root / "slack" / "eng" / "audit-thread.json"
            github_doc.parent.mkdir(parents=True)
            slack_doc.parent.mkdir(parents=True)
            github_doc.write_text(
                json.dumps(
                    {
                        "repo": "redwood",
                        "title": "Audit logging bundle verification PR",
                        "state": "merged",
                        "labels": ["security", "audit-logging"],
                        "description": "Audit logging for offline bundle verification.",
                        "title_field_name": "title",
                        "content_field_names": ["description"],
                        "dataset_doc_uuid": "dsid_github_audit",
                    }
                ),
                encoding="utf-8",
            )
            slack_doc.write_text(
                json.dumps(
                    {
                        "channel": "eng",
                        "title": "Audit logging rollout discussion",
                        "participants": ["maya", "logan"],
                        "messages": "Audit logging rollout notes for a different source.",
                        "title_field_name": "title",
                        "content_field_names": ["messages"],
                        "dataset_doc_uuid": "dsid_slack_audit",
                    }
                ),
                encoding="utf-8",
            )

            from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import EnterpriseRAGBenchmark
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")
            register_metadata_schema(
                filesystem,
                {
                    "repo": {"type": "string"},
                    "labels": {"type": "string"},
                },
            )
            EnterpriseRAGBenchmark(filesystem).ingest_sources(source_root)

            results = filesystem.search(
                "audit logging",
                scope={"folder_path": "/github", "recursive": True},
                metadata_filter={
                    "$and": [
                        {"repo": "redwood"},
                        {"labels": {"$eq": "audit-logging"}},
                    ],
                },
                limit=10,
            )

            self.assertEqual([result.external_id for result in results], ["dsid_github_audit"])

    def test_enterprise_rag_questions_and_answer_jsonl_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            questions_path = tmp_path / "questions.jsonl"
            answers_path = tmp_path / "answers.jsonl"
            questions_path.write_text(
                json.dumps(
                    {
                        "question_id": "qst_0001",
                        "question_type": "basic",
                        "source_types": ["github"],
                        "question": "Which PR added audit logging?",
                        "expected_doc_ids": ["dsid_github_audit"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import EnterpriseRAGBenchmark
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")
            benchmark = EnterpriseRAGBenchmark(filesystem)

            questions = benchmark.load_questions(questions_path)

            self.assertEqual(len(questions), 1)
            self.assertEqual(questions[0].question_id, "qst_0001")
            self.assertEqual(questions[0].question, "Which PR added audit logging?")

            benchmark.write_answers(
                answers_path,
                [
                    {
                        "question_id": questions[0].question_id,
                        "answer": "PR 56247 added audit logging.",
                        "document_ids": ["dsid_github_audit"],
                    }
                ],
            )

            rows = [
                json.loads(line)
                for line in answers_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                rows,
                [
                    {
                        "question_id": "qst_0001",
                        "answer": "PR 56247 added audit logging.",
                        "document_ids": ["dsid_github_audit"],
                    }
                ],
            )

    def test_search_handles_punctuation_heavy_enterprise_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            filesystem.register_file(
                storage_uri="file:///tmp/thread.json",
                source_path="slack/eng/auth-proxy-thread.json",
                folder_path="/slack/eng",
                external_id="dsid_auth_proxy",
                title="auth-proxy stream tool-call retry",
                metadata={"channel": "eng", "source_type": "slack"},
                content=(
                    "auth-proxy returned 401 during streaming+tool-call paths. "
                    "Tracking private.bundle_verification.succeeded separately."
                ),
            )

            results = filesystem.search("auth-proxy private.bundle_verification.succeeded")

            self.assertEqual([result.external_id for result in results], ["dsid_auth_proxy"])

    def test_search_falls_back_for_long_natural_language_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            filesystem.register_file(
                storage_uri="file:///tmp/pr.json",
                source_path="github/redwood/pr-upload.json",
                folder_path="/github/redwood",
                external_id="dsid_multipart_upload",
                title="Multipart upload defaults",
                metadata={"repo": "redwood", "source_type": "github"},
                content=(
                    "OpenAI compatible API endpoints accept multipart uploads. "
                    "max_file_size defaults to 10 MiB and "
                    "max_total_request_size defaults to 50 MiB."
                ),
            )

            results = filesystem.search(
                "What are the default size limits for file uploads and total "
                "request size for the new multipart upload support on the "
                "OpenAI-compatible API endpoints?",
            )

            self.assertEqual(
                [result.external_id for result in results],
                ["dsid_multipart_upload"],
            )

    def test_open_all_returns_full_leaf_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            file_ref = filesystem.register_file(
                storage_uri="file:///tmp/full.json",
                source_path="github/redwood/full.json",
                external_id="dsid_full_leaf",
                title="Full leaf document",
                content="\n".join(f"line {i}" for i in range(1, 121)),
            )

            opened = filesystem.open(file_ref, "all")

            self.assertEqual(opened.start_line, 1)
            self.assertEqual(opened.end_line, 120)
            self.assertIn("line 1", opened.text)
            self.assertIn("line 120", opened.text)

    def test_pifs_cat_uses_full_leaf_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            long_prefix = "prefix " * 1200
            answer_tail = "FINAL_ANSWER_AFTER_CONTEXT_WINDOW"
            filesystem.register_file(
                storage_uri="file:///tmp/full.json",
                source_path="github/redwood/full.json",
                external_id="dsid_full_context",
                title="Full context document",
                content=long_prefix + "\n" + answer_tail,
            )

            output = PIFSCommandExecutor(filesystem, json_output=True).execute("cat dsid_full_context --all")
            payload = json.loads(output)

            self.assertIn(answer_tail, payload["data"]["text"])

    def test_browse_and_search_drive_multifile_exploration(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_root = tmp_path / "generated_data" / "sources"
            github_doc = source_root / "github" / "redwood" / "pr-audit.json"
            slack_doc = source_root / "slack" / "eng" / "audit-rollout.json"
            github_doc.parent.mkdir(parents=True)
            slack_doc.parent.mkdir(parents=True)
            github_doc.write_text(
                json.dumps(
                    {
                        "repo": "redwood",
                        "title": "Audit logging bundle verification PR",
                        "state": "merged",
                        "labels": ["security", "audit-logging"],
                        "description": "Adds bundle verification audit event.",
                        "title_field_name": "title",
                        "content_field_names": ["description"],
                        "dataset_doc_uuid": "dsid_github_tree",
                    }
                ),
                encoding="utf-8",
            )
            slack_doc.write_text(
                json.dumps(
                    {
                        "channel": "eng",
                        "title": "Audit logging rollout discussion",
                        "participants": ["maya", "logan"],
                        "messages": "Rollout notes for audit logging.",
                        "title_field_name": "title",
                        "content_field_names": ["messages"],
                        "dataset_doc_uuid": "dsid_slack_tree",
                    }
                ),
                encoding="utf-8",
            )

            from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import EnterpriseRAGBenchmark
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")
            register_metadata_schema(
                filesystem,
                {
                    "repo": {"type": "string"},
                    "labels": {"type": "string"},
                },
            )
            EnterpriseRAGBenchmark(filesystem).ingest_sources(source_root)

            root = filesystem.browse("/")
            recursive = filesystem.browse("/github", recursive=True)
            filtered = filesystem.search(
                "audit logging",
                scope={"folder_path": "/github", "recursive": True},
                metadata_filter='{"$and":[{"repo":"redwood"},{"labels":"audit-logging"}]}',
                limit=5,
            )

            self.assertIn("/github", [folder["path"] for folder in root["folders"]])
            self.assertIn("/github/redwood", [folder["path"] for folder in recursive["folders"]])
            self.assertEqual(
                [candidate.external_id for candidate in filtered],
                ["dsid_github_tree"],
            )

    def test_pifs_command_executor_maps_bash_names_to_filesystem_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            register_metadata_schema(
                filesystem,
                {
                    "repo": {"type": "string"},
                    "labels": {"type": "string"},
                },
            )
            filesystem.register_file(
                storage_uri="file:///tmp/pr.json",
                source_path="github/redwood/pr-audit.json",
                folder_path="/github/redwood",
                external_id="dsid_cli_audit",
                title="Audit logging PR",
                metadata={"repo": "redwood", "labels": ["audit-logging"], "source_type": "github"},
                content="The PR adds audit logging for bundle verification.",
            )
            executor = PIFSCommandExecutor(filesystem, json_output=True)

            listing = json.loads(executor.execute("ls /"))
            found = json.loads(
                executor.execute(
                    'find /github --where \'{"$and":[{"repo":"redwood"},{"labels":"audit-logging"}]}\''
                )
            )
            grepped = json.loads(executor.execute('grep -R "bundle verification" /github'))
            file_grep = json.loads(
                executor.execute('grep "bundle verification" /github/redwood/pr-audit.json')
            )
            stat = json.loads(executor.execute("stat dsid_cli_audit"))
            opened = json.loads(executor.execute("cat dsid_cli_audit --all"))

            self.assertIn("/github", [folder["path"] for folder in listing["data"]["folders"]])
            self.assertEqual(found["data"][0]["external_id"], "dsid_cli_audit")
            self.assertEqual(grepped["data"][0]["external_id"], "dsid_cli_audit")
            self.assertEqual(file_grep["data"][0]["external_id"], "dsid_cli_audit")
            self.assertIn("bundle verification", file_grep["data"][0]["text"])
            self.assertEqual(stat["data"]["external_id"], "dsid_cli_audit")
            self.assertIn("audit logging", opened["data"]["text"])

    def test_pifs_command_executor_supports_safe_and_chains(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            filesystem.register_file(
                storage_uri="file:///tmp/pr.json",
                source_path="github/redwood/pr-audit.json",
                folder_path="/github/redwood",
                external_id="dsid_chain_audit",
                title="Audit logging PR",
                metadata={"repo": "redwood"},
                content="first line\nThe PR adds audit logging and literal alpha && beta wording.",
            )
            filesystem.register_file(
                storage_uri="file:///tmp/pr2.json",
                source_path="github/redwood/pr-audit-2.json",
                folder_path="/github/redwood",
                external_id="dsid_chain_audit_2",
                title="Audit logging follow-up",
                metadata={"repo": "redwood"},
                content="The follow-up adds audit logging export notes.",
            )
            executor = PIFSCommandExecutor(filesystem, json_output=True)

            chained = executor.execute('ls / && grep -R "audit logging" /github')
            listing, grepped = [json.loads(line) for line in chained.splitlines()]
            quoted = json.loads(executor.execute('grep -R "alpha && beta" /github'))
            piped_listing = json.loads(executor.execute("ls / | grep github"))
            headed = json.loads(executor.execute('grep -R "audit logging" /github | head -n 1'))
            sed = json.loads(executor.execute("cat dsid_chain_audit --all | sed -n '1,1p'"))

            self.assertIn("/github", [folder["path"] for folder in listing["data"]["folders"]])
            self.assertGreaterEqual(len(grepped["data"]), 2)
            self.assertEqual(quoted["data"][0]["external_id"], "dsid_chain_audit")
            self.assertEqual(piped_listing["data"]["folders"][0]["path"], "/github")
            self.assertEqual(len(headed["data"]), 1)
            self.assertEqual(sed["data"]["text"], "first line")

    def test_pifs_command_executor_allows_metadata_comparison_dsl(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            register_metadata_schema(filesystem, {"year": {"type": "number"}})
            filesystem.register_file(
                storage_uri="file:///tmp/report.json",
                source_path="finance/apple/report.json",
                folder_path="/finance/apple",
                external_id="dsid_year_2024",
                title="Apple 2024 report",
                metadata={"company": "Apple", "year": 2024},
                content="Apple 2024 report text.",
            )

            payload = json.loads(
                PIFSCommandExecutor(filesystem, json_output=True).execute(
                    'find /finance --where \'{"year":{"$gte":2024}}\''
                )
            )

            self.assertEqual(payload["data"][0]["external_id"], "dsid_year_2024")

    def test_metadata_filter_uses_compute_json_dsl_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            register_metadata_schema(
                filesystem,
                {
                    "repo": {"type": "string"},
                    "year": {"type": "number"},
                    "channel": {"type": "string"},
                    "private": {"type": "boolean"},
                },
            )
            filesystem.register_files(
                [
                    {
                        "storage_uri": "file:///tmp/a.json",
                        "source_path": "github/redwood/a.json",
                        "folder_path": "/github/redwood",
                        "external_id": "dsid_redwood_2024",
                        "title": "Redwood 2024",
                        "metadata": {"repo": "redwood", "year": 2024, "private": True},
                        "content": "metadata json dsl document",
                    },
                    {
                        "storage_uri": "file:///tmp/b.json",
                        "source_path": "github/redwood/b.json",
                        "folder_path": "/github/redwood",
                        "external_id": "dsid_redwood_2020",
                        "title": "Redwood 2020",
                        "metadata": {"repo": "redwood", "year": 2020, "private": False},
                        "content": "metadata json dsl document",
                    },
                    {
                        "storage_uri": "file:///tmp/c.json",
                        "source_path": "slack/eng/c.json",
                        "folder_path": "/slack/eng",
                        "external_id": "dsid_missing_repo",
                        "title": "No repo",
                        "metadata": {"channel": "eng", "year": 2025},
                        "content": "metadata json dsl document",
                    },
                ]
            )

            filtered = filesystem.search(
                None,
                metadata_filter={"$and": [{"repo": "redwood"}, {"year": {"$gte": 2024}}]},
                limit=10,
            )
            not_redwood = filesystem.search(None, metadata_filter={"repo": {"$ne": "redwood"}}, limit=10)
            either = filesystem.search(
                None,
                metadata_filter={"$or": [{"repo": "redwood"}, {"channel": "eng"}]},
                limit=10,
            )

            self.assertEqual([result.external_id for result in filtered], ["dsid_redwood_2024"])
            self.assertEqual([result.external_id for result in not_redwood], ["dsid_missing_repo"])
            self.assertEqual(
                {result.external_id for result in either},
                {"dsid_redwood_2024", "dsid_redwood_2020", "dsid_missing_repo"},
            )

    def test_metadata_filter_rejects_old_sql_like_dsl_and_unknown_operators(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem
            from pageindex.filesystem.metadata import MetadataQueryError

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            register_metadata_schema(filesystem, {"repo": {"type": "string"}})
            filesystem.register_file(
                storage_uri="file:///tmp/doc.json",
                source_path="github/redwood/doc.json",
                folder_path="/github/redwood",
                external_id="dsid_redwood",
                title="Redwood doc",
                metadata={"repo": "redwood"},
                content="metadata dsl",
            )

            with self.assertRaises(MetadataQueryError):
                filesystem.search(None, metadata_filter='repo = "redwood"')
            with self.assertRaises(MetadataQueryError):
                filesystem.search(None, metadata_filter={"repo": {"$contains": "red"}})
            with self.assertRaises(MetadataQueryError):
                filesystem.search(None, metadata_filter={"_repo": "redwood"})

    def test_browse_returns_chat_compatible_folder_and_document_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            filesystem.register_file(
                storage_uri="file:///tmp/doc.json",
                source_path="github/redwood/doc.json",
                folder_path="/github/redwood",
                external_id="dsid_browse",
                title="Browse doc",
                metadata={"repo": "redwood"},
                content="browse shape",
            )

            root = filesystem.browse("/")
            recursive = filesystem.browse("/github", recursive=True)
            leaf = filesystem.browse("/github/redwood")

            folder = root["folders"][0]
            redwood_folder = next(item for item in recursive["folders"] if item["path"] == "/github/redwood")
            doc = leaf["files"][0]
            self.assertIn("id", folder)
            self.assertIn("parent_folder_id", folder)
            self.assertIn("file_count", folder)
            self.assertIn("children_count", folder)
            self.assertEqual(doc["id"], "dsid_browse")
            self.assertEqual(doc["name"], "Browse doc")
            self.assertEqual(doc["folderId"], redwood_folder["id"])

    def test_pifs_command_executor_rejects_real_shell_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem
            from pageindex.filesystem.commands import PIFSCommandError

            executor = PIFSCommandExecutor(PageIndexFileSystem(workspace=Path(tmp) / "workspace"))

            with self.assertRaises(PIFSCommandError):
                executor.execute("rm -rf /")
            with self.assertRaises(PIFSCommandError):
                executor.execute("ls / | cat")
            with self.assertRaises(PIFSCommandError):
                executor.execute("ls / & cat")
            with self.assertRaises(PIFSCommandError):
                executor.execute("ls / || cat")

    def test_pifs_cli_module_outputs_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            repo_root = Path(__file__).resolve().parents[1]
            workspace = Path(tmp) / "workspace"
            filesystem = PageIndexFileSystem(workspace=workspace)
            filesystem.register_file(
                storage_uri="file:///tmp/doc.json",
                source_path="github/redwood/doc.json",
                folder_path="/github/redwood",
                external_id="dsid_cli_module",
                title="CLI module doc",
                metadata={"repo": "redwood"},
                content="CLI module smoke content",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pageindex.filesystem.cli",
                    "--workspace",
                    str(workspace),
                    "--json",
                    "grep",
                    "-R",
                    "smoke content",
                    "/github",
                ],
                cwd=repo_root,
                text=True,
                capture_output=True,
            )

            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["data"][0]["external_id"], "dsid_cli_module")

    def test_reopen_workspace_keeps_migrated_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            workspace = Path(tmp) / "workspace"
            first = PageIndexFileSystem(workspace=workspace)
            register_metadata_schema(first, {"repo": {"type": "string"}})
            first.register_file(
                storage_uri="file:///tmp/doc.json",
                source_path="github/redwood/doc.json",
                folder_path="/github/redwood",
                external_id="dsid_reopen",
                title="Reopen doc",
                metadata={"repo": "redwood"},
                content="catalog survives reopen",
            )

            second = PageIndexFileSystem(workspace=workspace)
            results = second.search("catalog", metadata_filter='{"repo":"redwood"}')
            from pageindex.filesystem import PIFSCommandExecutor

            schema = json.loads(
                PIFSCommandExecutor(second, json_output=True).execute("stat --schema /")
            )["data"]

            self.assertEqual([result.external_id for result in results], ["dsid_reopen"])
            self.assertIn("repo", schema["fields"])
            self.assertEqual(schema["fields"]["repo"]["type"], "string")

    def test_legacy_workspace_with_folder_path_column_can_still_register(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            with sqlite3.connect(workspace / "filesystem.sqlite") as conn:
                conn.execute(
                    """
                    CREATE TABLE files (
                        file_ref TEXT PRIMARY KEY,
                        external_id TEXT,
                        storage_uri TEXT NOT NULL,
                        source_path TEXT NOT NULL,
                        title TEXT NOT NULL,
                        descriptor TEXT NOT NULL,
                        content_type TEXT NOT NULL,
                        source_type TEXT,
                        fingerprint TEXT NOT NULL,
                        text_artifact_path TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        folder_path TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE folders (
                        path TEXT PRIMARY KEY,
                        kind TEXT NOT NULL DEFAULT 'physical',
                        source TEXT NOT NULL DEFAULT 'source'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE file_fts
                    USING fts5(file_ref UNINDEXED, title, body, metadata_text)
                    """
                )

            filesystem = PageIndexFileSystem(workspace=workspace)
            register_metadata_schema(filesystem, {"repo": {"type": "string"}})
            filesystem.register_file(
                storage_uri="file:///tmp/new.json",
                source_path="github/redwood/new.json",
                folder_path="/github/redwood",
                external_id="dsid_legacy_insert",
                title="Legacy insert",
                metadata={"repo": "redwood"},
                content="legacy schema insert works",
            )

            results = filesystem.search("legacy", metadata_filter='{"repo":"redwood"}')
            self.assertEqual([result.external_id for result in results], ["dsid_legacy_insert"])


if __name__ == "__main__":
    unittest.main()
