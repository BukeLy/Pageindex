"""
Run a small EnterpriseRAG smoke test with the current PageIndex FileSystem.

This script intentionally stays lightweight:
  - no benchmark API wrapper
  - no vector database
  - local SQLite/FTS FileSystem index
  - OpenAI-compatible chat completions for Gemini or OpenAI-style providers

Default mode indexes only the source types mentioned by the selected questions
so a 5-question smoke is fast. Use --source-scope all for a fairer run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_RUN_DIR = BENCHMARK_DIR / "runs" / "smoke"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODEL = "gemini-2.5-flash"

sys.path.insert(0, str(REPO_ROOT))

from pageindex.filesystem import EnterpriseRAGBenchmark, PageIndexFileSystem  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EnterpriseRAG smoke benchmark.")
    parser.add_argument(
        "--bench-root",
        default="/private/tmp/EnterpriseRAG-Bench-20260514",
        help="Path containing questions.jsonl and generated_data/sources.",
    )
    parser.add_argument("--limit", type=int, default=5, help="Number of questions to run.")
    parser.add_argument(
        "--question-ids",
        default=None,
        help="Comma-separated question IDs to run. When set, this overrides --limit.",
    )
    parser.add_argument(
        "--workspace",
        default=str(DEFAULT_RUN_DIR / "workspace"),
        help="Local PageIndex FileSystem workspace.",
    )
    parser.add_argument(
        "--results-path",
        default=str(DEFAULT_RUN_DIR / "results.jsonl"),
        help="JSONL path for per-question results.",
    )
    parser.add_argument(
        "--summary-path",
        default=str(DEFAULT_RUN_DIR / "summary.json"),
        help="JSON path for aggregate metrics.",
    )
    parser.add_argument(
        "--source-scope",
        choices=("question-source-types", "all"),
        default="question-source-types",
        help=(
            "question-source-types indexes only source types named by selected questions; "
            "all indexes the full EnterpriseRAG source tree."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="OpenAI-compatible model name. Defaults to PAGEINDEX_ANSWER_MODEL, OPENAI_MODEL, or gemini-2.5-flash.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible base URL. Defaults to OPENAI_BASE_URL, OPENAI_API_BASE, or Gemini endpoint.",
    )
    parser.add_argument(
        "--max-context-docs",
        type=int,
        default=6,
        help="Number of retrieved documents to pass to the answer model.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of JSON files to write per SQLite transaction during ingestion.",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Only evaluate retrieval metrics; do not generate or judge answers with an LLM.",
    )
    return parser.parse_args()


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def load_question_rows(
    questions_path: Path,
    limit: int,
    question_ids: str | None,
) -> list[dict[str, Any]]:
    wanted = [item.strip() for item in question_ids.split(",") if item.strip()] if question_ids else []
    wanted_set = set(wanted)
    rows = []
    with questions_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if wanted and row["question_id"] not in wanted_set:
                continue
            rows.append(row)
            if not wanted and len(rows) >= limit:
                break
    if wanted:
        found = {row["question_id"] for row in rows}
        missing = [question_id for question_id in wanted if question_id not in found]
        if missing:
            raise ValueError(f"Question IDs not found: {', '.join(missing)}")
    return rows


def count_files(fs: PageIndexFileSystem) -> int:
    with fs._connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()
    return int(row["n"])


def selected_source_types(source_root: Path, questions: list[dict[str, Any]], source_scope: str) -> list[str]:
    if source_scope == "all":
        return sorted(path.name for path in source_root.iterdir() if path.is_dir())
    return sorted({source for row in questions for source in row.get("source_types", [])})


def ingest_sources(
    fs: PageIndexFileSystem,
    benchmark: EnterpriseRAGBenchmark,
    source_root: Path,
    source_types: list[str],
    batch_size: int,
) -> int:
    existing = count_files(fs)
    if existing:
        print(f"reuse_index files={existing}", flush=True)
        return existing

    started = time.time()
    indexed = 0
    for source_type in source_types:
        source_dir = source_root / source_type
        if not source_dir.exists():
            raise FileNotFoundError(source_dir)
        paths = sorted(source_dir.rglob("*.json"))
        print(f"ingest_source source_type={source_type} json_files={len(paths)}", flush=True)
        for start in range(0, len(paths), batch_size):
            batch_paths = paths[start:start + batch_size]
            benchmark.ingest_paths(source_root, batch_paths, batch_size=batch_size)
            indexed += len(batch_paths)
            if indexed % 5000 == 0 or start + batch_size >= len(paths):
                elapsed = time.time() - started
                print(f"ingest_progress indexed={indexed} elapsed_sec={elapsed:.1f}", flush=True)

    elapsed = time.time() - started
    print(f"ingest_done indexed={indexed} elapsed_sec={elapsed:.1f}", flush=True)
    return indexed


def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str,
    base_url: str,
    json_mode: bool = False,
) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_error = None
    for attempt in range(1, 4):
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise RuntimeError(f"LLM HTTP {exc.code}: {detail[:1000]}") from exc
            last_error = RuntimeError(f"LLM HTTP {exc.code}: {detail[:1000]}")
            time.sleep(2 * attempt)
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt == 3:
                raise RuntimeError(f"LLM request failed after retries: {exc}") from exc
            time.sleep(2 * attempt)
    else:
        raise RuntimeError(f"LLM request failed after retries: {last_error}")

    parsed = json.loads(body)
    return parsed["choices"][0]["message"]["content"]


def build_context(
    fs: PageIndexFileSystem,
    candidates: list[Any],
    max_docs: int,
) -> str:
    chunks = []
    for i, candidate in enumerate(candidates[:max_docs], 1):
        try:
            opened = fs.open(candidate.reference_id, "1:80")
            body = opened.text
        except Exception:
            body = candidate.snippet
        chunks.append(
            "\n".join(
                [
                    f"[doc {i}]",
                    f"document_id: {candidate.external_id}",
                    f"title: {candidate.title}",
                    f"folder: {candidate.folder_path}",
                    "content:",
                    body[:6000],
                ]
            )
        )
    return "\n\n---\n\n".join(chunks)


def answer_question(
    fs: PageIndexFileSystem,
    question: str,
    candidates: list[Any],
    *,
    model: str,
    base_url: str,
    max_context_docs: int,
) -> str:
    context = build_context(fs, candidates, max_context_docs)
    return chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "Answer the user question using only the provided documents. "
                    "Be concise but include all requested concrete facts. "
                    "If the documents do not contain the answer, say NOT FOUND."
                ),
            },
            {"role": "user", "content": f"Question:\n{question}\n\nDocuments:\n{context}"},
        ],
        model=model,
        base_url=base_url,
    )


def judge_answer(
    question: str,
    gold_answer: str,
    candidate_answer: str,
    *,
    model: str,
    base_url: str,
) -> dict[str, Any]:
    raw = chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "You are grading a retrieval QA benchmark. Return JSON only with keys "
                    "correct (boolean) and reason (short string). Mark correct only when "
                    "the candidate answer contains the essential facts from the gold answer."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question:\n{question}\n\n"
                    f"Gold answer:\n{gold_answer}\n\n"
                    f"Candidate answer:\n{candidate_answer}"
                ),
            },
        ],
        model=model,
        base_url=base_url,
        json_mode=True,
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"correct": False, "reason": "judge did not return JSON"}
    parsed["raw"] = raw
    return parsed


def main() -> None:
    args = parse_args()
    load_env(REPO_ROOT / ".env")

    bench_root = Path(args.bench_root).expanduser()
    source_root = bench_root / "generated_data" / "sources"
    questions_path = bench_root / "questions.jsonl"
    questions = load_question_rows(questions_path, args.limit, args.question_ids)
    source_types = selected_source_types(source_root, questions, args.source_scope)
    model = args.model or os.environ.get("PAGEINDEX_ANSWER_MODEL") or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or DEFAULT_BASE_URL

    fs = PageIndexFileSystem(Path(args.workspace).expanduser())
    benchmark = EnterpriseRAGBenchmark(fs)
    indexed = ingest_sources(fs, benchmark, source_root, source_types, args.batch_size)

    results = []
    doc_top1_hits = 0
    doc_top5_hits = 0
    judged_correct = 0
    judged_questions = 0
    results_path = Path(args.results_path).expanduser()
    results_path.parent.mkdir(parents=True, exist_ok=True)

    with results_path.open("w", encoding="utf-8") as results_file:
        for row in questions:
            question_id = row["question_id"]
            question = row["question"]
            expected = set(row.get("expected_doc_ids") or [])
            traversal = fs.tree_search(question, limit=10)
            candidates = traversal.candidates or fs.search(question, limit=10)
            retrieved_ids = [candidate.external_id for candidate in candidates if candidate.external_id]
            top1_hit = bool(retrieved_ids[:1] and retrieved_ids[0] in expected)
            top5_hit = bool(expected.intersection(retrieved_ids[:5]))
            doc_top1_hits += int(top1_hit)
            doc_top5_hits += int(top5_hit)

            answer = ""
            correct = None
            judge_reason = "skipped"
            llm_error = None
            if not args.skip_llm:
                try:
                    answer = answer_question(
                        fs,
                        question,
                        candidates,
                        model=model,
                        base_url=base_url,
                        max_context_docs=args.max_context_docs,
                    )
                    judge = judge_answer(
                        question,
                        row.get("gold_answer", ""),
                        answer,
                        model=model,
                        base_url=base_url,
                    )
                    correct = bool(judge.get("correct"))
                    judge_reason = judge.get("reason", "")
                    judged_correct += int(correct)
                    judged_questions += 1
                except RuntimeError as exc:
                    llm_error = str(exc)
                    judge_reason = "llm_error"

            result = {
                "question_id": question_id,
                "source_types": row.get("source_types", []),
                "question": question,
                "expected_doc_ids": list(expected),
                "retrieved_doc_ids": retrieved_ids[:10],
                "top1_hit": top1_hit,
                "top5_hit": top5_hit,
                "answer": answer,
                "judge_correct": correct,
                "judge_reason": judge_reason,
                "llm_error": llm_error,
                "tree_nodes": [node.__dict__ for node in traversal.nodes],
            }
            results.append(result)
            results_file.write(json.dumps(result, ensure_ascii=False) + "\n")
            results_file.flush()
            print(
                "result "
                f"question_id={question_id} "
                f"top1={top1_hit} top5={top5_hit} judged={correct} "
                f"retrieved={retrieved_ids[:3]}",
                flush=True,
            )

    summary = {
        "questions": len(questions),
        "question_ids": [row["question_id"] for row in questions],
        "indexed_files": indexed,
        "source_scope": args.source_scope,
        "label_leakage": args.source_scope == "question-source-types",
        "source_types_indexed": source_types,
        "model": model,
        "base_url": base_url,
        "doc_top1": doc_top1_hits / len(questions),
        "doc_top5": doc_top5_hits / len(questions),
        "llm_judged_accuracy": None if judged_questions == 0 else judged_correct / judged_questions,
        "judged_questions": judged_questions,
        "skip_llm": args.skip_llm,
        "results_path": str(results_path),
    }

    summary_path = Path(args.summary_path).expanduser()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("summary " + json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
