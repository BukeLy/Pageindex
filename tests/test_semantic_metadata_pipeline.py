import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = REPO_ROOT / "examples" / "Benchmark" / "enterprise_rag_benchmark" / "semantic_metadata_pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))


def normalized_row(doc_id: str, source_type: str = "github") -> dict:
    return {
        "dataset_doc_uuid": doc_id,
        "system": {
            "dataset_doc_uuid": doc_id,
            "source_type": source_type,
            "title": doc_id,
        },
        "metadata_base": {
            "doc_type": "runbook",
            "domain": "security",
            "topic": "audit logging",
        },
        "extension_candidates": {
            "business_unit": "enterprise",
        },
        "provenance": {},
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_legacy_schema_run_artifacts(run_dir: Path) -> None:
    write_jsonl(run_dir / "metadata.normalized.jsonl", [normalized_row("doc_1"), normalized_row("doc_2")])
    (run_dir / "extension_schema.json").write_text(
        json.dumps(
            {
                "generated_by": "heuristic_extension_discovery",
                "fields": [
                    {
                        "name": "business_unit",
                        "suitable_for_dsl": True,
                        "suitable_for_folder": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "folder_plan.json").write_text(
        json.dumps(
            {
                "selected_fields": ["business_unit"],
                "folders": [
                    {
                        "path": "/source_type=github/business_unit=enterprise",
                        "field": "business_unit",
                        "value": "enterprise",
                    }
                ],
                "memberships": [],
            }
        ),
        encoding="utf-8",
    )


class SemanticMetadataPipelineTest(unittest.TestCase):
    def test_pending_discovery_tombstones_stale_schema_and_blocks_folder_build(self):
        import build_folders
        import discover_extensions

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            metadata_path = run_dir / "metadata.normalized.jsonl"
            write_jsonl(metadata_path, [normalized_row("doc_1"), normalized_row("doc_2")])
            stale_schema_path = run_dir / "extension_schema.json"
            stale_schema_path.write_text(
                json.dumps(
                    {
                        "generated_by": "llm_extension_schema_discovery",
                        "status": "ready",
                        "schema_provider": "openai",
                        "schema_model": "test-model",
                        "fields": [
                            {
                                "name": "business_unit",
                                "suitable_for_folder": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(schema_provider="offline", schema_model="")
            status = discover_extensions.write_pending_artifact(
                run_dir,
                args,
                metadata_path=metadata_path,
                sample_prompt_path=run_dir / "extension_schema_prompt.sample.md",
                reason="offline_mode",
                message="offline",
            )

            self.assertEqual(status, 2)
            self.assertFalse(stale_schema_path.exists())
            self.assertTrue((run_dir / "extension_schema.stale.json").exists())

            with patch.object(sys, "argv", ["build_folders.py", "--run-dir", str(run_dir)]):
                with self.assertRaises(SystemExit) as raised:
                    build_folders.main()

            self.assertIn("Extension schema is not ready", str(raised.exception))
            self.assertFalse((run_dir / "folder_plan.json").exists())

    def test_folder_build_rejects_legacy_extension_schema_without_status(self):
        import build_folders

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            metadata_path = run_dir / "metadata.normalized.jsonl"
            write_jsonl(metadata_path, [normalized_row("doc_1"), normalized_row("doc_2")])
            schema_path = run_dir / "extension_schema.json"
            schema_path.write_text(
                json.dumps(
                    {
                        "generated_by": "heuristic_extension_discovery",
                        "fields": [
                            {
                                "name": "business_unit",
                                "suitable_for_folder": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(sys, "argv", ["build_folders.py", "--run-dir", str(run_dir)]):
                with self.assertRaises(SystemExit) as raised:
                    build_folders.main()

            self.assertIn("status=missing", str(raised.exception))
            self.assertFalse((run_dir / "folder_plan.json").exists())

    def test_agent_materialize_rejects_legacy_extension_schema_without_status(self):
        import run_agent_smoke

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_legacy_schema_run_artifacts(run_dir)

            with self.assertRaises(SystemExit) as raised:
                run_agent_smoke.materialize_workspace(
                    run_dir=run_dir,
                    dataset_dir=run_dir / "dataset",
                    workspace=run_dir / "workspace",
                    reset=True,
                )

            self.assertIn("status=missing", str(raised.exception))
            self.assertFalse((run_dir / "workspace" / "filesystem.sqlite").exists())

    def test_inspect_rejects_legacy_extension_schema_without_status(self):
        import inspect_artifacts

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_legacy_schema_run_artifacts(run_dir)
            (run_dir / "projection_manifest.json").write_text(
                json.dumps({"channels": {}, "forbidden_channels_not_built": []}),
                encoding="utf-8",
            )
            (run_dir / "sample_projection_rows.json").write_text(
                json.dumps({"summary": [], "entity": [], "relation": []}),
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit) as raised:
                inspect_artifacts.inspect_run(run_dir, sample_size=1, seed=7)

            self.assertIn("status=missing", str(raised.exception))

    def test_extension_audit_rejects_malformed_lists_objects_and_non_numeric_coverage(self):
        import discover_extensions

        valid_field = {
            "name": "deployment_stage",
            "description": "Deployment stage discussed in the document.",
            "why_queryable": "Allows queries by rollout stage.",
            "coverage_estimate": 0.7,
            "canonical_values": ["planning", "rollout"],
            "synonyms": {"planning": ["design"], "rollout": ["release"]},
            "suitable_for_dsl": True,
            "suitable_for_folder": True,
            "cardinality_expectation": "low",
            "empty_policy": "Empty when no deployment stage appears.",
            "example_values": ["planning"],
            "source_evidence": "Sample documents mention planning and rollout.",
        }
        provider_schema = {
            "fields": [
                {
                    **valid_field,
                    "name": "business_unit",
                    "coverage_estimate": "0.8",
                    "canonical_values": {"enterprise": "Enterprise"},
                    "synonyms": {"enterprise": {"bad": "object"}},
                    "example_values": [{"bad": "object"}],
                },
                {
                    **valid_field,
                    "name": "risk_level",
                    "synonyms": ["critical"],
                },
                valid_field,
            ]
        }

        schema, audit = discover_extensions.audit_extension_schema(
            provider_schema,
            rows=[normalized_row("doc_1")],
            sample_rows=[normalized_row("doc_1")],
            args=argparse.Namespace(schema_provider="openai", schema_model="test-model"),
        )

        self.assertEqual(audit["status"], "rejected")
        self.assertEqual(schema["status"], "rejected")
        self.assertEqual(audit["accepted_fields"], ["deployment_stage"])
        self.assertEqual([field["name"] for field in schema["fields"]], ["deployment_stage"])
        rejected = {item["name"]: item["reasons"] for item in audit["rejected_fields"]}
        self.assertIn("coverage_estimate must be a JSON number", rejected["business_unit"])
        self.assertIn("canonical_values must be a list of strings", rejected["business_unit"])
        self.assertIn("synonyms.enterprise must be a list of strings", rejected["business_unit"])
        self.assertIn("example_values[0] must be a string", rejected["business_unit"])
        self.assertIn("synonyms must be an object", rejected["risk_level"])

    def test_extension_audit_rejects_nan_coverage_and_object_list_string_fields(self):
        import discover_extensions

        valid_field = {
            "name": "deployment_stage",
            "description": "Deployment stage discussed in the document.",
            "why_queryable": "Allows queries by rollout stage.",
            "coverage_estimate": 0.7,
            "canonical_values": ["planning", "rollout"],
            "synonyms": {"planning": ["design"], "rollout": ["release"]},
            "suitable_for_dsl": True,
            "suitable_for_folder": True,
            "cardinality_expectation": "low",
            "empty_policy": "Empty when no deployment stage appears.",
            "example_values": ["planning"],
            "source_evidence": "Sample documents mention planning and rollout.",
        }
        provider_schema = {
            "fields": [
                {
                    **valid_field,
                    "name": "nan_coverage",
                    "coverage_estimate": float("nan"),
                },
                {
                    **valid_field,
                    "name": "bad_string_values",
                    "description": {"text": "object should not be coerced"},
                    "why_queryable": ["list should not be coerced"],
                    "empty_policy": {"text": "object should not be coerced"},
                    "source_evidence": ["list should not be coerced"],
                },
                valid_field,
            ]
        }

        schema, audit = discover_extensions.audit_extension_schema(
            provider_schema,
            rows=[normalized_row("doc_1")],
            sample_rows=[normalized_row("doc_1")],
            args=argparse.Namespace(schema_provider="openai", schema_model="test-model"),
        )

        self.assertEqual(audit["status"], "rejected")
        self.assertEqual(schema["status"], "rejected")
        self.assertEqual(audit["accepted_fields"], ["deployment_stage"])
        self.assertEqual([field["name"] for field in schema["fields"]], ["deployment_stage"])
        rejected = {item["name"]: item["reasons"] for item in audit["rejected_fields"]}
        self.assertIn("coverage_estimate must be a finite JSON number", rejected["nan_coverage"])
        self.assertIn("description must be a string", rejected["bad_string_values"])
        self.assertIn("why_queryable must be a string", rejected["bad_string_values"])
        self.assertIn("empty_policy must be a string", rejected["bad_string_values"])
        self.assertIn("source_evidence must be a string", rejected["bad_string_values"])


if __name__ == "__main__":
    unittest.main()
