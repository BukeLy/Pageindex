from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from pipeline_common import (
    DEFAULT_DATASET_DIR,
    DatasetDocument,
    dedupe_strings,
    is_forbidden_extension_field,
    load_dataset_documents,
    load_normalized_metadata,
    text_value,
    update_config,
    write_json,
    write_summary,
)

SCHEMA_PROVIDER_ALIASES = {
    "google": "genai",
    "gemini": "genai",
}
SUPPORTED_SCHEMA_PROVIDERS = {"openai", "genai", "offline"}
SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
CARDINALITY_EXPECTATIONS = {"low", "medium", "high", "unknown"}
FIELD_REQUIRED_KEYS = [
    "name",
    "description",
    "why_queryable",
    "coverage_estimate",
    "canonical_values",
    "synonyms",
    "suitable_for_dsl",
    "suitable_for_folder",
    "cardinality_expectation",
    "empty_policy",
    "example_values",
    "source_evidence",
]


def main() -> int:
    args = parse_args()
    normalize_schema_provider_args(args)
    run_dir = Path(args.run_dir).expanduser()
    run_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = Path(args.metadata) if args.metadata else run_dir / "metadata.normalized.jsonl"
    rows = load_normalized_metadata(metadata_path)
    sample_rows = rows[: args.sample_size]
    docs_by_id, document_error = load_sample_documents(sample_rows, args)

    sample_prompt_path = run_dir / "extension_schema_prompt.sample.md"
    sample_prompt_path.write_text(render_sample_prompt(sample_rows, docs_by_id, args), encoding="utf-8")

    if args.schema_provider == "offline":
        return write_pending_artifact(
            run_dir,
            args,
            metadata_path=metadata_path,
            sample_prompt_path=sample_prompt_path,
            reason="offline_mode",
            message="Extension Schema Discovery requires an LLM provider; offline mode cannot produce a schema.",
        )
    if not args.schema_provider:
        return write_pending_artifact(
            run_dir,
            args,
            metadata_path=metadata_path,
            sample_prompt_path=sample_prompt_path,
            reason="schema_provider_required",
            message="Rerun with --schema-provider openai or --schema-provider genai to generate extension_schema.json.",
        )
    if document_error:
        return write_pending_artifact(
            run_dir,
            args,
            metadata_path=metadata_path,
            sample_prompt_path=sample_prompt_path,
            reason="sample_documents_unavailable",
            message=document_error,
        )
    missing_doc_ids = [
        str(row["dataset_doc_uuid"])
        for row in sample_rows
        if str(row["dataset_doc_uuid"]) not in docs_by_id
    ]
    if missing_doc_ids:
        return write_pending_artifact(
            run_dir,
            args,
            metadata_path=metadata_path,
            sample_prompt_path=sample_prompt_path,
            reason="sample_documents_missing",
            message=f"Dataset did not provide sample documents for {len(missing_doc_ids)} metadata rows.",
            details={"missing_doc_ids": missing_doc_ids[:20]},
        )

    try:
        provider_schema = call_schema_provider(sample_prompt_path.read_text(encoding="utf-8"), args)
    except Exception as exc:  # noqa: BLE001 - provider failures should leave an explicit pending artifact.
        return write_pending_artifact(
            run_dir,
            args,
            metadata_path=metadata_path,
            sample_prompt_path=sample_prompt_path,
            reason="provider_call_failed",
            message=str(exc),
        )

    schema, audit = audit_extension_schema(provider_schema, rows, sample_rows, args)
    schema_path = run_dir / "extension_schema.json"
    audit_path = run_dir / "extension_schema_audit.json"
    if audit["status"] != "ready":
        rejected_schema_path = run_dir / "extension_schema.rejected.json"
        stale_schema_path = neutralize_schema_artifact(run_dir, reason=audit["status"])
        write_json(rejected_schema_path, schema)
        write_json(audit_path, audit)
        update_config(
            run_dir,
            "extension_schema",
            {
                "metadata_path": str(metadata_path),
                "schema_path": "",
                "rejected_schema_path": str(rejected_schema_path),
                "audit_path": str(audit_path),
                "stale_schema_path": stale_schema_path,
                "sample_prompt_path": str(sample_prompt_path),
                "schema_provider": args.schema_provider,
                "schema_model": args.schema_model,
                "sample_size": len(sample_rows),
                "max_doc_chars": args.max_doc_chars,
                "status": audit["status"],
            },
        )
        write_summary(
            run_dir,
            {
                "extension_schema": {
                    "documents": len(rows),
                    "sampled_documents": len(sample_rows),
                    "schema_provider": args.schema_provider,
                    "schema_model": args.schema_model,
                    "status": audit["status"],
                    "accepted_fields": len(schema["fields"]),
                    "rejected_fields": len(audit["rejected_fields"]),
                    "rejected_schema_path": str(rejected_schema_path),
                }
            },
        )
        print(json.dumps({"schema": schema, "audit": audit}, ensure_ascii=False, indent=2))
        return 2

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
            "schema_provider": args.schema_provider,
            "schema_model": args.schema_model,
            "sample_size": len(sample_rows),
            "max_doc_chars": args.max_doc_chars,
            "status": audit["status"],
        },
    )
    write_summary(
        run_dir,
        {
            "extension_schema": {
                "documents": len(rows),
                "sampled_documents": len(sample_rows),
                "schema_provider": args.schema_provider,
                "schema_model": args.schema_model,
                "status": audit["status"],
                "accepted_fields": len(schema["fields"]),
                "rejected_fields": len(audit["rejected_fields"]),
            }
        },
    )
    print(json.dumps({"schema": schema, "audit": audit}, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover extension metadata fields with an LLM provider")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--metadata", default="")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_DIR))
    parser.add_argument("--sample-size", type=int, default=25)
    parser.add_argument("--max-doc-chars", type=int, default=4000)
    parser.add_argument("--schema-provider", default=os.environ.get("PIFS_EXTENSION_SCHEMA_PROVIDER", ""))
    parser.add_argument("--schema-model", default=os.environ.get("PIFS_EXTENSION_SCHEMA_MODEL", ""))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", ""))
    parser.add_argument("--prompt-cache-key", default=os.environ.get("PIFS_EXTENSION_SCHEMA_PROMPT_CACHE_KEY", ""))
    parser.add_argument(
        "--prompt-cache-retention",
        choices=["", "in_memory", "24h"],
        default=os.environ.get("PIFS_EXTENSION_SCHEMA_PROMPT_CACHE_RETENTION", ""),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Write extension_schema.pending.json and fail clearly instead of pretending to discover fields.",
    )
    return parser.parse_args()


def normalize_schema_provider_args(args: argparse.Namespace) -> None:
    if args.sample_size < 1:
        raise SystemExit("--sample-size must be positive")
    if args.max_doc_chars < 0:
        raise SystemExit("--max-doc-chars must be zero or positive")
    provider = "offline" if args.offline else str(args.schema_provider or "").strip().lower()
    args.schema_provider = SCHEMA_PROVIDER_ALIASES.get(provider, provider)
    if args.schema_provider and args.schema_provider not in SUPPORTED_SCHEMA_PROVIDERS:
        raise SystemExit("--schema-provider must be one of: openai, genai")
    if not args.schema_model:
        if args.schema_provider == "genai":
            args.schema_model = os.environ.get("PIFS_GENAI_EXTENSION_SCHEMA_MODEL", "gemini-3.5-flash")
        elif args.schema_provider == "openai":
            args.schema_model = os.environ.get("PIFS_OPENAI_EXTENSION_SCHEMA_MODEL", "gpt-5-nano")


def load_sample_documents(
    sample_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[dict[str, DatasetDocument], str]:
    sample_ids = {str(row["dataset_doc_uuid"]) for row in sample_rows}
    if not sample_ids:
        return {}, ""
    try:
        docs = load_dataset_documents(args.dataset, target_doc_ids=sample_ids)
    except Exception as exc:  # noqa: BLE001 - converted to a pending artifact for the CLI.
        return {}, f"Could not load sample documents from {args.dataset}: {exc}"
    return {doc.dataset_doc_uuid: doc for doc in docs}, ""


def render_sample_prompt(
    sample_rows: list[dict[str, Any]],
    docs_by_id: dict[str, DatasetDocument],
    args: argparse.Namespace,
) -> str:
    prompt_template = (
        Path(__file__).resolve().parent / "prompts" / "extension_schema_discovery.md"
    ).read_text(encoding="utf-8")
    sample = []
    for index, row in enumerate(sample_rows, 1):
        doc_id = str(row["dataset_doc_uuid"])
        doc = docs_by_id.get(doc_id)
        sample.append(sample_payload(index, row, doc, max_doc_chars=args.max_doc_chars))
    return prompt_template.replace("{sample_json}", json.dumps(sample, ensure_ascii=False, indent=2))


def sample_payload(
    index: int,
    row: dict[str, Any],
    doc: DatasetDocument | None,
    *,
    max_doc_chars: int,
) -> dict[str, Any]:
    system = row.get("system") or {}
    text = doc.content if doc else ""
    if max_doc_chars > 0:
        text = text[:max_doc_chars]
    return {
        "sample_number": index,
        "document": {
            "source_type": doc.source_type if doc else text_value(system.get("source_type")),
            "title": doc.title if doc else text_value(system.get("title")),
            "content_type": doc.content_type if doc else text_value(system.get("content_type")),
            "text": text,
            "text_truncated": bool(doc and max_doc_chars > 0 and len(doc.content) > max_doc_chars),
        },
        "normalized_metadata": {
            "metadata_base": row.get("metadata_base") or {},
            "extension_candidates": row.get("extension_candidates") or {},
        },
    }


def call_schema_provider(prompt: str, args: argparse.Namespace) -> dict[str, Any]:
    if args.schema_provider == "openai":
        return call_openai_schema_provider(prompt, args)
    if args.schema_provider == "genai":
        return call_genai_schema_provider(prompt, args)
    raise RuntimeError("Extension Schema Discovery requires --schema-provider openai or genai")


def call_openai_schema_provider(prompt: str, args: argparse.Namespace) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url=args.base_url or None)
    body: dict[str, Any] = {
        "model": args.schema_model,
        "messages": [
            {
                "role": "system",
                "content": "You are the PIFS Extension Schema Discovery capability. Return strict JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "pifs_extension_schema_discovery",
                "strict": True,
                "schema": extension_schema_response_schema(),
            },
        },
    }
    extra_body = {}
    if args.prompt_cache_key:
        extra_body["prompt_cache_key"] = args.prompt_cache_key
    if args.prompt_cache_retention:
        extra_body["prompt_cache_retention"] = args.prompt_cache_retention
    if extra_body:
        body["extra_body"] = extra_body
    response = client.chat.completions.create(**body)
    return json.loads(response.choices[0].message.content or "{}")


def call_genai_schema_provider(prompt: str, args: argparse.Namespace) -> dict[str, Any]:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is required for --schema-provider genai") from exc
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for --schema-provider genai")
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=args.schema_model,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": extension_schema_response_schema(),
        },
    )
    content = text_value(getattr(response, "text", ""))
    if not content and getattr(response, "parsed", None) is not None:
        parsed = getattr(response, "parsed")
        if isinstance(parsed, dict):
            return parsed
        content = json.dumps(parsed, ensure_ascii=False)
    return json.loads(content or "{}")


def extension_schema_response_schema() -> dict[str, Any]:
    field_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": FIELD_REQUIRED_KEYS,
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "why_queryable": {"type": "string"},
            "coverage_estimate": {"type": "number"},
            "canonical_values": {"type": "array", "items": {"type": "string"}},
            "synonyms": {
                "type": "object",
                "additionalProperties": {"type": "array", "items": {"type": "string"}},
            },
            "suitable_for_dsl": {"type": "boolean"},
            "suitable_for_folder": {"type": "boolean"},
            "cardinality_expectation": {"type": "string", "enum": sorted(CARDINALITY_EXPECTATIONS)},
            "empty_policy": {"type": "string"},
            "example_values": {"type": "array", "items": {"type": "string"}},
            "source_evidence": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["fields"],
        "properties": {
            "fields": {
                "type": "array",
                "items": field_schema,
            }
        },
    }


def audit_extension_schema(
    provider_schema: dict[str, Any],
    rows: list[dict[str, Any]],
    sample_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_fields = provider_schema.get("fields") if isinstance(provider_schema, dict) else None
    rejected_fields: list[dict[str, Any]] = []
    accepted_fields: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    if not isinstance(raw_fields, list):
        rejected_fields.append(
            {
                "index": None,
                "name": "",
                "reasons": ["provider response must be an object with a fields array"],
            }
        )
        raw_fields = []
    for index, raw_field in enumerate(raw_fields):
        field, reasons = normalize_provider_field(raw_field)
        name = field.get("name") if field else text_value(raw_field.get("name")) if isinstance(raw_field, dict) else ""
        if name in seen_names:
            reasons.append("duplicate field name")
        if reasons:
            rejected_fields.append({"index": index, "name": name, "reasons": reasons})
            continue
        accepted_fields.append(field)
        seen_names.add(name)

    status = "ready" if not rejected_fields else "rejected"
    audit = {
        "status": status,
        "documents": len(rows),
        "sampled_documents": len(sample_rows),
        "schema_provider": args.schema_provider,
        "schema_model": args.schema_model,
        "accepted_fields": [field["name"] for field in accepted_fields],
        "rejected_fields": rejected_fields,
        "policy": {
            "llm_required": True,
            "audit_only": [
                "strict JSON object shape",
                "snake_case field names",
                "forbidden field names and base metadata fields",
                "field-level boolean/list/string formatting",
            ],
            "not_performed_by_code": [
                "coverage-based field selection",
                "cardinality-based field selection",
                "field-name heuristic discovery",
                "source_type special-case discovery",
            ],
        },
    }
    schema = {
        "generated_by": "llm_extension_schema_discovery",
        "status": status,
        "schema_provider": args.schema_provider,
        "schema_model": args.schema_model,
        "fields": accepted_fields,
    }
    return schema, audit


def normalize_provider_field(raw_field: Any) -> tuple[dict[str, Any], list[str]]:
    reasons: list[str] = []
    if not isinstance(raw_field, dict):
        return {}, ["field entry must be an object"]
    missing = [key for key in FIELD_REQUIRED_KEYS if key not in raw_field]
    if missing:
        reasons.append(f"missing required keys: {', '.join(missing)}")
    name = text_value(raw_field.get("name"))
    if not name:
        reasons.append("field name is required")
    elif not SNAKE_CASE_RE.match(name):
        reasons.append("field name must be snake_case")
    elif is_forbidden_extension_field(name):
        reasons.append("field name is forbidden for extension schema")
    coverage_estimate = raw_field.get("coverage_estimate")
    if isinstance(coverage_estimate, bool) or not isinstance(coverage_estimate, (int, float)):
        coverage_number = 0.0
        reasons.append("coverage_estimate must be a JSON number")
    else:
        coverage_number = float(coverage_estimate)
    if coverage_number < 0.0 or coverage_number > 1.0:
        reasons.append("coverage_estimate must be between 0 and 1")
    suitable_for_dsl = raw_field.get("suitable_for_dsl")
    suitable_for_folder = raw_field.get("suitable_for_folder")
    if not isinstance(suitable_for_dsl, bool):
        reasons.append("suitable_for_dsl must be boolean")
        suitable_for_dsl = False
    if not isinstance(suitable_for_folder, bool):
        reasons.append("suitable_for_folder must be boolean")
        suitable_for_folder = False
    if suitable_for_dsl is False and suitable_for_folder is False:
        reasons.append("field must support suitable_for_dsl or suitable_for_folder")
    cardinality = text_value(raw_field.get("cardinality_expectation")).lower() or "unknown"
    if cardinality not in CARDINALITY_EXPECTATIONS:
        reasons.append("cardinality_expectation must be low, medium, high, or unknown")
        cardinality = "unknown"
    synonyms, synonym_reasons = normalize_synonyms(raw_field.get("synonyms"))
    reasons.extend(synonym_reasons)
    canonical_values, canonical_reasons = normalize_string_list_field(
        raw_field.get("canonical_values"),
        "canonical_values",
        require_non_empty=True,
    )
    reasons.extend(canonical_reasons)
    example_values, example_reasons = normalize_string_list_field(
        raw_field.get("example_values"),
        "example_values",
    )
    reasons.extend(example_reasons)
    field = {
        "name": name,
        "description": text_value(raw_field.get("description")),
        "why_queryable": text_value(raw_field.get("why_queryable")),
        "coverage_estimate": round(max(0.0, min(1.0, coverage_number)), 4),
        "canonical_values": canonical_values,
        "synonyms": synonyms,
        "suitable_for_dsl": bool(suitable_for_dsl),
        "suitable_for_folder": bool(suitable_for_folder),
        "cardinality_expectation": cardinality,
        "empty_policy": text_value(raw_field.get("empty_policy")),
        "example_values": example_values,
        "source_evidence": text_value(raw_field.get("source_evidence")),
    }
    for string_key in ["description", "why_queryable", "empty_policy", "source_evidence"]:
        if not field[string_key]:
            reasons.append(f"{string_key} must be a non-empty string")
    return field, reasons


def normalize_synonyms(value: Any) -> tuple[dict[str, list[str]], list[str]]:
    if not isinstance(value, dict):
        return {}, ["synonyms must be an object"]
    synonyms: dict[str, list[str]] = {}
    reasons: list[str] = []
    for key, raw_values in value.items():
        canonical = key if isinstance(key, str) else ""
        canonical = canonical.strip()
        if not canonical:
            reasons.append("synonym keys must be non-empty strings")
            continue
        values, value_reasons = normalize_string_list_field(raw_values, f"synonyms.{canonical}")
        reasons.extend(value_reasons)
        synonyms[canonical] = values
    return synonyms, reasons


def normalize_string_list_field(
    value: Any,
    field_name: str,
    *,
    require_non_empty: bool = False,
) -> tuple[list[str], list[str]]:
    if not isinstance(value, list):
        return [], [f"{field_name} must be a list of strings"]
    reasons: list[str] = []
    strings: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            reasons.append(f"{field_name}[{index}] must be a string")
            continue
        strings.append(item)
    values = dedupe_strings(strings)
    if require_non_empty and not values:
        reasons.append(f"{field_name} must contain at least one string")
    return values, reasons


def neutralize_schema_artifact(run_dir: Path, *, reason: str) -> str:
    schema_path = run_dir / "extension_schema.json"
    if not schema_path.exists():
        return ""
    safe_reason = re.sub(r"[^a-z0-9_-]+", "-", reason.lower()).strip("-") or "not-ready"
    stale_path = run_dir / "extension_schema.stale.json"
    if stale_path.exists():
        stale_path = run_dir / f"extension_schema.stale.{safe_reason}.{int(time.time())}.json"
    schema_path.replace(stale_path)
    return str(stale_path)


def write_pending_artifact(
    run_dir: Path,
    args: argparse.Namespace,
    *,
    metadata_path: Path,
    sample_prompt_path: Path,
    reason: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> int:
    pending_path = run_dir / "extension_schema.pending.json"
    stale_schema_path = neutralize_schema_artifact(run_dir, reason=reason)
    pending = {
        "status": "pending",
        "reason": reason,
        "message": message,
        "metadata_path": str(metadata_path),
        "sample_prompt_path": str(sample_prompt_path),
        "schema_provider": args.schema_provider,
        "schema_model": args.schema_model,
        "stale_schema_path": stale_schema_path,
        "next_step": "Configure --schema-provider openai or --schema-provider genai and rerun discovery.",
    }
    if details:
        pending["details"] = details
    write_json(pending_path, pending)
    update_config(
        run_dir,
        "extension_schema",
        {
            "status": "pending",
            "reason": reason,
            "metadata_path": str(metadata_path),
            "pending_path": str(pending_path),
            "stale_schema_path": stale_schema_path,
            "sample_prompt_path": str(sample_prompt_path),
            "schema_provider": args.schema_provider,
            "schema_model": args.schema_model,
        },
    )
    write_summary(
        run_dir,
        {
            "extension_schema": {
                "status": "pending",
                "reason": reason,
                "pending_path": str(pending_path),
            }
        },
    )
    print(json.dumps(pending, ensure_ascii=False, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
