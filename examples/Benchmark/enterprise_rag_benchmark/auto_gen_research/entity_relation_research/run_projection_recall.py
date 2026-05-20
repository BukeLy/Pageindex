from __future__ import annotations

import argparse
import collections
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


RESEARCH_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS_DIR = RESEARCH_DIR / "results"
AUTO_GEN_DIR = RESEARCH_DIR.parent
DEFAULT_SELECTED_RUN = AUTO_GEN_DIR / "results" / "20260515-100docs-agent-eval"
DEFAULT_SELECTED_QUESTIONS = DEFAULT_SELECTED_RUN / "selected_questions.json"
DEFAULT_SELECTED_DOCUMENTS = DEFAULT_SELECTED_RUN / "selected_documents.json"
DEFAULT_PROFILES = AUTO_GEN_DIR / "generated" / "doc_profiles.json"

LOGGER = logging.getLogger("projection_recall")

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "did",
    "do",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "there",
    "this",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "without",
}

TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_./:-]{1,}|[0-9]+(?:\.[0-9]+)?(?:mi?b|gb|ms|s|%|x)?")
CONSTRAINT_RE = re.compile(
    r"\b(?:default|defaults?|limit|limits?|quota|timeout|sla|threshold|latency|p\d{2}|"
    r"max|min|size|duration|deadline|version|flag|metric|status|state|region|date|"
    r"[0-9]+(?:\.[0-9]+)?\s*(?:mib|mb|gib|gb|ms|seconds?|minutes?|hours?|days?|%|x))\b",
    re.IGNORECASE,
)
ENTITY_HINT_RE = re.compile(
    r"`([^`]{2,80})`|"
    r"\b[A-Z][A-Za-z0-9]*(?:[-_/.:][A-Za-z0-9]+)+\b|"
    r"\b[a-zA-Z][a-zA-Z0-9]+(?:[-_/.:][a-zA-Z0-9]+){1,}\b|"
    r"\b[A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+){1,3}\b|"
    r"\b(?:API|SLA|SSO|SCIM|KMS|VPC|GPU|PR|SDK|CRM|UI|QA|CI|CD)\b"
)
RELATION_VERBS = {
    "add",
    "adds",
    "added",
    "allow",
    "allows",
    "block",
    "blocks",
    "change",
    "changes",
    "configure",
    "defines",
    "disable",
    "enable",
    "enforce",
    "enforces",
    "fix",
    "fixes",
    "include",
    "includes",
    "introduce",
    "introduces",
    "limit",
    "limits",
    "migrate",
    "require",
    "requires",
    "resolve",
    "resolves",
    "roll",
    "route",
    "support",
    "supports",
    "track",
    "tracks",
    "validate",
    "validates",
}


@dataclass(frozen=True)
class Question:
    question_id: str
    question: str
    question_type: str
    source_types: list[str]
    expected_doc_ids: list[str]


@dataclass(frozen=True)
class Document:
    doc_id: str
    source_type: str
    title: str
    content: str
    is_expected: bool


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    started = time.time()

    questions = load_selected_questions(args.selected_questions_json)
    if args.question_ids:
        keep = {item.strip() for item in args.question_ids.split(",") if item.strip()}
        questions = [question for question in questions if question.question_id in keep]
    if args.question_limit > 0:
        questions = questions[: args.question_limit]
    expected_ids = sorted({doc_id for q in questions for doc_id in q.expected_doc_ids})
    docs = load_profile_documents(
        args.selected_documents_json,
        args.profiles_json,
        expected_ids=expected_ids,
    )
    source_types = sorted({source_type for q in questions for source_type in q.source_types})

    LOGGER.info(
        "loaded questions=%s expected_docs=%s source_types=%s",
        len(questions),
        len(expected_ids),
        ",".join(source_types),
    )
    LOGGER.info("loaded candidate docs=%s expected_present=%s", len(docs), sum(doc.is_expected for doc in docs))

    result = run_experiment(questions, docs, candidate_limit=args.candidate_limit)
    result["config"] = {
        "question_limit": args.question_limit,
        "question_ids": args.question_ids,
        "candidate_limit": args.candidate_limit,
        "selected_questions_json": str(args.selected_questions_json),
        "selected_documents_json": str(args.selected_documents_json),
        "profiles_json": str(args.profiles_json),
    }
    result["seconds"] = round(time.time() - started, 3)

    output_dir = args.output_dir or DEFAULT_RESULTS_DIR / time.strftime("projection-preflight-%Y%m%d-%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "summary.md").write_text(render_summary(result), encoding="utf-8")
    LOGGER.info("wrote results to %s", output_dir)
    print(json.dumps({"output_dir": str(output_dir), "summary": result["summary"]}, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline entity/relation projection recall preflight")
    parser.add_argument("--question-limit", type=int, default=0)
    parser.add_argument("--question-ids", default="")
    parser.add_argument("--candidate-limit", type=int, default=100)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--selected-questions-json", type=Path, default=DEFAULT_SELECTED_QUESTIONS)
    parser.add_argument("--selected-documents-json", type=Path, default=DEFAULT_SELECTED_DOCUMENTS)
    parser.add_argument("--profiles-json", type=Path, default=DEFAULT_PROFILES)
    return parser.parse_args()


def load_selected_questions(path: Path) -> list[Question]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        Question(
            question_id=str(row["question_id"]),
            question=str(row["question"]),
            question_type=str(row.get("question_type") or ""),
            source_types=[str(item) for item in (row.get("source_types") or [])],
            expected_doc_ids=[str(item) for item in (row.get("expected_doc_ids") or [])],
        )
        for row in rows
    ]


def load_profile_documents(
    selected_documents_path: Path,
    profiles_path: Path,
    *,
    expected_ids: list[str],
) -> list[Document]:
    selected = json.loads(selected_documents_path.read_text(encoding="utf-8"))
    profiles = json.loads(profiles_path.read_text(encoding="utf-8"))
    expected = set(expected_ids)
    docs: list[Document] = []
    for row in selected:
        doc_id = str(row["dataset_doc_uuid"])
        profile = profiles.get(doc_id, {})
        docs.append(
            Document(
                doc_id=doc_id,
                source_type=str(row.get("source_type") or profile.get("communication_channel") or ""),
                title=str(row.get("title") or profile.get("primary_topic") or doc_id),
                content=profile_to_text(profile),
                is_expected=doc_id in expected,
            )
        )
    return docs


def profile_to_text(profile: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "doc_type",
        "primary_topic",
        "topic_cluster_hint",
        "time_period",
        "communication_channel",
        "product_or_system",
        "primary_entity",
        "action_or_event",
        "semantic_summary",
    ):
        value = profile.get(key)
        if value:
            parts.append(f"{key}: {value}")
    for key in ("secondary_topics", "search_terms", "folder_hints"):
        value = profile.get(key) or []
        if value:
            parts.append(f"{key}: {', '.join(str(item) for item in value)}")
    entities = []
    for entity in profile.get("entities") or []:
        if isinstance(entity, dict):
            aliases = entity.get("aliases") or []
            entities.append(
                " | ".join(
                    str(item)
                    for item in [
                        entity.get("name"),
                        entity.get("type"),
                        ", ".join(str(alias) for alias in aliases),
                    ]
                    if item
                )
            )
        else:
            entities.append(str(entity))
    if entities:
        parts.append("entities: " + "\n".join(entities))
    relations = []
    for relation in profile.get("relations") or []:
        if isinstance(relation, dict):
            evidence = relation.get("evidence_terms") or []
            relations.append(
                " | ".join(
                    str(item)
                    for item in [
                        relation.get("subject"),
                        relation.get("relation"),
                        relation.get("object"),
                        ", ".join(str(term) for term in evidence),
                    ]
                    if item
                )
            )
        else:
            relations.append(str(relation))
    if relations:
        parts.append("relations: " + "\n".join(relations))
    return "\n".join(parts)


def run_experiment(
    questions: list[Question],
    docs: list[Document],
    *,
    candidate_limit: int,
) -> dict[str, Any]:
    strategies = {
        "baseline_metadata_text": build_doc_texts(docs, "metadata"),
        "summary_only_text": build_doc_texts(docs, "summary"),
        "entity_constraint_projection": build_doc_texts(docs, "entity_constraint"),
        "entity_relation_projection": build_doc_texts(docs, "entity_relation"),
        "hybrid_projection_text": build_doc_texts(docs, "hybrid"),
    }
    indexes = {name: build_inverted_index(texts) for name, texts in strategies.items()}
    rows: list[dict[str, Any]] = []
    for index, question in enumerate(questions, start=1):
        if index == 1 or index % 10 == 0:
            LOGGER.info("probing question %s/%s %s", index, len(questions), question.question_id)
        query_parts = query_projection_texts(question.question)
        row: dict[str, Any] = {
            "question_id": question.question_id,
            "question": question.question,
            "expected_doc_ids": question.expected_doc_ids,
            "source_types": question.source_types,
            "strategy_results": {},
        }
        for strategy_name, inverted in indexes.items():
            if strategy_name == "summary_only_text":
                query_texts = [question.question]
            elif strategy_name == "baseline_metadata_text":
                query_texts = [question.question]
            elif strategy_name == "entity_constraint_projection":
                query_texts = query_parts["entity_constraint"]
            elif strategy_name == "entity_relation_projection":
                query_texts = query_parts["entity_relation"]
            else:
                query_texts = [question.question, *query_parts["entity_relation"]]
            ranked = score_query(inverted, query_texts, limit=candidate_limit)
            ranked_ids = [doc_id for doc_id, _score in ranked]
            row["strategy_results"][strategy_name] = strategy_result(question.expected_doc_ids, ranked_ids)
        rows.append(row)
    summary = {
        strategy_name: summarize_strategy(rows, strategy_name)
        for strategy_name in strategies
    }
    return {
        "summary": summary,
        "rows": rows,
        "doc_universe": {
            "documents": len(docs),
            "expected_documents_present": sum(doc.is_expected for doc in docs),
            "source_counts": dict(collections.Counter(doc.source_type for doc in docs).most_common()),
        },
    }


def build_doc_texts(docs: list[Document], mode: str) -> dict[str, str]:
    texts: dict[str, str] = {}
    for index, doc in enumerate(docs, start=1):
        if index == 1 or index % 5000 == 0:
            LOGGER.info("building %s projection %s/%s", mode, index, len(docs))
        preview = compact(doc.content, 2400)
        summary = f"{doc.title}\n{compact(doc.content, 500)}"
        entities = extract_entities(f"{doc.title}\n{preview}")
        constraints = extract_constraints(f"{doc.title}\n{preview}")
        relations = extract_relations(f"{doc.title}. {preview}", entities=entities, constraints=constraints)
        if mode == "summary":
            text = summary
        elif mode == "metadata":
            text = f"{doc.source_type}\n{doc.title}\n{summary}"
        elif mode == "entity_constraint":
            text = "\n".join([doc.title, *entities, *constraints])
        elif mode == "entity_relation":
            text = "\n".join([doc.title, *entities, *constraints, *relations])
        elif mode == "hybrid":
            text = "\n".join([doc.source_type, summary, *entities, *constraints, *relations])
        else:
            raise ValueError(f"unknown mode: {mode}")
        texts[doc.doc_id] = text
    return texts


def query_projection_texts(question: str) -> dict[str, list[str]]:
    entities = extract_entities(question)
    constraints = extract_constraints(question)
    relations = extract_relations(question, entities=entities, constraints=constraints)
    entity_constraint = [question]
    if entities or constraints:
        entity_constraint.append(" ".join([*entities, *constraints]))
    entity_relation = [question]
    if entities or constraints or relations:
        entity_relation.append(" ".join([*entities, *constraints, *relations]))
    return {
        "entity_constraint": entity_constraint,
        "entity_relation": entity_relation,
    }


def build_inverted_index(texts: dict[str, str]) -> dict[str, Any]:
    postings: dict[str, dict[str, int]] = collections.defaultdict(dict)
    doc_lengths: dict[str, int] = {}
    for doc_id, text in texts.items():
        counts = collections.Counter(tokens(text))
        doc_lengths[doc_id] = sum(counts.values()) or 1
        for token, count in counts.items():
            postings[token][doc_id] = count
    total_docs = max(len(texts), 1)
    idf = {
        token: math.log((total_docs + 1) / (len(doc_postings) + 1)) + 1.0
        for token, doc_postings in postings.items()
    }
    avg_len = sum(doc_lengths.values()) / max(len(doc_lengths), 1)
    return {
        "postings": postings,
        "idf": idf,
        "doc_lengths": doc_lengths,
        "avg_len": avg_len,
        "total_docs": total_docs,
    }


def score_query(index: dict[str, Any], query_texts: list[str], *, limit: int) -> list[tuple[str, float]]:
    query_counts = collections.Counter()
    for text in query_texts:
        query_counts.update(tokens(text))
    scores: dict[str, float] = collections.defaultdict(float)
    postings = index["postings"]
    idf = index["idf"]
    doc_lengths = index["doc_lengths"]
    avg_len = index["avg_len"]
    k1 = 1.2
    b = 0.75
    for token, query_count in query_counts.items():
        token_postings = postings.get(token)
        if not token_postings:
            continue
        token_idf = idf[token] * (1.0 + 0.15 * min(query_count, 3))
        for doc_id, tf in token_postings.items():
            length = doc_lengths[doc_id]
            denom = tf + k1 * (1.0 - b + b * length / avg_len)
            scores[doc_id] += token_idf * (tf * (k1 + 1.0)) / denom
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]


def strategy_result(expected_doc_ids: list[str], ranked_ids: list[str]) -> dict[str, Any]:
    expected = set(expected_doc_ids)
    ranks = [rank for rank, doc_id in enumerate(ranked_ids, start=1) if doc_id in expected]
    return {
        "hit_at_10": bool(ranks and min(ranks) <= 10),
        "hit_at_20": bool(ranks and min(ranks) <= 20),
        "hit_at_50": bool(ranks and min(ranks) <= 50),
        "hit_at_100": bool(ranks and min(ranks) <= 100),
        "best_rank": min(ranks) if ranks else None,
        "top_doc_ids": ranked_ids[:10],
    }


def summarize_strategy(rows: list[dict[str, Any]], strategy_name: str) -> dict[str, Any]:
    answerable = [row for row in rows if row["expected_doc_ids"]]
    total = len(answerable) or 1
    results = [row["strategy_results"][strategy_name] for row in answerable]
    reciprocal_ranks = [1.0 / result["best_rank"] for result in results if result["best_rank"]]
    return {
        "questions": len(answerable),
        "hit@10": round(sum(result["hit_at_10"] for result in results) / total, 4),
        "hit@20": round(sum(result["hit_at_20"] for result in results) / total, 4),
        "hit@50": round(sum(result["hit_at_50"] for result in results) / total, 4),
        "hit@100": round(sum(result["hit_at_100"] for result in results) / total, 4),
        "mrr": round(sum(reciprocal_ranks) / total, 4),
        "misses": [
            row["question_id"]
            for row in answerable
            if not row["strategy_results"][strategy_name]["hit_at_100"]
        ],
    }


def tokens(text: str) -> list[str]:
    values = []
    for match in TOKEN_RE.finditer(text.lower()):
        token = match.group(0).strip("._-:/")
        if len(token) < 2 or token in STOPWORDS:
            continue
        values.append(token)
    return values


def extract_entities(text: str, *, limit: int = 32) -> list[str]:
    seen = set()
    values: list[str] = []
    for match in ENTITY_HINT_RE.finditer(text):
        value = next((item for item in match.groups() if item), None) or match.group(0)
        value = normalize_phrase(value)
        if not value or value.lower() in STOPWORDS or value.lower() in seen:
            continue
        seen.add(value.lower())
        values.append(value)
        if len(values) >= limit:
            break
    for token in tokens(text):
        if any(char.isdigit() for char in token) or "_" in token or "-" in token or "/" in token:
            if token not in seen:
                seen.add(token)
                values.append(token)
        if len(values) >= limit:
            break
    return values


def extract_constraints(text: str, *, limit: int = 32) -> list[str]:
    seen = set()
    values: list[str] = []
    for match in CONSTRAINT_RE.finditer(text):
        value = normalize_phrase(match.group(0))
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        values.append(value)
        if len(values) >= limit:
            break
    return values


def extract_relations(text: str, *, entities: list[str], constraints: list[str], limit: int = 24) -> list[str]:
    hints = [item.lower() for item in [*entities[:12], *constraints[:12]] if len(item) > 2]
    values: list[str] = []
    seen = set()
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        normalized = normalize_phrase(sentence)
        if len(normalized) < 20 or len(normalized) > 260:
            continue
        sentence_tokens = set(tokens(normalized))
        has_verb = bool(sentence_tokens & RELATION_VERBS)
        has_hint = any(hint in normalized.lower() for hint in hints)
        if not has_verb and not has_hint:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        values.append(normalized)
        if len(values) >= limit:
            break
    return values


def normalize_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" \t\r\n,.;:()[]{}\"'")).strip()


def compact(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0]


def render_summary(result: dict[str, Any]) -> str:
    lines = [
        "# Entity Relation Projection Preflight",
        "",
        "This is a cheap offline recall preflight. It uses lexical BM25-style scoring",
        "over projection text, not embeddings. The purpose is to verify whether the",
        "entity/relation projection direction has signal before spending API tokens",
        "on projection embeddings.",
        "",
        "## Config",
        "",
        "```json",
        json.dumps(result["config"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Document Universe",
        "",
        "```json",
        json.dumps(result["doc_universe"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Summary",
        "",
        "| strategy | hit@10 | hit@20 | hit@50 | hit@100 | MRR | misses@100 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, row in result["summary"].items():
        lines.append(
            f"| `{strategy}` | {row['hit@10']:.4f} | {row['hit@20']:.4f} | "
            f"{row['hit@50']:.4f} | {row['hit@100']:.4f} | {row['mrr']:.4f} | "
            f"{len(row['misses'])} |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
