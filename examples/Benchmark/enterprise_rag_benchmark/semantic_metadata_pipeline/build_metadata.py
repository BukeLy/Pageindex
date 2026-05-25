from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from pipeline_common import (
    DEFAULT_DOC_PROFILES,
    DEFAULT_RESULTS_DIR,
    DatasetDocument,
    dedupe_strings,
    ensure_list,
    ensure_run_dir,
    has_unsafe_generation,
    is_forbidden_extension_field,
    is_structured_entities,
    is_structured_relations,
    load_dataset_documents,
    load_metadata_sources,
    normalize_entity,
    normalize_relation,
    provenance,
    read_json,
    source_provenance,
    text_value,
    update_config,
    write_json,
    write_jsonl,
    write_summary,
)


BASE_FIELD_DEFAULTS = {
    "doc_type": "",
    "domain": "",
    "topic": "",
    "summary": "",
    "entities": [],
    "relations": [],
    "constraints": [],
    "retrieval_cues": [],
}
BASE_SOURCE_PRIORITY = {
    "doc_type": ["doc_type"],
    "domain": ["domain", "product_or_system", "topic_cluster_hint"],
    "topic": ["topic", "primary_topic", "topic_cluster_hint"],
}
PROFILE_EXTENSION_FIELDS = {
    "primary_topic",
    "secondary_topics",
    "topic_cluster_hint",
    "time_period",
    "communication_channel",
    "product_or_system",
    "primary_entity",
    "action_or_event",
}
TEXT_HEAVY_PROFILE_FIELDS = {
    "semantic_summary",
    "summary",
    "entities",
    "relations",
    "constraints",
    "search_terms",
    "folder_hints",
    "retrieval_cues",
}
ALL_METADATA_FIELDS = set(BASE_FIELD_DEFAULTS)
REUSE_SIGNAL_FIELDS = {"summary", "entities", "relations", "retrieval_cues"}
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SUMMARY_ONLY_FIELDS = {"summary"}
SAFE_REUSABLE_BASE_METHODS = {
    "llm",
    "normalized_from_llm",
    "generated_incrementally",
    "source_metadata",
}


def main() -> int:
    args = parse_args()
    run_dir = resolve_run_dir(args)
    apply_collect_config_defaults(args, run_dir)
    normalize_provider_args(args)
    validate_generation_args(args)
    metadata_fields = selected_metadata_fields(args)
    prompt_cache_key = effective_prompt_cache_key(args, metadata_fields)
    if args.metadata_batch_mode == "submit" and batch_manifest_path(run_dir, args).exists():
        manifest = read_json(batch_manifest_path(run_dir, args))
        manifest = submit_metadata_batches(manifest, args=args, manifest_path=batch_manifest_path(run_dir, args))
        write_json(batch_manifest_path(run_dir, args), manifest)
        print(
            json.dumps(
                {
                    "run_name": run_dir.name,
                    "metadata_batch_mode": "submit",
                    "batch_manifest_path": str(batch_manifest_path(run_dir, args)),
                    "request_count": manifest.get("request_count", 0),
                    "batch_count": manifest.get("batch_count", 0),
                    "metadata_provider": manifest.get("metadata_provider") or args.metadata_provider,
                    "submitted_batches": sum(1 for batch in manifest.get("batches", []) if batch.get("batch_id")),
                    "status": manifest.get("status"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    metadata_sources = args.metadata_source or []
    if not metadata_sources and DEFAULT_DOC_PROFILES.exists():
        metadata_sources = [str(DEFAULT_DOC_PROFILES)]

    source_by_doc = load_metadata_sources(metadata_sources) if metadata_sources else {}
    target_doc_ids = set(source_by_doc) if args.metadata_source_docs_only else None
    docs = load_dataset_documents(args.dataset, max_docs=args.max_docs, target_doc_ids=target_doc_ids)
    if args.metadata_source_docs_only and not docs:
        raise SystemExit("No dataset documents matched --metadata-source docs")

    generated_by_doc: dict[str, dict[str, Any]] = {}
    batch_report: dict[str, Any] | None = None
    if args.metadata_batch_mode == "collect":
        generated_by_doc, batch_report = collect_metadata_batch_results(
            run_dir,
            args=args,
        )

    rows: list[dict[str, Any]] = []
    accepted = 0
    generated = 0
    missing_docs = 0
    unsafe_legacy_fields: list[dict[str, Any]] = []
    missing_reusable: list[str] = []

    for doc in docs:
        normalized = normalize_document_metadata(
            doc,
            source_by_doc.get(doc.dataset_doc_uuid, []),
            metadata_fields=metadata_fields,
            generate_missing=args.generate_missing,
            provider=args.metadata_provider,
            model=args.metadata_model,
            base_url=args.base_url,
            max_doc_chars=args.max_doc_chars,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=args.prompt_cache_retention,
            generated_metadata=generated_by_doc.get(doc.dataset_doc_uuid),
        )
        if normalized["reuse_status"] == "none":
            missing_docs += 1
            missing_reusable.append(doc.dataset_doc_uuid)
        accepted += normalized["reuse_status"] == "reused"
        generated += normalized["reuse_status"] == "generated"
        unsafe_legacy_fields.extend(normalized.get("unsafe_legacy_generation", []))
        rows.append(normalized)

    if args.reuse_generated_only and missing_reusable:
        raise SystemExit(
            "--reuse-generated-only was set, but no reusable LLM metadata was found for: "
            + ", ".join(missing_reusable[:10])
        )

    batch_manifest: dict[str, Any] | None = None
    if args.metadata_batch_mode in {"prepare", "submit"}:
        docs_by_id = {doc.dataset_doc_uuid: doc for doc in docs}
        missing_generation_docs = [
            docs_by_id[row["dataset_doc_uuid"]]
            for row in rows
            if needs_generation(row, metadata_fields)
        ]
        manifest_path = batch_manifest_path(run_dir, args)
        if args.metadata_batch_mode == "submit" and manifest_path.exists():
            batch_manifest = read_json(manifest_path)
        else:
            batch_manifest = prepare_metadata_batch(
                run_dir,
                docs=missing_generation_docs,
                args=args,
            )
        if args.metadata_batch_mode == "submit" and batch_manifest.get("batches"):
            batch_manifest = submit_metadata_batches(
                batch_manifest,
                args=args,
                manifest_path=batch_manifest_path(run_dir, args),
            )
            write_json(batch_manifest_path(run_dir, args), batch_manifest)

    metadata_path = run_dir / "metadata.normalized.jsonl"
    report_path = run_dir / "metadata_reuse_report.json"
    write_jsonl(metadata_path, rows)
    report = {
        "run_name": run_dir.name,
        "dataset": str(args.dataset),
        "metadata_sources": metadata_sources,
        "documents": len(rows),
        "reused_documents": accepted,
        "generated_documents": generated,
        "documents_without_safe_reuse": missing_docs,
        "reuse_generated_only": args.reuse_generated_only,
        "generate_missing": args.generate_missing,
        "metadata_batch_mode": args.metadata_batch_mode,
        "metadata_provider": args.metadata_provider,
        "metadata_fields": sorted(metadata_fields),
        "metadata_prompt_file": metadata_prompt_file(metadata_fields).name,
        "response_format": "json_schema_strict",
        "prompt_cache_key": prompt_cache_key,
        "prompt_cache_retention": args.prompt_cache_retention,
        "batch_documents_requested": (batch_manifest or {}).get("request_count", 0),
        "batch_manifest_path": str(batch_manifest_path(run_dir, args))
        if args.metadata_batch_mode != "off"
        else "",
        "accepted_policy": {
            "summary": "semantic_summary only, or generated_incrementally by the metadata prompt",
            "entities": "structured LLM entity objects only",
            "relations": "structured LLM subject-relation-object objects only",
            "retrieval_cues": "LLM search_terms/folder_hints/retrieval_cues, routed to lexical payloads only",
        },
        "rejected_policy": [
            "compact_summary(title, preview)",
            "title + preview truncation",
            "keyword_terms rule entities",
            "infer_predicate rule relations",
            "fulltext preview embedding summary",
        ],
        "unsafe_legacy_fields": unsafe_legacy_fields,
        "missing_reusable_doc_ids_sample": missing_reusable[:25],
    }
    if batch_report is not None:
        report["batch_collect_report"] = batch_report
    write_json(report_path, report)
    update_config(
        run_dir,
        "metadata",
        {
            "dataset": str(args.dataset),
            "metadata_sources": metadata_sources,
            "metadata_path": str(metadata_path),
            "reuse_report_path": str(report_path),
            "max_docs": args.max_docs,
            "metadata_source_docs_only": args.metadata_source_docs_only,
            "metadata_batch_mode": args.metadata_batch_mode,
            "metadata_provider": args.metadata_provider,
            "metadata_fields": sorted(metadata_fields),
            "metadata_model": args.metadata_model,
            "max_doc_chars": args.max_doc_chars,
            "document_text_policy": document_text_policy(args.max_doc_chars),
            "metadata_prompt_file": metadata_prompt_file(metadata_fields).name,
            "response_format": "json_schema_strict",
            "prompt_cache_key": prompt_cache_key,
            "prompt_cache_retention": args.prompt_cache_retention,
            "batch_manifest_path": str(batch_manifest_path(run_dir, args))
            if args.metadata_batch_mode != "off"
            else "",
        },
    )
    write_summary(
        run_dir,
        {
            "metadata": {
                "documents": len(rows),
                "reused_documents": accepted,
                "generated_documents": generated,
                "documents_without_safe_reuse": missing_docs,
                "unsafe_legacy_fields": len(unsafe_legacy_fields),
            }
        },
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build normalized EnterpriseRAG metadata for the semantic pipeline")
    parser.add_argument("--dataset", default=str(Path(__file__).resolve().parent.parent / "dataset"))
    parser.add_argument("--run-name", default="")
    parser.add_argument("--run-dir", default="", help="Use an existing or explicit run directory, mainly for batch collection.")
    parser.add_argument("--max-docs", type=int, default=0)
    parser.add_argument("--metadata-source", action="append", default=[])
    parser.add_argument(
        "--metadata-source-docs-only",
        action="store_true",
        help="Load only dataset documents that appear in the provided metadata sources.",
    )
    parser.add_argument("--reuse-generated-only", action="store_true")
    parser.add_argument(
        "--generate-missing",
        action="store_true",
        help="Legacy synchronous generation path. For full runs use --metadata-batch-mode prepare/submit/collect.",
    )
    parser.add_argument("--metadata-provider", default=os.environ.get("PIFS_METADATA_PROVIDER", "openai"))
    parser.add_argument("--metadata-model", default=os.environ.get("PIFS_METADATA_MODEL", ""))
    parser.add_argument(
        "--metadata-fields",
        default="all",
        help=(
            "Comma-separated metadata_base fields to reuse/generate. Use 'all' for the full base schema, "
            "or 'summary' for a summary-only full-corpus run."
        ),
    )
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument(
        "--max-doc-chars",
        type=int,
        default=0,
        help="0 sends the full document text. Positive values are an explicit legacy character cap.",
    )
    parser.add_argument(
        "--prompt-cache-key",
        default=os.environ.get("PIFS_METADATA_PROMPT_CACHE_KEY", ""),
        help="Optional OpenAI prompt_cache_key. Defaults to a stable key derived from the selected metadata fields.",
    )
    parser.add_argument(
        "--prompt-cache-retention",
        choices=["", "in_memory", "24h"],
        default=os.environ.get("PIFS_METADATA_PROMPT_CACHE_RETENTION", ""),
        help="Optional OpenAI prompt_cache_retention request parameter.",
    )
    parser.add_argument(
        "--metadata-batch-mode",
        choices=["off", "prepare", "submit", "collect"],
        default="off",
        help=(
            "Provider Batch workflow for missing metadata: prepare writes JSONL, submit uploads and creates "
            "provider batch jobs, collect downloads completed results and merges them."
        ),
    )
    parser.add_argument("--batch-manifest", default="")
    parser.add_argument("--batch-completion-window", default="24h")
    parser.add_argument("--batch-max-requests", type=int, default=50000)
    parser.add_argument("--batch-max-bytes", type=int, default=180_000_000)
    parser.add_argument(
        "--genai-submit-active-token-budget",
        type=int,
        default=int(os.environ.get("PIFS_GENAI_SUBMIT_ACTIVE_TOKEN_BUDGET", "90000000")),
        help=(
            "Estimated active GenerateContentBatch token budget for GenAI submit. "
            "0 disables local throttling. Default leaves headroom under the observed 100M project/model quota."
        ),
    )
    parser.add_argument(
        "--genai-submit-token-estimate-per-byte",
        type=float,
        default=float(os.environ.get("PIFS_GENAI_SUBMIT_TOKEN_ESTIMATE_PER_BYTE", "0.24")),
        help="Conservative token estimate for GenAI batch JSONL bytes.",
    )
    parser.add_argument(
        "--genai-submit-max-new-batches",
        type=int,
        default=int(os.environ.get("PIFS_GENAI_SUBMIT_MAX_NEW_BATCHES", "0")),
        help="Optional cap on newly created GenAI batches per submit run. 0 means budget-bound only.",
    )
    parser.add_argument("--allow-partial-batch-results", action="store_true")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR), help=argparse.SUPPRESS)
    return parser.parse_args()


def resolve_run_dir(args: argparse.Namespace) -> Path:
    if args.run_dir:
        run_dir = Path(args.run_dir).expanduser()
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir
    return ensure_run_dir(args.run_name)


def apply_collect_config_defaults(args: argparse.Namespace, run_dir: Path) -> None:
    if args.metadata_batch_mode not in {"submit", "collect"}:
        return
    config_path = run_dir / "config.json"
    if not config_path.exists():
        return
    config = read_json(config_path)
    metadata_config = config.get("metadata") if isinstance(config, dict) else {}
    if not isinstance(metadata_config, dict):
        return
    default_dataset = str(Path(__file__).resolve().parent.parent / "dataset")
    if args.dataset == default_dataset and metadata_config.get("dataset"):
        args.dataset = str(metadata_config["dataset"])
    if not args.metadata_source and metadata_config.get("metadata_sources"):
        args.metadata_source = list(metadata_config["metadata_sources"])
    if args.max_docs == 0 and metadata_config.get("max_docs"):
        args.max_docs = int(metadata_config["max_docs"])
    if not args.metadata_source_docs_only and metadata_config.get("metadata_source_docs_only"):
        args.metadata_source_docs_only = bool(metadata_config["metadata_source_docs_only"])
    if args.max_doc_chars == 0 and metadata_config.get("max_doc_chars") is not None:
        args.max_doc_chars = int(metadata_config["max_doc_chars"])
    if args.metadata_fields == "all" and metadata_config.get("metadata_fields"):
        args.metadata_fields = ",".join(str(item) for item in metadata_config["metadata_fields"])
    default_provider = os.environ.get("PIFS_METADATA_PROVIDER", "openai")
    if args.metadata_provider == default_provider and metadata_config.get("metadata_provider"):
        args.metadata_provider = str(metadata_config["metadata_provider"])
    if not args.prompt_cache_key and metadata_config.get("prompt_cache_key"):
        args.prompt_cache_key = str(metadata_config["prompt_cache_key"])
    if not args.prompt_cache_retention and metadata_config.get("prompt_cache_retention"):
        args.prompt_cache_retention = str(metadata_config["prompt_cache_retention"])
    if not args.metadata_model and metadata_config.get("metadata_model"):
        args.metadata_model = str(metadata_config["metadata_model"])


def normalize_provider_args(args: argparse.Namespace) -> None:
    provider = str(args.metadata_provider or "openai").strip().lower()
    aliases = {
        "google": "genai",
        "gemini": "genai",
    }
    args.metadata_provider = aliases.get(provider, provider)
    if not args.metadata_model:
        if args.metadata_provider == "genai":
            args.metadata_model = os.environ.get("PIFS_GENAI_METADATA_MODEL", "gemini-3.5-flash")
        else:
            args.metadata_model = os.environ.get("PIFS_OPENAI_METADATA_MODEL", "gpt-5-nano")


def validate_generation_args(args: argparse.Namespace) -> None:
    if args.metadata_provider not in {"openai", "genai"}:
        raise SystemExit("--metadata-provider must be one of: openai, genai")
    if args.metadata_batch_mode != "off" and args.generate_missing:
        raise SystemExit("--generate-missing is the legacy synchronous path; use either it or --metadata-batch-mode, not both")
    if args.metadata_batch_mode in {"prepare", "submit"} and args.reuse_generated_only:
        raise SystemExit("--reuse-generated-only conflicts with metadata batch generation")
    if args.batch_max_requests < 1:
        raise SystemExit("--batch-max-requests must be positive")
    if args.batch_max_bytes < 1024:
        raise SystemExit("--batch-max-bytes is too small")
    if args.max_doc_chars < 0:
        raise SystemExit("--max-doc-chars must be >= 0")
    if args.genai_submit_active_token_budget < 0:
        raise SystemExit("--genai-submit-active-token-budget must be >= 0")
    if args.genai_submit_token_estimate_per_byte <= 0:
        raise SystemExit("--genai-submit-token-estimate-per-byte must be > 0")
    if args.genai_submit_max_new_batches < 0:
        raise SystemExit("--genai-submit-max-new-batches must be >= 0")
    selected_metadata_fields(args)


def selected_metadata_fields(args: argparse.Namespace) -> set[str]:
    raw = str(getattr(args, "metadata_fields", "all") or "all").strip().lower()
    if raw in {"", "all", "*"}:
        return set(ALL_METADATA_FIELDS)
    fields = {item.strip() for item in raw.split(",") if item.strip()}
    unknown = sorted(fields - ALL_METADATA_FIELDS)
    if unknown:
        raise SystemExit(f"Unknown --metadata-fields values: {', '.join(unknown)}")
    if not fields:
        raise SystemExit("--metadata-fields must select at least one field")
    return fields


def effective_prompt_cache_key(args: argparse.Namespace, metadata_fields: set[str]) -> str:
    if getattr(args, "metadata_provider", "openai") != "openai":
        return ""
    if args.prompt_cache_key:
        return str(args.prompt_cache_key)
    if metadata_fields == SUMMARY_ONLY_FIELDS:
        return "pifs-enterpriserag-summary-v1"
    field_suffix = "-".join(sorted(metadata_fields))
    return f"pifs-enterpriserag-metadata-v1-{field_suffix}"


def document_text_policy(max_doc_chars: int) -> str:
    if max_doc_chars > 0:
        return f"explicit_char_cap:{max_doc_chars}"
    return "full_text_no_truncation"


def batch_manifest_path(run_dir: Path, args: argparse.Namespace) -> Path:
    return Path(args.batch_manifest).expanduser() if args.batch_manifest else run_dir / "metadata_batch_manifest.json"


def normalize_document_metadata(
    doc: DatasetDocument,
    sources: list[dict[str, Any]],
    *,
    metadata_fields: set[str],
    generate_missing: bool,
    provider: str,
    model: str,
    base_url: str | None,
    max_doc_chars: int,
    prompt_cache_key: str,
    prompt_cache_retention: str,
    generated_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    system, system_provenance = system_fields(doc, sources)
    metadata_base = dict(BASE_FIELD_DEFAULTS)
    base_provenance = {
        field: missing_provenance(field)
        for field in BASE_FIELD_DEFAULTS
    }
    extension_candidates: dict[str, Any] = {}
    extension_provenance: dict[str, Any] = {}
    unsafe: list[dict[str, Any]] = []

    normalized_sources = [source for source in sources if source.get("source_kind") == "normalized_metadata"]
    profile_sources = [source for source in sources if source.get("source_kind") == "doc_profiles"]
    registered_sources = [source for source in sources if source.get("source_kind") == "registered_files"]

    for source in normalized_sources:
        merge_normalized_metadata(
            source,
            metadata_base,
            base_provenance,
            extension_candidates,
            extension_provenance,
        )

    for source in profile_sources:
        profile = source.get("payload") or {}
        if not isinstance(profile, dict):
            continue
        merge_profile_base(profile, source, metadata_base, base_provenance, metadata_fields=metadata_fields)
        merge_profile_extension_candidates(profile, source, extension_candidates, extension_provenance)

    for source in registered_sources:
        metadata = source.get("metadata") or {}
        if isinstance(metadata, dict):
            merge_registered_metadata(
                metadata,
                source,
                metadata_base,
                base_provenance,
                extension_candidates,
                extension_provenance,
                metadata_fields=metadata_fields,
            )

    reuse_status = "reused" if safe_reuse_present(metadata_base, base_provenance, metadata_fields=metadata_fields) else "none"
    if reuse_status == "none" and generated_metadata is not None:
        merge_generated_base(generated_metadata, metadata_base, base_provenance, metadata_fields=metadata_fields)
        reuse_status = "generated" if safe_reuse_present(metadata_base, base_provenance, metadata_fields=metadata_fields) else "none"
    if reuse_status == "none" and generate_missing:
        generated = generate_metadata(
            doc,
            provider=provider,
            model=model,
            base_url=base_url,
            max_doc_chars=max_doc_chars,
            metadata_fields=metadata_fields,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=prompt_cache_retention,
        )
        merge_generated_base(generated, metadata_base, base_provenance, metadata_fields=metadata_fields)
        reuse_status = "generated" if safe_reuse_present(metadata_base, base_provenance, metadata_fields=metadata_fields) else "none"

    for field, prov in base_provenance.items():
        if has_unsafe_generation(prov):
            unsafe.append({"field": field, "provenance": prov})

    return {
        "dataset_doc_uuid": doc.dataset_doc_uuid,
        "system": system,
        "metadata_base": metadata_base,
        "extension_candidates": extension_candidates,
        "provenance": {
            "system": system_provenance,
            "metadata_base": base_provenance,
            "extension_candidates": extension_provenance,
        },
        "reuse_status": reuse_status,
        "unsafe_legacy_generation": unsafe,
    }


def system_fields(doc: DatasetDocument, sources: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    system = {
        "dataset_doc_uuid": doc.dataset_doc_uuid,
        "source_type": doc.source_type,
        "source_path": doc.source_path,
        "title": doc.title,
        "content_type": doc.content_type,
        "storage_uri": doc.storage_uri,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }
    for source in sources:
        payload = source.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("system"), dict):
            payload = payload["system"]
        for field in ["source_type", "source_path", "title", "content_type", "storage_uri", "created_at", "updated_at"]:
            value = text_value(payload.get(field))
            if value:
                system[field] = value
    return system, {field: source_provenance(doc, field) for field in system}


def missing_provenance(field: str) -> dict[str, Any]:
    return provenance(
        source_file="",
        source_field=field,
        generation_method="missing",
        confidence=0.0,
        notes="No safe reusable LLM metadata was found for this field.",
    )


def profile_provenance(source: dict[str, Any], source_field: str, *, confidence: float, notes: str) -> dict[str, Any]:
    return provenance(
        source_file=str(source.get("source_file") or ""),
        source_field=source_field,
        generation_method="normalized_from_llm",
        confidence=confidence,
        notes=notes,
    )


def merge_normalized_metadata(
    source: dict[str, Any],
    metadata_base: dict[str, Any],
    base_provenance: dict[str, Any],
    extension_candidates: dict[str, Any],
    extension_provenance: dict[str, Any],
) -> None:
    payload = source.get("payload") or {}
    if not isinstance(payload, dict):
        return
    previous_base = payload.get("metadata_base") or {}
    previous_base_provenance = ((payload.get("provenance") or {}).get("metadata_base") or {})
    if isinstance(previous_base, dict):
        for field in BASE_FIELD_DEFAULTS:
            if field not in previous_base:
                continue
            if metadata_field_available(field, metadata_base, base_provenance):
                continue
            previous_prov = previous_base_provenance.get(field) or {}
            if not safe_previous_field(field, previous_base.get(field), previous_prov):
                continue
            metadata_base[field] = previous_base.get(field)
            base_provenance[field] = normalized_reuse_provenance(source, field, previous_prov)

    previous_extensions = payload.get("extension_candidates") or {}
    previous_extension_provenance = ((payload.get("provenance") or {}).get("extension_candidates") or {})
    if isinstance(previous_extensions, dict):
        for field, value in previous_extensions.items():
            if field in TEXT_HEAVY_PROFILE_FIELDS or is_forbidden_extension_field(field):
                continue
            if not non_empty_extension_value(value):
                continue
            extension_candidates.setdefault(field, value)
            if field in previous_extension_provenance:
                extension_provenance.setdefault(field, previous_extension_provenance[field])
            else:
                extension_provenance.setdefault(
                    field,
                    provenance(
                        source_file=str(source.get("source_file") or ""),
                        source_field=f"extension_candidates.{field}",
                        generation_method="source_metadata",
                        confidence=0.7,
                        notes="Reused from normalized metadata source.",
                    ),
                )


def safe_previous_field(field: str, value: Any, previous_prov: dict[str, Any]) -> bool:
    method = str((previous_prov or {}).get("generation_method") or "")
    if method not in SAFE_REUSABLE_BASE_METHODS:
        return False
    if field in {"doc_type", "domain", "topic", "summary"}:
        return bool(text_value(value))
    if field == "entities":
        return isinstance(value, list) and (not value or is_structured_entities(value))
    if field == "relations":
        return isinstance(value, list) and (not value or is_structured_relations(value))
    if field in {"constraints", "retrieval_cues"}:
        return isinstance(value, list)
    return False


def normalized_reuse_provenance(source: dict[str, Any], field: str, previous_prov: dict[str, Any]) -> dict[str, Any]:
    if isinstance(previous_prov, dict) and previous_prov:
        copied = dict(previous_prov)
        copied["notes"] = (
            text_value(copied.get("notes"))
            + f" Reused from normalized metadata source {source.get('source_file')}."
        ).strip()
        return copied
    return provenance(
        source_file=str(source.get("source_file") or ""),
        source_field=f"metadata_base.{field}",
        generation_method="source_metadata",
        confidence=0.7,
        notes="Reused from normalized metadata source.",
    )


def merge_profile_base(
    profile: dict[str, Any],
    source: dict[str, Any],
    metadata_base: dict[str, Any],
    base_provenance: dict[str, Any],
    *,
    metadata_fields: set[str],
) -> None:
    for target_field, source_fields in BASE_SOURCE_PRIORITY.items():
        if target_field not in metadata_fields:
            continue
        if metadata_base.get(target_field):
            continue
        for source_field in source_fields:
            value = text_value(profile.get(source_field))
            if value:
                metadata_base[target_field] = value
                base_provenance[target_field] = profile_provenance(
                    source,
                    source_field,
                    confidence=0.9,
                    notes="Accepted from generated LLM document profile.",
                )
                break

    summary = text_value(profile.get("semantic_summary"))
    if summary and "summary" in metadata_fields:
        metadata_base["summary"] = summary
        base_provenance["summary"] = profile_provenance(
            source,
            "semantic_summary",
            confidence=0.98,
            notes="Accepted only from semantic_summary in generated doc_profiles; no title/preview fallback.",
        )

    entities = profile.get("entities")
    if "entities" in metadata_fields and is_structured_entities(entities):
        metadata_base["entities"] = [normalize_entity(item) for item in ensure_list(entities)]
        base_provenance["entities"] = profile_provenance(
            source,
            "entities",
            confidence=0.98,
            notes="Accepted structured LLM entity objects with name/type/salience.",
        )

    relations = profile.get("relations")
    if "relations" in metadata_fields and is_structured_relations(relations):
        metadata_base["relations"] = [normalize_relation(item) for item in ensure_list(relations)]
        base_provenance["relations"] = profile_provenance(
            source,
            "relations",
            confidence=0.98,
            notes="Accepted structured LLM subject-relation-object facts with evidence terms.",
        )

    constraints = profile.get("constraints")
    if "constraints" in metadata_fields and isinstance(constraints, list):
        metadata_base["constraints"] = dedupe_strings(constraints)
        base_provenance["constraints"] = profile_provenance(
            source,
            "constraints",
            confidence=0.85,
            notes="Accepted only because the source provided an explicit constraints list.",
        )

    retrieval_cues = dedupe_strings(
        [
            *ensure_list(profile.get("search_terms")),
            *ensure_list(profile.get("folder_hints")),
            *ensure_list(profile.get("retrieval_cues")),
        ]
    )
    if "retrieval_cues" in metadata_fields and retrieval_cues:
        metadata_base["retrieval_cues"] = retrieval_cues
        base_provenance["retrieval_cues"] = profile_provenance(
            source,
            "search_terms|folder_hints|retrieval_cues",
            confidence=0.9,
            notes="Accepted LLM retrieval cues for lexical FTS/BM25/grep payloads only; not embedded.",
        )


def merge_profile_extension_candidates(
    profile: dict[str, Any],
    source: dict[str, Any],
    extension_candidates: dict[str, Any],
    extension_provenance: dict[str, Any],
) -> None:
    for field in PROFILE_EXTENSION_FIELDS:
        if field in TEXT_HEAVY_PROFILE_FIELDS or is_forbidden_extension_field(field):
            continue
        value = profile.get(field)
        if not non_empty_extension_value(value):
            continue
        extension_candidates[field] = value
        extension_provenance[field] = profile_provenance(
            source,
            field,
            confidence=0.85,
            notes="Candidate extension value from generated LLM profile; extension schema discovery must explicitly approve the field before downstream use.",
        )


def merge_registered_metadata(
    metadata: dict[str, Any],
    source: dict[str, Any],
    metadata_base: dict[str, Any],
    base_provenance: dict[str, Any],
    extension_candidates: dict[str, Any],
    extension_provenance: dict[str, Any],
    *,
    metadata_fields: set[str],
) -> None:
    if "summary" in metadata_fields and text_value(metadata.get("semantic_summary")) and not metadata_base.get("summary"):
        metadata_base["summary"] = text_value(metadata["semantic_summary"])
        base_provenance["summary"] = profile_provenance(
            source,
            "metadata.semantic_summary",
            confidence=0.8,
            notes="Accepted from registered metadata only because it retained semantic_summary.",
        )
    if "entities" in metadata_fields and is_structured_entities(metadata.get("entities")) and not metadata_base.get("entities"):
        metadata_base["entities"] = [normalize_entity(item) for item in ensure_list(metadata.get("entities"))]
        base_provenance["entities"] = profile_provenance(
            source,
            "metadata.entities",
            confidence=0.8,
            notes="Accepted only because registered metadata retained structured entity objects.",
        )
    if "relations" in metadata_fields and is_structured_relations(metadata.get("relations")) and not metadata_base.get("relations"):
        metadata_base["relations"] = [normalize_relation(item) for item in ensure_list(metadata.get("relations"))]
        base_provenance["relations"] = profile_provenance(
            source,
            "metadata.relations",
            confidence=0.8,
            notes="Accepted only because registered metadata retained structured relation objects.",
        )
    cues = dedupe_strings([*ensure_list(metadata.get("search_terms")), *ensure_list(metadata.get("retrieval_cues"))])
    if "retrieval_cues" in metadata_fields and cues and not metadata_base.get("retrieval_cues"):
        metadata_base["retrieval_cues"] = cues
        base_provenance["retrieval_cues"] = profile_provenance(
            source,
            "metadata.search_terms|metadata.retrieval_cues",
            confidence=0.75,
            notes="Accepted registered lexical retrieval cues; not embedded.",
        )

    for field, value in metadata.items():
        if field in TEXT_HEAVY_PROFILE_FIELDS or is_forbidden_extension_field(field):
            continue
        if not non_empty_extension_value(value):
            continue
        extension_candidates.setdefault(field, value)
        extension_provenance.setdefault(
            field,
            provenance(
                source_file=str(source.get("source_file") or ""),
                source_field=f"metadata.{field}",
                generation_method="source_metadata",
                confidence=0.7,
                notes="Registered metadata candidate; extension schema discovery must explicitly approve the field before downstream use.",
            ),
        )


def non_empty_extension_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, list):
        return bool([item for item in value if text_value(item)])
    if isinstance(value, dict):
        return False
    rendered = text_value(value)
    return bool(rendered) and len(rendered) <= 300


def safe_reuse_present(
    metadata_base: dict[str, Any],
    base_provenance: dict[str, Any],
    *,
    metadata_fields: set[str],
) -> bool:
    return bool(metadata_fields) and all(
        metadata_field_available(field, metadata_base, base_provenance)
        for field in metadata_fields
    )


def needs_generation(row: dict[str, Any], metadata_fields: set[str]) -> bool:
    metadata_base = row.get("metadata_base") or {}
    base_provenance = ((row.get("provenance") or {}).get("metadata_base") or {})
    for field in sorted(metadata_fields):
        if not metadata_field_available(field, metadata_base, base_provenance):
            return True
    return False


def metadata_field_available(
    field: str,
    metadata_base: dict[str, Any],
    base_provenance: dict[str, Any],
) -> bool:
    value = metadata_base.get(field)
    prov = base_provenance.get(field) or {}
    method = str(prov.get("generation_method") or "")
    if method not in {"llm", "normalized_from_llm", "generated_incrementally", "source_metadata"}:
        return False
    if field in {"doc_type", "domain", "topic", "summary"}:
        return bool(text_value(value))
    if field in {"entities", "relations", "constraints", "retrieval_cues"}:
        return isinstance(value, list)
    return bool(value)


def generate_metadata(
    doc: DatasetDocument,
    *,
    provider: str,
    model: str,
    base_url: str | None,
    max_doc_chars: int,
    metadata_fields: set[str],
    prompt_cache_key: str,
    prompt_cache_retention: str,
) -> dict[str, Any]:
    if provider == "genai":
        request = metadata_genai_generate_request(
            doc,
            metadata_fields=metadata_fields,
            max_doc_chars=max_doc_chars,
        )
        return call_genai_json_model(model=model, request=request)
    body = metadata_openai_chat_completion_body(
        doc,
        model=model,
        metadata_fields=metadata_fields,
        max_doc_chars=max_doc_chars,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=prompt_cache_retention,
    )
    return call_json_model(body=body, base_url=base_url)


def metadata_openai_chat_completion_body(
    doc: DatasetDocument,
    *,
    model: str,
    metadata_fields: set[str],
    max_doc_chars: int,
    prompt_cache_key: str,
    prompt_cache_retention: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": metadata_messages(doc, metadata_fields=metadata_fields, max_doc_chars=max_doc_chars),
        "response_format": metadata_response_format(metadata_fields),
    }
    if prompt_cache_key:
        body["prompt_cache_key"] = prompt_cache_key
    if prompt_cache_retention:
        body["prompt_cache_retention"] = prompt_cache_retention
    return body


def metadata_genai_generate_request(
    doc: DatasetDocument,
    *,
    metadata_fields: set[str],
    max_doc_chars: int,
) -> dict[str, Any]:
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": json.dumps(
                            metadata_document_json(doc, max_doc_chars=max_doc_chars),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    }
                ],
            }
        ],
        "system_instruction": {
            "parts": [
                {
                    "text": metadata_prompt_file(metadata_fields).read_text(encoding="utf-8")
                }
            ]
        },
        "generation_config": {
            "response_mime_type": "application/json",
            "response_json_schema": metadata_json_schema(metadata_fields),
        },
    }


def metadata_messages(
    doc: DatasetDocument,
    *,
    metadata_fields: set[str],
    max_doc_chars: int,
) -> list[dict[str, str]]:
    system_prompt = metadata_prompt_file(metadata_fields).read_text(encoding="utf-8")
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": json.dumps(metadata_document_json(doc, max_doc_chars=max_doc_chars), ensure_ascii=False, separators=(",", ":")),
        },
    ]


def metadata_prompt_file(metadata_fields: set[str]) -> Path:
    if metadata_fields == SUMMARY_ONLY_FIELDS:
        return PROMPTS_DIR / "metadata_summary_generation.md"
    return PROMPTS_DIR / "metadata_generation.md"


def metadata_document_json(doc: DatasetDocument, *, max_doc_chars: int) -> dict[str, Any]:
    text = doc.content
    if max_doc_chars > 0:
        text = text[:max_doc_chars]
    return {
        "source_type": doc.source_type,
        "title": doc.title,
        "content_type": doc.content_type,
        "text": text,
    }


def metadata_response_format(metadata_fields: set[str]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": metadata_schema_name(metadata_fields),
            "strict": True,
            "schema": metadata_json_schema(metadata_fields),
        },
    }


def metadata_schema_name(metadata_fields: set[str]) -> str:
    if metadata_fields == SUMMARY_ONLY_FIELDS:
        return "pifs_summary_metadata"
    return "pifs_retrieval_metadata"


def metadata_json_schema(metadata_fields: set[str]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for field in sorted(metadata_fields):
        if field in {"doc_type", "domain", "topic", "summary"}:
            properties[field] = {"type": "string"}
        elif field == "entities":
            properties[field] = {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "type", "aliases", "salience"],
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "aliases": {"type": "array", "items": {"type": "string"}},
                        "salience": {"type": "number"},
                    },
                },
            }
        elif field == "relations":
            properties[field] = {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["subject", "relation", "object", "evidence_terms"],
                    "properties": {
                        "subject": {"type": "string"},
                        "relation": {"type": "string"},
                        "object": {"type": "string"},
                        "evidence_terms": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        elif field in {"constraints", "retrieval_cues"}:
            properties[field] = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(metadata_fields),
        "properties": properties,
    }


def call_json_model(*, body: dict[str, Any], base_url: str | None) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url=base_url or None)
    sdk_body = dict(body)
    extra_body = {}
    for field in ["prompt_cache_key", "prompt_cache_retention"]:
        value = sdk_body.pop(field, "")
        if value:
            extra_body[field] = value
    if extra_body:
        sdk_body["extra_body"] = extra_body
    response = client.chat.completions.create(**sdk_body)
    content = response.choices[0].message.content or "{}"
    return json.loads(content)


def call_genai_json_model(*, model: str, request: dict[str, Any]) -> dict[str, Any]:
    client = genai_client()
    response = client.models.generate_content(
        model=model,
        contents=request["contents"],
        config={
            "system_instruction": request.get("system_instruction"),
            **(request.get("generation_config") or {}),
        },
    )
    content = text_value(getattr(response, "text", ""))
    if not content and getattr(response, "parsed", None) is not None:
        parsed = getattr(response, "parsed")
        if isinstance(parsed, dict):
            return parsed
        content = json.dumps(parsed, ensure_ascii=False)
    return json.loads(content or "{}")


def metadata_generation_prompt(doc: DatasetDocument, *, max_doc_chars: int) -> str:
    """Compatibility helper for older smoke snippets; generation now uses structured messages."""
    prompt_template = metadata_prompt_file(set(ALL_METADATA_FIELDS)).read_text(encoding="utf-8")
    document_json = {
        "source_type": doc.source_type,
        "title": doc.title,
        "content_type": doc.content_type,
        "text": metadata_document_json(doc, max_doc_chars=max_doc_chars)["text"],
    }
    return prompt_template + "\n\nDocument JSON:\n" + json.dumps(document_json, ensure_ascii=False, indent=2)


def prepare_metadata_batch(
    run_dir: Path,
    *,
    docs: list[DatasetDocument],
    args: argparse.Namespace,
) -> dict[str, Any]:
    metadata_fields = selected_metadata_fields(args)
    prompt_cache_key = effective_prompt_cache_key(args, metadata_fields)
    provider = args.metadata_provider
    manifest_path = batch_manifest_path(run_dir, args)
    batch_dir = run_dir / "metadata_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    batches: list[dict[str, Any]] = []
    request_count = 0
    truncated_documents = 0
    current_lines: list[str] = []
    current_bytes = 0

    def flush() -> None:
        nonlocal current_lines, current_bytes
        if not current_lines:
            return
        index = len(batches) + 1
        path = batch_dir / f"metadata_batch_input_{index:05d}.jsonl"
        path.write_text("".join(current_lines), encoding="utf-8")
        batches.append(
            {
                "index": index,
                "input_path": str(path),
                "request_count": len(current_lines),
                "byte_count": current_bytes,
                "uploaded_file_id": "",
                "batch_id": "",
                "status": "prepared",
                "output_file_id": "",
                "error_file_id": "",
                "output_path": "",
                "error_path": "",
            }
        )
        current_lines = []
        current_bytes = 0

    for doc in docs:
        if args.max_doc_chars > 0 and len(doc.content) > args.max_doc_chars:
            truncated_documents += 1
        line = json.dumps(
            metadata_batch_request(
                doc,
                provider=provider,
                model=args.metadata_model,
                metadata_fields=metadata_fields,
                max_doc_chars=args.max_doc_chars,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=args.prompt_cache_retention,
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        line_bytes = len(line.encode("utf-8"))
        if line_bytes > args.batch_max_bytes:
            raise SystemExit(f"metadata batch request for {doc.dataset_doc_uuid} exceeds --batch-max-bytes")
        if (
            current_lines
            and (
                len(current_lines) >= args.batch_max_requests
                or current_bytes + line_bytes > args.batch_max_bytes
            )
        ):
            flush()
        current_lines.append(line)
        current_bytes += line_bytes
        request_count += 1
    flush()

    manifest = {
        "generated_by": "semantic_metadata_pipeline.build_metadata",
        "status": "prepared" if docs else "no_missing_metadata",
        "metadata_provider": provider,
        "endpoint": metadata_batch_endpoint(provider),
        "model": args.metadata_model,
        "metadata_fields": sorted(metadata_fields),
        "prompt_file": metadata_prompt_file(metadata_fields).name,
        "response_format": "json_schema_strict",
        "prompt_cache_key": prompt_cache_key,
        "prompt_cache_retention": args.prompt_cache_retention,
        "completion_window": args.batch_completion_window,
        "max_doc_chars": args.max_doc_chars,
        "document_text_policy": document_text_policy(args.max_doc_chars),
        "truncated_documents": truncated_documents,
        "batch_max_requests": args.batch_max_requests,
        "batch_max_bytes": args.batch_max_bytes,
        "request_count": request_count,
        "batch_count": len(batches),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "batches": batches,
        "policy": {
            "input_exclusions": [
                "benchmark questions",
                "gold answers",
                "expected ids",
                "file paths",
                "URLs",
                "storage URIs",
            ],
            "output": "strict JSON metadata generated from document text and ordinary source metadata",
        },
    }
    write_json(manifest_path, manifest)
    return manifest


def metadata_batch_request(
    doc: DatasetDocument,
    *,
    provider: str,
    model: str,
    metadata_fields: set[str],
    max_doc_chars: int,
    prompt_cache_key: str,
    prompt_cache_retention: str,
) -> dict[str, Any]:
    if provider == "genai":
        return {
            "key": metadata_batch_custom_id(doc.dataset_doc_uuid),
            "request": metadata_genai_generate_request(
                doc,
                metadata_fields=metadata_fields,
                max_doc_chars=max_doc_chars,
            ),
        }
    return {
        "custom_id": metadata_batch_custom_id(doc.dataset_doc_uuid),
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": metadata_openai_chat_completion_body(
            doc,
            model=model,
            metadata_fields=metadata_fields,
            max_doc_chars=max_doc_chars,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=prompt_cache_retention,
        ),
    }


def metadata_batch_endpoint(provider: str) -> str:
    if provider == "genai":
        return "genai:batchGenerateContent"
    return "/v1/chat/completions"


def submit_metadata_batches(
    manifest: dict[str, Any],
    *,
    args: argparse.Namespace,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    if not manifest.get("batches"):
        return manifest
    provider = str(manifest.get("metadata_provider") or getattr(args, "metadata_provider", "openai") or "openai")
    if provider == "genai":
        return submit_genai_metadata_batches(manifest, args=args, manifest_path=manifest_path)
    client = openai_client(args)
    updated_batches = []
    for batch in manifest["batches"]:
        if batch.get("batch_id"):
            updated_batches.append(batch)
            continue
        input_path = Path(batch["input_path"])
        with input_path.open("rb") as handle:
            uploaded = client.files.create(file=handle, purpose="batch")
        uploaded_file_id = openai_object_value(uploaded, "id")
        created = client.batches.create(
            input_file_id=uploaded_file_id,
            endpoint=manifest["endpoint"],
            completion_window=manifest["completion_window"],
            metadata={
                "pipeline": "semantic_metadata_pipeline",
                "artifact": "metadata_generation",
                "batch_index": str(batch["index"]),
            },
        )
        batch.update(
            {
                "uploaded_file_id": uploaded_file_id,
                "batch_id": openai_object_value(created, "id"),
                "status": openai_object_value(created, "status") or "submitted",
                "output_file_id": openai_object_value(created, "output_file_id"),
                "error_file_id": openai_object_value(created, "error_file_id"),
                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        updated_batches.append(batch)
        manifest["batches"] = [*updated_batches, *manifest["batches"][len(updated_batches):]]
        manifest["status"] = "submitting"
        manifest["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if manifest_path is not None:
            write_json(manifest_path, manifest)
    manifest["batches"] = updated_batches
    manifest["status"] = "submitted"
    manifest["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return manifest


def submit_genai_metadata_batches(
    manifest: dict[str, Any],
    *,
    args: argparse.Namespace,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    client = genai_client()
    updated_batches = []
    batches = list(manifest["batches"])
    token_budget = int(getattr(args, "genai_submit_active_token_budget", 0) or 0)
    token_estimate_per_byte = float(getattr(args, "genai_submit_token_estimate_per_byte", 0.24) or 0.24)
    max_new_batches = int(getattr(args, "genai_submit_max_new_batches", 0) or 0)
    active_token_estimate = sum(
        estimate_genai_batch_active_tokens(batch, token_estimate_per_byte)
        for batch in batches
        if genai_batch_active(batch)
    )
    submitted_this_run = 0
    for batch in batches:
        if batch.get("batch_id"):
            updated_batches.append(batch)
            continue
        next_token_estimate = estimate_genai_batch_active_tokens(batch, token_estimate_per_byte)
        if token_budget and active_token_estimate + next_token_estimate > token_budget:
            manifest["batches"] = [*updated_batches, *batches[len(updated_batches):]]
            manifest["status"] = "submit_throttled"
            manifest["genai_submit_throttle"] = genai_submit_throttle_report(
                reason="estimated_active_token_budget",
                active_token_estimate=active_token_estimate,
                next_batch=batch,
                next_token_estimate=next_token_estimate,
                token_budget=token_budget,
                token_estimate_per_byte=token_estimate_per_byte,
                submitted_this_run=submitted_this_run,
            )
            manifest["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if manifest_path is not None:
                write_json(manifest_path, manifest)
            return manifest
        if max_new_batches and submitted_this_run >= max_new_batches:
            manifest["batches"] = [*updated_batches, *batches[len(updated_batches):]]
            manifest["status"] = "submit_throttled"
            manifest["genai_submit_throttle"] = genai_submit_throttle_report(
                reason="max_new_batches",
                active_token_estimate=active_token_estimate,
                next_batch=batch,
                next_token_estimate=next_token_estimate,
                token_budget=token_budget,
                token_estimate_per_byte=token_estimate_per_byte,
                submitted_this_run=submitted_this_run,
            )
            manifest["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if manifest_path is not None:
                write_json(manifest_path, manifest)
            return manifest
        input_path = Path(batch["input_path"])
        uploaded = genai_upload_file(
            client,
            input_path,
            display_name=f"pifs-metadata-{Path(input_path).stem}",
        )
        uploaded_file_id = genai_object_value(uploaded, "name")
        try:
            created = client.batches.create(
                model=manifest.get("model") or args.metadata_model,
                src=uploaded_file_id,
                config={
                    "display_name": f"pifs-metadata-batch-{batch['index']:05d}",
                },
            )
        except Exception as exc:  # noqa: BLE001 - SDK raises provider-specific API errors.
            if not genai_resource_exhausted(exc):
                raise
            batch.update(
                {
                    "uploaded_file_id": uploaded_file_id,
                    "last_submit_error": summarize_exception(exc),
                    "last_submit_error_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "estimated_active_tokens": next_token_estimate,
                }
            )
            manifest["batches"] = [*updated_batches, *batches[len(updated_batches):]]
            manifest["status"] = "submit_throttled"
            manifest["genai_submit_throttle"] = genai_submit_throttle_report(
                reason="provider_resource_exhausted",
                active_token_estimate=active_token_estimate,
                next_batch=batch,
                next_token_estimate=next_token_estimate,
                token_budget=token_budget,
                token_estimate_per_byte=token_estimate_per_byte,
                submitted_this_run=submitted_this_run,
            )
            manifest["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if manifest_path is not None:
                write_json(manifest_path, manifest)
            return manifest
        batch.update(
            {
                "uploaded_file_id": uploaded_file_id,
                "batch_id": genai_object_value(created, "name"),
                "status": genai_batch_state(created) or "submitted",
                "output_file_id": genai_batch_dest_file(created),
                "error_file_id": "",
                "estimated_active_tokens": next_token_estimate,
                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        updated_batches.append(batch)
        active_token_estimate += next_token_estimate
        submitted_this_run += 1
        manifest["batches"] = [*updated_batches, *manifest["batches"][len(updated_batches):]]
        manifest["status"] = "submitting"
        manifest["genai_submit_throttle"] = genai_submit_throttle_report(
            reason="submitting",
            active_token_estimate=active_token_estimate,
            next_batch=None,
            next_token_estimate=0,
            token_budget=token_budget,
            token_estimate_per_byte=token_estimate_per_byte,
            submitted_this_run=submitted_this_run,
        )
        manifest["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if manifest_path is not None:
            write_json(manifest_path, manifest)
    manifest["batches"] = updated_batches
    manifest["status"] = "submitted"
    manifest["genai_submit_throttle"] = genai_submit_throttle_report(
        reason="all_submitted",
        active_token_estimate=active_token_estimate,
        next_batch=None,
        next_token_estimate=0,
        token_budget=token_budget,
        token_estimate_per_byte=token_estimate_per_byte,
        submitted_this_run=submitted_this_run,
    )
    manifest["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return manifest


def genai_batch_active(batch: dict[str, Any]) -> bool:
    if not batch.get("batch_id"):
        return False
    status = str(batch.get("status") or "")
    if metadata_batch_terminal("genai", status):
        return False
    return True


def estimate_genai_batch_active_tokens(batch: dict[str, Any], token_estimate_per_byte: float) -> int:
    existing = batch.get("estimated_active_tokens")
    if isinstance(existing, int) and existing > 0:
        return existing
    byte_count = int(batch.get("byte_count") or 0)
    if byte_count <= 0 and batch.get("input_path"):
        path = Path(str(batch["input_path"]))
        if path.exists():
            byte_count = path.stat().st_size
    if byte_count <= 0:
        return 0
    return max(1, int(byte_count * token_estimate_per_byte + 0.999999))


def genai_submit_throttle_report(
    *,
    reason: str,
    active_token_estimate: int,
    next_batch: dict[str, Any] | None,
    next_token_estimate: int,
    token_budget: int,
    token_estimate_per_byte: float,
    submitted_this_run: int,
) -> dict[str, Any]:
    return {
        "reason": reason,
        "active_token_estimate": active_token_estimate,
        "token_budget": token_budget,
        "token_estimate_per_byte": token_estimate_per_byte,
        "submitted_this_run": submitted_this_run,
        "next_batch_index": next_batch.get("index") if next_batch else None,
        "next_batch_estimated_active_tokens": next_token_estimate,
        "next_batch_request_count": next_batch.get("request_count") if next_batch else None,
        "next_batch_byte_count": next_batch.get("byte_count") if next_batch else None,
    }


def genai_resource_exhausted(exc: Exception) -> bool:
    text = summarize_exception(exc)
    return "RESOURCE_EXHAUSTED" in text or " 429 " in f" {text} " or "429" in text


def summarize_exception(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    if len(text) > 1000:
        return text[:997] + "..."
    return text


def collect_metadata_batch_results(
    run_dir: Path,
    *,
    args: argparse.Namespace,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    manifest_path = batch_manifest_path(run_dir, args)
    if not manifest_path.exists():
        raise SystemExit(f"metadata batch manifest not found: {manifest_path}")
    manifest = read_json(manifest_path)
    provider = str(manifest.get("metadata_provider") or getattr(args, "metadata_provider", "openai") or "openai")
    client = genai_client() if provider == "genai" else openai_client(args)
    generated_by_doc: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    completed = 0
    terminal = 0
    pending = 0

    for batch in manifest.get("batches", []):
        batch_id = batch.get("batch_id")
        if not batch_id:
            pending += 1
            continue
        if provider == "genai":
            remote = client.batches.get(name=batch_id)
            batch["status"] = genai_batch_state(remote) or batch.get("status") or ""
            batch["output_file_id"] = genai_batch_dest_file(remote) or batch.get("output_file_id") or ""
            batch["error_file_id"] = ""
        else:
            remote = client.batches.retrieve(batch_id)
            batch["status"] = openai_object_value(remote, "status") or batch.get("status") or ""
            batch["output_file_id"] = openai_object_value(remote, "output_file_id") or batch.get("output_file_id") or ""
            batch["error_file_id"] = openai_object_value(remote, "error_file_id") or batch.get("error_file_id") or ""
        if metadata_batch_succeeded(provider, batch["status"]):
            completed += 1
        if metadata_batch_terminal(provider, batch["status"]):
            terminal += 1
        else:
            pending += 1
        if batch.get("output_file_id"):
            output_path = run_dir / "metadata_batches" / f"metadata_batch_output_{int(batch['index']):05d}.jsonl"
            try:
                if provider == "genai":
                    download_genai_file(client, batch["output_file_id"], output_path)
                else:
                    download_openai_file(client, batch["output_file_id"], output_path)
                batch["output_path"] = str(output_path)
                parsed, parse_errors = parse_metadata_batch_output(output_path, provider=provider)
                generated_by_doc.update(parsed)
                errors.extend(parse_errors)
            except Exception as exc:  # noqa: BLE001 - partial downloads should be resumable on the next collect.
                errors.append(
                    {
                        "path": str(output_path),
                        "batch_index": batch.get("index"),
                        "file_id": batch.get("output_file_id"),
                        "download_error": str(exc),
                    }
                )
        if provider != "genai" and batch.get("error_file_id"):
            error_path = run_dir / "metadata_batches" / f"metadata_batch_errors_{int(batch['index']):05d}.jsonl"
            download_openai_file(client, batch["error_file_id"], error_path)
            batch["error_path"] = str(error_path)
            errors.extend(parse_metadata_batch_errors(error_path))
        write_json(manifest_path, manifest)

    manifest["status"] = "collected" if pending == 0 else "partially_collected"
    manifest["collected_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["collected_documents"] = len(generated_by_doc)
    manifest["parse_error_count"] = len(errors)
    write_json(manifest_path, manifest)

    error_report_path = run_dir / "metadata_batch_parse_errors.json"
    write_json(error_report_path, errors)
    report = {
        "manifest_path": str(manifest_path),
        "completed_batches": completed,
        "terminal_batches": terminal,
        "pending_batches": pending,
        "generated_documents": len(generated_by_doc),
        "parse_errors": len(errors),
        "parse_error_report_path": str(error_report_path),
    }
    if pending and not args.allow_partial_batch_results:
        raise SystemExit(
            "metadata batches are not all complete; rerun collect later or pass --allow-partial-batch-results"
        )
    return generated_by_doc, report


def parse_metadata_batch_output(path: Path, *, provider: str = "openai") -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    generated_by_doc: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if provider == "genai":
                    doc_id = metadata_doc_id_from_custom_id(str(row.get("key") or row.get("metadata", {}).get("key") or ""))
                    if row.get("error"):
                        errors.append({"path": str(path), "line": line_number, "custom_id": row.get("key"), "error": row.get("error")})
                        continue
                    content = extract_genai_response_text(row.get("response") or row)
                    generated_by_doc[doc_id] = json.loads(content)
                    continue
                doc_id = metadata_doc_id_from_custom_id(str(row.get("custom_id") or ""))
                body = ((row.get("response") or {}).get("body") or {})
                if (row.get("response") or {}).get("status_code") != 200:
                    errors.append({"path": str(path), "line": line_number, "custom_id": row.get("custom_id"), "error": row.get("error") or row.get("response")})
                    continue
                content = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}")
                generated_by_doc[doc_id] = json.loads(content)
            except Exception as exc:  # noqa: BLE001 - batch output should be diagnosed, not crash obscurely.
                errors.append({"path": str(path), "line": line_number, "error": str(exc)})
    return generated_by_doc, errors


def parse_metadata_batch_errors(path: Path) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                row = {"raw": line.strip(), "parse_error": str(exc)}
            row["path"] = str(path)
            row["line"] = line_number
            errors.append(row)
    return errors


def extract_genai_response_text(response: Any) -> str:
    if response is None:
        return "{}"
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        text = text_value(response.get("text"))
        if text:
            return text
        candidates = response.get("candidates") or []
        parts: list[str] = []
        for candidate in candidates:
            content = (candidate or {}).get("content") or {}
            for part in content.get("parts") or []:
                part_text = text_value((part or {}).get("text"))
                if part_text:
                    parts.append(part_text)
        if parts:
            return "\n".join(parts)
        if isinstance(response.get("response"), dict):
            return extract_genai_response_text(response["response"])
    text = text_value(getattr(response, "text", ""))
    if text:
        return text
    candidates = getattr(response, "candidates", None) or []
    parts = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = text_value(getattr(part, "text", ""))
            if part_text:
                parts.append(part_text)
    return "\n".join(parts) if parts else "{}"


def download_openai_file(client: Any, file_id: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = client.files.content(file_id)
    if hasattr(content, "write_to_file"):
        content.write_to_file(path)
        return
    raw = content.read() if hasattr(content, "read") else getattr(content, "content", None)
    if raw is None:
        raw = getattr(content, "text", None)
    if isinstance(raw, bytes):
        path.write_bytes(raw)
    else:
        path.write_text(str(raw or ""), encoding="utf-8")


def download_genai_file(client: Any, file_id: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    for attempt in range(1, 5):
        try:
            content = client.files.download(file=file_id)
            if isinstance(content, bytes):
                tmp_path.write_bytes(content)
            else:
                tmp_path.write_text(str(content or ""), encoding="utf-8")
            os.replace(tmp_path, path)
            return
        except Exception as exc:  # noqa: BLE001 - SDK can surface transient transport errors here.
            last_error = exc
            if tmp_path.exists():
                tmp_path.unlink()
            time.sleep(min(30, attempt * 5))
    raise RuntimeError(f"failed to download GenAI file {file_id} after retries: {last_error}")


def metadata_batch_custom_id(doc_id: str) -> str:
    return f"metadata:{doc_id}"


def metadata_doc_id_from_custom_id(custom_id: str) -> str:
    if not custom_id.startswith("metadata:"):
        raise ValueError(f"unexpected metadata batch custom_id: {custom_id}")
    return custom_id.split(":", 1)[1]


def openai_client(args: argparse.Namespace) -> Any:
    from openai import OpenAI

    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url=args.base_url or None)


def genai_client() -> Any:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is required for --metadata-provider genai") from exc
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for --metadata-provider genai")
    return genai.Client(api_key=api_key)


def genai_upload_file(client: Any, path: Path, *, display_name: str) -> Any:
    try:
        from google.genai import types
    except ImportError:
        types = None
    if types is not None and hasattr(types, "UploadFileConfig"):
        return client.files.upload(
            file=str(path),
            config=types.UploadFileConfig(
                display_name=display_name,
                mime_type="jsonl",
            ),
        )
    return client.files.upload(file=str(path))


def openai_object_value(obj: Any, field: str) -> str:
    if obj is None:
        return ""
    if isinstance(obj, dict):
        return text_value(obj.get(field))
    return text_value(getattr(obj, field, ""))


def genai_object_value(obj: Any, field: str) -> str:
    if obj is None:
        return ""
    if isinstance(obj, dict):
        value = obj.get(field)
    else:
        value = getattr(obj, field, "")
    if hasattr(value, "name"):
        return text_value(getattr(value, "name"))
    return text_value(value)


def genai_batch_state(batch: Any) -> str:
    return genai_object_value(batch, "state")


def genai_batch_dest_file(batch: Any) -> str:
    dest = batch.get("dest") if isinstance(batch, dict) else getattr(batch, "dest", None)
    if not dest:
        return ""
    return genai_object_value(dest, "file_name")


def metadata_batch_succeeded(provider: str, status: str) -> bool:
    if provider == "genai":
        return status == "JOB_STATE_SUCCEEDED"
    return status == "completed"


def metadata_batch_terminal(provider: str, status: str) -> bool:
    if provider == "genai":
        return status in {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}
    return status in {"completed", "failed", "expired", "cancelled"}


def merge_generated_base(
    generated: dict[str, Any],
    metadata_base: dict[str, Any],
    base_provenance: dict[str, Any],
    *,
    metadata_fields: set[str],
) -> None:
    for field in ["doc_type", "domain", "topic", "summary"]:
        if field not in metadata_fields or field not in generated:
            continue
        value = text_value(generated.get(field))
        metadata_base[field] = value
        base_provenance[field] = generated_field_provenance(field, metadata_fields)
    entities = generated.get("entities")
    if "entities" in metadata_fields and isinstance(entities, list):
        metadata_base["entities"] = [
            normalize_entity(item)
            for item in ensure_list(entities)
            if isinstance(item, dict) and text_value(item.get("name"))
        ]
        base_provenance["entities"] = generated_field_provenance("entities", metadata_fields)
    relations = generated.get("relations")
    if "relations" in metadata_fields and isinstance(relations, list):
        metadata_base["relations"] = [
            normalize_relation(item)
            for item in ensure_list(relations)
            if isinstance(item, dict)
            and text_value(item.get("subject"))
            and text_value(item.get("relation"))
            and text_value(item.get("object"))
        ]
        base_provenance["relations"] = generated_field_provenance("relations", metadata_fields)
    for field in ["constraints", "retrieval_cues"]:
        if field not in metadata_fields or field not in generated:
            continue
        values = dedupe_strings(ensure_list(generated.get(field)))
        metadata_base[field] = values
        base_provenance[field] = generated_field_provenance(field, metadata_fields)


def generated_field_provenance(field: str, metadata_fields: set[str]) -> dict[str, Any]:
    return provenance(
        source_file=f"prompts/{metadata_prompt_file(metadata_fields).name}",
        source_field=field,
        generation_method="generated_incrementally",
        confidence=0.85,
        notes="Generated by the configured metadata provider with strict structured output from grounded document text.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
