from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


V2_DIR = Path(__file__).resolve().parent
RESEARCH_DIR = V2_DIR.parent
BENCHMARK_DIR = RESEARCH_DIR.parent
REPO_ROOT = V2_DIR.parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
from openai import OpenAI

from examples.Benchmark.enterprise_rag_benchmark.auto_gen_research.run_auto_gen_research import (
    doc_summary,
    load_selected_documents,
    question_to_json,
    select_questions,
)
from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import load_questions
from pageindex.filesystem import PIFSCommandExecutor, PageIndexFileSystem
from pageindex.filesystem.store import metadata_text


LOGGER = logging.getLogger("semantic_folder_v2")

SEMANTIC_METADATA_SCHEMA = {
    "semantic_summary": {"type": "string", "description": "Short factual summary extracted from document text only"},
    "semantic_topics": {"type": "string", "description": "Content topics extracted from document text only"},
    "semantic_entities": {"type": "string", "description": "Named entities or important concepts from document text only"},
    "semantic_systems": {"type": "string", "description": "Systems, services, modules, interfaces, or components named in text"},
    "semantic_problems": {"type": "string", "description": "Problems, risks, failures, or pain points described in text"},
    "semantic_actions": {"type": "string", "description": "Actions, changes, decisions, or operations described in text"},
    "semantic_constraints": {"type": "string", "description": "Rules, requirements, defaults, thresholds, or limits described in text"},
    "semantic_measurements": {"type": "string", "description": "Numbers, units, metrics, limits, or measurements described in text"},
    "semantic_events": {"type": "string", "description": "Events or state changes described in text"},
    "semantic_time_hints": {"type": "string", "description": "Dates or time periods explicitly present in text"},
    "semantic_aliases": {"type": "string", "description": "Search aliases grounded in the document wording"},
    "semantic_evidence_terms": {"type": "string", "description": "Exact or near-exact terms useful for finding evidence in the text"},
}

FOLDER_FIELD_ROOTS = {
    "semantic_topics": "topics",
    "semantic_problems": "problems",
    "semantic_actions": "actions",
    "semantic_constraints": "constraints",
    "semantic_systems": "systems",
    "semantic_entities": "entities",
    "semantic_events": "events",
    "semantic_measurements": "measurements",
}

FOLDER_FIELD_ORDER = [
    "semantic_topics",
    "semantic_problems",
    "semantic_actions",
    "semantic_constraints",
    "semantic_systems",
    "semantic_entities",
    "semantic_events",
    "semantic_measurements",
]

LIST_FIELDS = [
    "semantic_topics",
    "semantic_entities",
    "semantic_systems",
    "semantic_problems",
    "semantic_actions",
    "semantic_constraints",
    "semantic_measurements",
    "semantic_events",
    "semantic_time_hints",
    "semantic_aliases",
    "semantic_evidence_terms",
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[semantic-v2] %(message)s")
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BENCHMARK_DIR / ".env")
    args = parse_args()

    dataset_root = (BENCHMARK_DIR / args.dataset).resolve()
    source_root = dataset_root / "generated_data" / "sources"
    questions_path = dataset_root / "questions.jsonl"
    run_name = args.run_name or time.strftime(f"%Y%m%d-%H%M%S-{args.target_docs}docs-semantic-v2")
    run_dir = V2_DIR / "results" / run_name
    generated_dir = V2_DIR / "generated" / run_name
    workspace = V2_DIR / "workspaces" / run_name / args.workspace_name

    if args.reset:
        for path in [run_dir, generated_dir, workspace.parent]:
            if path.exists():
                shutil.rmtree(path)
    run_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    LOGGER.info("run=%s target_docs=%s workspace=%s", run_name, args.target_docs, workspace)

    questions = select_questions(
        load_questions(questions_path),
        question_ids=args.question_ids,
        target_docs=args.target_docs,
    )
    docs = load_selected_documents(source_root, questions)
    selection_id = selection_cache_id(docs)
    LOGGER.info("selected questions=%d docs=%d selection_id=%s", len(questions), len(docs), selection_id)
    write_json(run_dir / "selected_questions.json", [question_to_json(question) for question in questions])
    write_json(run_dir / "selected_documents.json", [doc_summary(doc) for doc in docs])

    filesystem = PageIndexFileSystem(workspace=workspace)
    filesystem._register_metadata_schema({"fields": SEMANTIC_METADATA_SCHEMA})
    LOGGER.info("registered metadata schema fields=%d", len(SEMANTIC_METADATA_SCHEMA))
    file_refs = register_documents(filesystem, docs)
    LOGGER.info("registered documents=%d", len(file_refs))
    semantic_inputs = semantic_document_inputs(docs)

    semantic_metadata = load_or_generate_semantic_metadata(
        semantic_inputs,
        generated_dir / f"semantic_metadata_{selection_id}.json",
        model=args.generation_model,
        max_doc_chars=args.max_doc_chars,
        workers=args.metadata_workers,
        reuse_only=args.reuse_generated_only,
    )
    LOGGER.info("semantic metadata ready docs=%d", len(semantic_metadata))
    for index, doc in enumerate(docs, 1):
        doc_id = doc["dataset_doc_uuid"]
        update_registered_semantic_metadata(filesystem, file_refs[doc_id], semantic_metadata[doc_id])
        LOGGER.info("persisted metadata %d/%d %s", index, len(docs), doc_id)
    LOGGER.info("semantic metadata persisted to sqlite/fts")

    canonicalization = load_or_generate_canonicalization(
        semantic_metadata,
        generated_dir / f"canonicalization_{selection_id}.json",
        model=args.generation_model,
        reuse_only=args.reuse_generated_only,
    )
    LOGGER.info("canonicalization ready fields=%d", len(canonicalization))
    folder_plan = build_folder_plan(
        docs,
        semantic_metadata,
        canonicalization,
        max_folders_per_doc=args.max_folders_per_doc,
    )
    LOGGER.info(
        "folder plan built folders=%d memberships=%d",
        len(folder_plan["folders"]),
        len(folder_plan["memberships"]),
    )
    materialize_folder_plan(filesystem, file_refs, folder_plan)
    LOGGER.info("folder plan materialized")

    write_json(run_dir / "semantic_metadata.json", semantic_metadata)
    write_json(run_dir / "canonicalization.json", canonicalization)
    write_json(run_dir / "folder_plan.json", folder_plan)
    summary = {
        "run_name": run_name,
        "selection_id": selection_id,
        "workspace": str(workspace),
        "dataset_root": str(dataset_root),
        "question_ids": [question.question_id for question in questions],
        "document_count": len(docs),
        "generation_model": args.generation_model,
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "semantic_metadata_schema": SEMANTIC_METADATA_SCHEMA,
        "folder_count": len(folder_plan["folders"]),
        "membership_count": len(folder_plan["memberships"]),
        "smoke": smoke(filesystem),
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate semantic metadata and folders for an EnterpriseRAG subset")
    parser.add_argument("--dataset", default="dataset")
    parser.add_argument("--question-ids", default="")
    parser.add_argument("--target-docs", type=int, default=10)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--workspace-name", default="semantic-metadata-folders-v2")
    parser.add_argument(
        "--generation-model",
        default=os.environ.get("PIFS_GENERATION_MODEL", os.environ.get("PIFS_AGENT_MODEL", "gpt-4.1-mini")),
    )
    parser.add_argument("--max-doc-chars", type=int, default=12000)
    parser.add_argument("--metadata-workers", type=int, default=int(os.environ.get("PIFS_METADATA_WORKERS", "4")))
    parser.add_argument("--max-folders-per-doc", type=int, default=6)
    parser.add_argument("--reuse-generated-only", action="store_true")
    parser.add_argument("--reset", action="store_true")
    return parser.parse_args()


def register_documents(filesystem: PageIndexFileSystem, docs: list[dict[str, Any]]) -> dict[str, str]:
    file_refs = {}
    for index, doc in enumerate(docs, 1):
        file_refs[doc["dataset_doc_uuid"]] = filesystem.register_file(
            storage_uri=doc["path"],
            source_path=doc["source_path"],
            external_id=doc["dataset_doc_uuid"],
            title=doc["title"],
            metadata={},
            content=doc["text"],
            content_type="application/json",
            source_type=doc["source_type"],
        )
        LOGGER.info("registered document %d/%d %s", index, len(docs), doc["dataset_doc_uuid"])
    return file_refs


def semantic_document_inputs(docs: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "dataset_doc_uuid": doc["dataset_doc_uuid"],
            "text": doc["text"],
        }
        for doc in docs
    ]


def update_registered_semantic_metadata(
    filesystem: PageIndexFileSystem,
    file_ref: str,
    semantic_metadata: dict[str, Any],
) -> None:
    normalized = normalize_semantic_metadata(semantic_metadata)
    entry = filesystem.store.get_file(file_ref)
    text = filesystem.store.read_text(file_ref)
    metadata_json = json.dumps(normalized, ensure_ascii=False)
    with filesystem.store.connect() as conn:
        conn.execute(
            """
            UPDATE files
            SET metadata_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE file_ref = ? AND deleted_at IS NULL
            """,
            (metadata_json, file_ref),
        )
        filesystem.store.replace_metadata_values(conn, file_ref, normalized)
        conn.execute("DELETE FROM file_fts WHERE file_ref = ?", (file_ref,))
        conn.execute(
            """
            INSERT INTO file_fts(file_ref, title, body, metadata_text)
            VALUES (?, ?, ?, ?)
            """,
            (file_ref, entry.title, text, metadata_text(normalized)),
        )


def load_or_generate_semantic_metadata(
    docs: list[dict[str, str]],
    cache_path: Path,
    *,
    model: str,
    max_doc_chars: int,
    workers: int,
    reuse_only: bool,
) -> dict[str, dict[str, Any]]:
    cached = read_json(cache_path) if cache_path.exists() else {}
    metadata = {doc_id: normalize_semantic_metadata(value) for doc_id, value in cached.items()}
    missing = [doc for doc in docs if doc["dataset_doc_uuid"] not in metadata]
    LOGGER.info("semantic metadata cache hit=%d missing=%d", len(metadata), len(missing))
    if missing and reuse_only:
        raise RuntimeError(
            "Missing cached semantic metadata: "
            + ", ".join(doc["dataset_doc_uuid"] for doc in missing)
        )
    if not missing:
        return metadata
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if workers <= 1:
        client = openai_client()
        for index, doc in enumerate(missing, 1):
            LOGGER.info("metadata %d/%d %s", index, len(missing), doc["dataset_doc_uuid"])
            metadata[doc["dataset_doc_uuid"]] = generate_semantic_metadata(client, model, doc, max_doc_chars)
            write_json(cache_path, metadata)
        return metadata
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(generate_semantic_metadata_for_doc, model, doc, max_doc_chars): doc
            for doc in missing
        }
        for index, future in enumerate(as_completed(futures), 1):
            doc = futures[future]
            metadata[doc["dataset_doc_uuid"]] = future.result()
            write_json(cache_path, metadata)
            LOGGER.info("metadata %d/%d %s", index, len(missing), doc["dataset_doc_uuid"])
    return metadata


def generate_semantic_metadata_for_doc(model: str, doc: dict[str, Any], max_doc_chars: int) -> dict[str, Any]:
    client = openai_client()
    return generate_semantic_metadata(client, model, doc, max_doc_chars)


def generate_semantic_metadata(
    client: OpenAI,
    model: str,
    doc: dict[str, str],
    max_doc_chars: int,
) -> dict[str, Any]:
    document_text = doc["text"][:max_doc_chars]
    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract document-level semantic metadata for retrieval. "
                    "Use ONLY the provided document text. Do not infer from filenames, paths, "
                    "source systems, benchmark questions, gold answers, or outside knowledge. "
                    "If a field is not grounded in the text, return an empty list or empty string. "
                    "Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Return exactly these keys: "
                    "semantic_summary:string, semantic_topics:string[], semantic_entities:string[], "
                    "semantic_systems:string[], semantic_problems:string[], semantic_actions:string[], "
                    "semantic_constraints:string[], semantic_measurements:string[], semantic_events:string[], "
                    "semantic_time_hints:string[], semantic_aliases:string[], semantic_evidence_terms:string[]. "
                    "Keep labels short, lowercase when natural, and grounded in the text.\n\n"
                    f"Document text:\n{document_text}"
                ),
            },
        ],
    )
    return normalize_semantic_metadata(json.loads(response.choices[0].message.content or "{}"))


def normalize_semantic_metadata(value: dict[str, Any]) -> dict[str, Any]:
    normalized = {"semantic_summary": as_text(value.get("semantic_summary"))}
    for field in LIST_FIELDS:
        normalized[field] = clean_list(value.get(field))
    return normalized


def load_or_generate_canonicalization(
    semantic_metadata: dict[str, dict[str, Any]],
    cache_path: Path,
    *,
    model: str,
    reuse_only: bool,
) -> dict[str, dict[str, dict[str, Any]]]:
    labels_by_field = collect_labels(semantic_metadata)
    cached_raw = read_json(cache_path) if cache_path.exists() else {}
    cached_fields = cached_raw.get("fields", cached_raw) if isinstance(cached_raw, dict) else {}
    canonicalization: dict[str, dict[str, dict[str, Any]]] = {}
    fields = list(labels_by_field.items())
    LOGGER.info("canonicalization fields=%d cache_path=%s", len(fields), cache_path)
    client = openai_client() if fields else None
    for index, (field, labels) in enumerate(fields, 1):
        cached_field = cached_fields.get(field) if isinstance(cached_fields, dict) else None
        if cached_field:
            canonicalization[field] = normalize_canonicalization(
                {"fields": {field: cached_field}},
                {field: labels},
            )[field]
            LOGGER.info(
                "canonicalization %d/%d %s labels=%d cache-hit",
                index,
                len(fields),
                field,
                len(labels),
            )
            continue
        if reuse_only:
            raise RuntimeError(f"Missing cached canonicalization field: {field}")
        LOGGER.info(
            "canonicalization %d/%d %s labels=%d start",
            index,
            len(fields),
            field,
            len(labels),
        )
        assert client is not None
        field_result = canonicalize_labels(client, model, {field: labels})
        canonicalization[field] = field_result[field]
        write_json(cache_path, {"fields": canonicalization})
        LOGGER.info("canonicalization %d/%d %s done", index, len(fields), field)
    return canonicalization


def collect_labels(semantic_metadata: dict[str, dict[str, Any]]) -> dict[str, dict[str, int]]:
    labels_by_field: dict[str, Counter[str]] = {}
    for metadata in semantic_metadata.values():
        for field in FOLDER_FIELD_ORDER:
            labels_by_field.setdefault(field, Counter())
            for label in clean_list(metadata.get(field)):
                labels_by_field[field][label] += 1
    return {field: dict(counter) for field, counter in labels_by_field.items() if counter}


def canonicalize_labels(
    client: OpenAI,
    model: str,
    labels_by_field: dict[str, dict[str, int]],
) -> dict[str, dict[str, dict[str, Any]]]:
    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Normalize semantic metadata labels for a virtual filesystem. "
                    "Only merge labels that mean the same thing in the same field. "
                    "Create broad parent_label values so folders have at most three levels: "
                    "/semantic/<field-root>/<parent>/<canonical>. Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    "For every raw label in every field, return "
                    "{fields:{<field>:[{raw_label, canonical_label, parent_label, aliases:string[]}]}}. "
                    "Do not drop labels. Keep canonical_label and parent_label short and browseable.\n\n"
                    f"Labels by field with counts:\n{json.dumps(labels_by_field, ensure_ascii=False, sort_keys=True)}"
                ),
            },
        ],
    )
    generated = json.loads(response.choices[0].message.content or "{}")
    return normalize_canonicalization(generated, labels_by_field)


def normalize_canonicalization(
    generated: dict[str, Any],
    labels_by_field: dict[str, dict[str, int]],
) -> dict[str, dict[str, dict[str, Any]]]:
    raw_fields = generated.get("fields", generated)
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for field, labels in labels_by_field.items():
        result[field] = {}
        generated_items = raw_fields.get(field, []) if isinstance(raw_fields, dict) else []
        generated_by_raw = {}
        if isinstance(generated_items, list):
            for item in generated_items:
                if isinstance(item, dict) and item.get("raw_label"):
                    generated_by_raw[as_text(item["raw_label"])] = item
        elif isinstance(generated_items, dict):
            for raw_label, item in generated_items.items():
                if isinstance(item, dict):
                    generated_by_raw[as_text(raw_label)] = item
        for raw_label in labels:
            item = generated_by_raw.get(raw_label, {})
            canonical_label = as_text(item.get("canonical_label")) or fallback_label(raw_label)
            parent_label = as_text(item.get("parent_label")) or parent_from_field(field, canonical_label)
            result[field][raw_label] = {
                "canonical_label": canonical_label,
                "parent_label": parent_label,
                "aliases": clean_list(item.get("aliases")) or ([] if canonical_label == raw_label else [raw_label]),
                "count": labels[raw_label],
            }
    return result


def build_folder_plan(
    docs: list[dict[str, Any]],
    semantic_metadata: dict[str, dict[str, Any]],
    canonicalization: dict[str, dict[str, dict[str, Any]]],
    *,
    max_folders_per_doc: int = 6,
) -> dict[str, Any]:
    folders: dict[str, dict[str, Any]] = {}
    memberships = []
    for index, doc in enumerate(docs, 1):
        doc_id = doc["dataset_doc_uuid"]
        selected = selected_memberships_for_doc(
            doc_id,
            semantic_metadata.get(doc_id, {}),
            canonicalization,
            max_folders_per_doc=max_folders_per_doc,
        )
        LOGGER.info("planned folders %d/%d %s memberships=%d", index, len(docs), doc_id, len(selected))
        for item in selected:
            path = item["path"]
            folders.setdefault(
                path,
                {
                    "path": path,
                    "kind": "semantic",
                    "description": item["description"],
                    "metadata": item["folder_metadata"],
                },
            )
            memberships.append(
                {
                    "dataset_doc_uuid": doc_id,
                    "folder_path": path,
                    "metadata": item["membership_metadata"],
                }
            )
    return {
        "folders": sorted(folders.values(), key=lambda item: item["path"]),
        "memberships": memberships,
    }


def selected_memberships_for_doc(
    doc_id: str,
    metadata: dict[str, Any],
    canonicalization: dict[str, dict[str, dict[str, Any]]],
    *,
    max_folders_per_doc: int,
) -> list[dict[str, Any]]:
    selected = []
    seen_paths = set()
    for field in FOLDER_FIELD_ORDER:
        for raw_label in clean_list(metadata.get(field))[:3]:
            item = canonicalization.get(field, {}).get(raw_label)
            if not item:
                canonical_label = fallback_label(raw_label)
                parent_label = parent_from_field(field, canonical_label)
                aliases = [] if canonical_label == raw_label else [raw_label]
            else:
                canonical_label = as_text(item.get("canonical_label")) or fallback_label(raw_label)
                parent_label = as_text(item.get("parent_label")) or parent_from_field(field, canonical_label)
                aliases = clean_list(item.get("aliases"))
            path = semantic_folder_path(field, parent_label, canonical_label)
            if path in seen_paths:
                continue
            seen_paths.add(path)
            selected.append(
                {
                    "path": path,
                    "description": f"{field_root_name(field)}: {canonical_label}",
                    "folder_metadata": {
                        "source_field": field,
                        "canonical_label": canonical_label,
                        "parent_label": parent_label,
                        "aliases": aliases,
                        "generator": "semantic-folder-v2",
                    },
                    "membership_metadata": {
                        "dataset_doc_uuid": doc_id,
                        "source_field": field,
                        "raw_label": raw_label,
                        "canonical_label": canonical_label,
                        "parent_label": parent_label,
                        "aliases": aliases,
                    },
                }
            )
            if len(selected) >= max_folders_per_doc:
                return selected
    return selected


def materialize_folder_plan(
    filesystem: PageIndexFileSystem,
    file_refs: dict[str, str],
    folder_plan: dict[str, Any],
) -> None:
    folders = folder_plan["folders"]
    for index, folder in enumerate(folders, 1):
        filesystem.create_folder(
            folder["path"],
            kind=folder.get("kind", "semantic"),
            description=folder.get("description", ""),
            metadata=folder.get("metadata") or {},
        )
        if index == 1 or index % 100 == 0 or index == len(folders):
            LOGGER.info("created folder %d/%d %s", index, len(folders), folder["path"])
    memberships = folder_plan["memberships"]
    for index, membership in enumerate(memberships, 1):
        filesystem.attach_file_to_folder(
            file_refs[membership["dataset_doc_uuid"]],
            membership["folder_path"],
            metadata=membership.get("metadata") or {},
        )
        LOGGER.info(
            "attached file %d/%d %s -> %s",
            index,
            len(memberships),
            membership["dataset_doc_uuid"],
            membership["folder_path"],
        )


def smoke(filesystem: PageIndexFileSystem) -> dict[str, Any]:
    executor = PIFSCommandExecutor(filesystem, json_output=True)
    data = {
        "ls_root": json.loads(executor.execute("ls /")),
    }
    try:
        data["ls_semantic"] = json.loads(executor.execute("ls /semantic"))
    except Exception as exc:  # noqa: BLE001 - smoke should report, not fail the run.
        data["ls_semantic_error"] = f"{type(exc).__name__}: {exc}"
    return data


def openai_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    kwargs = {"api_key": api_key}
    if os.environ.get("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
    return OpenAI(**kwargs)


def selection_cache_id(docs: list[dict[str, Any]]) -> str:
    doc_ids = [doc["dataset_doc_uuid"] for doc in docs]
    import hashlib

    digest = hashlib.sha1("\n".join(doc_ids).encode("utf-8")).hexdigest()[:10]
    return f"{len(doc_ids)}docs-{digest}"


def semantic_folder_path(field: str, parent_label: str, canonical_label: str) -> str:
    root = FOLDER_FIELD_ROOTS[field]
    parent_slug = slug(parent_label)
    label_slug = slug(canonical_label)
    if not parent_slug or parent_slug == label_slug:
        return f"/semantic/{root}/{label_slug}"
    return f"/semantic/{root}/{parent_slug}/{label_slug}"


def field_root_name(field: str) -> str:
    return FOLDER_FIELD_ROOTS.get(field, field).replace("_", " ")


def parent_from_field(field: str, canonical_label: str) -> str:
    words = [word for word in re.split(r"\s+", canonical_label.strip()) if word]
    if len(words) >= 3:
        return " ".join(words[:2])
    return field_root_name(field)


def fallback_label(raw_label: str) -> str:
    normalized = re.sub(r"[\s_-]+", " ", raw_label.strip().lower())
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized or "unknown"


def slug(value: str) -> str:
    text = fallback_label(value)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "unknown"


def clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("name") or item.get("label") or item.get("value") or ""
            values.append(as_text(item))
    else:
        values = [as_text(value)]
    cleaned = []
    seen = set()
    for item in values:
        item = re.sub(r"\s+", " ", item.strip())
        if not item or item.lower() in {"none", "n/a", "unknown"}:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return cleaned[:12]


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
