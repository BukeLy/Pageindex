from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pipeline_common import (
    FOLDER_BASE_FIELDS,
    TEXT_HEAVY_FIELDS,
    cardinality_expectation,
    field_distribution,
    field_values_for_doc,
    is_forbidden_extension_field,
    load_ready_extension_schema,
    load_normalized_metadata,
    slug,
    update_config,
    value_key,
    write_json,
    write_summary,
)


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
    metadata_path = Path(args.metadata) if args.metadata else run_dir / "metadata.normalized.jsonl"
    schema_path = Path(args.extension_schema) if args.extension_schema else run_dir / "extension_schema.json"
    rows = load_normalized_metadata(metadata_path)
    extension_schema = load_ready_extension_schema(run_dir, schema_path)

    folder_plan, field_report = build_folder_plan(rows, extension_schema, args)
    plan_path = run_dir / "folder_plan.json"
    report_path = run_dir / "folder_field_report.json"
    write_json(plan_path, folder_plan)
    write_json(report_path, field_report)
    update_config(
        run_dir,
        "folders",
        {
            "metadata_path": str(metadata_path),
            "extension_schema_path": str(schema_path),
            "folder_plan_path": str(plan_path),
            "folder_field_report_path": str(report_path),
            "mode": args.mode,
            "max_depth": args.max_depth,
        },
    )
    write_summary(
        run_dir,
        {
            "folders": {
                "selected_fields": ",".join(folder_plan["selected_fields"]),
                "folder_count": len(folder_plan["folders"]),
                "membership_count": len(folder_plan["memberships"]),
                "mode": args.mode,
            }
        },
    )
    print(json.dumps({"folder_plan": folder_plan, "field_report": field_report}, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build folder browse projection from base metadata and extension schema")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--metadata", default="")
    parser.add_argument("--extension-schema", default="")
    parser.add_argument("--mode", choices=["multimount", "tree", "both"], default="both")
    parser.add_argument("--include-source-root", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-coverage", type=float, default=0.25)
    parser.add_argument("--min-next-coverage", type=float, default=0.4)
    parser.add_argument("--max-cardinality-rate", type=float, default=0.65)
    parser.add_argument("--max-field-values", type=int, default=50)
    parser.add_argument("--max-folder-singleton-rate", type=float, default=0.65)
    parser.add_argument("--min-files-per-folder", type=int, default=2)
    parser.add_argument("--target-folder-size", type=int, default=18)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--max-values-per-field", type=int, default=2)
    return parser.parse_args()


def build_folder_plan(
    rows: list[dict[str, Any]],
    extension_schema: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    allowed_extension_fields = {
        field["name"]
        for field in extension_schema.get("fields", [])
        if field.get("suitable_for_folder") is True
    }
    candidate_fields = sorted(FOLDER_BASE_FIELDS | allowed_extension_fields)
    field_report = build_field_report(rows, candidate_fields, args)
    selected_fields = [
        field
        for field, report in field_report["fields"].items()
        if report["selected_for_folder"] and field not in FORBIDDEN_FOLDER_FIELDS
    ]
    selected_fields = sorted(selected_fields, key=lambda field: field_sort_key(field, field_report))

    folders: dict[str, dict[str, Any]] = {}
    memberships: list[dict[str, Any]] = []
    if args.include_source_root:
        add_source_root_memberships(rows, folders, memberships)
    if args.mode in {"multimount", "both"}:
        add_multimount_memberships(rows, selected_fields, folders, memberships, args)
    if args.mode in {"tree", "both"}:
        add_tree_memberships(rows, selected_fields, folders, memberships, args)

    plan = {
        "generated_by": "semantic_metadata_pipeline.build_folders",
        "mode": args.mode,
        "policy": {
            "allowed_base_fields": sorted(FOLDER_BASE_FIELDS),
            "allowed_extension_fields": sorted(allowed_extension_fields),
            "source_type_root": bool(args.include_source_root),
            "forbidden_fields": sorted(FORBIDDEN_FOLDER_FIELDS),
            "max_depth": args.max_depth,
        },
        "selected_fields": selected_fields,
        "folders": sorted(folders.values(), key=lambda item: item["path"]),
        "memberships": dedupe_memberships(memberships),
    }
    return plan, field_report


def build_field_report(rows: list[dict[str, Any]], fields: list[str], args: argparse.Namespace) -> dict[str, Any]:
    doc_count = max(1, len(rows))
    reports: dict[str, Any] = {}
    rejected: list[dict[str, Any]] = []
    for field in sorted(fields):
        values_by_doc = {
            row["dataset_doc_uuid"]: field_values_for_doc(row, field)
            for row in rows
        }
        values_by_doc = {doc_id: values for doc_id, values in values_by_doc.items() if values}
        values = [value for per_doc in values_by_doc.values() for value in per_doc]
        distribution = field_distribution(values_by_doc)
        unique_count = distribution["unique_values"]
        coverage = len(values_by_doc) / doc_count
        cardinality_rate = unique_count / max(1, len(values_by_doc))
        reasons = []
        if field in FORBIDDEN_FOLDER_FIELDS or field in TEXT_HEAVY_FIELDS or is_forbidden_extension_field(field) and field not in FOLDER_BASE_FIELDS:
            reasons.append("field is forbidden for folder generation")
        if coverage < args.min_coverage:
            reasons.append("coverage below folder threshold")
        if unique_count < 2:
            reasons.append("field does not partition the corpus")
        if unique_count > args.max_field_values:
            reasons.append("too many distinct values for browse")
        if cardinality_rate > args.max_cardinality_rate:
            reasons.append("cardinality too close to one value per document")
        if distribution["singleton_rate"] > args.max_folder_singleton_rate:
            reasons.append("would create too many one-file folders")
        selected = not reasons
        report = {
            "field": field,
            "coverage": round(coverage, 4),
            "unique_values": unique_count,
            "cardinality_rate": round(cardinality_rate, 4),
            "cardinality_expectation": cardinality_expectation(unique_count, doc_count),
            "singleton_rate": round(distribution["singleton_rate"], 4),
            "min_folder_size": distribution["min_folder_size"],
            "max_folder_size": distribution["max_folder_size"],
            "selected_for_folder": selected,
            "reasons": reasons,
            "top_values": distribution["value_counts"],
        }
        reports[field] = report
        if not selected:
            rejected.append({"field": field, "reasons": reasons})
    return {
        "documents": len(rows),
        "fields": reports,
        "rejected": rejected,
        "policy": {
            "folder_inputs": "metadata_base doc_type/domain/topic plus extension fields marked suitable_for_folder=true",
            "excluded": sorted(FORBIDDEN_FOLDER_FIELDS),
        },
    }


def field_sort_key(field: str, field_report: dict[str, Any]) -> tuple[float, int, str]:
    report = field_report["fields"][field]
    return (-float(report["coverage"]), int(report["unique_values"]), field)


def add_source_root_memberships(
    rows: list[dict[str, Any]],
    folders: dict[str, dict[str, Any]],
    memberships: list[dict[str, Any]],
) -> None:
    for row in rows:
        source_type = row.get("system", {}).get("source_type") or "unknown"
        path = f"/source_type={slug(source_type)}"
        folders.setdefault(
            path,
            {
                "path": path,
                "kind": "source_root",
                "field": "source_type",
                "value": source_type,
                "description": "System source_type browse root; not LLM metadata.",
                "metadata": {"field": "source_type", "system_field": True},
            },
        )
        memberships.append(
            membership(row["dataset_doc_uuid"], path, "source_type", source_type, "source_root")
        )


def add_multimount_memberships(
    rows: list[dict[str, Any]],
    selected_fields: list[str],
    folders: dict[str, dict[str, Any]],
    memberships: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    counts_by_field_value = folder_counts(rows, selected_fields)
    for row in rows:
        root = source_root(row, args)
        for field in selected_fields:
            values = field_values_for_doc(row, field)[: args.max_values_per_field]
            for value in values:
                key = (field, value_key(value))
                if counts_by_field_value.get(key, 0) < args.min_files_per_folder:
                    continue
                path = f"{root}/facets/{field}={slug(value)}"
                folders.setdefault(
                    path,
                    {
                        "path": path,
                        "kind": "facet",
                        "field": field,
                        "value": value,
                        "description": f"{field}: {value}",
                        "metadata": {"field": field, "value": value, "mount": "multimount"},
                    },
                )
                memberships.append(membership(row["dataset_doc_uuid"], path, field, value, "multimount"))


def add_tree_memberships(
    rows: list[dict[str, Any]],
    selected_fields: list[str],
    folders: dict[str, dict[str, Any]],
    memberships: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    roots: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        roots.setdefault(source_root(row, args), []).append(row)
    for root, root_rows in roots.items():
        build_tree_level(
            root_rows,
            selected_fields,
            prefix=f"{root}/tree",
            folders=folders,
            memberships=memberships,
            args=args,
            depth=0,
        )


def build_tree_level(
    rows: list[dict[str, Any]],
    remaining_fields: list[str],
    *,
    prefix: str,
    folders: dict[str, dict[str, Any]],
    memberships: list[dict[str, Any]],
    args: argparse.Namespace,
    depth: int,
) -> None:
    if depth >= args.max_depth or len(rows) <= args.target_folder_size or not remaining_fields:
        return
    field = choose_next_tree_field(rows, remaining_fields, args)
    if not field:
        return
    groups: dict[str, list[dict[str, Any]]] = {}
    display_values: dict[str, str] = {}
    for row in rows:
        values = field_values_for_doc(row, field)
        if not values:
            continue
        value = values[0]
        key = value_key(value)
        groups.setdefault(key, []).append(row)
        display_values.setdefault(key, value)
    for key, group_rows in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(group_rows) < args.min_files_per_folder:
            continue
        value = display_values[key]
        path = f"{prefix}/{field}={slug(value)}"
        folders.setdefault(
            path,
            {
                "path": path,
                "kind": "tree",
                "field": field,
                "value": value,
                "description": f"{field}: {value}",
                "metadata": {"field": field, "value": value, "mount": "tree", "depth": depth + 1},
            },
        )
        for row in group_rows:
            memberships.append(membership(row["dataset_doc_uuid"], path, field, value, "tree"))
        next_fields = [item for item in remaining_fields if item != field]
        build_tree_level(
            group_rows,
            next_fields,
            prefix=path,
            folders=folders,
            memberships=memberships,
            args=args,
            depth=depth + 1,
        )


def choose_next_tree_field(rows: list[dict[str, Any]], fields: list[str], args: argparse.Namespace) -> str:
    best: tuple[float, str] | None = None
    doc_count = max(1, len(rows))
    for field in fields:
        values_by_doc = {
            row["dataset_doc_uuid"]: field_values_for_doc(row, field)[:1]
            for row in rows
        }
        values_by_doc = {doc_id: values for doc_id, values in values_by_doc.items() if values}
        coverage = len(values_by_doc) / doc_count
        if coverage < args.min_next_coverage:
            continue
        distribution = field_distribution(values_by_doc)
        unique_count = distribution["unique_values"]
        if unique_count < 2 or unique_count > args.max_field_values:
            continue
        if distribution["singleton_rate"] > args.max_folder_singleton_rate:
            continue
        if distribution["max_folder_size"] <= args.target_folder_size and depthless_small_enough(distribution):
            score = coverage * 0.5
        else:
            score = coverage + min(unique_count, 20) / 100
        candidate = (-score, field)
        if best is None or candidate < best:
            best = candidate
    return best[1] if best else ""


def depthless_small_enough(distribution: dict[str, Any]) -> bool:
    return distribution["max_folder_size"] <= max(2, distribution["docs_with_value"] // 2)


def folder_counts(rows: list[dict[str, Any]], fields: list[str]) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        for field in fields:
            keys = {value_key(value) for value in field_values_for_doc(row, field)}
            for key in keys:
                counts[(field, key)] += 1
    return counts


def source_root(row: dict[str, Any], args: argparse.Namespace) -> str:
    if not args.include_source_root:
        return ""
    source_type = row.get("system", {}).get("source_type") or "unknown"
    return f"/source_type={slug(source_type)}"


def membership(doc_id: str, path: str, field: str, value: str, mount_kind: str) -> dict[str, Any]:
    normalized_path = path if path.startswith("/") else "/" + path
    return {
        "dataset_doc_uuid": doc_id,
        "folder_path": normalized_path,
        "field": field,
        "value": value,
        "mount_kind": mount_kind,
        "folder_metadata": {
            "field": field,
            "value": value,
            "mount_kind": mount_kind,
        },
    }


def dedupe_memberships(memberships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in memberships:
        key = (item["dataset_doc_uuid"], item["folder_path"], item["mount_kind"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
