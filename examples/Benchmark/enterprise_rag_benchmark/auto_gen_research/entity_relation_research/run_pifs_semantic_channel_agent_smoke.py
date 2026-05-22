from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.Benchmark.enterprise_rag_benchmark.run_enterprise_rag_pifs_agent import (
    PIFSAgentAnswer,
    agent_prompt,
    parse_agent_json,
    read_prompt_file,
)
from pageindex.filesystem import HybridProjectionSearchBackend, PageIndexFileSystem
from pageindex.filesystem.agent import run_pifs_agent


BENCHMARK_DIR = REPO_ROOT / "examples" / "Benchmark" / "enterprise_rag_benchmark"
DEFAULT_DATASET_DIR = BENCHMARK_DIR / "dataset"
DEFAULT_INDEX_DIR = (
    BENCHMARK_DIR
    / "auto_gen_research"
    / "entity_relation_research"
    / "cache"
    / "projection_indexes"
    / "enterprise_full_v1"
)
DEFAULT_PROMPT = BENCHMARK_DIR / "prompts" / "pifs_question_semantic_channels.md"
DEFAULT_SYSTEM_PROMPT = BENCHMARK_DIR / "prompts" / "pifs_agent_system.md"
DEFAULT_OUTPUT_DIR = (
    BENCHMARK_DIR
    / "auto_gen_research"
    / "entity_relation_research"
    / "results"
    / "pifs-semantic-channel-agent-smoke-20260522"
)


class HydratingPageIndexFileSystem(PageIndexFileSystem):
    def __init__(self, *args: Any, documents_path: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.documents_path = documents_path
        self._hydrated_doc_ids: set[str] = set()
        self._missing_doc_ids: set[str] = set()

    def hydrate_document_ids(self, document_ids: list[str]) -> None:
        wanted = [
            str(doc_id)
            for doc_id in document_ids
            if doc_id and doc_id not in self._hydrated_doc_ids and doc_id not in self._missing_doc_ids
        ]
        unresolved = []
        for doc_id in wanted:
            try:
                self.store.resolve_file_ref(doc_id)
                self._hydrated_doc_ids.add(doc_id)
            except KeyError:
                unresolved.append(doc_id)
        if not unresolved:
            return
        table = pq.read_table(
            self.documents_path,
            filters=[("doc_id", "in", unresolved)],
        )
        found = set()
        records = []
        for row in table.to_pylist():
            doc_id = str(row.get("doc_id") or "")
            if not doc_id:
                continue
            found.add(doc_id)
            source_type = str(row.get("source_type") or "unknown")
            title = str(row.get("title") or doc_id)
            content = str(row.get("content") or title)
            metadata = {
                "doc_id": doc_id,
                "dataset_doc_uuid": doc_id,
                "source_type": source_type,
                "title": title,
            }
            records.append(
                {
                    "storage_uri": f"parquet://enterprise-rag/{doc_id}",
                    "source_path": f"{source_type}/{doc_id}",
                    "folder_path": f"/{source_type}",
                    "metadata": metadata,
                    "external_id": doc_id,
                    "title": title,
                    "content": content,
                    "content_type": "application/json",
                    "source_type": source_type,
                }
            )
        if records:
            self.register_files(records)
            self._hydrated_doc_ids.update(found)
        self._missing_doc_ids.update(set(unresolved) - found)

    def _semantic_search(
        self,
        query: Any,
        *,
        scope: dict[str, Any] | None,
        metadata_filter: dict[str, Any] | None,
        limit: int,
        channel: str | None = None,
    ):
        backend = self.semantic_retrieval_backend
        if backend is None:
            return []
        filters = self._semantic_filters_for_scope(scope)
        fetch_limit = max(limit * 10, 50)
        query_text = self._query_text(query)
        if not query_text:
            return []
        if channel:
            candidates = backend.search_channel(
                channel,
                query_text,
                limit=fetch_limit,
                filters=filters,
            )
        else:
            candidates = backend.search(query_text, limit=fetch_limit, filters=filters)
        self.hydrate_document_ids([candidate.document_id for candidate in candidates])
        return self._semantic_candidates_to_results(
            candidates,
            scope=scope,
            metadata_filter=metadata_filter,
            limit=limit,
        )

    def _semantic_candidates_to_results(
        self,
        candidates: list[Any],
        *,
        scope: dict[str, Any] | None,
        metadata_filter: dict[str, Any] | None,
        limit: int,
    ):
        from pageindex.filesystem.types import SearchResult

        results: list[SearchResult] = []
        seen: set[str] = set()
        scope_path = self._scope_folder_path(scope)
        for candidate in candidates:
            try:
                file_ref = self.store.resolve_file_ref(candidate.document_id)
            except KeyError:
                continue
            if file_ref in seen:
                continue
            if not self.store.file_matches(file_ref, scope=scope, metadata_filter=metadata_filter):
                continue
            seen.add(file_ref)
            entry = self.store.get_file(file_ref)
            reference_id = self._reference_for(file_ref)
            folder_paths = [
                folder["path"]
                for folder in self.store.folder_memberships(file_ref)
            ]
            folder_path = self._preferred_folder_path(folder_paths, scope_path, entry.folder_path)
            results.append(
                SearchResult(
                    reference_id=reference_id,
                    file_ref=file_ref,
                    external_id=entry.external_id,
                    title=entry.title,
                    snippet=candidate.snippet or entry.descriptor,
                    folder_path=folder_path,
                    folder_paths=folder_paths,
                    metadata=entry.metadata,
                    source_path=entry.source_path,
                    id=entry.external_id or file_ref,
                    document_id=entry.external_id,
                    name=entry.title,
                    description=entry.descriptor,
                    status=entry.pageindex_tree_status,
                    pageNum=None,
                    createdAt=None,
                    folderId=None,
                )
            )
            if len(results) >= limit:
                break
        return results


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BENCHMARK_DIR / ".env")
    args = parse_args()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    questions = load_questions(dataset_dir / "data" / "questions" / "test.parquet")
    selected = [questions[qid] for qid in args.question_ids.split(",") if qid in questions]
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    backend = HybridProjectionSearchBackend.from_provider(
        args.index_dir,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        embedding_dimensions=args.embedding_dimensions,
        embedding_timeout=args.embedding_timeout,
        per_channel_limit=args.per_channel_limit,
        fetch_multiplier=args.fetch_multiplier,
    )
    system_prompt = read_prompt_file(args.system_prompt_file)
    question_prompt_template = read_prompt_file(args.question_prompt_file)
    results = []
    results_path = output_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8", buffering=1) as results_file:
        for question in selected:
            print(f"\n[semantic-channel-agent question] {question['question_id']}", flush=True)
            result = run_question(
                question,
                backend=backend,
                dataset_dir=dataset_dir,
                system_prompt=system_prompt,
                question_prompt_template=question_prompt_template,
                args=args,
            )
            results.append(result)
            results_file.write(json.dumps(result, ensure_ascii=False) + "\n")
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
    summary = summarize(results, args=args, output_dir=output_dir)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(render_summary_md(summary), encoding="utf-8")
    print("\n[semantic-channel-agent summary]", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def run_question(
    question: dict[str, Any],
    *,
    backend: HybridProjectionSearchBackend,
    dataset_dir: Path,
    system_prompt: str,
    question_prompt_template: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    workspace = Path(args.output_dir).expanduser().resolve() / "workspaces" / question["question_id"]
    filesystem = HydratingPageIndexFileSystem(
        workspace=workspace,
        semantic_retrieval_backend=backend,
        documents_path=dataset_dir / "data" / "documents" / "test.parquet",
    )
    for source_type in question["source_types"]:
        filesystem.create_folder(f"/{source_type}")
    prompt = agent_prompt(
        question_obj(question),
        retrieval_mode="hybrid",
        template=question_prompt_template,
    )
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
    expected_doc_ids = [str(item) for item in question["expected_doc_ids"]]
    commands = [
        item["command"]
        for item in agent_log
        if item.get("kind") == "tool_call" and item.get("command")
    ]
    return {
        "question_id": str(question["question_id"]),
        "source_types": [str(item) for item in question["source_types"]],
        "question": str(question["question"]),
        "expected_doc_ids": expected_doc_ids,
        "answer": parsed.get("answer", ""),
        "document_ids": document_ids,
        "citations": parsed.get("citations", []),
        "doc_hit": bool(set(expected_doc_ids).intersection(document_ids)),
        "raw_output": raw_output,
        "error": error,
        "seconds": round(time.time() - started, 3),
        "commands": commands,
        "command_kinds": classify_commands(commands),
        "agent_log": agent_log,
        "workspace": str(workspace),
        "hydrated_documents": len(filesystem._hydrated_doc_ids),
    }


def question_obj(row: dict[str, Any]) -> Any:
    class Question:
        question_id = str(row["question_id"])
        question_type = str(row.get("question_type") or "")
        source_types = [str(item) for item in row["source_types"]]
        question = str(row["question"])
        expected_doc_ids = [str(item) for item in row["expected_doc_ids"]]

    return Question()


def load_questions(path: Path) -> dict[str, dict[str, Any]]:
    rows = pq.read_table(path).to_pylist()
    return {str(row["question_id"]): normalize_question(row) for row in rows}


def normalize_question(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": str(row["question_id"]),
        "question_type": str(row.get("question_type") or ""),
        "source_types": [str(item) for item in row.get("source_types") or []],
        "question": str(row["question"]),
        "expected_doc_ids": [str(item) for item in row.get("expected_doc_ids") or []],
    }


def classify_commands(commands: list[str]) -> dict[str, int]:
    prefixes = {
        "search-summary": 0,
        "search-entity": 0,
        "search-relation": 0,
        "grep -R": 0,
        "find --name": 0,
        "find --relation": 0,
        "cat": 0,
        "grep-ref": 0,
    }
    for command in commands:
        for stripped in split_chained_command(command):
            classify_single_command(stripped, prefixes)
    return prefixes


def split_chained_command(command: str) -> list[str]:
    return [part.strip() for part in str(command).split("&&") if part.strip()]


def classify_single_command(stripped: str, prefixes: dict[str, int]) -> None:
    if stripped.startswith("search-summary "):
        prefixes["search-summary"] += 1
    if stripped.startswith("search-entity "):
        prefixes["search-entity"] += 1
    if stripped.startswith("search-relation "):
        prefixes["search-relation"] += 1
    if stripped.startswith("grep -R ") or stripped.startswith("grep -r "):
        prefixes["grep -R"] += 1
    if stripped.startswith("find ") and " --name " in stripped:
        prefixes["find --name"] += 1
    if stripped.startswith("find ") and " --relation " in stripped:
        prefixes["find --relation"] += 1
    if stripped.startswith("cat "):
        prefixes["cat"] += 1
    if stripped.startswith("grep ") and not (
        stripped.startswith("grep -R ") or stripped.startswith("grep -r ")
    ):
        prefixes["grep-ref"] += 1


def summarize(results: list[dict[str, Any]], *, args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
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
        "question_prompt_file": str(Path(args.question_prompt_file).resolve()),
        "system_prompt_file": str(Path(args.system_prompt_file).resolve()),
        "output_dir": str(output_dir),
        "results_path": str(output_dir / "results.jsonl"),
        "tool_usage": aggregate_tool_usage(results),
        "results": [
            {
                "question_id": result["question_id"],
                "doc_hit": result["doc_hit"],
                "document_ids": result["document_ids"],
                "expected_doc_ids": result["expected_doc_ids"],
                "commands": result["commands"],
                "command_kinds": result["command_kinds"],
                "seconds": result["seconds"],
                "error": result["error"],
                "hydrated_documents": result["hydrated_documents"],
                "workspace": result["workspace"],
            }
            for result in results
        ],
    }


def aggregate_tool_usage(results: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for result in results:
        for key, value in result["command_kinds"].items():
            totals[key] = totals.get(key, 0) + int(value)
    return totals


def render_summary_md(summary: dict[str, Any]) -> str:
    lines = [
        "# PIFS Semantic Channel Agent Smoke",
        "",
        f"- questions: `{summary['questions']}`",
        f"- doc_hit_rate: `{summary['doc_hit_rate']:.4f}`",
        f"- model: `{summary['model']}`",
        f"- prompt: `{summary['question_prompt_file']}`",
        "",
        "## Tool Usage",
        "",
    ]
    for key, value in summary["tool_usage"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Results", ""])
    for result in summary["results"]:
        lines.append(
            f"- `{result['question_id']}` hit=`{result['doc_hit']}` "
            f"docs=`{','.join(result['document_ids']) or '-'}` "
            f"expected=`{','.join(result['expected_doc_ids'])}`"
        )
        for command in result["commands"]:
            lines.append(f"  - `{command}`")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a PIFS semantic-channel agent smoke")
    parser.add_argument("--dataset-dir", default=str(DEFAULT_DATASET_DIR))
    parser.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--question-ids", default="qst_0007,qst_0009,qst_0011")
    parser.add_argument("--model", default=os.environ.get("PIFS_AGENT_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--max-seconds", type=float, default=float(os.environ.get("PIFS_MAX_SECONDS", "90")))
    parser.add_argument("--max-turns", type=int, default=int(os.environ.get("PIFS_MAX_TURNS", "120")))
    parser.add_argument("--stream-mode", default=os.environ.get("PIFS_AGENT_STREAM_MODE", "tools"))
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--reasoning-effort", default=os.environ.get("PIFS_AGENT_REASONING_EFFORT"))
    parser.add_argument("--reasoning-summary", default=os.environ.get("PIFS_AGENT_REASONING_SUMMARY"))
    parser.add_argument("--embedding-provider", default=os.environ.get("PIFS_PROJECTION_EMBEDDING_PROVIDER", "openai"))
    parser.add_argument("--embedding-model", default=os.environ.get("PIFS_PROJECTION_EMBEDDING_MODEL", "text-embedding-3-small"))
    parser.add_argument("--embedding-dimensions", type=int, default=int(os.environ.get("PIFS_PROJECTION_EMBEDDING_DIMENSIONS", "256")))
    parser.add_argument("--embedding-timeout", type=float, default=float(os.environ.get("PIFS_PROJECTION_EMBEDDING_TIMEOUT", "60")))
    parser.add_argument("--per-channel-limit", type=int, default=int(os.environ.get("PIFS_PROJECTION_PER_CHANNEL_LIMIT", "100")))
    parser.add_argument("--fetch-multiplier", type=int, default=int(os.environ.get("PIFS_PROJECTION_FETCH_MULTIPLIER", "100")))
    parser.add_argument("--system-prompt-file", default=str(DEFAULT_SYSTEM_PROMPT))
    parser.add_argument("--question-prompt-file", default=str(DEFAULT_PROMPT))
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
