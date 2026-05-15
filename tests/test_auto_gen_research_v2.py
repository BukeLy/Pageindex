import tempfile
import unittest
from pathlib import Path


class AutoGenResearchV2Test(unittest.TestCase):
    def test_semantic_metadata_is_persisted_to_registered_file(self):
        from examples.Benchmark.enterprise_rag_benchmark.auto_gen_research.v2.run_semantic_folder_v2 import (
            SEMANTIC_METADATA_SCHEMA,
            update_registered_semantic_metadata,
        )
        from pageindex.filesystem import PageIndexFileSystem

        with tempfile.TemporaryDirectory() as tmp:
            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            filesystem._register_metadata_schema({"fields": SEMANTIC_METADATA_SCHEMA})
            file_ref = filesystem.register_file(
                storage_uri="file:///tmp/doc.json",
                source_path="docs/doc.json",
                external_id="dsid_semantic",
                title="Semantic doc",
                content="Multipart upload limits default to 10 MiB per file and 50 MiB total.",
            )

            update_registered_semantic_metadata(
                filesystem,
                file_ref,
                {
                    "semantic_summary": "Documents multipart upload size limits.",
                    "semantic_topics": ["multipart upload limits", "openai-compatible api"],
                    "semantic_entities": ["multipart parser"],
                    "semantic_systems": ["openai-compatible api"],
                    "semantic_problems": ["memory pressure"],
                    "semantic_actions": ["enforce payload limits"],
                    "semantic_constraints": ["10 MiB per file", "50 MiB total request size"],
                    "semantic_measurements": ["10 MiB", "50 MiB"],
                    "semantic_events": ["multipart upload support"],
                    "semantic_time_hints": [],
                    "semantic_aliases": ["file upload limit"],
                    "semantic_evidence_terms": ["10 MiB", "50 MiB"],
                },
            )

            results = filesystem.search(None, metadata_filter={"semantic_topics": "multipart upload limits"})
            stat = filesystem._stat(file_ref)

            self.assertEqual([result.external_id for result in results], ["dsid_semantic"])
            self.assertEqual(stat["metadata"]["semantic_topics"], ["multipart upload limits", "openai-compatible api"])

    def test_canonical_metadata_projects_to_semantic_folders(self):
        from examples.Benchmark.enterprise_rag_benchmark.auto_gen_research.v2.run_semantic_folder_v2 import (
            build_folder_plan,
            materialize_folder_plan,
        )
        from pageindex.filesystem import PageIndexFileSystem

        with tempfile.TemporaryDirectory() as tmp:
            filesystem = PageIndexFileSystem(workspace=Path(tmp) / "workspace")
            file_ref = filesystem.register_file(
                storage_uri="file:///tmp/doc.json",
                source_path="docs/doc.json",
                external_id="dsid_folder",
                title="Folder doc",
                content="Multipart upload limits reduce memory pressure.",
            )
            doc = {"dataset_doc_uuid": "dsid_folder", "file_ref": file_ref, "title": "Folder doc"}
            semantic_metadata = {
                "dsid_folder": {
                    "semantic_topics": ["file upload size limit", "OpenAI compatible API"],
                    "semantic_problems": ["memory pressure"],
                    "semantic_actions": ["enforce payload size limits"],
                }
            }
            canonicalization = {
                "semantic_topics": {
                    "file upload size limit": {
                        "canonical_label": "multipart upload limits",
                        "parent_label": "upload limits",
                        "aliases": ["file upload size limit"],
                    },
                    "OpenAI compatible API": {
                        "canonical_label": "openai-compatible api",
                        "parent_label": "api compatibility",
                        "aliases": ["OpenAI compatible API"],
                    },
                },
                "semantic_problems": {
                    "memory pressure": {
                        "canonical_label": "memory pressure",
                        "parent_label": "resource pressure",
                        "aliases": [],
                    }
                },
                "semantic_actions": {
                    "enforce payload size limits": {
                        "canonical_label": "enforce payload size limits",
                        "parent_label": "payload validation",
                        "aliases": [],
                    }
                },
            }

            plan = build_folder_plan(
                [doc],
                semantic_metadata,
                canonicalization,
                max_folders_per_doc=4,
            )
            materialize_folder_plan(filesystem, {doc["dataset_doc_uuid"]: file_ref}, plan)

            topic_results = filesystem.search(
                "multipart upload",
                scope={"folder_path": "/semantic/topics/upload-limits"},
            )
            problem_results = filesystem.search(
                "memory pressure",
                scope={"folder_path": "/semantic/problems/resource-pressure"},
            )
            stat = filesystem._stat(file_ref)

            self.assertEqual([result.external_id for result in topic_results], ["dsid_folder"])
            self.assertEqual([result.external_id for result in problem_results], ["dsid_folder"])
            self.assertIn(
                "/semantic/topics/upload-limits/multipart-upload-limits",
                {folder["path"] for folder in stat["folders"]},
            )


if __name__ == "__main__":
    unittest.main()
