import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class EnterpriseRAGFileSystemTest(unittest.TestCase):
    def test_filesystem_import_does_not_require_core_dependencies(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = (
            "from pageindex.filesystem import PageIndexFileSystem, EnterpriseRAGBenchmark; "
            "print(PageIndexFileSystem.__name__, EnterpriseRAGBenchmark.__name__)"
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
        self.assertIn("EnterpriseRAGBenchmark", result.stdout)

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

            from pageindex.filesystem import EnterpriseRAGBenchmark, PageIndexFileSystem

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

            from pageindex.filesystem import EnterpriseRAGBenchmark, PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")
            EnterpriseRAGBenchmark(filesystem).ingest_sources(source_root)

            results = filesystem.search(
                "audit logging",
                scope={"folder_path": "/github", "recursive": True},
                metadata_filter={
                    "repo": {"$eq": "redwood"},
                    "labels": {"$contains": "audit-logging"},
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

            from pageindex.filesystem import EnterpriseRAGBenchmark, PageIndexFileSystem

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

    def test_enterprise_rag_context_uses_full_leaf_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            from examples.enterprise_rag_benchmark.run_smoke import build_context
            from pageindex.filesystem import PageIndexFileSystem

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

            candidate = filesystem.search("prefix", limit=1)[0]
            context = build_context(filesystem, [candidate], max_docs=1)

            self.assertIn(answer_tail, context)

    def test_tree_search_uses_folder_and_virtual_nodes_before_leaf_files(self):
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

            from pageindex.filesystem import EnterpriseRAGBenchmark, PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")
            EnterpriseRAGBenchmark(filesystem).ingest_sources(source_root)

            traversal = filesystem.tree_search("redwood audit logging", limit=5)

            self.assertIn("/github/redwood", [node.path for node in traversal.nodes])
            self.assertIn("/metadata/repo/redwood", [node.path for node in traversal.nodes])
            self.assertIn("/metadata/labels/audit-logging", [node.path for node in traversal.nodes])
            self.assertEqual(
                [candidate.external_id for candidate in traversal.candidates],
                ["dsid_github_tree"],
            )


if __name__ == "__main__":
    unittest.main()
