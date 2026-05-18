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

    def test_hybrid_v2_extension_schema_keeps_canonical_hints(self):
        from examples.Benchmark.schema_discovery_research.run_schema_discovery_experiment import (
            BASE_SCHEMA,
            normalize_schema_draft,
        )

        draft = {
            "fields": [
                {"name": "user_goal", "description": "duplicate of base intent"},
                {"name": "source_path", "description": "leaky artifact path"},
                {
                    "name": "source_channel",
                    "description": "where the content originated",
                    "why_queryable": "lets the agent filter github, slack, email, or kb content",
                    "coverage_estimate": 0.8,
                    "canonical_values": ["github", "slack", "email", "kb"],
                    "synonyms": {"github": ["github pull request", "pr"], "kb": ["help center", "knowledge base"]},
                    "empty_policy": "empty if source channel is not clear",
                },
                {
                    "name": "risk_level",
                    "description": "operational risk level discussed",
                    "why_queryable": "lets the agent narrow by severity",
                    "coverage_estimate": 0.5,
                    "canonical_values": ["low", "medium", "high"],
                    "synonyms": {"high": ["critical"]},
                    "empty_policy": "empty if no risk is described",
                },
            ]
        }

        result = normalize_schema_draft(
            draft,
            strategy="hybrid_v2_extension",
            base_schema=BASE_SCHEMA,
            max_extension_fields=6,
        )

        schema = result["schema"]
        self.assertTrue(set(BASE_SCHEMA).issubset(schema))
        self.assertIn("source_channel", schema)
        self.assertEqual(schema["source_channel"]["canonical_values"], ["github", "slack", "email", "kb"])
        self.assertEqual(schema["source_channel"]["synonyms"]["github"], ["github pull request", "pr"])
        self.assertIn("risk_level", schema)
        rejected = {item["name"]: item["reason"] for item in result["audit"]["rejected"]}
        self.assertEqual(rejected["user_goal"], "base_duplicate")
        self.assertEqual(rejected["source_path"], "disallowed")

    def test_metadata_normalization_uses_canonical_value_hints(self):
        from examples.Benchmark.schema_discovery_research.run_schema_discovery_experiment import (
            BASE_SCHEMA,
            normalize_metadata_for_schema,
        )

        schema = dict(BASE_SCHEMA)
        schema["source_channel"] = {
            "type": "string",
            "description": "source channel",
            "canonical_values": ["github", "slack", "email", "kb"],
            "synonyms": {"github": ["github pull request", "pr"], "kb": ["help center"]},
        }
        metadata = {field: "" for field in BASE_SCHEMA}
        metadata["source_channel"] = "GitHub Pull Request"

        normalized = normalize_metadata_for_schema(schema, metadata)

        self.assertEqual(normalized["source_channel"], "github")

    def test_quality_gate_removes_bad_extension_fields(self):
        from examples.Benchmark.schema_discovery_research.run_schema_discovery_experiment import (
            BASE_SCHEMA,
            apply_schema_quality_gate,
        )

        schema = dict(BASE_SCHEMA)
        schema.update(
            {
                "source_channel": {
                    "type": "string",
                    "description": "source channel",
                    "canonical_values": ["github", "slack", "email", "kb"],
                },
                "sparse_signal": {
                    "type": "string",
                    "description": "rare signal",
                    "canonical_values": ["yes", "no"],
                },
                "unique_label": {
                    "type": "string",
                    "description": "unique per doc label",
                    "canonical_values": ["a", "b", "c", "d", "e"],
                },
            }
        )
        metadata = {}
        channels = ["github", "github", "slack", "email", "github", "kb", "github", "slack", "email", "github"]
        for index, channel in enumerate(channels):
            metadata[f"doc_{index}"] = {
                **{field: "base" for field in BASE_SCHEMA},
                "source_channel": channel,
                "sparse_signal": "yes" if index == 0 else "",
                "unique_label": f"unique_{index}",
            }
        audit = {"strategy": "hybrid_v2_extension", "kept": ["source_channel", "sparse_signal", "unique_label"], "rejected": []}

        gated_schema, gated_metadata, gated_audit = apply_schema_quality_gate(
            schema,
            metadata,
            audit,
            strategy="hybrid_v2_extension",
            min_coverage=0.3,
            max_high_cardinality=0.75,
        )

        self.assertIn("source_channel", gated_schema)
        self.assertNotIn("sparse_signal", gated_schema)
        self.assertNotIn("unique_label", gated_schema)
        self.assertNotIn("sparse_signal", gated_metadata["doc_0"])
        reasons = {item["field"]: item["reason"] for item in gated_audit["quality_rejected"]}
        self.assertEqual(reasons["sparse_signal"], "low_coverage")
        self.assertEqual(reasons["unique_label"], "high_cardinality")


if __name__ == "__main__":
    unittest.main()
