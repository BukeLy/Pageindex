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

    def test_semantic_scope_parses_source_type_facet_folder(self):
        from pageindex.filesystem import PageIndexFileSystem

        self.assertEqual(
            PageIndexFileSystem._semantic_filters_for_scope(
                {"folder_path": "/source_type=google-drive", "recursive": True}
            ),
            {"source_type": "google_drive"},
        )
        self.assertEqual(
            PageIndexFileSystem._semantic_filters_for_scope(
                {
                    "folder_path": "/semantic/source_type=github/facets/doc_type=pull-request",
                    "recursive": True,
                }
            ),
            {"source_type": "github"},
        )

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

    def test_enterprise_rag_agent_prompt_supports_retrieval_modes(self):
        from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import EnterpriseRAGQuestion
        from examples.Benchmark.enterprise_rag_benchmark.run_enterprise_rag_pifs_agent import (
            agent_prompt,
        )

        question = EnterpriseRAGQuestion(
            question_id="qst_test",
            question_type="basic",
            source_types=["github"],
            question="Which doc mentions audit logging?",
            expected_doc_ids=["dsid_audit"],
        )

        folder_prompt = agent_prompt(question, retrieval_mode="folder")
        metadata_prompt = agent_prompt(question, retrieval_mode="metadata")
        hybrid_prompt = agent_prompt(question, retrieval_mode="hybrid")

        self.assertIn("Retrieval mode: folder", folder_prompt)
        self.assertIn("Do not use `find --where`", folder_prompt)
        self.assertIn("Retrieval mode: metadata", metadata_prompt)
        self.assertIn("Start with `stat --schema /`", metadata_prompt)
        self.assertIn("Do not use `ls`, `tree`, or `grep -R <query> <folder>`", metadata_prompt)
        self.assertIn("Retrieval mode: hybrid", hybrid_prompt)
        self.assertIn("find <path> -type d --where", hybrid_prompt)

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

    def test_register_file_without_folder_path_mounts_at_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            file_ref = filesystem.register_file(
                storage_uri="file:///tmp/root.json",
                source_path="github/redwood/root.json",
                external_id="dsid_root_mount",
                title="Root mounted document",
                content="root mounted content",
            )

            root = filesystem.browse("/")
            results = filesystem.search("root mounted", scope={"folder_path": "/"})
            opened = filesystem.open(file_ref)

            self.assertEqual([doc["external_id"] for doc in root["files"]], ["dsid_root_mount"])
            self.assertEqual([result.external_id for result in results], ["dsid_root_mount"])
            self.assertEqual(opened.folder_path, "/")

    def test_file_can_be_attached_to_multiple_folders_and_searched_from_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            file_ref = filesystem.register_file(
                storage_uri="file:///tmp/multipart.json",
                source_path="github/redwood/multipart.json",
                folder_path="/docs/github",
                external_id="dsid_multi_folder",
                title="Multipart upload limits",
                content="multipart upload limit enforcement prevents memory pressure",
            )
            semantic_one = filesystem.create_folder(
                "/semantic/api/file-upload",
                kind="semantic",
                description="API file upload behavior",
                metadata={"generated_by": "test"},
            )
            semantic_two = filesystem.create_folder(
                "/semantic/problem/memory-pressure",
                kind="semantic",
            )
            filesystem.attach_file_to_folder(
                file_ref,
                semantic_one,
                metadata={"note": "topic membership"},
            )
            filesystem.attach_file_to_folder(file_ref, "/semantic/problem/memory-pressure")

            by_topic = filesystem.search(
                "multipart upload",
                scope={"folder_path": "/semantic/api"},
                limit=10,
            )
            by_problem = filesystem.search(
                "memory pressure",
                scope={"folder_path": "/semantic/problem"},
                limit=10,
            )
            docs_listing = filesystem.browse("/docs/github")
            semantic_listing = filesystem.browse("/semantic/api/file-upload")
            semantic_root = filesystem.browse("/semantic", recursive=True)

            self.assertEqual([result.external_id for result in by_topic], ["dsid_multi_folder"])
            self.assertEqual([result.external_id for result in by_problem], ["dsid_multi_folder"])
            self.assertEqual(docs_listing["files"][0]["file_ref"], file_ref)
            self.assertEqual(semantic_listing["files"][0]["file_ref"], file_ref)
            semantic_problem = next(
                folder
                for folder in semantic_root["folders"]
                if folder["path"] == "/semantic/problem/memory-pressure"
            )
            self.assertEqual(semantic_problem["kind"], "semantic")

    def test_semantic_folder_projection_is_explicit_and_coexists_with_registered_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            file_ref = filesystem.register_file(
                storage_uri="file:///tmp/pr.json",
                source_path="github/redwood/pr.json",
                folder_path="/docs/github",
                external_id="dsid_projection",
                title="Projection doc",
                metadata={"doc_type": "pull-request"},
                content="semantic folder projection keeps explicit folders",
            )

            projection = filesystem.apply_semantic_folder_projection(
                {
                    "policy": {
                        "allowed_base_fields": ["doc_type", "domain", "topic"],
                        "allowed_extension_fields": [],
                        "forbidden_fields": ["summary", "entities", "dataset_doc_uuid", "path", "uri"],
                    },
                    "folders": [
                        {
                            "path": "/semantic/source_type=github",
                            "kind": "source_root",
                            "field": "source_type",
                            "value": "github",
                        },
                        {
                            "path": "/semantic/source_type=github/facets/doc_type=pull-request",
                            "kind": "facet",
                            "field": "doc_type",
                            "value": "pull-request",
                        },
                    ],
                    "memberships": [
                        {
                            "file_key": file_ref,
                            "folder_path": "/semantic/source_type=github",
                            "field": "source_type",
                            "value": "github",
                            "mount_kind": "source_root",
                        },
                        {
                            "file_key": file_ref,
                            "folder_path": "/semantic/source_type=github/facets/doc_type=pull-request",
                            "field": "doc_type",
                            "value": "pull-request",
                            "mount_kind": "multimount",
                        },
                    ],
                }
            )

            stat = filesystem._stat(file_ref)
            explicit_results = filesystem.search(
                "explicit folders",
                scope={"folder_path": "/docs/github"},
            )
            semantic_results = filesystem.search(
                "explicit folders",
                scope={"folder_path": "/semantic/source_type=github", "recursive": True},
            )

            self.assertEqual(projection["projection"], "Semantic Folder Projection")
            self.assertEqual(projection["memberships_attached"], 2)
            self.assertEqual(
                {folder["path"] for folder in stat["folders"]},
                {
                    "/docs/github",
                    "/semantic/source_type=github",
                    "/semantic/source_type=github/facets/doc_type=pull-request",
                },
            )
            self.assertEqual([result.external_id for result in explicit_results], ["dsid_projection"])
            self.assertEqual([result.external_id for result in semantic_results], ["dsid_projection"])

    def test_semantic_folder_projection_rejects_forbidden_product_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            filesystem.register_file(
                storage_uri="file:///tmp/pr.json",
                source_path="github/redwood/pr.json",
                external_id="dsid_forbidden_projection",
                title="Forbidden projection doc",
                content="forbidden fields should not project",
            )

            with self.assertRaisesRegex(ValueError, "Semantic Folder Projection"):
                filesystem.apply_semantic_folder_projection(
                {
                    "policy": {"allowed_extension_fields": []},
                    "folders": [
                        {
                            "path": "/semantic/summary=memory-pressure",
                            "kind": "facet",
                            "value": "memory pressure",
                        }
                    ],
                    "memberships": [
                        {
                            "file_key": "file_forbidden_projection",
                            "folder_path": "/semantic/summary=memory-pressure",
                            "value": "memory pressure",
                        }
                    ],
                }
            )

    def test_semantic_folder_projection_rejects_forbidden_payload_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            file_ref = filesystem.register_file(
                storage_uri="file:///tmp/pr.json",
                source_path="github/redwood/pr.json",
                external_id="dsid_forbidden_payload",
                title="Forbidden payload doc",
                content="forbidden metadata payload should not persist",
            )

            plans = [
                {
                    "folders": [
                        {
                            "path": "/semantic/source_type=github",
                            "kind": "source_root",
                            "field": "source_type",
                            "value": "github",
                            "metadata": {"summary": "leaked summary"},
                        }
                    ],
                    "memberships": [],
                },
                {
                    "folders": [
                        {
                            "path": "/semantic/source_type=github",
                            "kind": "source_root",
                            "field": "source_type",
                            "value": "github",
                        }
                    ],
                    "memberships": [
                        {
                            "file_key": file_ref,
                            "folder_path": "/semantic/source_type=github",
                            "field": "source_type",
                            "value": "github",
                            "folder_metadata": {"source_path": "github/redwood/pr.json"},
                        }
                    ],
                },
            ]
            for plan in plans:
                with self.subTest(plan=plan):
                    with self.assertRaisesRegex(ValueError, "Semantic Folder Projection"):
                        filesystem.apply_semantic_folder_projection(plan)

    def test_semantic_folder_projection_builder_excludes_forbidden_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            from examples.Benchmark.enterprise_rag_benchmark.semantic_metadata_pipeline.pipeline_common import (
                semantic_folder_file_key,
            )
            from pageindex.filesystem import PageIndexFileSystem

            repo_root = Path(__file__).resolve().parents[1]
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            rows = []
            for index, (doc_type, topic, project) in enumerate(
                [
                    ("pull-request", "api", "redwood"),
                    ("pull-request", "api", "redwood"),
                    ("incident", "audit", "maple"),
                    ("incident", "audit", "maple"),
                ],
                1,
            ):
                rows.append(
                    {
                        "dataset_doc_uuid": f"dsid_projection_{index}",
                        "system": {
                            "source_type": "github",
                            "source_path": f"github/redwood/{index}.json",
                            "storage_uri": f"file:///tmp/{index}.json",
                        },
                        "metadata_base": {
                            "doc_type": doc_type,
                            "domain": "engineering",
                            "topic": topic,
                            "summary": "forbidden summary value",
                            "entities": ["forbidden entity"],
                            "relations": ["forbidden relation"],
                            "constraints": ["forbidden constraint"],
                            "retrieval_cues": ["forbidden cue"],
                        },
                        "extension_candidates": {
                            "project": project,
                            "dataset_doc_uuid": "dsid_forbidden_value",
                            "path": "/forbidden/path",
                            "uri": "https://example.invalid/forbidden",
                            "summary": "forbidden extension summary",
                            "retrieval_cues": "forbidden extension cue",
                        },
                        "provenance": {},
                    }
                )
            (run_dir / "metadata.normalized.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            (run_dir / "extension_schema.json").write_text(
                json.dumps(
                    {
                        "fields": [
                            {"name": "project", "suitable_for_folder": True},
                            {"name": "summary", "suitable_for_folder": True},
                            {"name": "entities", "suitable_for_folder": True},
                            {"name": "relations", "suitable_for_folder": True},
                            {"name": "constraints", "suitable_for_folder": True},
                            {"name": "retrieval_cues", "suitable_for_folder": True},
                            {"name": "dataset_doc_uuid", "suitable_for_folder": True},
                            {"name": "path", "suitable_for_folder": True},
                            {"name": "uri", "suitable_for_folder": True},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(
                        repo_root
                        / "examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/build_folders.py"
                    ),
                    "--run-dir",
                    str(run_dir),
                    "--mode",
                    "both",
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
            plan = json.loads((run_dir / "folder_plan.json").read_text(encoding="utf-8"))
            forbidden = {
                "summary",
                "entities",
                "relations",
                "constraints",
                "retrieval_cues",
                "dataset_doc_uuid",
                "path",
                "uri",
            }
            plan_fields = (
                set(plan["selected_fields"])
                | {folder.get("field") for folder in plan["folders"]}
                | {membership.get("field") for membership in plan["memberships"]}
            )
            plan_paths = json.dumps(
                [folder["path"] for folder in plan["folders"]]
                + [membership["folder_path"] for membership in plan["memberships"]],
                ensure_ascii=False,
            )
            plan_memberships = json.dumps(plan["memberships"], ensure_ascii=False)

            self.assertEqual(plan["name"], "Semantic Folder Projection")
            self.assertEqual(plan["root"], "/semantic")
            self.assertIn("project", plan["selected_fields"])
            self.assertFalse(forbidden & plan_fields)
            self.assertTrue(all("file_key" in membership for membership in plan["memberships"]))
            self.assertTrue(all("dataset_doc_uuid" not in membership for membership in plan["memberships"]))
            self.assertTrue(all(folder["path"].startswith("/semantic") for folder in plan["folders"]))
            self.assertNotIn("dataset_doc_uuid", plan_memberships)
            self.assertNotIn("dsid_projection_", plan_memberships)
            self.assertNotIn("dsid_forbidden_value", plan_paths)
            self.assertNotIn("/forbidden/path", plan_paths)
            self.assertNotIn("example.invalid", plan_paths)
            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            file_ref_by_key = {}
            for row in rows:
                file_ref = filesystem.register_file(
                    storage_uri=row["system"]["storage_uri"],
                    source_path=row["system"]["source_path"],
                    external_id=row["dataset_doc_uuid"],
                    title=row["dataset_doc_uuid"],
                    content="materialized folder plan doc",
                )
                file_ref_by_key[semantic_folder_file_key(row["dataset_doc_uuid"])] = file_ref
            projection = filesystem.apply_semantic_folder_projection(
                plan,
                file_ref_by_document_id=file_ref_by_key,
            )
            self.assertGreater(projection["memberships_attached"], 0)

    def test_stat_returns_all_folder_memberships_with_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            file_ref = filesystem.register_file(
                storage_uri="file:///tmp/stat.json",
                source_path="github/redwood/stat.json",
                folder_path="/docs/github",
                external_id="dsid_stat_memberships",
                title="Stat memberships",
                content="stat membership content",
            )
            filesystem.create_folder("/semantic/topic/audit", kind="semantic")
            filesystem.attach_file_to_folder(
                file_ref,
                "/semantic/topic/audit",
                metadata={"why": "semantic match"},
            )

            stat = filesystem._stat(file_ref)

            self.assertEqual(stat["external_id"], "dsid_stat_memberships")
            self.assertEqual(
                {folder["path"] for folder in stat["folders"]},
                {"/docs/github", "/semantic/topic/audit"},
            )
            semantic = next(folder for folder in stat["folders"] if folder["path"] == "/semantic/topic/audit")
            self.assertEqual(semantic["metadata"], {"why": "semantic match"})

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
            leaf_grepped = json.loads(executor.execute('grep -R "bundle verification" /github/redwood'))
            file_grep = json.loads(
                executor.execute('grep "bundle verification" /github/redwood/pr-audit.json')
            )
            stat = json.loads(executor.execute("stat dsid_cli_audit"))
            opened = json.loads(executor.execute("cat dsid_cli_audit --all"))

            self.assertIn("/github", [folder["path"] for folder in listing["data"]["folders"]])
            self.assertEqual(found["data"][0]["external_id"], "dsid_cli_audit")
            self.assertEqual(grepped["data"]["mode"], "folders")
            self.assertEqual(grepped["data"]["data"][0]["path"], "/github/redwood")
            self.assertEqual(leaf_grepped["data"]["mode"], "files")
            self.assertEqual(leaf_grepped["data"]["data"][0]["external_id"], "dsid_cli_audit")
            self.assertEqual(file_grep["data"]["mode"], "matches")
            self.assertEqual(file_grep["data"]["data"][0]["external_id"], "dsid_cli_audit")
            self.assertIn("bundle verification", file_grep["data"]["data"][0]["text"])
            self.assertEqual(stat["data"]["external_id"], "dsid_cli_audit")
            self.assertIn("audit logging", opened["data"]["text"])

    def test_pifs_command_executor_supports_semantic_channel_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem
            from pageindex.filesystem.hybrid_projection import (
                HybridProjectionSearchBackend,
                INDEX_BY_CHANNEL,
            )
            from pageindex.filesystem.semantic_index import (
                SQLiteVecSemanticIndex,
                SemanticIndexRecord,
            )

            class StaticEmbedder:
                def embed(self, texts):
                    return [[1.0, 0.0, 0.0] for _ in texts]

            tmp_path = Path(tmp)
            filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")
            docs = {
                "summary": (
                    "dsid_summary_semantic",
                    "Summary channel doc",
                    "The summary text covers retention evidence.",
                ),
                "entity": (
                    "dsid_entity_semantic",
                    "Entity channel doc",
                    "The AuditService entity owns the retention evidence.",
                ),
                "relation": (
                    "dsid_relation_semantic",
                    "Relation channel doc",
                    "AuditService emits the audit event relation for retention evidence.",
                ),
            }
            for channel, (doc_id, title, content) in docs.items():
                filesystem.register_file(
                    storage_uri=f"file:///tmp/{channel}.json",
                    source_path=f"github/redwood/{channel}.json",
                    folder_path="/github/redwood",
                    external_id=doc_id,
                    title=title,
                    metadata={"source_type": "github"},
                    content=content,
                )

            index_dir = tmp_path / "projection-index"
            for channel in ("summary", "entity", "relation"):
                doc_id, title, content = docs[channel]
                index = SQLiteVecSemanticIndex(index_dir / f"{INDEX_BY_CHANNEL[channel]}.sqlite")
                index.reset(dimension=3, metadata={"channel": channel})
                index.upsert_many(
                    [
                        SemanticIndexRecord(
                            file_ref=doc_id,
                            external_id=doc_id,
                            source_type="github",
                            source_path=f"github/redwood/{channel}.json",
                            title=title,
                            text=content,
                            vector=[1.0, 0.0, 0.0],
                            metadata={"source_type": "github"},
                        )
                    ]
                )
            filesystem.semantic_retrieval_backend = HybridProjectionSearchBackend(
                index_dir,
                embedder=StaticEmbedder(),
                embedding_provider="test",
                embedding_model="static",
                embedding_dimensions=3,
                embedding_cache_path=tmp_path / "embedding_cache.sqlite",
                fetch_multiplier=1,
            )
            executor = PIFSCommandExecutor(
                filesystem,
                json_output=True,
                query_context="Which audit retention evidence should be opened?",
            )

            summary = json.loads(executor.execute('search-summary "retention evidence" /github'))
            entity = json.loads(executor.execute('search-entity "AuditService" /github'))
            relation = json.loads(
                executor.execute('search-relation "AuditService emits audit event" /github')
            )
            grep_alias = json.loads(executor.execute('grep -R "retention evidence" /github/redwood'))
            grep_no_match = json.loads(executor.execute('grep -R "unmentioned exact phrase" /github/redwood'))
            semantic_grep = json.loads(executor.execute('semantic-grep -R "retention evidence" /github'))
            semantic_grep_no_match = json.loads(
                executor.execute('semantic-grep -R "unmentioned exact phrase" /github')
            )
            find_entity_alias = json.loads(executor.execute('find /github --name "AuditService"'))
            find_relation_alias = json.loads(
                executor.execute('find /github --relation "AuditService emits audit event"')
            )

            self.assertEqual(summary["data"]["retrieval"], "summary_vector")
            self.assertEqual(summary["data"]["data"][0]["external_id"], "dsid_summary_semantic")
            self.assertEqual(entity["data"]["retrieval"], "entity_vector")
            self.assertEqual(entity["data"]["data"][0]["external_id"], "dsid_entity_semantic")
            self.assertEqual(relation["data"]["retrieval"], "relation_vector")
            self.assertEqual(relation["data"]["data"][0]["external_id"], "dsid_relation_semantic")
            self.assertNotIn("retrieval", grep_alias["data"])
            self.assertTrue(grep_alias["data"]["data"])
            self.assertIn("retention evidence", grep_alias["data"]["data"][0]["text"])
            self.assertEqual(grep_no_match["data"]["data"], [])
            self.assertEqual(semantic_grep["data"]["retrieval"], "semantic_grep_entity_then_relation")
            self.assertEqual(semantic_grep["data"]["candidate_limit_per_channel"], 20)
            self.assertEqual(semantic_grep["data"]["matched_channel"], "entity")
            self.assertEqual(semantic_grep["data"]["data"][0]["external_id"], "dsid_entity_semantic")
            self.assertIn("retention evidence", semantic_grep["data"]["data"][0]["text"])
            self.assertEqual(semantic_grep_no_match["data"]["retrieval"], "semantic_grep_entity_then_relation")
            self.assertEqual(semantic_grep_no_match["data"]["data"], [])
            self.assertIn("candidate_debug", semantic_grep_no_match["data"])
            self.assertEqual(find_entity_alias["data"][0]["external_id"], "dsid_entity_semantic")
            self.assertEqual(find_relation_alias["data"][0]["external_id"], "dsid_relation_semantic")

    def test_pifs_command_executor_defaults_to_shell_like_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            filesystem.create_folder("/semantic/topics/api-input")
            filesystem.register_file(
                storage_uri="file:///tmp/pr.json",
                source_path="github/pr-18421.json",
                folder_path="/semantic/topics/api-input",
                external_id="dsid_shell_text",
                title="Multipart upload limits",
                metadata={
                    "semantic_topics": ["multipart upload", "api input"],
                    "long_note": "metadata must not be printed by ls or tree",
                },
                content="line one\nmax_file_size defaults to 10MiB\nprivate full document tail",
            )
            executor = PIFSCommandExecutor(filesystem)

            listing = executor.execute("ls /semantic")
            tree = executor.execute("tree /semantic")
            stat = executor.execute("stat dsid_shell_text")
            content = executor.execute("cat dsid_shell_text --all")
            headed = executor.execute("head -2 dsid_shell_text")
            tailed = executor.execute("tail -1 dsid_shell_text")
            sed_range = executor.execute("sed -n '2,2p' dsid_shell_text")
            head_tail = executor.execute("head -3 dsid_shell_text | tail -1")
            mkdir = executor.execute("mkdir /manual/new")
            copied = executor.execute(
                'cp file:///tmp/copied.txt /manual/new --content "copy body" '
                '--external-id dsid_copied --title "Copied doc"'
            )

            self.assertFalse(listing.lstrip().startswith("{"))
            self.assertIn("topics/", listing)
            self.assertIn("folders=", listing)
            self.assertIn("/semantic", tree)
            self.assertIn("api-input/", tree)
            self.assertNotIn("file_", tree)
            self.assertNotIn("metadata must not be printed", listing)
            self.assertNotIn("metadata must not be printed", tree)
            self.assertIn("document_id: dsid_shell_text", stat)
            self.assertIn("/semantic/topics/api-input", stat)
            self.assertNotIn("private full document tail", stat)
            self.assertEqual(content, "line one\nmax_file_size defaults to 10MiB\nprivate full document tail")
            self.assertEqual(headed, "line one\nmax_file_size defaults to 10MiB")
            self.assertEqual(tailed, "private full document tail")
            self.assertEqual(sed_range, "max_file_size defaults to 10MiB")
            self.assertEqual(head_tail, "private full document tail")
            self.assertEqual(mkdir, "created folder: /manual/new")
            self.assertIn("copied file:", copied)
            self.assertIn("dsid_copied", copied)
            self.assertIn("-> /manual/new", copied)

    def test_recursive_grep_prunes_large_folders_before_returning_file_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            file_ref = filesystem.register_file(
                storage_uri="file:///tmp/pr.json",
                source_path="github/pr-18421.json",
                folder_path="/semantic/topics/api-input/multipart",
                external_id="dsid_multipart_limits",
                title="Multipart upload limits",
                metadata={"semantic_topics": ["multipart upload"]},
                content=(
                    "title line\n"
                    "The multipart upload path sets max_file_size to 10MiB. "
                    + ("long-context " * 80)
                    + "\n"
                    "The total request limit is 50MiB.\n"
                    "private full document tail that should only appear in cat"
                ),
            )
            filesystem.attach_file_to_folder(
                file_ref,
                "/semantic/problems/payload-limits/request-size",
            )
            executor = PIFSCommandExecutor(filesystem)

            broad = executor.execute('grep -R "multipart upload" /semantic')
            broad_case = executor.execute('grep -Ri "multipart upload" /semantic')
            listing = executor.execute("ls -R /semantic/topics/api-input/multipart")
            tree = executor.execute("tree /semantic -L 2")
            leaf = executor.execute('grep -R "multipart upload" /semantic/topics/api-input/multipart')
            local = executor.execute('grep "max_file_size" ref_1')

            self.assertIn("# grep -R skipped for broad folder: /semantic", broad)
            self.assertIn("# grep -R skipped for broad folder: /semantic", broad_case)
            self.assertIn("deeper than 2 levels or has more than 10 files", broad)
            self.assertIn("semantic-grep -R", broad)
            self.assertNotIn("dsid_multipart_limits", broad)
            self.assertNotIn("ref_1", broad)
            self.assertIn("/semantic", tree)
            self.assertIn("topics/", tree)
            self.assertIn("ref_1 dsid_multipart_limits", listing)
            self.assertIn("github/pr-18421.json", listing)
            self.assertNotIn("file_", listing)
            self.assertIn("ref_1 dsid_multipart_limits github/pr-18421.json:", leaf)
            self.assertIn("multipart upload", leaf)
            self.assertNotIn("private full document tail", leaf)
            self.assertIn("ref_1:", local)
            self.assertIn("max_file_size", local)
            self.assertLessEqual(len(local.splitlines()[0]), 240)
            self.assertNotIn("private full document tail", local)

    def test_recursive_grep_limits_deep_large_folder_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            for index in range(10):
                filesystem.register_file(
                    storage_uri=f"file:///tmp/pr-{index}.json",
                    source_path=f"github/pr-{index}.json",
                    folder_path="/semantic/topics/api-input/multipart",
                    external_id=f"dsid_multipart_limits_{index}",
                    title=f"Multipart upload limits {index}",
                    metadata={"semantic_topics": ["multipart upload"]},
                    content=(
                        "title line\n"
                        f"The multipart upload path sets max_file_size to {10 + index}MiB.\n"
                    ),
                )
            executor = PIFSCommandExecutor(filesystem, json_output=True)

            blocked = json.loads(executor.execute('grep -R "multipart upload" /semantic'))
            leaf = json.loads(
                executor.execute('grep -R "multipart upload" /semantic/topics/api-input/multipart')
            )
            shell_blocked = PIFSCommandExecutor(filesystem).execute(
                'grep -R "multipart upload" /semantic'
            )

            self.assertEqual(blocked["data"]["mode"], "limited")
            self.assertEqual(blocked["data"]["folder_depth_limit"], 2)
            self.assertEqual(blocked["data"]["file_count_limit"], 10)
            self.assertEqual(blocked["data"]["sampled_file_count"], 10)
            self.assertTrue(blocked["data"]["folder_depth_exceeds_limit"])
            self.assertFalse(blocked["data"]["file_count_exceeds_limit"])
            self.assertIn("semantic-grep -R", blocked["data"]["hint"])
            self.assertIn("search-summary", blocked["data"]["hint"])
            self.assertEqual(leaf["data"]["mode"], "files")
            self.assertTrue(leaf["data"]["data"])
            self.assertIn("# grep -R skipped for broad folder: /semantic", shell_blocked)
            self.assertIn("semantic-grep -R", shell_blocked)

    def test_recursive_grep_limits_shallow_large_folder_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            for index in range(11):
                filesystem.register_file(
                    storage_uri=f"file:///tmp/shallow-{index}.json",
                    source_path=f"github/shallow-{index}.json",
                    folder_path="/github",
                    external_id=f"dsid_shallow_limits_{index}",
                    title=f"Shallow upload limits {index}",
                    content=f"The shallow folder mentions multipart upload {index}.\n",
                )
            executor = PIFSCommandExecutor(filesystem, json_output=True)

            blocked = json.loads(executor.execute('grep -R "multipart upload" /github'))

            self.assertEqual(blocked["data"]["mode"], "limited")
            self.assertFalse(blocked["data"]["folder_depth_exceeds_limit"])
            self.assertTrue(blocked["data"]["file_count_exceeds_limit"])
            self.assertEqual(blocked["data"]["sampled_file_count"], 11)

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
            self.assertEqual(grepped["data"]["mode"], "folders")
            self.assertEqual(grepped["data"]["data"][0]["path"], "/github/redwood")
            self.assertEqual(quoted["data"]["mode"], "folders")
            self.assertEqual(quoted["data"]["data"][0]["path"], "/github/redwood")
            self.assertEqual(piped_listing["data"]["folders"][0]["path"], "/github")
            self.assertEqual(len(headed["data"]["data"]), 1)
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
                    "labels": {"type": "string"},
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
                        "metadata": {
                            "repo": "redwood",
                            "year": 2024,
                            "private": True,
                            "labels": ["Audit Logging", "security"],
                        },
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
            contains = filesystem.search(
                None,
                metadata_filter={"labels": {"$contains": "audit"}},
                limit=10,
            )

            self.assertEqual([result.external_id for result in filtered], ["dsid_redwood_2024"])
            self.assertEqual([result.external_id for result in not_redwood], ["dsid_missing_repo"])
            self.assertEqual(
                {result.external_id for result in either},
                {"dsid_redwood_2024", "dsid_redwood_2020", "dsid_missing_repo"},
            )
            self.assertEqual([result.external_id for result in contains], ["dsid_redwood_2024"])

    def test_find_folders_filters_descendant_folders_by_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem

            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            register_metadata_schema(
                filesystem,
                {
                    "source_type": {"type": "string"},
                    "labels": {"type": "string"},
                },
            )
            file_ref = filesystem.register_file(
                storage_uri="file:///tmp/github.json",
                source_path="github/redwood/pr.json",
                folder_path="/github/redwood",
                external_id="dsid_github",
                title="GitHub audit doc",
                metadata={"source_type": "github", "labels": ["audit-logging"]},
                content="audit logging text",
            )
            filesystem.create_folder("/semantic/topics/api", kind="semantic")
            filesystem.create_folder("/semantic/topics/audit", kind="semantic")
            filesystem.create_folder("/semantic/topics/slack", kind="semantic")
            filesystem.attach_file_to_folder(file_ref, "/semantic/topics/api")
            filesystem.attach_file_to_folder(file_ref, "/semantic/topics/audit")
            filesystem.register_file(
                storage_uri="file:///tmp/slack.json",
                source_path="slack/eng/thread.json",
                folder_path="/slack/eng",
                external_id="dsid_slack",
                title="Slack thread",
                metadata={"source_type": "slack", "labels": ["chat"]},
                content="slack thread text",
            )

            folders = filesystem.find_folders(
                "/semantic",
                metadata_filter={"source_type": "github"},
                limit=10,
            )
            payload = json.loads(
                PIFSCommandExecutor(filesystem, json_output=True).execute(
                    'find /semantic -type d --where \'{"labels":{"$contains":"audit"}}\''
                )
            )
            text = PIFSCommandExecutor(filesystem).execute(
                'find /semantic -type d --where \'{"source_type":"github"}\''
            )

            self.assertEqual(
                [folder["path"] for folder in folders],
                ["/semantic/topics", "/semantic/topics/api", "/semantic/topics/audit"],
            )
            self.assertEqual({folder["matched_files"] for folder in folders}, {1})
            self.assertEqual(
                [folder["path"] for folder in payload["data"]],
                ["/semantic/topics", "/semantic/topics/api", "/semantic/topics/audit"],
            )
            self.assertIn("/semantic/topics/api/ matched_files=1", text)
            self.assertNotIn("/semantic/topics/slack", text)

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
                filesystem.search(None, metadata_filter={"repo": {"$match": "red"}})
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
            with self.assertRaisesRegex(PIFSCommandError, "Unsupported ls option"):
                executor.execute('ls / --where \'{"repo":"redwood"}\'')
            with self.assertRaises(PIFSCommandError):
                executor.execute("ls / & cat")
            with self.assertRaises(PIFSCommandError):
                executor.execute("ls / || cat")
            with self.assertRaisesRegex(PIFSCommandError, "Unknown file target"):
                executor.execute("stat missing_ref")

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
                    "/github/redwood",
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
            self.assertEqual(payload["data"]["mode"], "files")
            self.assertEqual(payload["data"]["data"][0]["external_id"], "dsid_cli_module")

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
