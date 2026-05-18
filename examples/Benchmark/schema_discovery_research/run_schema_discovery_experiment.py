from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

from examples.Benchmark.enterprise_rag_benchmark.enterprise_rag import (
    EnterpriseRAGBenchmark,
    EnterpriseRAGQuestion,
    load_questions as load_enterprise_questions,
)
from examples.Benchmark.enterprise_rag_benchmark.run_enterprise_rag_pifs_agent import (
    PROMPTS_DIR as ENTERPRISE_PROMPTS_DIR,
    read_prompt_file as read_enterprise_prompt,
    run_question as run_enterprise_question,
)
from examples.Benchmark.wixqa_benchmark.run_wixqa_pifs_agent import (
    PROMPTS_DIR as WIXQA_PROMPTS_DIR,
    read_prompt_file as read_wixqa_prompt,
    run_question as run_wixqa_question,
)
from examples.Benchmark.wixqa_benchmark.wixqa import (
    DATASET_NAME as WIXQA_DATASET_NAME,
    WixQAArticle,
    WixQAQuestion,
    article_spec,
    load_articles as load_wixqa_articles,
    load_questions as load_wixqa_questions,
    write_official_predictions,
)
from pageindex.filesystem import PageIndexFileSystem


LOGGER = logging.getLogger("schema-discovery")
BENCHMARK_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BENCHMARK_DIR / "prompts"
STRATEGIES = ["manual_baseline", "base_only", "freeform_workspace", "hybrid_workspace"]
DATASETS = ["enterprise_rag", "wixqa"]

BASE_SCHEMA: dict[str, dict[str, str]] = {
    "doc_type": {"type": "string", "description": "Document type such as email, pull request, KB article, report, or invoice."},
    "domain": {"type": "string", "description": "Top-level business or knowledge domain covered by the document."},
    "topic": {"type": "string", "description": "Main subject discussed by the document."},
    "intent": {"type": "string", "description": "User goal, task, or question the document can help answer."},
    "entities": {"type": "string", "description": "Important objects, people, systems, products, APIs, fields, or organizations."},
    "constraints": {"type": "string", "description": "Conditions, rules, limits, numbers, defaults, date ranges, or requirements."},
    "summary": {"type": "string", "description": "One-sentence retrieval summary grounded in the document text."},
}

ENTERPRISE_MANUAL_SCHEMA = {
    "dataset_doc_uuid": {"type": "string", "description": "EnterpriseRAG document id"},
    "source_type": {"type": "string", "description": "Top-level EnterpriseRAG source system"},
    "title": {"type": "string", "description": "Document title"},
    "repo": {"type": "string", "description": "GitHub repository name"},
    "state": {"type": "string", "description": "GitHub pull request state"},
}

WIXQA_MANUAL_SCHEMA = {
    "article_id": {"type": "string", "description": "WixQA KB article id"},
    "article_type": {"type": "string", "description": "WixQA article type"},
    "url": {"type": "string", "description": "Wix Help Center article URL"},
    "url_path": {"type": "string", "description": "Normalized URL path"},
    "category": {"type": "string", "description": "URL-derived help-center category"},
    "subcategory": {"type": "string", "description": "URL-derived help-center subcategory"},
    "source_dataset": {"type": "string", "description": "Dataset name"},
}

FIELD_SYNONYMS = {
    "document_type": "doc_type",
    "file_type": "doc_type",
    "content_type": "doc_type",
    "knowledge_domain": "domain",
    "business_domain": "domain",
    "product_area": "domain",
    "primary_topic": "topic",
    "main_topic": "topic",
    "subject": "topic",
    "task": "intent",
    "task_or_intent": "intent",
    "user_goal": "intent",
    "goal": "intent",
    "action": "intent",
    "actions": "intent",
    "problem": "intent",
    "problem_or_symptom": "intent",
    "symptom": "intent",
    "named_entities": "entities",
    "key_entities": "entities",
    "systems": "entities",
    "products": "entities",
    "rules": "constraints",
    "requirements": "constraints",
    "limits": "constraints",
    "measurements": "constraints",
    "semantic_summary": "summary",
    "retrieval_summary": "summary",
}

DISALLOWED_FIELD_NAMES = {
    "id",
    "doc_id",
    "document_id",
    "dataset_doc_uuid",
    "article_id",
    "file_ref",
    "external_id",
    "question_id",
    "benchmark_id",
    "expected_doc_ids",
    "expected_article_ids",
    "gold_answer",
    "answer",
    "source_path",
    "storage_uri",
    "file_path",
    "path",
    "url",
    "url_path",
    "source_dataset",
}
DISALLOWED_FIELD_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r"(^|_)gold(_|$)",
        r"(^|_)expected(_|$)",
        r"(^|_)benchmark(_|$)",
        r"(^|_)dataset(_|$)",
        r"(^|_)uuid(_|$)",
        r"(^|_)uri(_|$)",
        r"(^|_)url(_|$)",
        r"(^|_)path(_|$)",
        r"(^|_)filename(_|$)",
    ]
]


@dataclass(frozen=True)
class ResearchDocument:
    doc_id: str
    title: str
    text: str
    storage_uri: str
    source_path: str
    folder_path: str
    content_type: str
    source_type: str
    manual_metadata: dict[str, Any]


@dataclass(frozen=True)
class DatasetBundle:
    name: str
    documents: list[ResearchDocument]
    questions: list[Any]
    manual_schema: dict[str, dict[str, str]]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[schema-discovery] %(message)s")
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BENCHMARK_DIR / ".env")
    args = parse_args()

    run_name = args.run_name or time.strftime("%Y%m%d-%H%M%S-schema-discovery")
    run_dir = BENCHMARK_DIR / "results" / run_name
    if args.reset and run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    selected_datasets = parse_csv(args.datasets, DATASETS)
    selected_strategies = parse_csv(args.strategies, STRATEGIES)
    LOGGER.info("run=%s datasets=%s strategies=%s", run_name, ",".join(selected_datasets), ",".join(selected_strategies))

    summaries = []
    for dataset_name in selected_datasets:
        bundle = load_dataset_bundle(dataset_name, args)
        LOGGER.info("dataset=%s docs=%d questions=%d", dataset_name, len(bundle.documents), len(bundle.questions))
        for strategy in selected_strategies:
            strategy_dir = run_dir / dataset_name / strategy
            strategy_dir.mkdir(parents=True, exist_ok=True)
            summary = run_strategy(bundle, strategy, strategy_dir, args)
            summaries.append(summary)

    global_summary = {
        "run_name": run_name,
        "target_docs": args.target_docs,
        "schema_sample_size": args.schema_sample_size,
        "generation_model": args.generation_model,
        "agent_model": args.agent_model,
        "datasets": selected_datasets,
        "strategies": selected_strategies,
        "results": summaries,
        "cross_dataset_drift": cross_dataset_drift(summaries),
    }
    write_json(run_dir / "summary.json", global_summary)
    write_summary_markdown(run_dir / "summary.md", global_summary)
    print(json.dumps(global_summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run cross-dataset PIFS schema discovery experiments")
    parser.add_argument("--datasets", default=",".join(DATASETS))
    parser.add_argument("--strategies", default=",".join(STRATEGIES))
    parser.add_argument("--target-docs", type=int, default=100)
    parser.add_argument("--schema-sample-size", type=int, default=30)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--generation-model", default=os.environ.get("PIFS_GENERATION_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--agent-model", default=os.environ.get("PIFS_AGENT_MODEL", "gpt-5.4-mini"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--metadata-workers", type=int, default=1, help="Reserved for future parallel generation; currently sequential for reproducibility.")
    parser.add_argument("--max-doc-chars", type=int, default=12000)
    parser.add_argument("--max-agent-questions", type=int, default=0)
    parser.add_argument("--skip-agent", action="store_true")
    parser.add_argument("--reuse-generated-only", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--enterprise-dataset", default="examples/Benchmark/enterprise_rag_benchmark/dataset")
    parser.add_argument("--wixqa-kb-jsonl", default="")
    parser.add_argument("--wixqa-questions-jsonl", default="")
    parser.add_argument("--wixqa-split", default="wixqa_expertwritten")
    parser.add_argument("--stream-mode", default=os.environ.get("PIFS_AGENT_STREAM_MODE", "off"))
    parser.add_argument("--reasoning-effort", default=os.environ.get("PIFS_AGENT_REASONING_EFFORT"))
    parser.add_argument("--reasoning-summary", default=os.environ.get("PIFS_AGENT_REASONING_SUMMARY"))
    return parser.parse_args(argv)


def run_strategy(
    bundle: DatasetBundle,
    strategy: str,
    strategy_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy}")
    workspace = strategy_dir / "workspace"
    if workspace.exists() and args.reset:
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    schema_result = build_schema(bundle, strategy, strategy_dir, args)
    schema = schema_result["schema"]
    metadata_by_doc = build_metadata(bundle, strategy, schema, strategy_dir, args)
    filesystem = materialize_workspace(bundle, workspace, schema, metadata_by_doc)
    LOGGER.info("workspace ready dataset=%s strategy=%s path=%s", bundle.name, strategy, workspace)

    agent_summary = None
    if not args.skip_agent:
        agent_summary = evaluate_workspace(bundle, strategy, filesystem, strategy_dir, args)

    quality = schema_quality(schema, metadata_by_doc, schema_result.get("audit", {}), len(bundle.documents))
    summary = {
        "dataset": bundle.name,
        "strategy": strategy,
        "workspace": str(workspace),
        "schema_path": str(strategy_dir / "schema.json"),
        "metadata_path": str(strategy_dir / "metadata.json"),
        "schema_quality": quality,
        "agent": agent_summary,
    }
    write_json(strategy_dir / "summary.json", summary)
    return summary


def build_schema(
    bundle: DatasetBundle,
    strategy: str,
    strategy_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    schema_path = strategy_dir / "schema.json"
    audit_path = strategy_dir / "schema_audit.json"
    prompt_path = strategy_dir / "schema_prompt.md"
    if schema_path.exists() and audit_path.exists():
        return {"schema": read_json(schema_path)["fields"], "audit": read_json(audit_path)}
    if args.reuse_generated_only:
        raise RuntimeError(f"Missing generated schema: {schema_path}")

    if strategy == "manual_baseline":
        schema = bundle.manual_schema
        audit = {"strategy": strategy, "kept": list(schema), "rejected": [], "merged": []}
    elif strategy == "base_only":
        schema = BASE_SCHEMA
        audit = {"strategy": strategy, "kept": list(schema), "rejected": [], "merged": []}
    else:
        sample = sample_documents(bundle.documents, args.schema_sample_size, args.max_doc_chars)
        prompt = render_schema_prompt(
            strategy=strategy,
            sample=sample,
            base_schema=BASE_SCHEMA if strategy == "hybrid_workspace" else {},
            max_fields=6 if strategy == "hybrid_workspace" else 16,
        )
        prompt_path.write_text(prompt, encoding="utf-8")
        draft = call_json_model(args.generation_model, system="", user=prompt, base_url=args.base_url)
        write_json(strategy_dir / "schema_draft.json", draft)
        normalized = normalize_schema_draft(
            draft,
            strategy=strategy,
            base_schema=BASE_SCHEMA if strategy == "hybrid_workspace" else {},
            max_extension_fields=6 if strategy == "hybrid_workspace" else 16,
        )
        schema = normalized["schema"]
        audit = normalized["audit"]

    write_json(schema_path, {"fields": schema})
    write_json(audit_path, audit)
    if not prompt_path.exists():
        prompt_path.write_text(schema_prompt_markdown(strategy, schema, audit), encoding="utf-8")
    return {"schema": schema, "audit": audit}


def build_metadata(
    bundle: DatasetBundle,
    strategy: str,
    schema: dict[str, dict[str, str]],
    strategy_dir: Path,
    args: argparse.Namespace,
) -> dict[str, dict[str, Any]]:
    metadata_path = strategy_dir / "metadata.json"
    existing = read_json(metadata_path) if metadata_path.exists() else {}
    if not isinstance(existing, dict):
        existing = {}
    if metadata_path.exists():
        missing = [doc for doc in bundle.documents if doc.doc_id not in existing]
        if not missing:
            return existing
        if args.reuse_generated_only:
            raise RuntimeError(f"Generated metadata is incomplete: {metadata_path}")
        LOGGER.info(
            "metadata cache incomplete dataset=%s strategy=%s complete=%d missing=%d",
            bundle.name,
            strategy,
            len(existing),
            len(missing),
        )
    if args.reuse_generated_only:
        raise RuntimeError(f"Missing generated metadata: {metadata_path}")

    if strategy == "manual_baseline":
        metadata = {
            doc.doc_id: normalize_metadata_for_schema(schema, doc.manual_metadata)
            for doc in bundle.documents
        }
        write_json(metadata_path, metadata)
        return metadata

    result: dict[str, dict[str, Any]] = dict(existing)
    missing_docs = [doc for doc in bundle.documents if doc.doc_id not in result]
    completed = len(bundle.documents) - len(missing_docs)
    for index, doc in enumerate(missing_docs, completed + 1):
        generated = generate_metadata_for_doc(doc, schema, args)
        result[doc.doc_id] = normalize_metadata_for_schema(schema, generated)
        write_json(metadata_path, result)
        LOGGER.info("metadata %s %s %d/%d", bundle.name, strategy, index, len(bundle.documents))
    return result


def generate_metadata_for_doc(
    doc: ResearchDocument,
    schema: dict[str, dict[str, str]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    prompt_template = (PROMPTS_DIR / "metadata_generation.md").read_text(encoding="utf-8")
    prompt = prompt_template.format(
        schema_json=json.dumps({"fields": schema}, ensure_ascii=False, indent=2),
        document_text=truncate_doc_text(doc, args.max_doc_chars),
    )
    return call_json_model(args.generation_model, system="", user=prompt, base_url=args.base_url)


def materialize_workspace(
    bundle: DatasetBundle,
    workspace: Path,
    schema: dict[str, dict[str, str]],
    metadata_by_doc: dict[str, dict[str, Any]],
) -> PageIndexFileSystem:
    filesystem = PageIndexFileSystem(workspace=workspace)
    filesystem._register_metadata_schema({"fields": schema})
    for doc in bundle.documents:
        filesystem.register_file(
            storage_uri=doc.storage_uri,
            source_path=doc.source_path,
            folder_path=doc.folder_path,
            metadata=metadata_by_doc.get(doc.doc_id, {}),
            external_id=doc.doc_id,
            title=doc.title,
            content=doc.text,
            content_type=doc.content_type,
            source_type=doc.source_type,
        )
    return filesystem


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
        question_prompt = read_enterprise_prompt(ENTERPRISE_PROMPTS_DIR / "pifs_question_metadata.md")
        for question in questions:
            result = run_enterprise_question(
                filesystem,
                question,
                model=args.agent_model,
                retrieval_mode="metadata",
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
        question_prompt = read_wixqa_prompt(WIXQA_PROMPTS_DIR / "pifs_question_metadata.md")
        for question in questions:
            result = run_wixqa_question(
                filesystem,
                question,
                model=args.agent_model,
                retrieval_mode="metadata",
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


def summarize_agent_results(dataset: str, results: list[dict[str, Any]], results_path: Path) -> dict[str, Any]:
    hit_key = "doc_hit" if dataset == "enterprise_rag" else "article_hit"
    hits = sum(1 for result in results if result.get(hit_key))
    calls = [
        event
        for result in results
        for event in result.get("agent_log", [])
        if event.get("kind") == "tool_call"
    ]
    commands = [str(event.get("command") or "") for event in calls]
    errors = [result.get("error") for result in results if result.get("error")]
    return {
        "questions": len(results),
        "hits": hits,
        "hit_rate": hits / len(results) if results else 0,
        "tool_calls": len(calls),
        "avg_tool_calls": len(calls) / len(results) if results else 0,
        "cat_count": sum(1 for command in commands if command.strip().startswith("cat ")),
        "find_count": sum(1 for command in commands if command.strip().startswith("find ")),
        "grep_count": sum(1 for command in commands if command.strip().startswith("grep") or " grep" in command),
        "max_turns": sum(1 for error in errors if "MaxTurnsExceeded" in str(error)),
        "errors": len(errors),
        "first_relevant_doc_turn": None,
        "results_path": str(results_path),
    }


def load_dataset_bundle(dataset_name: str, args: argparse.Namespace) -> DatasetBundle:
    if dataset_name == "enterprise_rag":
        return load_enterprise_bundle(args)
    if dataset_name == "wixqa":
        return load_wixqa_bundle(args)
    raise ValueError(f"Unsupported dataset: {dataset_name}")


def load_enterprise_bundle(args: argparse.Namespace) -> DatasetBundle:
    dataset_root = (REPO_ROOT / args.enterprise_dataset).resolve()
    source_root = dataset_root / "generated_data" / "sources"
    questions = select_questions_until_target_docs(
        load_enterprise_questions(dataset_root / "questions.jsonl"),
        target_docs=args.target_docs,
        expected_attr="expected_doc_ids",
    )
    target_ids = ordered_expected_ids(questions, "expected_doc_ids", args.target_docs)
    paths_by_id = enterprise_paths_by_doc_id(source_root)
    documents = []
    benchmark = EnterpriseRAGBenchmark(filesystem=None)  # type: ignore[arg-type]
    for doc_id in target_ids:
        path = paths_by_id.get(doc_id)
        if path is None:
            raise FileNotFoundError(f"EnterpriseRAG expected doc not found: {doc_id}")
        data = benchmark._read_json(path)
        spec = benchmark._document_spec(source_root, path, data)
        documents.append(
            ResearchDocument(
                doc_id=str(spec["external_id"]),
                title=str(spec["title"]),
                text=str(spec["content"]),
                storage_uri=str(spec["storage_uri"]),
                source_path=str(spec["source_path"]),
                folder_path=str(spec["folder_path"]),
                content_type=str(spec["content_type"]),
                source_type=str(spec["source_type"] or ""),
                manual_metadata=dict(spec["metadata"]),
            )
        )
    return DatasetBundle("enterprise_rag", documents, questions, ENTERPRISE_MANUAL_SCHEMA)


def load_wixqa_bundle(args: argparse.Namespace) -> DatasetBundle:
    articles = load_wixqa_articles(args.wixqa_kb_jsonl or None)
    questions = select_questions_until_target_docs(
        load_wixqa_questions(args.wixqa_questions_jsonl or None, split=args.wixqa_split),
        target_docs=args.target_docs,
        expected_attr="expected_article_ids",
    )
    target_ids = ordered_expected_ids(questions, "expected_article_ids", args.target_docs)
    articles_by_id = {article.article_id: article for article in articles}
    documents = []
    for article_id in target_ids:
        article = articles_by_id.get(article_id)
        if article is None:
            raise FileNotFoundError(f"WixQA expected article not found: {article_id}")
        spec = article_spec(article)
        documents.append(
            ResearchDocument(
                doc_id=str(spec["external_id"]),
                title=str(spec["title"]),
                text=str(spec["content"]),
                storage_uri=str(spec["storage_uri"]),
                source_path=str(spec["source_path"]),
                folder_path=str(spec["folder_path"]),
                content_type=str(spec["content_type"]),
                source_type=str(spec["source_type"] or WIXQA_DATASET_NAME),
                manual_metadata=dict(spec["metadata"]),
            )
        )
    return DatasetBundle("wixqa", documents, questions, WIXQA_MANUAL_SCHEMA)


def select_questions_until_target_docs(
    questions: list[Any],
    *,
    target_docs: int,
    expected_attr: str,
) -> list[Any]:
    if target_docs <= 0:
        return questions
    selected = []
    seen = set()
    for question in questions:
        ids = [str(item) for item in getattr(question, expected_attr)]
        if not ids:
            continue
        selected.append(question)
        seen.update(ids)
        if len(seen) >= target_docs:
            break
    return selected


def ordered_expected_ids(questions: Iterable[Any], expected_attr: str, limit: int) -> list[str]:
    ordered = []
    seen = set()
    for question in questions:
        for item in getattr(question, expected_attr):
            doc_id = str(item)
            if doc_id not in seen:
                seen.add(doc_id)
                ordered.append(doc_id)
            if limit > 0 and len(ordered) >= limit:
                return ordered
    return ordered


def enterprise_paths_by_doc_id(source_root: Path) -> dict[str, Path]:
    paths = {}
    for path in sorted(source_root.rglob("*.json")):
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        doc_id = data.get("dataset_doc_uuid")
        if doc_id:
            paths[str(doc_id)] = path
    return paths


def normalize_schema_draft(
    draft: dict[str, Any],
    *,
    strategy: str,
    base_schema: dict[str, dict[str, str]],
    max_extension_fields: int,
) -> dict[str, Any]:
    audit: dict[str, Any] = {"strategy": strategy, "kept": [], "rejected": [], "merged": []}
    schema = dict(base_schema)
    raw_fields = draft.get("fields", draft)
    items = schema_items(raw_fields)
    for raw_name, declaration in items:
        normalized_name = normalize_field_name(raw_name)
        canonical_name = FIELD_SYNONYMS.get(normalized_name, normalized_name)
        if canonical_name in base_schema:
            audit["merged"].append({"from": raw_name, "to": canonical_name})
            continue
        if is_disallowed_field(canonical_name):
            audit["rejected"].append({"name": raw_name, "normalized": canonical_name, "reason": "disallowed"})
            continue
        if canonical_name in schema:
            audit["merged"].append({"from": raw_name, "to": canonical_name})
            continue
        if len([name for name in schema if name not in base_schema]) >= max_extension_fields:
            audit["rejected"].append({"name": raw_name, "normalized": canonical_name, "reason": "max_fields"})
            continue
        schema[canonical_name] = {
            "type": "string",
            "description": field_description(declaration),
        }
        audit["kept"].append(canonical_name)
    if strategy == "freeform_workspace" and not schema:
        schema = dict(BASE_SCHEMA)
        audit["fallback"] = "base_schema"
    return {"schema": schema, "audit": audit}


def schema_items(raw_fields: Any) -> list[tuple[str, Any]]:
    if isinstance(raw_fields, dict):
        return [(str(name), declaration) for name, declaration in raw_fields.items()]
    if isinstance(raw_fields, list):
        items = []
        for item in raw_fields:
            if isinstance(item, dict) and item.get("name"):
                items.append((str(item["name"]), item))
        return items
    return []


def field_description(declaration: Any) -> str:
    if isinstance(declaration, dict):
        for key in ["description", "why_queryable", "source_evidence"]:
            value = declaration.get(key)
            if value:
                return str(value)
    return "Workspace-specific retrieval field."


def normalize_field_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").lower()
    if not text:
        return "field"
    if text[0].isdigit():
        text = f"field_{text}"
    return text


def is_disallowed_field(field: str) -> bool:
    if field in DISALLOWED_FIELD_NAMES:
        return True
    return any(pattern.search(field) for pattern in DISALLOWED_FIELD_PATTERNS)


def normalize_metadata_for_schema(
    schema: dict[str, dict[str, str]],
    metadata: dict[str, Any],
) -> dict[str, str]:
    normalized = {}
    for field in schema:
        normalized[field] = stringify_metadata_value(metadata.get(field, ""))
    return normalized


def stringify_metadata_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(stringify_metadata_value(item) for item in value if stringify_metadata_value(item))
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            item_text = stringify_metadata_value(item)
            if item_text:
                parts.append(f"{key}: {item_text}")
        return "; ".join(parts)
    return str(value).strip()


def schema_quality(
    schema: dict[str, dict[str, str]],
    metadata_by_doc: dict[str, dict[str, Any]],
    audit: dict[str, Any],
    doc_count: int,
) -> dict[str, Any]:
    empty_rates = {}
    high_cardinality = {}
    for field in schema:
        values = [stringify_metadata_value(metadata.get(field, "")) for metadata in metadata_by_doc.values()]
        non_empty = [value for value in values if value]
        empty_rates[field] = 1 - (len(non_empty) / doc_count if doc_count else 0)
        unique_count = len(set(non_empty))
        high_cardinality[field] = unique_count / len(non_empty) if non_empty else 0
    base_overlap = len(set(schema).intersection(BASE_SCHEMA)) / len(BASE_SCHEMA)
    return {
        "field_count": len(schema),
        "empty_rate": empty_rates,
        "avg_empty_rate": sum(empty_rates.values()) / len(empty_rates) if empty_rates else 0,
        "high_cardinality_rate": high_cardinality,
        "duplicate_field_rate": len(audit.get("merged", [])) / max(1, len(schema) + len(audit.get("merged", []))),
        "queryable_field_rate": sum(1 for field in schema.values() if field.get("description")) / max(1, len(schema)),
        "base_overlap": base_overlap,
        "rejected_fields": audit.get("rejected", []),
    }


def cross_dataset_drift(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    by_strategy: dict[str, dict[str, set[str]]] = {}
    for summary in summaries:
        schema_path = Path(summary["schema_path"])
        schema = read_json(schema_path).get("fields", {})
        by_strategy.setdefault(summary["strategy"], {})[summary["dataset"]] = set(schema)
    drift = {}
    for strategy, dataset_fields in by_strategy.items():
        if len(dataset_fields) < 2:
            continue
        field_sets = list(dataset_fields.values())
        union = set.union(*field_sets) if field_sets else set()
        intersection = set.intersection(*field_sets) if field_sets else set()
        extensions = [fields - set(BASE_SCHEMA) for fields in field_sets]
        extension_union = set.union(*extensions) if extensions else set()
        extension_intersection = set.intersection(*extensions) if extensions else set()
        drift[strategy] = {
            "field_jaccard": len(intersection) / len(union) if union else 1,
            "extension_jaccard": (
                len(extension_intersection) / len(extension_union) if extension_union else 1
            ),
            "fields_by_dataset": {dataset: sorted(fields) for dataset, fields in dataset_fields.items()},
        }
    return drift


def render_schema_prompt(
    *,
    strategy: str,
    sample: list[dict[str, str]],
    base_schema: dict[str, dict[str, str]],
    max_fields: int,
) -> str:
    template = (PROMPTS_DIR / "schema_discovery.md").read_text(encoding="utf-8")
    return template.format(
        strategy=strategy,
        base_schema_json=json.dumps({"fields": base_schema}, ensure_ascii=False, indent=2),
        max_fields=max_fields,
        sample_json=json.dumps(sample, ensure_ascii=False, indent=2),
    )


def schema_prompt_markdown(strategy: str, schema: dict[str, Any], audit: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# Schema Prompt Record: {strategy}",
            "",
            "No LLM schema discovery prompt was needed for this strategy.",
            "",
            "## Frozen Schema",
            "```json",
            json.dumps({"fields": schema}, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Audit",
            "```json",
            json.dumps(audit, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )


def sample_documents(
    documents: list[ResearchDocument],
    sample_size: int,
    max_doc_chars: int,
) -> list[dict[str, str]]:
    selected = documents[:sample_size] if sample_size > 0 else documents
    return [
        {
            "title": doc.title,
            "text": truncate_doc_text(doc, max_doc_chars),
        }
        for doc in selected
    ]


def truncate_doc_text(doc: ResearchDocument, max_doc_chars: int) -> str:
    text = doc.text[:max_doc_chars] if max_doc_chars > 0 else doc.text
    return f"Title: {doc.title}\n\n{text}".strip()


def call_json_model(model: str, *, system: str, user: str, base_url: str | None = None) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError("Schema discovery generation requires the optional openai package.") from exc
    kwargs = {}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=messages,
            )
            break
        except Exception as exc:  # noqa: BLE001 - benchmark generation should survive transient API failures.
            last_error = exc
            if attempt == 5:
                raise
            sleep_seconds = min(30, 2**attempt)
            LOGGER.warning(
                "json model call failed attempt=%d retry_in=%ss error=%s",
                attempt,
                sleep_seconds,
                f"{type(exc).__name__}: {exc}",
            )
            time.sleep(sleep_seconds)
    else:  # pragma: no cover - loop always raises or breaks.
        raise RuntimeError("unreachable json model retry state") from last_error
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object")
    return parsed


def parse_csv(value: str, allowed: list[str]) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in items if item not in allowed]
    if unknown:
        raise ValueError(f"Unknown values {unknown}; allowed={allowed}")
    return items or allowed


def write_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Schema Discovery Results",
        "",
        f"- run: `{summary['run_name']}`",
        f"- target_docs: `{summary['target_docs']}`",
        f"- generation_model: `{summary['generation_model']}`",
        f"- agent_model: `{summary['agent_model']}`",
        "",
        "| dataset | strategy | hit rate | max turns | avg tool calls | fields | avg empty rate |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in summary["results"]:
        agent = item.get("agent") or {}
        quality = item.get("schema_quality") or {}
        lines.append(
            "| {dataset} | {strategy} | {hit_rate} | {max_turns} | {avg_calls} | {fields} | {empty} |".format(
                dataset=item["dataset"],
                strategy=item["strategy"],
                hit_rate=round(agent.get("hit_rate", 0), 4) if agent else "skipped",
                max_turns=agent.get("max_turns", "skipped") if agent else "skipped",
                avg_calls=round(agent.get("avg_tool_calls", 0), 2) if agent else "skipped",
                fields=quality.get("field_count", 0),
                empty=round(quality.get("avg_empty_rate", 0), 4),
            )
        )
    lines.extend(["", "## Cross Dataset Drift", "", "```json", json.dumps(summary["cross_dataset_drift"], ensure_ascii=False, indent=2), "```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
