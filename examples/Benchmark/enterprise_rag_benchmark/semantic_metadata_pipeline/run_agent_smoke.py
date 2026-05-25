from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is optional for smoke import.
    load_dotenv = None


PIPELINE_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = PIPELINE_DIR.parent
REPO_ROOT = PIPELINE_DIR.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.Benchmark.enterprise_rag_benchmark.run_enterprise_rag_pifs_agent import (
    PIFSAgentAnswer,
    agent_prompt,
    parse_agent_json,
    read_prompt_file,
)
from examples.Benchmark.enterprise_rag_benchmark.semantic_metadata_pipeline.pipeline_common import (
    DEFAULT_DATASET_DIR,
    DatasetDocument,
    canonical_field_name,
    load_dataset_documents,
    read_json,
    read_jsonl,
    slug,
    write_json,
)
from pageindex.filesystem import HybridProjectionSearchBackend, PageIndexFileSystem
from pageindex.filesystem.agent import (
    AGENT_STREAM_MODE_CHOICES,
    REASONING_EFFORT_CHOICES,
    REASONING_SUMMARY_CHOICES,
    run_pifs_agent,
)


DEFAULT_SYSTEM_PROMPT = PIPELINE_DIR / "prompts" / "pifs_agent_system.md"
DEFAULT_QUESTION_PROMPT = PIPELINE_DIR / "prompts" / "pifs_question_pipeline_smoke.md"
DEFAULT_QUESTION_IDS = "qst_0007"


def main() -> int:
    if load_dotenv is not None:
        load_dotenv(REPO_ROOT / ".env")
        load_dotenv(BENCHMARK_DIR / ".env")
    args = parse_args()
    run_dir = resolve_path(args.run_dir)
    dataset_dir = resolve_path(args.dataset)
    output_dir = resolve_output_dir(run_dir, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    materialized = materialize_workspace(
        run_dir=run_dir,
        dataset_dir=dataset_dir,
        workspace=resolve_workspace(run_dir, args.workspace),
        reset=not args.reuse_workspace,
    )
    write_json(output_dir / "workspace_manifest.json", materialized)

    if args.materialize_only:
        print(json.dumps(materialized, ensure_ascii=False, indent=2), flush=True)
        return 0

    backend = HybridProjectionSearchBackend.from_provider(
        run_dir / "projection_indexes",
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        embedding_dimensions=args.embedding_dimensions,
        embedding_timeout=args.embedding_timeout,
        per_channel_limit=args.per_channel_limit,
        fetch_multiplier=args.fetch_multiplier,
    )
    filesystem = PageIndexFileSystem(
        workspace=materialized["workspace"],
        semantic_retrieval_backend=backend,
    )
    questions = load_questions(dataset_dir)
    selected_questions = select_questions(questions, args.question_ids)
    system_prompt = read_prompt_file(args.system_prompt_file)
    question_prompt_template = read_prompt_file(args.question_prompt_file)

    results = []
    results_path = output_dir / "results.jsonl"
    answers_path = output_dir / "answers.jsonl"
    with (
        results_path.open("w", encoding="utf-8", buffering=1) as results_file,
        answers_path.open("w", encoding="utf-8", buffering=1) as answers_file,
    ):
        for question in selected_questions:
            print(f"\n[pipeline-agent-smoke question] {question.question_id}", flush=True)
            result = run_question(
                filesystem,
                question,
                system_prompt=system_prompt,
                question_prompt_template=question_prompt_template,
                args=args,
            )
            results.append(result)
            results_file.write(json.dumps(result, ensure_ascii=False) + "\n")
            answers_file.write(
                json.dumps(
                    {
                        "question_id": result["question_id"],
                        "answer": result["answer"],
                        "document_ids": result["document_ids"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            print(
                json.dumps(
                    {
                        "question_id": result["question_id"],
                        "doc_hit": result["doc_hit"],
                        "document_ids": result["document_ids"],
                        "expected_doc_ids": result["expected_doc_ids"],
                        "commands": result["commands"],
                        "error": result["error"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                flush=True,
            )

    summary = summarize(results, args=args, output_dir=output_dir, materialized=materialized)
    write_json(output_dir / "summary.json", summary)
    (output_dir / "summary.md").write_text(render_summary_md(summary), encoding="utf-8")
    print("\n[pipeline-agent-smoke summary]", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize a semantic metadata pipeline run into PIFS and run agent smoke questions."
    )
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_DIR))
    parser.add_argument("--workspace", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--reuse-workspace", action="store_true")
    parser.add_argument("--materialize-only", action="store_true")
    parser.add_argument("--question-ids", default=DEFAULT_QUESTION_IDS)
    parser.add_argument("--model", default=os.environ.get("PIFS_AGENT_MODEL", "gpt-5.4-mini"))
    parser.add_argument("--max-seconds", type=float, default=float(os.environ.get("PIFS_MAX_SECONDS", "120")))
    parser.add_argument("--max-turns", type=int, default=int(os.environ.get("PIFS_MAX_TURNS", "160")))
    parser.add_argument("--stream-mode", default=os.environ.get("PIFS_AGENT_STREAM_MODE", "all"), choices=AGENT_STREAM_MODE_CHOICES)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--reasoning-effort", default=os.environ.get("PIFS_AGENT_REASONING_EFFORT"), choices=REASONING_EFFORT_CHOICES)
    parser.add_argument("--reasoning-summary", default=os.environ.get("PIFS_AGENT_REASONING_SUMMARY"), choices=REASONING_SUMMARY_CHOICES)
    parser.add_argument("--system-prompt-file", default=str(DEFAULT_SYSTEM_PROMPT))
    parser.add_argument("--question-prompt-file", default=str(DEFAULT_QUESTION_PROMPT))
    parser.add_argument("--embedding-provider", default=os.environ.get("PIFS_PROJECTION_EMBEDDING_PROVIDER", "openai"))
    parser.add_argument("--embedding-model", default=os.environ.get("PIFS_PROJECTION_EMBEDDING_MODEL", "text-embedding-3-small"))
    parser.add_argument("--embedding-dimensions", type=int, default=int(os.environ.get("PIFS_PROJECTION_EMBEDDING_DIMENSIONS", "256")))
    parser.add_argument("--embedding-timeout", type=float, default=float(os.environ.get("PIFS_PROJECTION_EMBEDDING_TIMEOUT", "60")))
    parser.add_argument("--per-channel-limit", type=int, default=int(os.environ.get("PIFS_PROJECTION_PER_CHANNEL_LIMIT", "100")))
    parser.add_argument("--fetch-multiplier", type=int, default=int(os.environ.get("PIFS_PROJECTION_FETCH_MULTIPLIER", "100")))
    return parser.parse_args()


def materialize_workspace(
    *,
    run_dir: Path,
    dataset_dir: Path,
    workspace: Path,
    reset: bool,
) -> dict[str, Any]:
    if reset and workspace.exists():
        shutil.rmtree(workspace)
    metadata_rows = read_jsonl(run_dir / "metadata.normalized.jsonl")
    folder_plan = read_json(run_dir / "folder_plan.json")
    extension_schema = read_json(run_dir / "extension_schema.json")
    doc_ids = {dataset_doc_uuid(row) for row in metadata_rows if dataset_doc_uuid(row)}
    docs = {
        doc.dataset_doc_uuid: doc
        for doc in load_dataset_documents(dataset_dir, target_doc_ids=doc_ids)
    }
    missing_docs = sorted(doc_ids - set(docs))
    if missing_docs:
        raise SystemExit(f"missing dataset documents for pipeline metadata: {missing_docs[:5]}")

    filesystem = PageIndexFileSystem(workspace=workspace)
    schema = metadata_schema(extension_schema)
    filesystem._register_metadata_schema(schema)

    file_specs = []
    ordered_rows = []
    for row in metadata_rows:
        doc_id = dataset_doc_uuid(row)
        doc = docs.get(doc_id)
        if doc is None:
            continue
        ordered_rows.append(row)
        file_specs.append(file_spec_for_row(row, doc, extension_schema))
    file_refs = filesystem.register_files(file_specs)
    file_ref_by_doc = {
        dataset_doc_uuid(row): file_ref
        for row, file_ref in zip(ordered_rows, file_refs)
    }
    projection = filesystem.apply_semantic_folder_projection(
        folder_plan,
        file_ref_by_document_id=file_ref_by_doc,
    )
    return {
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "dataset": str(dataset_dir),
        "files_registered": len(file_refs),
        "folders_created": projection["folders_applied"],
        "folder_memberships_attached": projection["memberships_attached"],
        "metadata_schema_fields": sorted(schema["fields"]),
        "projection_index_dir": str(run_dir / "projection_indexes"),
        "reset": reset,
    }


def metadata_schema(extension_schema: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, dict[str, str]] = {
        "source_type": {"type": "string", "description": "System source type browse/filter field."},
        "content_type": {"type": "string", "description": "System content type."},
        "doc_type": {"type": "string", "description": "LLM base metadata document type."},
        "domain": {"type": "string", "description": "LLM base metadata domain."},
        "topic": {"type": "string", "description": "LLM base metadata topic."},
    }
    for field in extension_schema.get("fields") or []:
        if not field.get("suitable_for_dsl"):
            continue
        name = canonical_field_name(field.get("name") or "")
        if not name:
            continue
        fields[name] = {
            "type": "string",
            "description": str(field.get("description") or "Discovered extension field."),
        }
    return {"fields": fields}


def create_plan_folders(filesystem: PageIndexFileSystem, folder_plan: dict[str, Any]) -> None:
    for folder in folder_plan.get("folders") or []:
        filesystem.create_folder(
            folder["path"],
            kind=str(folder.get("kind") or "pipeline"),
            description=str(folder.get("description") or ""),
            metadata=folder.get("metadata") if isinstance(folder.get("metadata"), dict) else {},
        )


def file_spec_for_row(row: dict[str, Any], doc: DatasetDocument, extension_schema: dict[str, Any]) -> dict[str, Any]:
    system = row.get("system") if isinstance(row.get("system"), dict) else {}
    metadata = metadata_for_row(row, extension_schema)
    retrieval_cues = metadata.get("retrieval_cues") if isinstance(metadata.get("retrieval_cues"), list) else []
    fts_content = doc.content
    if retrieval_cues:
        fts_content = f"{doc.content}\n\nRetrieval cues:\n" + "\n".join(str(item) for item in retrieval_cues)
    return {
        "storage_uri": system.get("storage_uri") or doc.storage_uri,
        "source_path": system.get("source_path") or doc.source_path,
        "folder_path": source_root_path(system.get("source_type") or doc.source_type),
        "metadata": metadata,
        "external_id": doc.dataset_doc_uuid,
        "title": system.get("title") or doc.title,
        "content": doc.content,
        "fts_content": fts_content,
        "content_type": system.get("content_type") or doc.content_type,
        "source_type": system.get("source_type") or doc.source_type,
    }


def metadata_for_row(row: dict[str, Any], extension_schema: dict[str, Any]) -> dict[str, Any]:
    system = row.get("system") if isinstance(row.get("system"), dict) else {}
    base = row.get("metadata_base") if isinstance(row.get("metadata_base"), dict) else {}
    metadata: dict[str, Any] = {
        "source_type": system.get("source_type", ""),
        "content_type": system.get("content_type", ""),
        "title": system.get("title", ""),
        "doc_type": base.get("doc_type", ""),
        "domain": base.get("domain", ""),
        "topic": base.get("topic", ""),
        "summary": base.get("summary", ""),
        "entities": base.get("entities") or [],
        "relations": base.get("relations") or [],
        "constraints": base.get("constraints") or [],
        "retrieval_cues": base.get("retrieval_cues") or [],
    }
    candidates = row.get("extension_candidates") if isinstance(row.get("extension_candidates"), dict) else {}
    for field in extension_schema.get("fields") or []:
        name = canonical_field_name(field.get("name") or "")
        if name and name in candidates:
            metadata[name] = candidates[name]
    return metadata


def attach_plan_memberships(
    filesystem: PageIndexFileSystem,
    folder_plan: dict[str, Any],
    rows: list[dict[str, Any]],
    file_ref_by_doc: dict[str, str],
) -> int:
    items: list[dict[str, Any]] = []
    folders = folder_plan.get("folders") or []
    for row in rows:
        doc_id = dataset_doc_uuid(row)
        file_ref = file_ref_by_doc.get(doc_id)
        if not file_ref:
            continue
        for folder in folders:
            if folder.get("kind") == "source_root":
                continue
            if folder_applies_to_row(folder, row):
                items.append(
                    {
                        "file_ref": file_ref,
                        "folder": folder["path"],
                        "metadata": {
                            "mount": "pipeline_folder_plan",
                            "field": folder.get("field", ""),
                            "value": folder.get("value", ""),
                        },
                    }
                )
    filesystem.attach_files_to_folders(items)
    return len(items)


def folder_applies_to_row(folder: dict[str, Any], row: dict[str, Any]) -> bool:
    system = row.get("system") if isinstance(row.get("system"), dict) else {}
    if not folder_path_matches_source(folder.get("path", ""), system.get("source_type", "")):
        return False
    field = str(folder.get("field") or "")
    if not field:
        return False
    actual = row_field_value(row, field)
    expected = folder.get("value")
    return values_match(actual, expected)


def row_field_value(row: dict[str, Any], field: str) -> Any:
    if field == "source_type":
        return (row.get("system") or {}).get("source_type", "")
    base = row.get("metadata_base") if isinstance(row.get("metadata_base"), dict) else {}
    if field in base:
        return base[field]
    candidates = row.get("extension_candidates") if isinstance(row.get("extension_candidates"), dict) else {}
    return candidates.get(field, "")


def values_match(actual: Any, expected: Any) -> bool:
    if isinstance(actual, list):
        return any(values_match(item, expected) for item in actual)
    return str(actual or "").strip().lower() == str(expected or "").strip().lower()


def folder_path_matches_source(path: str, source_type: str) -> bool:
    expected = source_root_path(source_type).strip("/")
    for segment in str(path).strip("/").split("/"):
        if segment.startswith("source_type="):
            return segment == expected
    return False


def source_root_path(source_type: Any) -> str:
    return f"/source_type={slug(source_type)}"


def dataset_doc_uuid(row: dict[str, Any]) -> str:
    return str(row.get("dataset_doc_uuid") or (row.get("system") or {}).get("dataset_doc_uuid") or "")


def run_question(
    filesystem: PageIndexFileSystem,
    question: Any,
    *,
    system_prompt: str,
    question_prompt_template: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    prompt = agent_prompt(question, retrieval_mode="hybrid", template=question_prompt_template)
    started = time.time()
    agent_log: list[dict[str, Any]] = []
    raw_output = ""
    error = None
    parsed = {"answer": "", "document_ids": [], "citations": []}
    try:
        raw_output = run_pifs_agent(
            filesystem,
            prompt,
            model=args.model,
            root="/",
            system_prompt=system_prompt,
            verbose=args.verbose,
            stream_mode=args.stream_mode,
            reasoning_effort=args.reasoning_effort,
            reasoning_summary=args.reasoning_summary,
            max_seconds=args.max_seconds,
            max_turns=args.max_turns,
            output_type=PIFSAgentAnswer,
            agent_log=agent_log,
        )
        parsed = parse_agent_json(raw_output)
    except Exception as exc:  # noqa: BLE001 - smoke records failures.
        error = f"{type(exc).__name__}: {exc}"
    document_ids = list(dict.fromkeys(str(item) for item in parsed.get("document_ids", [])))
    expected_doc_ids = [str(item) for item in question.expected_doc_ids]
    commands = [
        item["command"]
        for item in agent_log
        if item.get("kind") == "tool_call" and item.get("command")
    ]
    return {
        "question_id": str(question.question_id),
        "source_types": [str(item) for item in question.source_types],
        "question": str(question.question),
        "expected_doc_ids": expected_doc_ids,
        "answer": parsed.get("answer", ""),
        "document_ids": document_ids,
        "citations": parsed.get("citations", []),
        "doc_hit": bool(set(expected_doc_ids).intersection(document_ids)),
        "raw_output": raw_output,
        "error": error,
        "seconds": round(time.time() - started, 3),
        "commands": commands,
        "agent_log": agent_log,
    }


class SmokeQuestion:
    def __init__(self, row: dict[str, Any]) -> None:
        self.question_id = str(row["question_id"])
        self.question_type = str(row.get("question_type") or "")
        self.source_types = [str(item) for item in row.get("source_types") or []]
        self.question = str(row["question"])
        self.expected_doc_ids = [str(item) for item in row.get("expected_doc_ids") or []]


def load_questions(dataset_dir: Path) -> dict[str, SmokeQuestion]:
    parquet_path = dataset_dir / "data" / "questions" / "test.parquet"
    if parquet_path.exists():
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - depends on local environment.
            raise RuntimeError("pyarrow is required to read EnterpriseRAG parquet questions") from exc
        rows = pq.read_table(parquet_path).to_pylist()
    else:
        rows = [
            json.loads(line)
            for line in (dataset_dir / "questions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return {str(row["question_id"]): SmokeQuestion(row) for row in rows}


def select_questions(questions: dict[str, SmokeQuestion], question_ids: str) -> list[SmokeQuestion]:
    selected = []
    for question_id in [item.strip() for item in question_ids.split(",") if item.strip()]:
        if question_id not in questions:
            raise SystemExit(f"unknown question id: {question_id}")
        selected.append(questions[question_id])
    return selected


def summarize(
    results: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
    output_dir: Path,
    materialized: dict[str, Any],
) -> dict[str, Any]:
    return {
        "questions": len(results),
        "question_ids": [result["question_id"] for result in results],
        "doc_hit_rate": (
            sum(1 for result in results if result["doc_hit"]) / len(results)
            if results
            else 0
        ),
        "model": args.model,
        "stream_mode": args.stream_mode,
        "output_dir": str(output_dir),
        "results_path": str(output_dir / "results.jsonl"),
        "answers_path": str(output_dir / "answers.jsonl"),
        "workspace": materialized["workspace"],
        "files_registered": materialized["files_registered"],
        "folder_memberships_attached": materialized["folder_memberships_attached"],
        "results": [
            {
                "question_id": result["question_id"],
                "doc_hit": result["doc_hit"],
                "document_ids": result["document_ids"],
                "expected_doc_ids": result["expected_doc_ids"],
                "commands": result["commands"],
                "seconds": result["seconds"],
                "error": result["error"],
            }
            for result in results
        ],
    }


def render_summary_md(summary: dict[str, Any]) -> str:
    lines = [
        "# Semantic Metadata Pipeline Agent Smoke",
        "",
        f"- questions: `{summary['questions']}`",
        f"- doc_hit_rate: `{summary['doc_hit_rate']:.4f}`",
        f"- model: `{summary['model']}`",
        f"- stream_mode: `{summary['stream_mode']}`",
        f"- workspace: `{summary['workspace']}`",
        f"- files_registered: `{summary['files_registered']}`",
        f"- folder_memberships_attached: `{summary['folder_memberships_attached']}`",
        "",
        "## Results",
        "",
    ]
    for result in summary["results"]:
        lines.append(
            f"- `{result['question_id']}` hit=`{result['doc_hit']}` "
            f"docs=`{','.join(result['document_ids']) or '-'}` "
            f"expected=`{','.join(result['expected_doc_ids'])}`"
        )
        for command in result["commands"]:
            lines.append(f"  - `{command}`")
    return "\n".join(lines) + "\n"


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def resolve_workspace(run_dir: Path, value: str) -> Path:
    if value:
        return resolve_path(value)
    return run_dir / "pifs_workspace" / "workspace"


def resolve_output_dir(run_dir: Path, value: str) -> Path:
    if value:
        return resolve_path(value)
    return run_dir / "agent_smoke" / time.strftime("%Y%m%d-%H%M%S")


if __name__ == "__main__":
    raise SystemExit(main())
