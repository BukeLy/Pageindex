from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

from examples.Benchmark.enterprise_rag_benchmark.run_enterprise_rag_pifs_agent import (
    PROMPTS_DIR as ENTERPRISE_PROMPTS_DIR,
    read_prompt_file as read_enterprise_prompt,
    run_question as run_enterprise_question,
)
from examples.Benchmark.schema_discovery_research.run_schema_discovery_experiment import (
    DATASETS,
    DatasetBundle,
    load_dataset_bundle,
    read_json,
    summarize_agent_results,
    write_json,
    write_jsonl,
)
from examples.Benchmark.wixqa_benchmark.run_wixqa_pifs_agent import (
    PROMPTS_DIR as WIXQA_PROMPTS_DIR,
    read_prompt_file as read_wixqa_prompt,
    run_question as run_wixqa_question,
)
from examples.Benchmark.wixqa_benchmark.wixqa import write_official_predictions
from pageindex.filesystem import PageIndexFileSystem


LOGGER = logging.getLogger("folder-strategy")
BENCHMARK_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BENCHMARK_DIR / "results"

DEFAULT_FOLDER_STRATEGY = "default_semantic_folders"
FOLDER_STRATEGY_PROFILES = {
    DEFAULT_FOLDER_STRATEGY: "metadata_multimount",
}
EXPERIMENT_STRATEGIES = [
    "physical_baseline",
    "metadata_multimount",
    "metadata_fanout",
    "metadata_entropy_tree",
]
STRATEGIES = [DEFAULT_FOLDER_STRATEGY, *EXPERIMENT_STRATEGIES]
DEFAULT_STRATEGIES = [DEFAULT_FOLDER_STRATEGY]

TEXT_HEAVY_FIELDS = {
    "summary",
    "semantic_summary",
    "retrieval_summary",
    "retrieval_cues",
    "entities",
    "semantic_entities",
    "constraints",
    "semantic_constraints",
}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[folder-strategy] %(message)s")
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BENCHMARK_DIR / ".env")
    args = parse_args()

    run_name = args.run_name or time.strftime("%Y%m%d-%H%M%S-folder-strategy")
    run_dir = RESULTS_DIR / run_name
    if args.reset and run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    selected_datasets = parse_csv(args.datasets, DATASETS)
    selected_strategies = parse_csv(args.strategies, STRATEGIES)
    LOGGER.info(
        "run=%s datasets=%s strategies=%s source_run=%s source_strategy=%s retrieval_mode=%s",
        run_name,
        ",".join(selected_datasets),
        ",".join(selected_strategies),
        args.source_run,
        args.source_strategy,
        args.retrieval_mode,
    )

    summaries = []
    for dataset_name in selected_datasets:
        bundle = load_dataset_bundle(dataset_name, args)
        for strategy in selected_strategies:
            strategy_dir = run_dir / dataset_name / strategy
            strategy_dir.mkdir(parents=True, exist_ok=True)
            summary = run_strategy(bundle, strategy, strategy_dir, args)
            summaries.append(summary)

    global_summary = {
        "run_name": run_name,
        "source_run": args.source_run,
        "source_strategy": args.source_strategy,
        "retrieval_mode": args.retrieval_mode,
        "target_docs": args.target_docs,
        "max_agent_questions": args.max_agent_questions,
        "agent_model": args.agent_model,
        "skip_agent": args.skip_agent,
        "datasets": selected_datasets,
        "strategies": selected_strategies,
        "results": summaries,
    }
    write_json(run_dir / "summary.json", global_summary)
    write_summary_markdown(run_dir / "summary.md", global_summary)
    print(json.dumps(global_summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PIFS folder generation strategy experiments")
    parser.add_argument("--datasets", default=",".join(DATASETS))
    parser.add_argument("--strategies", default=",".join(DEFAULT_STRATEGIES))
    parser.add_argument("--source-run", default="20260519-gen-retrieval-cues-30q")
    parser.add_argument("--source-strategy", default="hybrid_v3_retrieval_cues")
    parser.add_argument("--run-name", default="")
    parser.add_argument("--target-docs", type=int, default=30)
    parser.add_argument("--max-agent-questions", type=int, default=30)
    parser.add_argument("--agent-model", default=os.environ.get("PIFS_AGENT_MODEL", "gpt-5.4-mini"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--retrieval-mode", default="folder", choices=["folder", "hybrid"])
    parser.add_argument("--skip-agent", action="store_true")
    parser.add_argument("--enterprise-dataset", default="examples/Benchmark/enterprise_rag_benchmark/dataset")
    parser.add_argument("--wixqa-kb-jsonl", default="")
    parser.add_argument("--wixqa-questions-jsonl", default="")
    parser.add_argument("--wixqa-split", default="wixqa_expertwritten")
    parser.add_argument("--stream-mode", default=os.environ.get("PIFS_AGENT_STREAM_MODE", "off"))
    parser.add_argument("--reasoning-effort", default=os.environ.get("PIFS_AGENT_REASONING_EFFORT"))
    parser.add_argument("--reasoning-summary", default=os.environ.get("PIFS_AGENT_REASONING_SUMMARY"))
    parser.add_argument("--min-field-coverage", type=float, default=0.25)
    parser.add_argument("--max-field-cardinality-rate", type=float, default=0.65)
    parser.add_argument("--max-field-values", type=int, default=24)
    parser.add_argument("--max-values-per-field", type=int, default=3)
    parser.add_argument("--max-folders-per-doc", type=int, default=8)
    parser.add_argument("--min-files-per-folder", type=int, default=2)
    parser.add_argument("--max-files-per-folder", type=int, default=18)
    parser.add_argument("--max-entropy-depth", type=int, default=3)
    parser.add_argument("--min-semantic-folders", type=int, default=4)
    parser.add_argument("--min-semantic-doc-coverage", type=float, default=0.5)
    parser.add_argument("--max-singleton-folder-rate", type=float, default=0.65)
    parser.add_argument("--max-largest-folder-rate", type=float, default=0.6)
    parser.add_argument("--reset", action="store_true")
    return parser.parse_args(argv)


def run_strategy(
    bundle: DatasetBundle,
    strategy: str,
    strategy_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    source_dir = source_strategy_dir(bundle.name, args)
    source_workspace = source_dir / "workspace"
    if not (source_workspace / "filesystem.sqlite").exists():
        raise FileNotFoundError(f"Missing source workspace: {source_workspace}")
    source_metadata = read_json(source_dir / "metadata.json")
    source_schema = read_json(source_dir / "schema.json").get("fields", {})
    bundle = align_bundle_to_source(bundle, source_metadata)
    strategy_profile = folder_strategy_profile(strategy)

    workspace = strategy_dir / "workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(source_workspace, workspace)

    filesystem = PageIndexFileSystem(workspace=workspace)
    folder_plan = build_folder_plan(bundle, strategy, source_schema, source_metadata, args)
    if strategy == DEFAULT_FOLDER_STRATEGY:
        folder_plan = apply_default_folder_quality_gate(folder_plan, len(bundle.documents), args)
    materialize_folder_plan(filesystem, folder_plan)
    folder_quality = folder_plan_quality(folder_plan, len(bundle.documents))
    if folder_plan.get("quality_gate"):
        folder_quality["quality_gate"] = folder_plan["quality_gate"]
    if folder_plan.get("fallback"):
        folder_quality["fallback"] = folder_plan["fallback"]

    write_json(strategy_dir / "folder_plan.json", folder_plan)
    write_json(strategy_dir / "folder_quality.json", folder_quality)
    LOGGER.info(
        "workspace ready dataset=%s strategy=%s folders=%d memberships=%d path=%s",
        bundle.name,
        strategy,
        len(folder_plan["folders"]),
        len(folder_plan["memberships"]),
        workspace,
    )

    agent_summary = None
    if not args.skip_agent:
        agent_summary = evaluate_workspace(bundle, strategy, filesystem, strategy_dir, args)
    summary = {
        "dataset": bundle.name,
        "strategy": strategy,
        "strategy_profile": strategy_profile,
        "source_run": args.source_run,
        "source_strategy": args.source_strategy,
        "workspace": str(workspace),
        "folder_quality": folder_quality,
        "agent": agent_summary,
    }
    write_json(strategy_dir / "summary.json", summary)
    return summary


def source_strategy_dir(dataset_name: str, args: argparse.Namespace) -> Path:
    path = RESULTS_DIR / args.source_run / dataset_name / args.source_strategy
    if path.exists():
        return path
    dataset_dir = RESULTS_DIR / args.source_run / dataset_name
    matches = [item for item in dataset_dir.iterdir() if item.is_dir()] if dataset_dir.exists() else []
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(f"Could not resolve source strategy dir: {path}")


def align_bundle_to_source(bundle: DatasetBundle, metadata_by_doc: dict[str, Any]) -> DatasetBundle:
    source_doc_ids = set(metadata_by_doc)
    documents = [doc for doc in bundle.documents if doc.doc_id in source_doc_ids]
    if not documents:
        raise RuntimeError(f"No {bundle.name} documents overlap with the source workspace metadata")
    if bundle.name == "enterprise_rag":
        questions = [
            question for question in bundle.questions
            if any(str(doc_id) in source_doc_ids for doc_id in question.expected_doc_ids)
        ]
    elif bundle.name == "wixqa":
        questions = [
            question for question in bundle.questions
            if any(str(article_id) in source_doc_ids for article_id in question.expected_article_ids)
        ]
    else:
        questions = bundle.questions
    LOGGER.info(
        "aligned dataset=%s docs=%d/%d questions=%d/%d",
        bundle.name,
        len(documents),
        len(bundle.documents),
        len(questions),
        len(bundle.questions),
    )
    return DatasetBundle(bundle.name, documents, questions, bundle.manual_schema)


def build_folder_plan(
    bundle: DatasetBundle,
    strategy: str,
    schema: dict[str, Any],
    metadata_by_doc: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    strategy_profile = folder_strategy_profile(strategy)
    if strategy_profile == "physical_baseline":
        return {"folders": [], "memberships": [], "selected_fields": [], "strategy": strategy}

    candidates = candidate_folder_memberships(bundle, schema, metadata_by_doc, args)
    if strategy_profile == "metadata_multimount":
        memberships = candidates
    elif strategy_profile == "metadata_fanout":
        memberships = fanout_filter(candidates, args)
    elif strategy_profile == "metadata_entropy_tree":
        memberships = entropy_tree_memberships(bundle, schema, metadata_by_doc, args)
    else:
        raise ValueError(f"Unknown folder strategy: {strategy}")

    folders: dict[str, dict[str, Any]] = {}
    for membership in memberships:
        folders.setdefault(
            membership["folder_path"],
            {
                "path": membership["folder_path"],
                "kind": "semantic",
                "description": membership["description"],
                "metadata": membership["folder_metadata"],
            },
        )
    selected_fields = sorted({membership["field"] for membership in memberships if membership.get("field")})
    return {
        "strategy": strategy,
        "strategy_profile": strategy_profile,
        "selected_fields": selected_fields,
        "folders": sorted(folders.values(), key=lambda item: item["path"]),
        "memberships": memberships,
    }


def candidate_folder_memberships(
    bundle: DatasetBundle,
    schema: dict[str, Any],
    metadata_by_doc: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    fields = selectable_fields(schema, metadata_by_doc, args)
    memberships = []
    for doc in bundle.documents:
        metadata = metadata_by_doc.get(doc.doc_id, {})
        doc_memberships = []
        for field in fields:
            values = split_metadata_values(metadata.get(field, ""))[: args.max_values_per_field]
            for value in values:
                path = f"/semantic/{slug(field)}/{slug(value)}"
                doc_memberships.append(
                    membership_row(
                        doc.doc_id,
                        path,
                        field=field,
                        value=value,
                        description=f"{field}: {value}",
                        generator="metadata_multimount",
                    )
                )
        memberships.extend(dedupe_memberships(doc_memberships)[: args.max_folders_per_doc])
    return memberships


def selectable_fields(
    schema: dict[str, Any],
    metadata_by_doc: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> list[str]:
    doc_count = max(1, len(metadata_by_doc))
    selected = []
    for field, declaration in schema.items():
        if field in TEXT_HEAVY_FIELDS:
            continue
        values = []
        for metadata in metadata_by_doc.values():
            values.extend(split_metadata_values(metadata.get(field, "")))
        non_empty_docs = sum(1 for metadata in metadata_by_doc.values() if split_metadata_values(metadata.get(field, "")))
        if not values or non_empty_docs / doc_count < args.min_field_coverage:
            continue
        unique_values = {value_key(value) for value in values}
        cardinality_rate = len(unique_values) / max(1, len(values))
        canonical_values = declaration.get("canonical_values") if isinstance(declaration, dict) else None
        has_bounded_values = bool(canonical_values)
        if len(unique_values) < 2:
            continue
        if len(unique_values) > args.max_field_values and not has_bounded_values:
            continue
        if cardinality_rate > args.max_field_cardinality_rate and not has_bounded_values:
            continue
        selected.append(field)
    return selected


def fanout_filter(memberships: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    counts = Counter(item["folder_path"] for item in memberships)
    kept = [
        item
        for item in memberships
        if args.min_files_per_folder <= counts[item["folder_path"]] <= args.max_files_per_folder
    ]
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in kept:
        by_doc[item["doc_id"]].append(item)
    fallback_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in memberships:
        fallback_by_doc[item["doc_id"]].append(item)
    for doc_id, original in fallback_by_doc.items():
        if by_doc.get(doc_id):
            continue
        fallback = sorted(original, key=lambda item: (-counts[item["folder_path"]], item["folder_path"]))[:1]
        kept.extend(fallback)
    return kept


def entropy_tree_memberships(
    bundle: DatasetBundle,
    schema: dict[str, Any],
    metadata_by_doc: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    fields = selectable_fields(schema, metadata_by_doc, args)
    entropy_by_field = {
        field: field_entropy(metadata_by_doc, field)
        for field in fields
    }
    ordered_fields = sorted(fields, key=lambda field: (-entropy_by_field[field], field))
    memberships = []
    for doc in bundle.documents:
        metadata = metadata_by_doc.get(doc.doc_id, {})
        segments = []
        used_fields = []
        for field in ordered_fields:
            values = split_metadata_values(metadata.get(field, ""))
            if not values:
                continue
            value = values[0]
            segments.append(f"{slug(field)}={slug(value)}")
            used_fields.append((field, value))
            if len(segments) >= args.max_entropy_depth:
                break
        if not segments:
            continue
        path = "/semantic/" + "/".join(segments)
        memberships.append(
            membership_row(
                doc.doc_id,
                path,
                field="entropy_path",
                value=" / ".join(f"{field}={value}" for field, value in used_fields),
                description="entropy ordered metadata path",
                generator="metadata_entropy_tree",
                extra={"field_order": [field for field, _ in used_fields]},
            )
        )
    return memberships


def field_entropy(metadata_by_doc: dict[str, dict[str, Any]], field: str) -> float:
    counts = Counter()
    for metadata in metadata_by_doc.values():
        values = split_metadata_values(metadata.get(field, ""))
        if values:
            counts[value_key(values[0])] += 1
    total = sum(counts.values())
    if not total:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def membership_row(
    doc_id: str,
    folder_path: str,
    *,
    field: str,
    value: str,
    description: str,
    generator: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = {
        "generator": generator,
        "field": field,
        "value": value,
    }
    if extra:
        metadata.update(extra)
    return {
        "doc_id": doc_id,
        "folder_path": folder_path,
        "field": field,
        "value": value,
        "description": description,
        "folder_metadata": metadata,
        "membership_metadata": metadata,
    }


def dedupe_memberships(memberships: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = []
    seen = set()
    for item in memberships:
        key = (item["doc_id"], item["folder_path"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def materialize_folder_plan(filesystem: PageIndexFileSystem, folder_plan: dict[str, Any]) -> None:
    doc_to_file_ref = workspace_file_refs(filesystem)
    for index, folder in enumerate(folder_plan["folders"], 1):
        filesystem.create_folder(
            folder["path"],
            kind=folder.get("kind", "semantic"),
            description=folder.get("description", ""),
            metadata=folder.get("metadata") or {},
        )
        if index == 1 or index % 100 == 0 or index == len(folder_plan["folders"]):
            LOGGER.info("created folder %d/%d %s", index, len(folder_plan["folders"]), folder["path"])
    for index, membership in enumerate(folder_plan["memberships"], 1):
        file_ref = doc_to_file_ref.get(membership["doc_id"])
        if not file_ref:
            raise KeyError(f"Missing file_ref for doc_id={membership['doc_id']}")
        filesystem.attach_file_to_folder(
            file_ref,
            membership["folder_path"],
            metadata=membership.get("membership_metadata") or {},
        )
        if index == 1 or index % 100 == 0 or index == len(folder_plan["memberships"]):
            LOGGER.info(
                "attached file %d/%d %s -> %s",
                index,
                len(folder_plan["memberships"]),
                membership["doc_id"],
                membership["folder_path"],
            )


def workspace_file_refs(filesystem: PageIndexFileSystem) -> dict[str, str]:
    with filesystem.store.connect() as conn:
        rows = conn.execute(
            """
            SELECT file_ref, external_id
            FROM files
            WHERE deleted_at IS NULL
            """
        ).fetchall()
    return {str(row["external_id"]): str(row["file_ref"]) for row in rows if row["external_id"]}


def evaluate_workspace(
    bundle: DatasetBundle,
    strategy: str,
    filesystem: PageIndexFileSystem,
    strategy_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    questions = bundle.questions[: args.max_agent_questions] if args.max_agent_questions > 0 else bundle.questions
    results = []
    predictions = []
    if bundle.name == "enterprise_rag":
        system_prompt = read_enterprise_prompt(ENTERPRISE_PROMPTS_DIR / "pifs_agent_system.md")
        question_prompt = read_enterprise_prompt(ENTERPRISE_PROMPTS_DIR / f"pifs_question_{args.retrieval_mode}.md")
        for question in questions:
            result = run_enterprise_question(
                filesystem,
                question,
                model=args.agent_model,
                retrieval_mode=args.retrieval_mode,
                system_prompt=system_prompt,
                question_prompt_template=question_prompt,
                skip_agent=False,
                verbose=False,
                stream_mode=args.stream_mode,
                reasoning_effort=args.reasoning_effort,
                reasoning_summary=args.reasoning_summary,
            )
            results.append(result)
            LOGGER.info("agent enterprise_rag %s %s hit=%s", strategy, result["question_id"], result["doc_hit"])
    elif bundle.name == "wixqa":
        system_prompt = read_wixqa_prompt(WIXQA_PROMPTS_DIR / "pifs_agent_system.md")
        question_prompt = read_wixqa_prompt(WIXQA_PROMPTS_DIR / f"pifs_question_{args.retrieval_mode}.md")
        for question in questions:
            result = run_wixqa_question(
                filesystem,
                question,
                model=args.agent_model,
                retrieval_mode=args.retrieval_mode,
                system_prompt=system_prompt,
                question_prompt_template=question_prompt,
                stream_mode=args.stream_mode,
                reasoning_effort=args.reasoning_effort,
                reasoning_summary=args.reasoning_summary,
            )
            results.append(result)
            predictions.append(
                {
                    "question": question.question,
                    "answer": result.get("answer", ""),
                    "article_ids": result.get("article_ids", []),
                }
            )
            LOGGER.info("agent wixqa %s %s hit=%s", strategy, result["question_id"], result["article_hit"])
    else:
        raise ValueError(f"Unsupported dataset: {bundle.name}")

    results_path = strategy_dir / "agent_results.jsonl"
    write_jsonl(results_path, results)
    if predictions:
        write_official_predictions(strategy_dir / "predictions.jsonl", predictions)
    return summarize_agent_results(bundle.name, results, results_path)


def folder_plan_quality(folder_plan: dict[str, Any], doc_count: int) -> dict[str, Any]:
    memberships = folder_plan.get("memberships", [])
    folder_counts = Counter(item["folder_path"] for item in memberships)
    field_counts = Counter(item.get("field") or "" for item in memberships)
    membership_counts_by_doc = Counter(item["doc_id"] for item in memberships)
    singleton_count = sum(1 for count in folder_counts.values() if count == 1)
    large_count = sum(1 for count in folder_counts.values() if count > max(5, doc_count * 0.2))
    largest_folder_count = max(folder_counts.values(), default=0)
    return {
        "folder_count": len(folder_counts),
        "membership_count": len(memberships),
        "avg_memberships_per_doc": len(memberships) / doc_count if doc_count else 0,
        "docs_with_semantic_folder": len(membership_counts_by_doc),
        "semantic_doc_coverage": len(membership_counts_by_doc) / doc_count if doc_count else 0,
        "singleton_folder_rate": singleton_count / len(folder_counts) if folder_counts else 0,
        "large_folder_rate": large_count / len(folder_counts) if folder_counts else 0,
        "largest_folder_count": largest_folder_count,
        "largest_folder_rate": largest_folder_count / doc_count if doc_count else 0,
        "field_counts": dict(sorted(field_counts.items())),
        "top_folders": [
            {"path": path, "files": count}
            for path, count in folder_counts.most_common(20)
        ],
    }


def apply_default_folder_quality_gate(
    folder_plan: dict[str, Any],
    doc_count: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    quality = folder_plan_quality(folder_plan, doc_count)
    rejection_reasons = []
    if quality["folder_count"] < args.min_semantic_folders:
        rejection_reasons.append(
            {
                "reason": "too_few_semantic_folders",
                "folder_count": quality["folder_count"],
                "min_semantic_folders": args.min_semantic_folders,
            }
        )
    if quality["semantic_doc_coverage"] < args.min_semantic_doc_coverage:
        rejection_reasons.append(
            {
                "reason": "low_semantic_doc_coverage",
                "semantic_doc_coverage": quality["semantic_doc_coverage"],
                "min_semantic_doc_coverage": args.min_semantic_doc_coverage,
            }
        )
    if quality["singleton_folder_rate"] > args.max_singleton_folder_rate:
        rejection_reasons.append(
            {
                "reason": "too_many_singleton_folders",
                "singleton_folder_rate": quality["singleton_folder_rate"],
                "max_singleton_folder_rate": args.max_singleton_folder_rate,
            }
        )
    if quality["largest_folder_rate"] > args.max_largest_folder_rate:
        rejection_reasons.append(
            {
                "reason": "largest_folder_too_dominant",
                "largest_folder_rate": quality["largest_folder_rate"],
                "max_largest_folder_rate": args.max_largest_folder_rate,
            }
        )

    if not rejection_reasons:
        folder_plan["quality_gate"] = {
            "status": "passed",
            "quality": quality,
        }
        return folder_plan

    LOGGER.info("default semantic folders rejected reasons=%s", rejection_reasons)
    return {
        "strategy": folder_plan.get("strategy", DEFAULT_FOLDER_STRATEGY),
        "strategy_profile": folder_plan.get("strategy_profile", "metadata_multimount"),
        "selected_fields": folder_plan.get("selected_fields", []),
        "folders": [],
        "memberships": [],
        "fallback": "physical_baseline",
        "quality_gate": {
            "status": "rejected",
            "reasons": rejection_reasons,
            "rejected_quality": quality,
        },
    }


def folder_strategy_profile(strategy: str) -> str:
    return FOLDER_STRATEGY_PROFILES.get(strategy, strategy)


def split_metadata_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = re.split(r"[,;\n|]+", str(value))
    values = []
    seen = set()
    for item in raw_values:
        text = compact_label(str(item))
        if not text:
            continue
        key = value_key(text)
        if key in seen:
            continue
        seen.add(key)
        values.append(text)
    return values


def compact_label(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = text.strip(" -_.,;:[](){}")
    if len(text) > 96:
        text = text[:96].rsplit(" ", 1)[0].strip() or text[:96].strip()
    return text


def slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(value).lower()).strip("-")
    if not text:
        return "unknown"
    return text[:64].strip("-") or "unknown"


def value_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def parse_csv(value: str, allowed: list[str]) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in items if item not in allowed]
    if unknown:
        raise ValueError(f"Unknown values {unknown}; allowed={allowed}")
    return items or allowed


def write_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Folder Strategy Results",
        "",
        f"- run: `{summary['run_name']}`",
        f"- source_run: `{summary['source_run']}`",
        f"- source_strategy: `{summary['source_strategy']}`",
        f"- retrieval_mode: `{summary['retrieval_mode']}`",
        f"- target_docs: `{summary['target_docs']}`",
        f"- max_agent_questions: `{summary['max_agent_questions']}`",
        "",
        "| dataset | strategy | hit rate | max turns | avg tool calls | cat | grep | folders | memberships | singleton folders | fallback |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in summary["results"]:
        agent = item.get("agent") or {}
        quality = item.get("folder_quality") or {}
        lines.append(
            "| {dataset} | {strategy} | {hit_rate} | {max_turns} | {avg_calls} | {cat} | {grep} | {folders} | {memberships} | {singletons} | {fallback} |".format(
                dataset=item["dataset"],
                strategy=item["strategy"],
                hit_rate=round(agent.get("hit_rate", 0), 4),
                max_turns=agent.get("max_turns", 0),
                avg_calls=round(agent.get("avg_tool_calls", 0), 2),
                cat=agent.get("cat_count", 0),
                grep=agent.get("grep_count", 0),
                folders=quality.get("folder_count", 0),
                memberships=quality.get("membership_count", 0),
                singletons=round(quality.get("singleton_folder_rate", 0), 4),
                fallback=quality.get("fallback", ""),
            )
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
