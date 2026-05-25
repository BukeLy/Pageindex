from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path
from typing import Any

from pipeline_common import has_unsafe_generation, load_normalized_metadata, read_json, write_json


FORBIDDEN_FOLDER_FIELDS = {
    "summary",
    "entities",
    "relations",
    "constraints",
    "retrieval_cues",
    "dataset_doc_uuid",
    "source_path",
    "storage_uri",
}


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser()
    report = inspect_run(run_dir, sample_size=args.sample_size, seed=args.seed)
    report_path = run_dir / "inspection_report.json"
    write_json(report_path, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_report(report))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect semantic metadata pipeline artifacts")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--sample-size", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def inspect_run(run_dir: Path, *, sample_size: int, seed: int) -> dict[str, Any]:
    metadata_rows = load_normalized_metadata(run_dir / "metadata.normalized.jsonl")
    extension_schema = read_json(run_dir / "extension_schema.json")
    folder_plan = read_json(run_dir / "folder_plan.json")
    projection_manifest = read_json(run_dir / "projection_manifest.json")
    sample_projection_rows = read_json(run_dir / "sample_projection_rows.json")

    rng = random.Random(seed)
    projection_samples = {}
    for channel in ["summary", "entity", "relation"]:
        rows = list(sample_projection_rows.get(channel) or [])
        projection_samples[channel] = rng.sample(rows, min(sample_size, len(rows))) if rows else []

    folder_check = prove_folder_exclusions(folder_plan)
    dataset_id_check = prove_dataset_doc_uuid_scope(
        metadata_rows=metadata_rows,
        extension_schema=extension_schema,
        folder_plan=folder_plan,
        projection_samples=sample_projection_rows,
    )
    unsafe = find_unsafe_legacy_fields(metadata_rows)
    projection_check = prove_projection_policy(projection_manifest, sample_projection_rows)

    return {
        "run_dir": str(run_dir),
        "projection_samples": projection_samples,
        "folder_exclusion_check": folder_check,
        "dataset_doc_uuid_scope_check": dataset_id_check,
        "projection_policy_check": projection_check,
        "unsafe_legacy_generation": unsafe,
        "ok": (
            folder_check["ok"]
            and dataset_id_check["ok"]
            and projection_check["ok"]
            and not unsafe["fields"]
        ),
    }


def prove_folder_exclusions(folder_plan: dict[str, Any]) -> dict[str, Any]:
    selected = set(folder_plan.get("selected_fields") or [])
    membership_fields = {item.get("field") for item in folder_plan.get("memberships", [])}
    folder_fields = {item.get("field") for item in folder_plan.get("folders", [])}
    violations = sorted((selected | membership_fields | folder_fields) & FORBIDDEN_FOLDER_FIELDS)
    path_violations = [
        item.get("path")
        for item in folder_plan.get("folders", [])
        if any(f"{field}=" in str(item.get("path") or "") for field in FORBIDDEN_FOLDER_FIELDS)
    ]
    return {
        "ok": not violations and not path_violations,
        "forbidden_fields": sorted(FORBIDDEN_FOLDER_FIELDS),
        "violating_fields": violations,
        "violating_paths_sample": path_violations[:10],
        "selected_fields": sorted(selected),
    }


def prove_dataset_doc_uuid_scope(
    *,
    metadata_rows: list[dict[str, Any]],
    extension_schema: dict[str, Any],
    folder_plan: dict[str, Any],
    projection_samples: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    schema_fields = [field.get("name") for field in extension_schema.get("fields", [])]
    folder_values = json.dumps(
        {
            "selected_fields": folder_plan.get("selected_fields"),
            "folders": [
                {
                    "path": item.get("path"),
                    "field": item.get("field"),
                    "value": item.get("value"),
                    "metadata": item.get("metadata"),
                }
                for item in folder_plan.get("folders", [])
            ],
            "membership_fields": [
                {
                    "folder_path": item.get("folder_path"),
                    "field": item.get("field"),
                    "value": item.get("value"),
                    "folder_metadata": item.get("folder_metadata"),
                }
                for item in folder_plan.get("memberships", [])
            ],
        },
        ensure_ascii=False,
    )
    projection_text = json.dumps(
        {
            channel: [row.get("text") for row in rows]
            for channel, rows in projection_samples.items()
        },
        ensure_ascii=False,
    )
    metadata_base_text = json.dumps([row.get("metadata_base") for row in metadata_rows], ensure_ascii=False)
    violations = []
    if "dataset_doc_uuid" in schema_fields:
        violations.append("extension_schema.field")
    if "dataset_doc_uuid=" in folder_values or re.search(r"dsid_[0-9a-f]{8,}", folder_values):
        violations.append("folder_plan")
    if re.search(r"dsid_[0-9a-f]{8,}", projection_text):
        violations.append("projection_text")
    if re.search(r"dsid_[0-9a-f]{8,}", metadata_base_text):
        violations.append("metadata_base")
    return {
        "ok": not violations,
        "violations": violations,
        "note": "dataset_doc_uuid may appear as row id, external_id, or provenance/eval id, but not as schema/folder/embedding text.",
    }


def prove_projection_policy(
    projection_manifest: dict[str, Any],
    sample_projection_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    channels = set((projection_manifest.get("channels") or {}).keys())
    forbidden = set(projection_manifest.get("forbidden_channels_not_built") or [])
    built_forbidden = sorted(channels & forbidden)
    text_blob = json.dumps(sample_projection_rows, ensure_ascii=False).lower()
    cue_vector_violation = "retrieval_cues_vectors" in channels or "constraint_vectors" in channels
    fallback_markers = [
        "compact_summary",
        "title + preview",
        "keyword_terms",
        "infer_predicate",
        "fulltext preview",
    ]
    fallback_violations = [marker for marker in fallback_markers if marker in text_blob]
    return {
        "ok": not built_forbidden and not cue_vector_violation and not fallback_violations,
        "built_channels": sorted(channels),
        "forbidden_channels_not_built": sorted(forbidden),
        "built_forbidden_channels": built_forbidden,
        "fallback_marker_violations": fallback_violations,
    }


def find_unsafe_legacy_fields(metadata_rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = []
    for row in metadata_rows:
        doc_id = row.get("dataset_doc_uuid")
        explicit = row.get("unsafe_legacy_generation") or []
        for item in explicit:
            fields.append({"dataset_doc_uuid": doc_id, **item})
        base_prov = ((row.get("provenance") or {}).get("metadata_base") or {})
        for field, prov in base_prov.items():
            if has_unsafe_generation(prov):
                fields.append({"dataset_doc_uuid": doc_id, "field": field, "provenance": prov})
    return {
        "count": len(fields),
        "fields": fields[:50],
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [
        f"run_dir: {report['run_dir']}",
        f"ok: {report['ok']}",
        "",
        "projection samples:",
    ]
    for channel, rows in report["projection_samples"].items():
        lines.append(f"- {channel}: {len(rows)} rows")
        for row in rows:
            prov = row.get("metadata_provenance") or {}
            lines.append(
                "  "
                + f"{row.get('dataset_doc_uuid')} {row.get('index_name')} "
                + f"source_field={prov.get('source_field')} method={prov.get('generation_method')}"
            )
    lines.extend(
        [
            "",
            f"folder_exclusion_ok: {report['folder_exclusion_check']['ok']}",
            f"dataset_doc_uuid_scope_ok: {report['dataset_doc_uuid_scope_check']['ok']}",
            f"projection_policy_ok: {report['projection_policy_check']['ok']}",
            f"unsafe_legacy_count: {report['unsafe_legacy_generation']['count']}",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
