from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


RESEARCH_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = RESEARCH_DIR.parent
REPO_ROOT = RESEARCH_DIR.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
from openai import OpenAI

from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import (
    EnterpriseRAGQuestion,
    load_questions,
)
from examples.Benchmark.enterprise_rag_benchmark.run_enterprise_rag_pifs_agent import (
    parse_agent_json,
)
from pageindex.filesystem import PageIndexFileSystem
from pageindex.filesystem.agent import run_pifs_agent


METADATA_STRATEGIES = ["entity_relation_fixed_schema", "dag_fixed_schema"]
FOLDER_STRATEGIES = ["topic_count_folder", "entropy_folder"]

ENTITY_RELATION_FIXED_SCHEMA = {
    "dataset_doc_uuid": {"type": "string", "description": "EnterpriseRAG document id"},
    "source_type": {"type": "string", "description": "Original EnterpriseRAG source type"},
    "title": {"type": "string", "description": "Document title"},
    "doc_type": {"type": "string", "description": "LLM-classified document kind"},
    "primary_topic": {"type": "string", "description": "Main semantic topic"},
    "topic_cluster": {"type": "string", "description": "Corpus-level topic cluster"},
    "secondary_topics": {"type": "string", "description": "Additional topic labels"},
    "primary_entity": {"type": "string", "description": "Most salient entity"},
    "entities": {"type": "string", "description": "Flattened salient entities"},
    "relations": {"type": "string", "description": "Flattened entity relation triples"},
    "action_or_event": {"type": "string", "description": "Main event, decision, or action"},
    "time_period": {"type": "string", "description": "Most specific time period"},
    "product_or_system": {"type": "string", "description": "Product, system, or service"},
    "communication_channel": {"type": "string", "description": "Document channel or medium"},
    "semantic_summary": {"type": "string", "description": "Short retrieval summary"},
    "search_terms": {"type": "string", "description": "Dense lexical retrieval terms"},
}

DAG_FIXED_SCHEMA = {
    "dataset_doc_uuid": {"type": "string", "description": "EnterpriseRAG document id"},
    "source_type": {"type": "string", "description": "Original EnterpriseRAG source type"},
    "title": {"type": "string", "description": "Document title"},
    "doc_type": {"type": "string", "description": "LLM-classified document kind"},
    "primary_topic": {"type": "string", "description": "Main semantic topic"},
    "topic_cluster": {"type": "string", "description": "Corpus-level topic cluster"},
    "graph_nodes": {"type": "string", "description": "Typed DAG nodes"},
    "graph_edges": {"type": "string", "description": "Directed relation edges"},
    "edge_directions": {"type": "string", "description": "Direction-only edge signatures"},
    "root_entities": {"type": "string", "description": "Entities that mostly act as relation sources"},
    "leaf_entities": {"type": "string", "description": "Entities that mostly act as relation targets"},
    "relation_path": {"type": "string", "description": "Compressed entity relation path"},
    "evidence_terms": {"type": "string", "description": "Important terms attached to graph edges"},
    "semantic_summary": {"type": "string", "description": "Short retrieval summary"},
}


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BENCHMARK_DIR / ".env")
    args = parse_args()

    dataset_root = (BENCHMARK_DIR / args.dataset).resolve()
    source_root = dataset_root / "generated_data" / "sources"
    questions_path = dataset_root / "questions.jsonl"
    generated_dir = RESEARCH_DIR / "generated"
    results_dir = RESEARCH_DIR / "results" / args.run_name
    workspaces_dir = RESEARCH_DIR / "workspaces" / args.run_name

    if args.reset:
        for path in [results_dir, workspaces_dir]:
            if path.exists():
                shutil.rmtree(path)
    generated_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    workspaces_dir.mkdir(parents=True, exist_ok=True)

    questions = select_questions(
        load_questions(questions_path),
        question_ids=args.question_ids,
        target_docs=args.target_docs,
    )
    docs = load_selected_documents(source_root, questions)
    profiles = load_or_generate_profiles(
        docs,
        generated_dir / "doc_profiles.json",
        model=args.generation_model,
        max_doc_chars=args.max_doc_chars,
        reuse_only=args.reuse_generated_only,
    )
    clusters = load_or_generate_clusters(
        docs,
        profiles,
        generated_dir / "topic_clusters.json",
        model=args.generation_model,
        reuse_only=args.reuse_generated_only,
    )
    plans = build_folder_plans(docs, profiles, clusters)

    write_json(results_dir / "selected_questions.json", [question_to_json(q) for q in questions])
    write_json(results_dir / "selected_documents.json", [doc_summary(doc) for doc in docs])
    write_json(results_dir / "folder_plans.json", plans)

    all_results = []
    for metadata_strategy in METADATA_STRATEGIES:
        for folder_strategy in FOLDER_STRATEGIES:
            combo = f"{metadata_strategy}__{folder_strategy}"
            print(f"[auto-gen] registering {combo}", flush=True)
            workspace = workspaces_dir / combo
            if workspace.exists():
                shutil.rmtree(workspace)
            filesystem = build_workspace(
                workspace,
                docs,
                profiles,
                clusters,
                plans,
                metadata_strategy=metadata_strategy,
                folder_strategy=folder_strategy,
            )
            print(f"[auto-gen] evaluating {combo}", flush=True)
            combo_results = evaluate_combo(
                filesystem,
                questions,
                model=args.agent_model,
                max_turns=args.max_turns,
                skip_agent=args.skip_agent,
                verbose=args.verbose,
            )
            combo_summary = summarize_combo(combo, combo_results)
            combo_summary.update(
                {
                    "metadata_strategy": metadata_strategy,
                    "folder_strategy": folder_strategy,
                    "workspace": str(workspace),
                }
            )
            write_json(results_dir / f"{combo}.results.json", combo_results)
            write_json(results_dir / f"{combo}.summary.json", combo_summary)
            all_results.append(combo_summary)
            print(json.dumps(combo_summary, ensure_ascii=False), flush=True)

    summary = {
        "run_name": args.run_name,
        "question_ids": [question.question_id for question in questions],
        "document_count": len(docs),
        "model": args.agent_model,
        "generation_model": args.generation_model,
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "results": sorted(
            all_results,
            key=lambda item: (
                -item["doc_hit_rate"],
                item["avg_tool_calls"],
                item["avg_seconds"],
            ),
        ),
    }
    write_json(results_dir / "summary.json", summary)
    write_markdown_summary(results_dir / "summary.md", summary, plans)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PIFS auto-generation research on a 10-doc EnterpriseRAG subset")
    parser.add_argument("--dataset", default="dataset")
    parser.add_argument("--question-ids", default="")
    parser.add_argument("--target-docs", type=int, default=10)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--agent-model", default=os.environ.get("PIFS_AGENT_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--generation-model", default=os.environ.get("PIFS_GENERATION_MODEL", os.environ.get("PIFS_AGENT_MODEL", "gpt-4.1-mini")))
    parser.add_argument("--max-doc-chars", type=int, default=8000)
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--reuse-generated-only", action="store_true")
    parser.add_argument("--skip-agent", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    if not args.run_name:
        mode = "register-only" if args.skip_agent else "agent-eval"
        args.run_name = time.strftime(f"%Y%m%d-%H%M%S-{args.target_docs}docs-{mode}")
    return args


def select_questions(
    questions: list[EnterpriseRAGQuestion],
    *,
    question_ids: str,
    target_docs: int,
) -> list[EnterpriseRAGQuestion]:
    by_id = {question.question_id: question for question in questions}
    explicit_ids = [item.strip() for item in question_ids.split(",") if item.strip()]
    if explicit_ids:
        selected = []
        for question_id in explicit_ids:
            if question_id not in by_id:
                raise ValueError(f"Unknown question id: {question_id}")
            selected.append(by_id[question_id])
        return selected

    selected = []
    seen_doc_ids = set()
    for question in questions:
        if not question.expected_doc_ids:
            continue
        selected.append(question)
        seen_doc_ids.update(question.expected_doc_ids)
        if len(seen_doc_ids) >= target_docs:
            break
    if len(seen_doc_ids) < target_docs:
        raise ValueError(f"Only found {len(seen_doc_ids)} unique expected_doc_ids; target is {target_docs}")
    return selected


def load_selected_documents(source_root: Path, questions: list[EnterpriseRAGQuestion]) -> list[dict[str, Any]]:
    docs = []
    seen = set()
    for question in questions:
        for doc_id in question.expected_doc_ids:
            if doc_id in seen:
                continue
            path = locate_document(source_root, question.source_types, doc_id)
            data = read_json(path)
            source_path = path.relative_to(source_root).as_posix()
            source_type = source_path.split("/", 1)[0]
            title = doc_title(data)
            text = doc_text(data, title)
            metadata = raw_metadata(data, source_type)
            docs.append(
                {
                    "dataset_doc_uuid": doc_id,
                    "path": str(path),
                    "source_path": source_path,
                    "source_type": source_type,
                    "title": title,
                    "text": text,
                    "raw_metadata": metadata,
                }
            )
            seen.add(doc_id)
    return docs


def locate_document(source_root: Path, source_types: list[str], doc_id: str) -> Path:
    search_dirs = [source_root / source_type for source_type in source_types]
    search_dirs.append(source_root)
    checked = set()
    for directory in search_dirs:
        if directory in checked or not directory.exists():
            continue
        checked.add(directory)
        try:
            completed = subprocess.run(
                ["rg", "-l", "-F", doc_id, str(directory)],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            completed = None
        if completed and completed.returncode in {0, 1}:
            for line in completed.stdout.splitlines():
                path = Path(line)
                if path.suffix == ".json" and read_json(path).get("dataset_doc_uuid") == doc_id:
                    return path
    for path in source_root.rglob("*.json"):
        if read_json(path).get("dataset_doc_uuid") == doc_id:
            return path
    raise FileNotFoundError(f"Could not locate EnterpriseRAG document: {doc_id}")


def load_or_generate_profiles(
    docs: list[dict[str, Any]],
    cache_path: Path,
    *,
    model: str,
    max_doc_chars: int,
    reuse_only: bool,
) -> dict[str, dict[str, Any]]:
    cached = read_json(cache_path) if cache_path.exists() else {}
    profiles = dict(cached)
    missing = [doc for doc in docs if doc["dataset_doc_uuid"] not in profiles]
    if missing and reuse_only:
        missing_ids = ", ".join(doc["dataset_doc_uuid"] for doc in missing)
        raise RuntimeError(f"Missing cached doc profiles: {missing_ids}")
    if not missing:
        return profiles

    client = openai_client()
    for index, doc in enumerate(missing, 1):
        print(f"[auto-gen] generating profile {index}/{len(missing)} {doc['dataset_doc_uuid']}", flush=True)
        profile = generate_profile(client, model, doc, max_doc_chars=max_doc_chars)
        profiles[doc["dataset_doc_uuid"]] = normalize_profile(profile)
        write_json(cache_path, profiles)
    return profiles


def load_or_generate_clusters(
    docs: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    cache_path: Path,
    *,
    model: str,
    reuse_only: bool,
) -> dict[str, Any]:
    if cache_path.exists():
        cached = read_json(cache_path)
        if all(doc["dataset_doc_uuid"] in cached.get("doc_cluster", {}) for doc in docs):
            return cached
    if reuse_only:
        raise RuntimeError("Missing cached topic clusters")
    client = openai_client()
    print("[auto-gen] generating topic clusters", flush=True)
    clusters = generate_clusters(client, model, docs, profiles)
    write_json(cache_path, clusters)
    return clusters


def openai_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    kwargs = {"api_key": api_key}
    if os.environ.get("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
    return OpenAI(**kwargs)


def generate_profile(client: OpenAI, model: str, doc: dict[str, Any], *, max_doc_chars: int) -> dict[str, Any]:
    raw_metadata_text = json.dumps(doc["raw_metadata"], ensure_ascii=False, sort_keys=True)
    user_content = {
        "dataset_doc_uuid": doc["dataset_doc_uuid"],
        "source_path": doc["source_path"],
        "source_type": doc["source_type"],
        "title": doc["title"],
        "raw_metadata": raw_metadata_text[:5000],
        "document_text_excerpt": doc["text"][:max_doc_chars],
    }
    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You generate retrieval metadata for a virtual filesystem. "
                    "Use only the provided document content and raw metadata. "
                    "Do not use benchmark questions, gold answers, or outside knowledge. "
                    "Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Extract a retrieval profile with this schema: "
                    "{doc_type:string, primary_topic:string, secondary_topics:string[], "
                    "topic_cluster_hint:string, time_period:string, communication_channel:string, "
                    "product_or_system:string, primary_entity:string, action_or_event:string, "
                    "entities:[{name,type,salience,aliases:string[]}], "
                    "relations:[{subject,relation,object,direction,evidence_terms:string[]}], "
                    "semantic_summary:string, search_terms:string[], folder_hints:string[]}.\n\n"
                    f"Document JSON:\n{json.dumps(user_content, ensure_ascii=False)}"
                ),
            },
        ],
    )
    return json.loads(response.choices[0].message.content or "{}")


def generate_clusters(
    client: OpenAI,
    model: str,
    docs: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    clustering_input = []
    for doc in docs:
        doc_id = doc["dataset_doc_uuid"]
        profile = profiles[doc_id]
        clustering_input.append(
            {
                "dataset_doc_uuid": doc_id,
                "title": doc["title"],
                "source_type": doc["source_type"],
                "doc_type": profile.get("doc_type", ""),
                "primary_topic": profile.get("primary_topic", ""),
                "secondary_topics": profile.get("secondary_topics", []),
                "semantic_summary": profile.get("semantic_summary", ""),
                "search_terms": profile.get("search_terms", []),
            }
        )
    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Cluster documents for a virtual filesystem. Use broad, human-readable "
                    "topic labels that help an agent browse from general topics to specific documents. "
                    "Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Assign every document to exactly one cluster. Return "
                    "{clusters:[{cluster_label,rationale,doc_ids:string[]}], "
                    "doc_cluster:{<dataset_doc_uuid>:<cluster_label>}}.\n\n"
                    f"Documents:\n{json.dumps(clustering_input, ensure_ascii=False)}"
                ),
            },
        ],
    )
    clusters = json.loads(response.choices[0].message.content or "{}")
    doc_cluster = clusters.get("doc_cluster", {})
    for doc in docs:
        doc_id = doc["dataset_doc_uuid"]
        if doc_id not in doc_cluster:
            doc_cluster[doc_id] = profiles[doc_id].get("topic_cluster_hint") or profiles[doc_id].get("primary_topic") or "general"
    clusters["doc_cluster"] = doc_cluster
    return clusters


def normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    profile.setdefault("doc_type", "unknown")
    profile.setdefault("primary_topic", "unknown")
    profile.setdefault("secondary_topics", [])
    profile.setdefault("topic_cluster_hint", profile.get("primary_topic", "unknown"))
    profile.setdefault("time_period", "unknown")
    profile.setdefault("communication_channel", "unknown")
    profile.setdefault("product_or_system", "unknown")
    profile.setdefault("primary_entity", "unknown")
    profile.setdefault("action_or_event", "unknown")
    profile.setdefault("entities", [])
    profile.setdefault("relations", [])
    profile.setdefault("semantic_summary", "")
    profile.setdefault("search_terms", [])
    profile.setdefault("folder_hints", [])
    return profile


def build_folder_plans(
    docs: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    clusters: dict[str, Any],
) -> dict[str, Any]:
    doc_cluster = clusters.get("doc_cluster", {})
    cluster_counts = Counter(doc_cluster.get(doc["dataset_doc_uuid"], "general") for doc in docs)
    ranked_clusters = {
        cluster: index + 1
        for index, (cluster, _count) in enumerate(
            sorted(cluster_counts.items(), key=lambda item: (-item[1], item[0]))
        )
    }
    entropy_fields = [
        "topic_cluster",
        "primary_topic",
        "source_type",
        "doc_type",
        "time_period",
        "product_or_system",
        "communication_channel",
    ]
    field_values = {field: [] for field in entropy_fields}
    for doc in docs:
        profile = profiles[doc["dataset_doc_uuid"]]
        enriched = profile_for_paths(doc, profile, doc_cluster)
        for field in entropy_fields:
            field_values[field].append(enriched.get(field) or "unknown")
    entropy_order = sorted(
        entropy_fields,
        key=lambda field: (-entropy(field_values[field]), field),
    )
    return {
        "topic_count_folder": {
            "cluster_counts": dict(cluster_counts),
            "ranked_clusters": ranked_clusters,
        },
        "entropy_folder": {
            "field_entropy": {field: entropy(field_values[field]) for field in entropy_fields},
            "field_order": entropy_order,
        },
    }


def build_workspace(
    workspace: Path,
    docs: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    clusters: dict[str, Any],
    plans: dict[str, Any],
    *,
    metadata_strategy: str,
    folder_strategy: str,
) -> PageIndexFileSystem:
    filesystem = PageIndexFileSystem(workspace=workspace)
    schema = ENTITY_RELATION_FIXED_SCHEMA if metadata_strategy == "entity_relation_fixed_schema" else DAG_FIXED_SCHEMA
    filesystem._register_metadata_schema({"fields": schema})
    for doc in docs:
        doc_id = doc["dataset_doc_uuid"]
        profile = profiles[doc_id]
        metadata = build_metadata(doc, profile, clusters, metadata_strategy)
        folder_path = build_folder_path(doc, profile, clusters, plans, folder_strategy)
        filesystem.register_file(
            storage_uri=doc["path"],
            source_path=doc["source_path"],
            folder_path=folder_path,
            external_id=doc_id,
            title=doc["title"],
            metadata=metadata,
            content=doc["text"],
            content_type="application/json",
            source_type=doc["source_type"],
        )
    return filesystem


def build_metadata(
    doc: dict[str, Any],
    profile: dict[str, Any],
    clusters: dict[str, Any],
    metadata_strategy: str,
) -> dict[str, str]:
    doc_cluster = clusters.get("doc_cluster", {}).get(doc["dataset_doc_uuid"], "general")
    common = {
        "dataset_doc_uuid": doc["dataset_doc_uuid"],
        "source_type": doc["source_type"],
        "title": doc["title"],
        "doc_type": as_text(profile.get("doc_type")),
        "primary_topic": as_text(profile.get("primary_topic")),
        "topic_cluster": as_text(doc_cluster),
    }
    if metadata_strategy == "entity_relation_fixed_schema":
        return {
            **common,
            "secondary_topics": join_values(profile.get("secondary_topics")),
            "primary_entity": as_text(profile.get("primary_entity")),
            "entities": entity_text(profile.get("entities")),
            "relations": relation_text(profile.get("relations")),
            "action_or_event": as_text(profile.get("action_or_event")),
            "time_period": as_text(profile.get("time_period")),
            "product_or_system": as_text(profile.get("product_or_system")),
            "communication_channel": as_text(profile.get("communication_channel")),
            "semantic_summary": as_text(profile.get("semantic_summary")),
            "search_terms": join_values(profile.get("search_terms")),
        }
    graph = graph_parts(profile)
    return {
        **common,
        "graph_nodes": graph["nodes"],
        "graph_edges": graph["edges"],
        "edge_directions": graph["directions"],
        "root_entities": graph["roots"],
        "leaf_entities": graph["leaves"],
        "relation_path": graph["path"],
        "evidence_terms": graph["evidence_terms"],
        "semantic_summary": as_text(profile.get("semantic_summary")),
    }


def build_folder_path(
    doc: dict[str, Any],
    profile: dict[str, Any],
    clusters: dict[str, Any],
    plans: dict[str, Any],
    folder_strategy: str,
) -> str:
    doc_cluster = clusters.get("doc_cluster", {}).get(doc["dataset_doc_uuid"], "general")
    enriched = profile_for_paths(doc, profile, clusters.get("doc_cluster", {}))
    if folder_strategy == "topic_count_folder":
        rank = plans["topic_count_folder"]["ranked_clusters"].get(doc_cluster, 99)
        return "/".join(
            [
                "",
                "topic_count",
                f"r{rank:02d}_{slug(doc_cluster)}",
                f"source_{slug(doc['source_type'])}",
                f"type_{slug(profile.get('doc_type'))}",
            ]
        )
    segments = ["", "entropy"]
    for field in plans["entropy_folder"]["field_order"][:5]:
        segments.append(f"{slug(field)}={slug(enriched.get(field))}")
    return "/".join(segments)


def profile_for_paths(
    doc: dict[str, Any],
    profile: dict[str, Any],
    doc_cluster: dict[str, str],
) -> dict[str, str]:
    return {
        "topic_cluster": doc_cluster.get(doc["dataset_doc_uuid"], "general"),
        "primary_topic": as_text(profile.get("primary_topic")),
        "source_type": doc["source_type"],
        "doc_type": as_text(profile.get("doc_type")),
        "time_period": as_text(profile.get("time_period")),
        "product_or_system": as_text(profile.get("product_or_system")),
        "communication_channel": as_text(profile.get("communication_channel")),
    }


def evaluate_combo(
    filesystem: PageIndexFileSystem,
    questions: list[EnterpriseRAGQuestion],
    *,
    model: str,
    max_turns: int,
    skip_agent: bool,
    verbose: bool,
) -> list[dict[str, Any]]:
    results = []
    for question in questions:
        started = time.time()
        tool_log: list[dict[str, Any]] = []
        raw_output = ""
        error = None
        parsed = {"answer": "", "document_ids": []}
        if skip_agent:
            error = "skipped"
        else:
            try:
                raw_output = run_pifs_agent(
                    filesystem,
                    auto_agent_prompt(question),
                    model=model,
                    root="/",
                    max_turns=max_turns,
                    verbose=verbose,
                    tool_log=tool_log,
                )
                parsed = parse_agent_json(raw_output)
            except Exception as exc:  # noqa: BLE001 - experiments record failures.
                error = f"{type(exc).__name__}: {exc}"
        document_ids = list(dict.fromkeys(str(item) for item in parsed.get("document_ids", [])))
        expected = set(question.expected_doc_ids)
        results.append(
            {
                "question_id": question.question_id,
                "question": question.question,
                "source_types": question.source_types,
                "expected_doc_ids": question.expected_doc_ids,
                "document_ids": document_ids,
                "doc_hit": bool(expected.intersection(document_ids)),
                "answer": parsed.get("answer", ""),
                "error": error,
                "seconds": round(time.time() - started, 3),
                "tool_calls": len(tool_log),
                "tool_log": tool_log,
                "raw_output": raw_output,
            }
        )
        print(
            json.dumps(
                {
                    "question_id": question.question_id,
                    "doc_hit": results[-1]["doc_hit"],
                    "tool_calls": len(tool_log),
                    "error": error,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return results


def auto_agent_prompt(question: EnterpriseRAGQuestion) -> str:
    return f"""
Question ID: {question.question_id}
Source types: {", ".join(question.source_types)}
Question: {question.question}

You are evaluating an automatically generated PageIndex FileSystem layout.
Source types are hints, not guaranteed folder names. Start from the folder tree
visible in the system context, inspect likely folders with `ls` or `tree`, then
use `grep -R` inside the most relevant folder. Use root `grep -R ... /` only if
folder-scoped search fails. Open full leaf documents with `cat --all` before
answering.

Return final output as a single JSON object only:
{{"answer":"...","document_ids":["dsid_..."]}}

Only include document_ids that appeared in tool output as external_id/document_id.
""".strip()


def summarize_combo(combo: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    hits = sum(1 for result in results if result["doc_hit"])
    return {
        "combo": combo,
        "questions": total,
        "doc_hits": hits,
        "doc_hit_rate": hits / total if total else 0,
        "avg_tool_calls": round(sum(result["tool_calls"] for result in results) / total, 3) if total else 0,
        "avg_seconds": round(sum(result["seconds"] for result in results) / total, 3) if total else 0,
        "errors": [result["question_id"] for result in results if result["error"]],
    }


def write_markdown_summary(path: Path, summary: dict[str, Any], plans: dict[str, Any]) -> None:
    rows = []
    for result in summary["results"]:
        error_text = ", ".join(result["errors"]) if result["errors"] else ""
        rows.append(
            f"| {result['combo']} | {result['doc_hits']}/{result['questions']} | "
            f"{result['doc_hit_rate']:.2f} | {result['avg_tool_calls']:.2f} | "
            f"{result['avg_seconds']:.2f} | {error_text} |"
        )
    best = summary["results"][0] if summary["results"] else None
    lines = [
        "# Auto-Gen FileSystem Research Summary",
        "",
        f"- Run: `{summary['run_name']}`",
        f"- Questions: `{', '.join(summary['question_ids'])}`",
        f"- Documents: `{summary['document_count']}`",
        f"- Agent model: `{summary['model']}`",
        f"- Generation model: `{summary['generation_model']}`",
        f"- Base URL: `{summary.get('base_url') or 'default OpenAI'}`",
        "",
        "## Result Table",
        "",
        "| Combo | Doc Hits | Hit Rate | Avg Tool Calls | Avg Seconds | Errors |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
        *rows,
        "",
        "## Folder Plans",
        "",
        "### Topic Count",
        "",
        "```json",
        json.dumps(plans["topic_count_folder"], ensure_ascii=False, indent=2),
        "```",
        "",
        "### Entropy",
        "",
        "```json",
        json.dumps(plans["entropy_folder"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Current Interpretation",
        "",
    ]
    if best:
        lines.extend(
            [
                f"The current best combo is `{best['combo']}` by hit rate, then tool calls, then runtime.",
                "Treat this as a small-corpus signal only; the next step is to add distractor documents and rerun.",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def question_to_json(question: EnterpriseRAGQuestion) -> dict[str, Any]:
    return {
        "question_id": question.question_id,
        "question": question.question,
        "question_type": question.question_type,
        "source_types": question.source_types,
        "expected_doc_ids": question.expected_doc_ids,
    }


def doc_summary(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_doc_uuid": doc["dataset_doc_uuid"],
        "source_type": doc["source_type"],
        "source_path": doc["source_path"],
        "title": doc["title"],
    }


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def doc_title(data: dict[str, Any]) -> str:
    field_name = data.get("title_field_name") or "title"
    title = data.get(field_name)
    return str(title) if title else str(data.get("dataset_doc_uuid") or "Untitled")


def doc_text(data: dict[str, Any], title: str) -> str:
    contents = [title, ""]
    for field_name in data.get("content_field_names") or []:
        if field_name in data:
            contents.append(stringify(data[field_name]))
    return "\n".join(contents)


def raw_metadata(data: dict[str, Any], source_type: str) -> dict[str, Any]:
    content_fields = set(data.get("content_field_names") or [])
    metadata = {
        key: value
        for key, value in data.items()
        if key not in content_fields and key not in {"content_field_names", "title_field_name"}
    }
    metadata["source_type"] = source_type
    return metadata


def stringify(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else str(value)


def as_text(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, str):
        return value or "unknown"
    return stringify(value)


def join_values(value: Any) -> str:
    if isinstance(value, list):
        return " | ".join(as_text(item) for item in value if as_text(item) != "unknown")
    return as_text(value)


def entity_text(entities: Any) -> str:
    if not isinstance(entities, list):
        return as_text(entities)
    parts = []
    for entity in entities:
        if isinstance(entity, dict):
            name = as_text(entity.get("name"))
            entity_type = as_text(entity.get("type"))
            salience = as_text(entity.get("salience"))
            aliases = join_values(entity.get("aliases", []))
            parts.append(f"{name} ({entity_type}, {salience}) aliases={aliases}")
        else:
            parts.append(as_text(entity))
    return " | ".join(parts)


def relation_text(relations: Any) -> str:
    if not isinstance(relations, list):
        return as_text(relations)
    parts = []
    for relation in relations:
        if isinstance(relation, dict):
            parts.append(
                "{subject} -[{relation}/{direction}]-> {object} evidence={evidence}".format(
                    subject=as_text(relation.get("subject")),
                    relation=as_text(relation.get("relation")),
                    direction=as_text(relation.get("direction")),
                    object=as_text(relation.get("object")),
                    evidence=join_values(relation.get("evidence_terms", [])),
                )
            )
    return " | ".join(parts)


def graph_parts(profile: dict[str, Any]) -> dict[str, str]:
    entities = profile.get("entities", [])
    relations = profile.get("relations", [])
    nodes = []
    if isinstance(entities, list):
        for entity in entities:
            if isinstance(entity, dict):
                nodes.append(f"{as_text(entity.get('type'))}:{as_text(entity.get('name'))}")
    edges = []
    directions = []
    evidence_terms = []
    subjects = []
    objects = []
    if isinstance(relations, list):
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            subject = as_text(relation.get("subject"))
            obj = as_text(relation.get("object"))
            rel = as_text(relation.get("relation"))
            direction = as_text(relation.get("direction"))
            edges.append(f"{subject} -[{rel}]-> {obj}")
            directions.append(f"{subject}->{obj}:{direction}")
            subjects.append(subject)
            objects.append(obj)
            evidence_terms.extend(as_text(item) for item in relation.get("evidence_terms", []))
    roots = sorted(set(subjects) - set(objects)) or sorted(set(subjects))
    leaves = sorted(set(objects) - set(subjects)) or sorted(set(objects))
    return {
        "nodes": " | ".join(nodes),
        "edges": " | ".join(edges),
        "directions": " | ".join(directions),
        "roots": " | ".join(roots),
        "leaves": " | ".join(leaves),
        "path": " ; ".join(edges[:8]),
        "evidence_terms": " | ".join(sorted(set(evidence_terms))),
    }


def entropy(values: list[str]) -> float:
    counts = Counter(value or "unknown" for value in values)
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return round(-sum((count / total) * math.log2(count / total) for count in counts.values()), 4)


def slug(value: Any) -> str:
    text = as_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or "unknown")[:60]


if __name__ == "__main__":
    raise SystemExit(main())
