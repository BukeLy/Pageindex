import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class SchemaDiscoveryResearchTest(unittest.TestCase):
    def test_hybrid_schema_keeps_base_and_rejects_artifact_fields(self):
        from examples.Benchmark.schema_discovery_research.run_schema_discovery_experiment import (
            BASE_SCHEMA,
            normalize_schema_draft,
        )

        draft = {
            "fields": [
                {"name": "article_id", "description": "unique article id"},
                {"name": "storage_uri", "description": "where the file came from"},
                {"name": "user_goal", "description": "what the user is trying to do"},
                {"name": "payment_surface", "description": "payment area mentioned in the document"},
                {"name": "resolution_channel", "description": "how the issue is resolved"},
                {"name": "dashboard_area", "description": "dashboard area"},
                {"name": "eligibility_signal", "description": "eligibility signal"},
                {"name": "policy_kind", "description": "policy kind"},
                {"name": "plan_tier", "description": "plan tier"},
                {"name": "extra_after_limit", "description": "should be rejected by max fields"},
            ]
        }

        result = normalize_schema_draft(
            draft,
            strategy="hybrid_workspace",
            base_schema=BASE_SCHEMA,
            max_extension_fields=6,
        )
        schema = result["schema"]
        rejected = {item["normalized"] for item in result["audit"]["rejected"]}
        merged = {item["to"] for item in result["audit"]["merged"]}

        self.assertTrue(set(BASE_SCHEMA).issubset(schema))
        self.assertEqual(merged, {"intent"})
        self.assertIn("article_id", rejected)
        self.assertIn("storage_uri", rejected)
        self.assertNotIn("extra_after_limit", schema)
        self.assertLessEqual(len(set(schema) - set(BASE_SCHEMA)), 6)
        self.assertTrue(all(field["type"] == "string" for field in schema.values()))

    def test_metadata_is_clamped_to_frozen_schema(self):
        from examples.Benchmark.schema_discovery_research.run_schema_discovery_experiment import (
            BASE_SCHEMA,
            normalize_metadata_for_schema,
        )

        metadata = {
            "doc_type": "kb article",
            "domain": "payments",
            "topic": ["payment verification", "account setup"],
            "intent": "answer whether payments can be accepted during verification",
            "entities": ["Wix Payments", "account"],
            "constraints": {"status": "under verification"},
            "summary": "Explains Wix Payments verification behavior.",
            "article_id": "leaked_id",
            "url_path": "/en/article/leak",
        }

        normalized = normalize_metadata_for_schema(BASE_SCHEMA, metadata)

        self.assertEqual(set(normalized), set(BASE_SCHEMA))
        self.assertNotIn("article_id", normalized)
        self.assertNotIn("url_path", normalized)
        self.assertEqual(normalized["topic"], "payment verification, account setup")
        self.assertEqual(normalized["constraints"], "status: under verification")

    def test_wixqa_local_bundle_uses_expected_articles_without_schema_leakage(self):
        from examples.Benchmark.schema_discovery_research.run_schema_discovery_experiment import (
            load_wixqa_bundle,
            parse_args,
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            kb_path = tmp_path / "kb.jsonl"
            qa_path = tmp_path / "qa.jsonl"
            kb_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "id": "a1",
                                "url": "https://support.wix.com/en/article/payments/verification",
                                "contents": "Payments can be accepted while verification is in progress.",
                                "article_type": "article",
                            }
                        ),
                        json.dumps(
                            {
                                "id": "a2",
                                "url": "https://support.wix.com/en/article/stores/inventory",
                                "contents": "Inventory can mark products as out of stock.",
                                "article_type": "article",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            qa_path.write_text(
                "\n".join(
                    [
                        json.dumps({"question": "Can I accept payments?", "answer": "", "article_ids": ["a1"]}),
                        json.dumps({"question": "How do I manage inventory?", "answer": "", "article_ids": ["a2"]}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            args = parse_args(
                [
                    "--target-docs",
                    "2",
                    "--wixqa-kb-jsonl",
                    str(kb_path),
                    "--wixqa-questions-jsonl",
                    str(qa_path),
                ]
            )

            bundle = load_wixqa_bundle(args)

            self.assertEqual([doc.doc_id for doc in bundle.documents], ["a1", "a2"])
            self.assertEqual([question.question_id for question in bundle.questions], ["wixqa_expertwritten_0001", "wixqa_expertwritten_0002"])
            self.assertIn("article_id", bundle.manual_schema)

    def test_metadata_generation_resumes_partial_cache(self):
        from examples.Benchmark.schema_discovery_research.run_schema_discovery_experiment import (
            BASE_SCHEMA,
            DatasetBundle,
            ResearchDocument,
            build_metadata,
            parse_args,
        )

        docs = [
            ResearchDocument(
                doc_id="doc_1",
                title="First",
                text="First document",
                storage_uri="memory://doc_1",
                source_path="doc_1.txt",
                folder_path="/",
                content_type="text/plain",
                source_type="test",
                manual_metadata={},
            ),
            ResearchDocument(
                doc_id="doc_2",
                title="Second",
                text="Second document",
                storage_uri="memory://doc_2",
                source_path="doc_2.txt",
                folder_path="/",
                content_type="text/plain",
                source_type="test",
                manual_metadata={},
            ),
        ]
        bundle = DatasetBundle("unit", docs, [], BASE_SCHEMA)

        with tempfile.TemporaryDirectory() as tmp:
            strategy_dir = Path(tmp)
            metadata_path = strategy_dir / "metadata.json"
            metadata_path.write_text(
                json.dumps({"doc_1": {field: "cached" for field in BASE_SCHEMA}}),
                encoding="utf-8",
            )
            args = parse_args(["--target-docs", "2"])

            with patch(
                "examples.Benchmark.schema_discovery_research.run_schema_discovery_experiment.generate_metadata_for_doc",
                return_value={field: "generated" for field in BASE_SCHEMA},
            ) as generate:
                metadata = build_metadata(bundle, "base_only", BASE_SCHEMA, strategy_dir, args)

            self.assertEqual(generate.call_count, 1)
            self.assertEqual(metadata["doc_1"]["summary"], "cached")
            self.assertEqual(metadata["doc_2"]["summary"], "generated")


if __name__ == "__main__":
    unittest.main()
