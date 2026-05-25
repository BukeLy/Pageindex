from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from pipeline_common import (
    BatchEmbeddingCache,
    batch_embed_with_cache,
    has_unsafe_generation,
    load_normalized_metadata,
    make_embedder,
    read_json,
    sample_rows,
    text_hash,
    text_value,
    update_config,
    write_json,
    write_summary,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pageindex.filesystem.semantic_index import SemanticIndexRecord, SQLiteVecSemanticIndex


CHANNEL_TO_INDEX = {
    "summary": "summary_only_vector",
    "entity": "entity_vectors",
    "relation": "relation_vectors",
}
FORBIDDEN_VECTOR_CHANNELS = {
    "constraint_vectors",
    "retrieval_cues_vectors",
    "metadata_composite_vector",
}


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser()
    channels = selected_channels(args)
    if args.embedding_batch_mode == "submit" and embedding_batch_manifest_path(run_dir, args).exists():
        manifest = read_json(embedding_batch_manifest_path(run_dir, args))
        if embedding_manifest_has_unsubmitted_batches(manifest):
            manifest = submit_embedding_batches(
                manifest,
                args=args,
                manifest_path=embedding_batch_manifest_path(run_dir, args),
            )
            write_json(embedding_batch_manifest_path(run_dir, args), manifest)
            print(
                json.dumps(
                    {
                        "run_name": run_dir.name,
                        "embedding_batch_mode": "submit",
                        "batch_manifest_path": str(embedding_batch_manifest_path(run_dir, args)),
                        "request_count": manifest.get("request_count", 0),
                        "batch_count": manifest.get("batch_count", 0),
                        "submitted_batches": sum(1 for batch in manifest.get("batches", []) if batch.get("batch_id")),
                        "status": manifest.get("status"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

    metadata_path = Path(args.metadata) if args.metadata else run_dir / "metadata.normalized.jsonl"
    rows = load_normalized_metadata(metadata_path)
    projection_rows, skipped_rows = collect_projection_rows(rows, metadata_path, channels=channels)
    index_dir = run_dir / "projection_indexes"
    cache = BatchEmbeddingCache(index_dir / "embedding_cache.sqlite")
    sample_by_channel: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for channel in channels:
        sample_by_channel[channel] = sample_rows(
            [row for row in projection_rows if row["channel"] == channel],
            limit=args.sample_rows,
        )

    if args.embedding_batch_mode in {"prepare", "submit"}:
        validate_embedding_batch_args(args)
        batch_manifest = prepare_embedding_batches(
            run_dir,
            projection_rows=projection_rows,
            cache=cache,
            args=args,
        )
        if args.embedding_batch_mode == "submit" and batch_manifest.get("batches"):
            batch_manifest = submit_embedding_batches(
                batch_manifest,
                args=args,
                manifest_path=embedding_batch_manifest_path(run_dir, args),
            )
            write_json(embedding_batch_manifest_path(run_dir, args), batch_manifest)
        write_projection_batch_prepare_artifacts(
            run_dir,
            args=args,
            metadata_path=metadata_path,
            index_dir=index_dir,
            projection_rows=projection_rows,
            skipped_rows=skipped_rows,
            sample_by_channel=sample_by_channel,
            batch_manifest=batch_manifest,
        )
        print(json.dumps(batch_manifest, ensure_ascii=False, indent=2))
        return 0

    batch_report: dict[str, Any] | None = None
    if args.embedding_batch_mode == "collect":
        validate_embedding_batch_args(args)
        batch_report = collect_embedding_batch_results(
            run_dir,
            cache=cache,
            args=args,
        )
        if batch_report.get("pending_batches") and args.allow_partial_batch_results:
            print(json.dumps(batch_report, ensure_ascii=False, indent=2))
            return 0

    embedder = make_embedder(
        args.embedding_provider,
        args.embedding_model,
        dimensions=args.embedding_dimensions,
        timeout=args.embedding_timeout,
    )

    manifest_channels: dict[str, Any] = {}
    for channel in channels:
        index_name = CHANNEL_TO_INDEX[channel]
        channel_rows = [row for row in projection_rows if row["channel"] == channel]
        if args.embedding_batch_mode == "collect":
            channel_summary = build_channel_index_from_cache(
                rows=channel_rows,
                index_path=index_dir / f"{index_name}.sqlite",
                channel=channel,
                index_name=index_name,
                cache=cache,
                args=args,
            )
        else:
            channel_summary = build_channel_index(
                rows=channel_rows,
                index_path=index_dir / f"{index_name}.sqlite",
                channel=channel,
                index_name=index_name,
                cache=cache,
                embedder=embedder,
                args=args,
            )
        channel_summary["skipped_rows"] = skipped_rows.get(channel, 0)
        manifest_channels[index_name] = channel_summary

    manifest = {
        "generated_by": "semantic_metadata_pipeline.build_projection_indexes",
        "metadata_source_path": str(metadata_path),
        "index_dir": str(index_dir),
        "embedding_provider": args.embedding_provider,
        "embedding_model": args.embedding_model,
        "embedding_dimensions": args.embedding_dimensions,
        "batch_size": args.batch_size,
        "embedding_batch_mode": args.embedding_batch_mode,
        "embedding_batch_manifest_path": str(embedding_batch_manifest_path(run_dir, args))
        if args.embedding_batch_mode != "off"
        else "",
        "input_rows": len(projection_rows),
        "skipped_rows": sum(skipped_rows.values()),
        "selected_channels": channels,
        "channels": manifest_channels,
        "forbidden_channels_not_built": sorted(FORBIDDEN_VECTOR_CHANNELS),
        "policy": {
            "summary_only_vector": "metadata.summary only; no compact_summary/title/preview fallback",
            "entity_vectors": "metadata.entities only; no keyword_terms fallback",
            "relation_vectors": "metadata.relations only; no infer_predicate fallback",
            "retrieval_cues": "lexical payload only; not embedded",
            "constraints": "grounded metadata only; not embedded by this pipeline",
        },
    }
    if batch_report is not None:
        manifest["embedding_batch_collect_report"] = batch_report
    sample_path = run_dir / "sample_projection_rows.json"
    manifest_path = run_dir / "projection_manifest.json"
    write_json(sample_path, sample_by_channel)
    write_json(manifest_path, manifest)
    update_config(
        run_dir,
        "projection_indexes",
        {
            "metadata_path": str(metadata_path),
            "index_dir": str(index_dir),
            "projection_manifest_path": str(manifest_path),
            "sample_projection_rows_path": str(sample_path),
            "embedding_provider": args.embedding_provider,
            "embedding_model": args.embedding_model,
            "embedding_dimensions": args.embedding_dimensions,
            "embedding_batch_mode": args.embedding_batch_mode,
            "embedding_batch_manifest_path": str(embedding_batch_manifest_path(run_dir, args))
            if args.embedding_batch_mode != "off"
            else "",
        },
    )
    write_summary(
        run_dir,
        {
            "projection_indexes": {
                "input_rows": len(projection_rows),
                "skipped_rows": sum(skipped_rows.values()),
                "channels": ",".join(CHANNEL_TO_INDEX.values()),
                "forbidden_channels_not_built": ",".join(sorted(FORBIDDEN_VECTOR_CHANNELS)),
            }
        },
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build semantic projection indexes from normalized metadata")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--metadata", default="")
    parser.add_argument("--embedding-provider", default="hash", choices=["hash", "openai"])
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    parser.add_argument("--embedding-dimensions", type=int, default=256)
    parser.add_argument("--embedding-timeout", type=float, default=60)
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--sample-rows", type=int, default=5)
    parser.add_argument(
        "--channels",
        default="summary,entity,relation",
        help="Comma-separated projection channels to build: summary, entity, relation. Use 'summary' for summary-only runs.",
    )
    parser.add_argument(
        "--embedding-batch-mode",
        choices=["off", "prepare", "submit", "collect"],
        default="off",
        help=(
            "OpenAI Batch workflow for embeddings: prepare writes /v1/embeddings JSONL, "
            "submit uploads and creates batches, collect downloads outputs and builds indexes from cache."
        ),
    )
    parser.add_argument("--batch-manifest", default="")
    parser.add_argument("--batch-completion-window", default="24h")
    parser.add_argument("--batch-max-requests", type=int, default=50000)
    parser.add_argument("--batch-max-bytes", type=int, default=180_000_000)
    parser.add_argument("--allow-partial-batch-results", action="store_true")
    return parser.parse_args()


def validate_embedding_batch_args(args: argparse.Namespace) -> None:
    if args.embedding_provider != "openai":
        raise SystemExit("--embedding-batch-mode requires --embedding-provider openai")
    if args.batch_max_requests < 1 or args.batch_max_requests > 50000:
        raise SystemExit("--batch-max-requests must be between 1 and 50000 for /v1/embeddings batches")
    if args.batch_max_bytes < 1024:
        raise SystemExit("--batch-max-bytes is too small")
    selected_channels(args)


def selected_channels(args: argparse.Namespace) -> list[str]:
    raw = str(getattr(args, "channels", "") or "").strip().lower()
    if raw in {"", "all", "*"}:
        return list(CHANNEL_TO_INDEX)
    channels = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(channels) - set(CHANNEL_TO_INDEX))
    if unknown:
        raise SystemExit(f"Unknown --channels values: {', '.join(unknown)}")
    ordered = [channel for channel in CHANNEL_TO_INDEX if channel in set(channels)]
    if not ordered:
        raise SystemExit("--channels must select at least one channel")
    return ordered


def embedding_batch_manifest_path(run_dir: Path, args: argparse.Namespace) -> Path:
    return Path(args.batch_manifest).expanduser() if args.batch_manifest else run_dir / "embedding_batch_manifest.json"


def collect_projection_rows(
    rows: list[dict[str, Any]],
    metadata_path: Path,
    *,
    channels: list[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    projection_rows: list[dict[str, Any]] = []
    skipped: dict[str, int] = defaultdict(int)
    selected = set(channels)
    for row in rows:
        doc_id = row["dataset_doc_uuid"]
        system = row.get("system") or {}
        base = row.get("metadata_base") or {}
        base_prov = ((row.get("provenance") or {}).get("metadata_base") or {})

        if "summary" in selected:
            summary = text_value(base.get("summary"))
            if summary and safe_projection_provenance(base_prov.get("summary")):
                projection_rows.append(
                    projection_row(
                        doc_id=doc_id,
                        system=system,
                        channel="summary",
                        ordinal=0,
                        text=summary,
                        metadata_source_path=metadata_path,
                        metadata_provenance=base_prov.get("summary"),
                    )
                )
            else:
                skipped["summary"] += 1

        if "entity" in selected:
            entities = base.get("entities") or []
            if entities and safe_projection_provenance(base_prov.get("entities")):
                for ordinal, entity in enumerate(entities):
                    text = entity_text(entity)
                    if text:
                        projection_rows.append(
                            projection_row(
                                doc_id=doc_id,
                                system=system,
                                channel="entity",
                                ordinal=ordinal,
                                text=text,
                                metadata_source_path=metadata_path,
                                metadata_provenance=base_prov.get("entities"),
                            )
                        )
            else:
                skipped["entity"] += 1

        if "relation" in selected:
            relations = base.get("relations") or []
            if relations and safe_projection_provenance(base_prov.get("relations")):
                for ordinal, relation in enumerate(relations):
                    text = relation_text(relation)
                    if text:
                        projection_rows.append(
                            projection_row(
                                doc_id=doc_id,
                                system=system,
                                channel="relation",
                                ordinal=ordinal,
                                text=text,
                                metadata_source_path=metadata_path,
                                metadata_provenance=base_prov.get("relations"),
                            )
                        )
            else:
                skipped["relation"] += 1
    return projection_rows, skipped


def safe_projection_provenance(prov: dict[str, Any] | None) -> bool:
    if not prov or has_unsafe_generation(prov):
        return False
    return str(prov.get("generation_method") or "") in {
        "llm",
        "normalized_from_llm",
        "generated_incrementally",
    }


def projection_row(
    *,
    doc_id: str,
    system: dict[str, Any],
    channel: str,
    ordinal: int,
    text: str,
    metadata_source_path: Path,
    metadata_provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    index_name = CHANNEL_TO_INDEX[channel]
    return {
        "dataset_doc_uuid": doc_id,
        "channel": channel,
        "index_name": index_name,
        "projection_ordinal": ordinal,
        "projection_file_ref": f"{doc_id}::{index_name}:{ordinal}:{text_hash(text)[:12]}",
        "text": text,
        "text_hash": text_hash(text),
        "source_type": system.get("source_type") or "",
        "source_path": system.get("source_path") or "",
        "title": system.get("title") or "",
        "metadata_source_path": str(metadata_source_path),
        "metadata_provenance": metadata_provenance or {},
    }


def entity_text(entity: Any) -> str:
    if not isinstance(entity, dict):
        return ""
    parts = [
        f"entity: {text_value(entity.get('name'))}",
        f"type: {text_value(entity.get('type'))}",
    ]
    aliases = entity.get("aliases") or []
    if aliases:
        parts.append("aliases: " + ", ".join(text_value(alias) for alias in aliases if text_value(alias)))
    return "\n".join(part for part in parts if part.split(":", 1)[-1].strip())


def relation_text(relation: Any) -> str:
    if not isinstance(relation, dict):
        return ""
    subject = text_value(relation.get("subject"))
    predicate = text_value(relation.get("relation"))
    obj = text_value(relation.get("object"))
    if not subject or not predicate or not obj:
        return ""
    parts = [f"{subject} | {predicate} | {obj}"]
    evidence = relation.get("evidence_terms") or []
    if evidence:
        parts.append("evidence: " + ", ".join(text_value(item) for item in evidence if text_value(item)))
    return "\n".join(parts)


def prepare_embedding_batches(
    run_dir: Path,
    *,
    projection_rows: list[dict[str, Any]],
    cache: BatchEmbeddingCache,
    args: argparse.Namespace,
) -> dict[str, Any]:
    manifest_path = embedding_batch_manifest_path(run_dir, args)
    batch_dir = run_dir / "embedding_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)

    unique_texts_by_hash: dict[str, str] = {}
    for row in projection_rows:
        unique_texts_by_hash.setdefault(row["text_hash"], row["text"])
    unique_hashes = list(unique_texts_by_hash)
    unique_texts = [unique_texts_by_hash[hash_value] for hash_value in unique_hashes]
    cached = cache.get_many(provider=args.embedding_provider, model=cache_model_key(args), texts=unique_texts)
    existing_manifest = read_json(manifest_path) if manifest_path.exists() else {}
    inflight_hashes = embedding_manifest_inflight_text_hashes(existing_manifest, args=args)
    missing_items = [
        (unique_hashes[index], unique_texts[index])
        for index in range(len(unique_texts))
        if index not in cached
        and unique_hashes[index] not in inflight_hashes
    ]

    preserved_batches = embedding_manifest_preserved_batches(existing_manifest, args=args)
    batches: list[dict[str, Any]] = [dict(batch) for batch in preserved_batches]
    next_batch_index = max((int(batch.get("index") or 0) for batch in batches), default=0) + 1
    current_lines: list[str] = []
    current_bytes = 0

    def flush() -> None:
        nonlocal current_lines, current_bytes, next_batch_index
        if not current_lines:
            return
        index = next_batch_index
        next_batch_index += 1
        path = batch_dir / f"embedding_batch_input_{index:05d}.jsonl"
        path.write_text("".join(current_lines), encoding="utf-8")
        batches.append(
            {
                "index": index,
                "input_path": str(path),
                "request_count": len(current_lines),
                "embedding_input_count": len(current_lines),
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

    for hash_value, text in missing_items:
        line = json.dumps(
            embedding_batch_request(hash_value, text, args=args),
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        line_bytes = len(line.encode("utf-8"))
        if line_bytes > args.batch_max_bytes:
            raise SystemExit(f"embedding batch request for text_hash={hash_value} exceeds --batch-max-bytes")
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
    flush()

    manifest = {
        "generated_by": "semantic_metadata_pipeline.build_projection_indexes",
        "status": "prepared" if missing_items else "all_cached",
        "endpoint": "/v1/embeddings",
        "embedding_provider": args.embedding_provider,
        "embedding_model": args.embedding_model,
        "embedding_dimensions": args.embedding_dimensions,
        "cache_model": cache_model_key(args),
        "completion_window": args.batch_completion_window,
        "projection_rows": len(projection_rows),
        "unique_texts": len(unique_texts),
        "cache_hits": len(cached),
        "cache_misses_before_inflight": len(unique_texts) - len(cached),
        "inflight_texts": len(inflight_hashes),
        "preserved_batches": len(preserved_batches),
        "preserved_request_count": sum(int(batch.get("request_count") or 0) for batch in preserved_batches),
        "cache_misses": len(missing_items),
        "request_count": len(missing_items),
        "batch_count": len(batches),
        "batch_max_requests": args.batch_max_requests,
        "batch_max_bytes": args.batch_max_bytes,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "batches": batches,
        "policy": {
            "channels": sorted({row["index_name"] for row in projection_rows}),
            "one_embedding_input_per_request": True,
            "forbidden_channels_not_built": sorted(FORBIDDEN_VECTOR_CHANNELS),
        },
    }
    write_json(manifest_path, manifest)
    return manifest


def embedding_batch_request(hash_value: str, text: str, *, args: argparse.Namespace) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": args.embedding_model,
        "input": text,
    }
    if args.embedding_dimensions > 0:
        body["dimensions"] = args.embedding_dimensions
    return {
        "custom_id": embedding_batch_custom_id(hash_value),
        "method": "POST",
        "url": "/v1/embeddings",
        "body": body,
    }


def embedding_manifest_inflight_text_hashes(manifest: dict[str, Any], *, args: argparse.Namespace) -> set[str]:
    if not embedding_manifest_compatible(manifest, args=args):
        return set()
    hashes: set[str] = set()
    for batch in manifest.get("batches", []):
        if not embedding_batch_inflight(batch):
            continue
        input_path = Path(batch.get("input_path") or "")
        if not input_path.exists():
            continue
        with input_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                hash_value = embedding_text_hash_from_custom_id(str(row.get("custom_id") or ""))
                if hash_value:
                    hashes.add(hash_value)
    return hashes


def embedding_manifest_preserved_batches(manifest: dict[str, Any], *, args: argparse.Namespace) -> list[dict[str, Any]]:
    if not embedding_manifest_compatible(manifest, args=args):
        return []
    return [dict(batch) for batch in manifest.get("batches", []) if embedding_batch_inflight(batch)]


def embedding_manifest_has_unsubmitted_batches(manifest: dict[str, Any]) -> bool:
    return any(batch.get("input_path") and not batch.get("batch_id") for batch in manifest.get("batches", []))


def embedding_manifest_compatible(manifest: dict[str, Any], *, args: argparse.Namespace) -> bool:
    if not manifest:
        return False
    return (
        manifest.get("endpoint") == "/v1/embeddings"
        and manifest.get("embedding_provider") == args.embedding_provider
        and manifest.get("embedding_model") == args.embedding_model
        and int(manifest.get("embedding_dimensions") or 0) == int(args.embedding_dimensions)
        and manifest.get("cache_model") == cache_model_key(args)
    )


def embedding_batch_inflight(batch: dict[str, Any]) -> bool:
    if not batch.get("batch_id"):
        return False
    status = str(batch.get("status") or "")
    if status in {"failed", "expired", "cancelled"}:
        return False
    if status == "completed" and batch.get("output_path"):
        return False
    return True


def embedding_text_hash_from_custom_id(custom_id: str) -> str:
    if not custom_id.startswith("embedding:"):
        return ""
    return custom_id.split(":", 1)[1]


def submit_embedding_batches(
    manifest: dict[str, Any],
    *,
    args: argparse.Namespace,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    if not manifest.get("batches"):
        return manifest
    client = openai_client(args)
    updated_batches = []
    for batch in manifest["batches"]:
        if batch.get("batch_id"):
            updated_batches.append(batch)
            continue
        input_path = Path(batch["input_path"])
        uploaded_file_id = str(batch.get("uploaded_file_id") or "")
        if not uploaded_file_id:
            with input_path.open("rb") as handle:
                uploaded = client.files.create(file=handle, purpose="batch")
            uploaded_file_id = openai_object_value(uploaded, "id")
            batch.update(
                {
                    "uploaded_file_id": uploaded_file_id,
                    "status": "uploaded",
                    "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
            manifest["batches"] = [*updated_batches, *manifest["batches"][len(updated_batches):]]
            manifest["status"] = "uploading"
            if manifest_path is not None:
                write_json(manifest_path, manifest)
        try:
            created = client.batches.create(
                input_file_id=uploaded_file_id,
                endpoint=manifest["endpoint"],
                completion_window=manifest["completion_window"],
                metadata={
                    "pipeline": "semantic_metadata_pipeline",
                    "artifact": "embedding_generation",
                    "batch_index": str(batch["index"]),
                },
            )
        except Exception as exc:  # noqa: BLE001 - provider errors should be recorded for retry.
            if not openai_batch_submit_blocked(exc):
                raise
            batch.update(
                {
                    "uploaded_file_id": uploaded_file_id,
                    "last_submit_error": summarize_exception(exc),
                    "last_submit_error_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "status": "submit_blocked",
                }
            )
            manifest["batches"] = [*updated_batches, *manifest["batches"][len(updated_batches):]]
            manifest["status"] = "submit_blocked"
            manifest["last_submit_error"] = summarize_exception(exc)
            manifest["last_submit_error_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if manifest_path is not None:
                write_json(manifest_path, manifest)
            return manifest
        batch.update(
            {
                "uploaded_file_id": uploaded_file_id,
                "batch_id": openai_object_value(created, "id"),
                "status": openai_object_value(created, "status") or "submitted",
                "output_file_id": openai_object_value(created, "output_file_id"),
                "error_file_id": openai_object_value(created, "error_file_id"),
                "last_submit_error": "",
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


def openai_batch_submit_blocked(exc: Exception) -> bool:
    text = summarize_exception(exc)
    markers = [
        "billing_hard_limit_reached",
        "insufficient_quota",
        "rate_limit",
        "429",
    ]
    return any(marker in text for marker in markers)


def summarize_exception(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    if len(text) > 1000:
        return text[:997] + "..."
    return text


def collect_embedding_batch_results(
    run_dir: Path,
    *,
    cache: BatchEmbeddingCache,
    args: argparse.Namespace,
) -> dict[str, Any]:
    manifest_path = embedding_batch_manifest_path(run_dir, args)
    if not manifest_path.exists():
        raise SystemExit(f"embedding batch manifest not found: {manifest_path}")
    manifest = read_json(manifest_path)
    client = openai_client(args)
    request_text_by_id = embedding_request_texts(manifest)
    errors: list[dict[str, Any]] = []
    vectors_by_text: dict[str, list[float]] = {}
    completed = 0
    terminal = 0
    pending = 0

    for batch in manifest.get("batches", []):
        batch_id = batch.get("batch_id")
        if not batch_id:
            pending += 1
            continue
        remote = client.batches.retrieve(batch_id)
        batch["status"] = openai_object_value(remote, "status") or batch.get("status") or ""
        batch["output_file_id"] = openai_object_value(remote, "output_file_id") or batch.get("output_file_id") or ""
        batch["error_file_id"] = openai_object_value(remote, "error_file_id") or batch.get("error_file_id") or ""
        if batch["status"] == "completed":
            completed += 1
        if batch["status"] in {"completed", "failed", "expired", "cancelled"}:
            terminal += 1
        else:
            pending += 1
        if batch.get("output_file_id"):
            output_path = run_dir / "embedding_batches" / f"embedding_batch_output_{int(batch['index']):05d}.jsonl"
            download_openai_file(client, batch["output_file_id"], output_path)
            batch["output_path"] = str(output_path)
            parsed, parse_errors = parse_embedding_batch_output(output_path, request_text_by_id)
            vectors_by_text.update(parsed)
            errors.extend(parse_errors)
        if batch.get("error_file_id"):
            error_path = run_dir / "embedding_batches" / f"embedding_batch_errors_{int(batch['index']):05d}.jsonl"
            download_openai_file(client, batch["error_file_id"], error_path)
            batch["error_path"] = str(error_path)
            errors.extend(parse_embedding_batch_errors(error_path))

    if vectors_by_text:
        texts = list(vectors_by_text)
        vectors = [vectors_by_text[text] for text in texts]
        cache.set_many(
            provider=manifest.get("embedding_provider") or args.embedding_provider,
            model=manifest.get("cache_model") or cache_model_key(args),
            texts=texts,
            vectors=vectors,
        )

    manifest["status"] = "collected" if pending == 0 else "partially_collected"
    manifest["collected_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["collected_embeddings"] = len(vectors_by_text)
    manifest["parse_error_count"] = len(errors)
    write_json(manifest_path, manifest)

    error_report_path = run_dir / "embedding_batch_parse_errors.json"
    write_json(error_report_path, errors)
    report = {
        "manifest_path": str(manifest_path),
        "completed_batches": completed,
        "terminal_batches": terminal,
        "pending_batches": pending,
        "collected_embeddings": len(vectors_by_text),
        "parse_errors": len(errors),
        "parse_error_report_path": str(error_report_path),
    }
    if pending and not args.allow_partial_batch_results:
        raise SystemExit(
            "embedding batches are not all complete; rerun collect later or pass --allow-partial-batch-results"
        )
    return report


def embedding_request_texts(manifest: dict[str, Any]) -> dict[str, str]:
    request_texts: dict[str, str] = {}
    for batch in manifest.get("batches", []):
        input_path = Path(batch.get("input_path") or "")
        if not input_path.exists():
            continue
        with input_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                request_texts[str(row.get("custom_id") or "")] = str((row.get("body") or {}).get("input") or "")
    return request_texts


def parse_embedding_batch_output(
    path: Path,
    request_text_by_id: dict[str, str],
) -> tuple[dict[str, list[float]], list[dict[str, Any]]]:
    vectors_by_text: dict[str, list[float]] = {}
    errors: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                custom_id = str(row.get("custom_id") or "")
                text = request_text_by_id.get(custom_id)
                if text is None:
                    raise ValueError(f"unknown embedding batch custom_id: {custom_id}")
                response = row.get("response") or {}
                body = response.get("body") or {}
                if response.get("status_code") != 200:
                    errors.append({"path": str(path), "line": line_number, "custom_id": custom_id, "error": row.get("error") or response})
                    continue
                data = body.get("data") or []
                if not data or "embedding" not in data[0]:
                    raise ValueError(f"missing embedding in response for {custom_id}")
                vectors_by_text[text] = [float(value) for value in data[0]["embedding"]]
            except Exception as exc:  # noqa: BLE001 - batch output should be diagnosed, not crash obscurely.
                errors.append({"path": str(path), "line": line_number, "error": str(exc)})
    return vectors_by_text, errors


def parse_embedding_batch_errors(path: Path) -> list[dict[str, Any]]:
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


def write_projection_batch_prepare_artifacts(
    run_dir: Path,
    *,
    args: argparse.Namespace,
    metadata_path: Path,
    index_dir: Path,
    projection_rows: list[dict[str, Any]],
    skipped_rows: dict[str, int],
    sample_by_channel: dict[str, list[dict[str, Any]]],
    batch_manifest: dict[str, Any],
) -> None:
    sample_path = run_dir / "sample_projection_rows.json"
    manifest_path = run_dir / "projection_manifest.json"
    write_json(sample_path, sample_by_channel)
    write_json(
        manifest_path,
        {
            "generated_by": "semantic_metadata_pipeline.build_projection_indexes",
            "status": f"embedding_batch_{batch_manifest.get('status', 'prepared')}",
            "metadata_source_path": str(metadata_path),
            "index_dir": str(index_dir),
            "embedding_provider": args.embedding_provider,
            "embedding_model": args.embedding_model,
            "embedding_dimensions": args.embedding_dimensions,
            "embedding_batch_mode": args.embedding_batch_mode,
            "embedding_batch_manifest_path": str(embedding_batch_manifest_path(run_dir, args)),
            "input_rows": len(projection_rows),
            "skipped_rows": sum(skipped_rows.values()),
            "batch_request_count": batch_manifest.get("request_count", 0),
            "batch_count": batch_manifest.get("batch_count", 0),
            "forbidden_channels_not_built": sorted(FORBIDDEN_VECTOR_CHANNELS),
        },
    )
    update_config(
        run_dir,
        "projection_indexes",
        {
            "metadata_path": str(metadata_path),
            "index_dir": str(index_dir),
            "projection_manifest_path": str(manifest_path),
            "sample_projection_rows_path": str(sample_path),
            "embedding_provider": args.embedding_provider,
            "embedding_model": args.embedding_model,
            "embedding_dimensions": args.embedding_dimensions,
            "embedding_batch_mode": args.embedding_batch_mode,
            "embedding_batch_manifest_path": str(embedding_batch_manifest_path(run_dir, args)),
        },
    )
    write_summary(
        run_dir,
        {
            "projection_indexes": {
                "status": f"embedding_batch_{batch_manifest.get('status', 'prepared')}",
                "input_rows": len(projection_rows),
                "batch_request_count": batch_manifest.get("request_count", 0),
                "batch_count": batch_manifest.get("batch_count", 0),
            }
        },
    )


def build_channel_index(
    *,
    rows: list[dict[str, Any]],
    index_path: Path,
    channel: str,
    index_name: str,
    cache: BatchEmbeddingCache,
    embedder: Any,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if not rows:
        return {
            "index_name": index_name,
            "index_path": str(index_path),
            "input_rows": 0,
            "indexed_rows": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }
    texts = [row["text"] for row in rows]
    vectors, cache_stats = batch_embed_with_cache(
        texts=texts,
        cache=cache,
        provider=args.embedding_provider,
        model=cache_model_key(args),
        embedder=embedder,
        batch_size=args.batch_size,
    )
    index = SQLiteVecSemanticIndex(index_path)
    index.reset(
        dimension=len(vectors[0]),
        metadata={
            "pipeline": "semantic_metadata_pipeline",
            "projection_index": index_name,
            "channel": channel,
            "embedding_provider": args.embedding_provider,
            "embedding_model": args.embedding_model,
            "embedding_dimensions": args.embedding_dimensions,
            "batch_size": args.batch_size,
            "input_rows": len(rows),
        },
    )
    indexed_rows = 0
    for start in range(0, len(rows), max(1, args.batch_size)):
        batch_rows = rows[start:start + max(1, args.batch_size)]
        batch_vectors = vectors[start:start + max(1, args.batch_size)]
        records = [
            SemanticIndexRecord(
                file_ref=row["projection_file_ref"],
                external_id=row["dataset_doc_uuid"],
                source_type=row["source_type"],
                source_path=row["source_path"],
                title=row["title"],
                text=row["text"],
                vector=vector,
                metadata={
                    "dataset_doc_uuid": row["dataset_doc_uuid"],
                    "projection_index": row["index_name"],
                    "projection_channel": row["channel"],
                    "projection_ordinal": row["projection_ordinal"],
                    "text_hash": row["text_hash"],
                    "metadata_source_path": row["metadata_source_path"],
                    "metadata_provenance": row["metadata_provenance"],
                    "text_preview": row["text"][:500],
                },
            )
            for row, vector in zip(batch_rows, batch_vectors)
        ]
        indexed_rows += index.upsert_many(records)
    return {
        "index_name": index_name,
        "index_path": str(index_path),
        "input_rows": len(rows),
        "indexed_rows": indexed_rows,
        "cache_hits": cache_stats["cache_hits"],
        "cache_misses": cache_stats["cache_misses"],
        "index_info": index.info(),
    }


def build_channel_index_from_cache(
    *,
    rows: list[dict[str, Any]],
    index_path: Path,
    channel: str,
    index_name: str,
    cache: BatchEmbeddingCache,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if not rows:
        return {
            "index_name": index_name,
            "index_path": str(index_path),
            "input_rows": 0,
            "indexed_rows": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }
    texts = [row["text"] for row in rows]
    cached = cache.get_many(
        provider=args.embedding_provider,
        model=cache_model_key(args),
        texts=texts,
    )
    missing = [index for index in range(len(texts)) if index not in cached]
    if missing:
        raise SystemExit(
            f"embedding cache is missing {len(missing)} rows for {index_name}; "
            "collect completed batch outputs before building indexes"
        )
    vectors = [cached[index] for index in range(len(texts))]
    index = SQLiteVecSemanticIndex(index_path)
    index.reset(
        dimension=len(vectors[0]),
        metadata={
            "pipeline": "semantic_metadata_pipeline",
            "projection_index": index_name,
            "channel": channel,
            "embedding_provider": args.embedding_provider,
            "embedding_model": args.embedding_model,
            "embedding_dimensions": args.embedding_dimensions,
            "embedding_batch_mode": args.embedding_batch_mode,
            "batch_size": args.batch_size,
            "input_rows": len(rows),
        },
    )
    indexed_rows = 0
    for start in range(0, len(rows), max(1, args.batch_size)):
        batch_rows = rows[start:start + max(1, args.batch_size)]
        batch_vectors = vectors[start:start + max(1, args.batch_size)]
        indexed_rows += index.upsert_many(records_from_rows(batch_rows, batch_vectors))
    return {
        "index_name": index_name,
        "index_path": str(index_path),
        "input_rows": len(rows),
        "indexed_rows": indexed_rows,
        "cache_hits": len(cached),
        "cache_misses": len(missing),
        "index_info": index.info(),
    }


def records_from_rows(rows: list[dict[str, Any]], vectors: list[list[float]]) -> list[SemanticIndexRecord]:
    return [
        SemanticIndexRecord(
            file_ref=row["projection_file_ref"],
            external_id=row["dataset_doc_uuid"],
            source_type=row["source_type"],
            source_path=row["source_path"],
            title=row["title"],
            text=row["text"],
            vector=vector,
            metadata={
                "dataset_doc_uuid": row["dataset_doc_uuid"],
                "projection_index": row["index_name"],
                "projection_channel": row["channel"],
                "projection_ordinal": row["projection_ordinal"],
                "text_hash": row["text_hash"],
                "metadata_source_path": row["metadata_source_path"],
                "metadata_provenance": row["metadata_provenance"],
                "text_preview": row["text"][:500],
            },
        )
        for row, vector in zip(rows, vectors)
    ]


def cache_model_key(args: argparse.Namespace) -> str:
    if args.embedding_dimensions > 0:
        return f"{args.embedding_model}:{args.embedding_dimensions}"
    return args.embedding_model


def embedding_batch_custom_id(hash_value: str) -> str:
    return f"embedding:{hash_value}"


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


def openai_client(args: argparse.Namespace) -> Any:
    from openai import OpenAI

    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url=args.base_url or None)


def openai_object_value(obj: Any, field: str) -> str:
    if obj is None:
        return ""
    if isinstance(obj, dict):
        return text_value(obj.get(field))
    return text_value(getattr(obj, field, ""))


if __name__ == "__main__":
    raise SystemExit(main())
