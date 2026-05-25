from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pipeline_common import (
    cardinality_expectation,
    canonical_field_name,
    dedupe_strings,
    ensure_list,
    field_distribution,
    is_forbidden_extension_field,
    load_normalized_metadata,
    text_value,
    update_config,
    value_key,
    write_json,
    write_summary,
)


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser()
    metadata_path = Path(args.metadata) if args.metadata else run_dir / "metadata.normalized.jsonl"
    rows = load_normalized_metadata(metadata_path)

    sample_prompt_path = run_dir / "extension_schema_prompt.sample.md"
    sample_prompt_path.write_text(render_sample_prompt(rows, args.sample_size), encoding="utf-8")

    schema, audit = discover_extension_schema(rows, args)
    schema_path = run_dir / "extension_schema.json"
    audit_path = run_dir / "extension_schema_audit.json"
    write_json(schema_path, schema)
    write_json(audit_path, audit)
    update_config(
        run_dir,
        "extension_schema",
        {
            "metadata_path": str(metadata_path),
            "schema_path": str(schema_path),
            "audit_path": str(audit_path),
            "sample_prompt_path": str(sample_prompt_path),
            "min_coverage": args.min_coverage,
        },
    )
    write_summary(
        run_dir,
        {
            "extension_schema": {
                "documents": len(rows),
                "accepted_fields": len(schema["fields"]),
                "rejected_fields": len(audit["rejected_fields"]),
            }
        },
    )
    print(json.dumps({"schema": schema, "audit": audit}, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover extension metadata fields for DSL/filter and folder browse")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--metadata", default="")
    parser.add_argument("--sample-size", type=int, default=25)
    parser.add_argument("--min-coverage", type=float, default=0.25)
    parser.add_argument("--max-dsl-values", type=int, default=120)
    parser.add_argument("--max-folder-values", type=int, default=40)
    parser.add_argument("--max-cardinality-rate", type=float, default=0.65)
    parser.add_argument("--max-folder-singleton-rate", type=float, default=0.65)
    parser.add_argument("--max-canonical-values", type=int, default=50)
    parser.add_argument("--max-average-value-chars", type=int, default=80)
    return parser.parse_args()


def discover_extension_schema(rows: list[dict[str, Any]], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    doc_count = max(1, len(rows))
    candidates: dict[str, dict[str, Any]] = defaultdict(lambda: {"values_by_doc": {}, "provenance": []})
    for row in rows:
        doc_id = row["dataset_doc_uuid"]
        extension_candidates = row.get("extension_candidates") or {}
        extension_provenance = ((row.get("provenance") or {}).get("extension_candidates") or {})
        for raw_field, raw_value in extension_candidates.items():
            field = canonical_field_name(raw_field)
            if is_forbidden_extension_field(field):
                continue
            values = normalize_field_values(raw_value)
            if not values:
                continue
            candidates[field]["values_by_doc"][doc_id] = values
            if raw_field in extension_provenance:
                candidates[field]["provenance"].append(extension_provenance[raw_field])

    accepted_fields: list[dict[str, Any]] = []
    rejected_fields: list[dict[str, Any]] = []
    for field, payload in sorted(candidates.items()):
        values_by_doc: dict[str, list[str]] = payload["values_by_doc"]
        values = [value for per_doc in values_by_doc.values() for value in per_doc]
        unique_keys = {value_key(value) for value in values}
        coverage = len(values_by_doc) / doc_count
        unique_count = len(unique_keys)
        cardinality_rate = unique_count / max(1, len(values_by_doc))
        lengths = [len(text_value(value)) for value in values if value]
        average_chars = statistics.mean(lengths) if lengths else 0.0
        distribution = field_distribution(values_by_doc)
        reasons = rejection_reasons(
            field=field,
            coverage=coverage,
            unique_count=unique_count,
            cardinality_rate=cardinality_rate,
            average_chars=average_chars,
            distribution=distribution,
            args=args,
        )
        suitable_for_dsl = not reasons["dsl"]
        suitable_for_folder = suitable_for_dsl and not reasons["folder"]
        if not suitable_for_dsl and not suitable_for_folder:
            rejected_fields.append(
                {
                    "name": field,
                    "coverage_estimate": round(coverage, 4),
                    "unique_values": unique_count,
                    "cardinality_rate": round(cardinality_rate, 4),
                    "average_value_chars": round(average_chars, 2),
                    "reasons": reasons["dsl"] + reasons["folder"],
                }
            )
            continue
        canonical_values = canonical_values_for(values, limit=args.max_canonical_values)
        accepted_fields.append(
            {
                "name": field,
                "description": field.replace("_", " "),
                "why_queryable": "Field has reusable canonical values with enough corpus coverage for exact filtering or browsing.",
                "coverage_estimate": round(coverage, 4),
                "canonical_values": canonical_values,
                "synonyms": {},
                "suitable_for_dsl": bool(suitable_for_dsl),
                "suitable_for_folder": bool(suitable_for_folder),
                "cardinality_expectation": cardinality_expectation(unique_count, doc_count),
                "empty_policy": "omit documents with no grounded value for this field",
                "example_values": canonical_values[:10],
                "source_evidence": source_evidence(field, payload.get("provenance") or []),
                "stats": {
                    "unique_values": unique_count,
                    "cardinality_rate": round(cardinality_rate, 4),
                    "average_value_chars": round(average_chars, 2),
                    "singleton_rate": round(distribution["singleton_rate"], 4),
                    "max_folder_size": distribution["max_folder_size"],
                },
            }
        )

    audit = {
        "documents": len(rows),
        "candidate_fields": len(candidates),
        "accepted_fields": [field["name"] for field in accepted_fields],
        "rejected_fields": rejected_fields,
        "policy": {
            "forbidden": [
                "system/provenance identifiers",
                "summary/entities/relations/constraints/retrieval_cues",
                "paths, URLs, storage URIs, filenames, benchmark ids",
                "high-cardinality or nearly unique fields",
                "long text-heavy fields",
            ],
            "selection": [
                "coverage above threshold",
                "controlled cardinality",
                "canonicalizable values",
                "useful exact filter or browse behavior",
            ],
        },
    }
    return {
        "generated_by": "heuristic_extension_discovery",
        "fields": accepted_fields,
    }, audit


def normalize_field_values(raw_value: Any) -> list[str]:
    values = []
    for item in ensure_list(raw_value):
        if isinstance(item, dict):
            continue
        value = text_value(item)
        if not value:
            continue
        if len(value) > 300:
            continue
        values.append(value)
    return dedupe_strings(values)


def rejection_reasons(
    *,
    field: str,
    coverage: float,
    unique_count: int,
    cardinality_rate: float,
    average_chars: float,
    distribution: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, list[str]]:
    base_reasons = []
    if is_forbidden_extension_field(field):
        base_reasons.append("field name is forbidden for extension schema")
    if coverage < args.min_coverage:
        base_reasons.append("coverage below threshold")
    if unique_count < 2:
        base_reasons.append("field does not partition the corpus")
    if cardinality_rate > args.max_cardinality_rate:
        base_reasons.append("cardinality is too close to one value per document")
    if average_chars > args.max_average_value_chars:
        base_reasons.append("values are too long/text-heavy")

    dsl_reasons = list(base_reasons)
    if unique_count > args.max_dsl_values:
        dsl_reasons.append("too many values for metadata DSL")

    folder_reasons = list(base_reasons)
    if unique_count > args.max_folder_values:
        folder_reasons.append("too many values for folder browse")
    if distribution["singleton_rate"] > args.max_folder_singleton_rate:
        folder_reasons.append("would create too many one-file folders")
    return {"dsl": dsl_reasons, "folder": folder_reasons}


def canonical_values_for(values: list[str], *, limit: int) -> list[str]:
    counter = Counter()
    display: dict[str, str] = {}
    for value in values:
        key = value_key(value)
        counter[key] += 1
        display.setdefault(key, text_value(value))
    return [display[key] for key, _count in counter.most_common(limit)]


def source_evidence(field: str, provenance_rows: list[dict[str, Any]]) -> str:
    if not provenance_rows:
        return f"Observed from normalized extension candidate `{field}`."
    sources = []
    for prov in provenance_rows[:3]:
        source_field = prov.get("source_field") or field
        source_file = Path(str(prov.get("source_file") or "")).name
        sources.append(f"{source_field} in {source_file}")
    return "; ".join(sources)


def render_sample_prompt(rows: list[dict[str, Any]], sample_size: int) -> str:
    prompt_template = (Path(__file__).resolve().parent / "prompts" / "extension_schema_discovery.md").read_text(encoding="utf-8")
    sample = []
    for row in rows[:sample_size]:
        sample.append(
            {
                "metadata_base": {
                    "doc_type": row.get("metadata_base", {}).get("doc_type"),
                    "domain": row.get("metadata_base", {}).get("domain"),
                    "topic": row.get("metadata_base", {}).get("topic"),
                },
                "extension_candidates": row.get("extension_candidates") or {},
            }
        )
    return prompt_template.replace("{sample_json}", json.dumps(sample, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
