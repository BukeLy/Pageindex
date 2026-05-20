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

SOURCE_HINTS = {
    "github": {
        "github",
        "pull request",
        "pr",
        "merge",
        "reviewer",
        "runtime",
        "api",
        "sdk",
    },
    "gmail": {"email", "thread", "inbox", "message", "vendor", "contract"},
    "slack": {"slack", "channel", "security team", "incident channel", "thread"},
    "jira": {"jira", "ticket", "issue", "sprint", "epic", "bug"},
    "linear": {"linear", "issue", "ticket", "roadmap", "engineering task"},
    "confluence": {"confluence", "policy", "runbook", "kb", "knowledge base", "page"},
    "google_drive": {"google drive", "doc", "document", "deck", "spreadsheet", "report"},
    "fireflies": {"meeting", "call", "transcript", "handoff", "standup"},
    "hubspot": {"hubspot", "customer", "account", "deal", "sales", "founder"},
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
    profile: dict[str, Any]


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
                profile=profile,
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
            "question_type": question.question_type,
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

        extra_rankings = advanced_strategy_rankings(
            question,
            docs=docs,
            field_texts=strategies,
            indexes=indexes,
            query_parts=query_parts,
            limit=candidate_limit,
        )
        for strategy_name, ranked_ids in extra_rankings.items():
            row["strategy_results"][strategy_name] = strategy_result(question.expected_doc_ids, ranked_ids)
        rows.append(row)
    summary = {
        strategy_name: summarize_strategy(rows, strategy_name)
        for strategy_name in rows[0]["strategy_results"]
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


def advanced_strategy_rankings(
    question: Question,
    *,
    docs: list[Document],
    field_texts: dict[str, dict[str, str]],
    indexes: dict[str, dict[str, Any]],
    query_parts: dict[str, list[str]],
    limit: int,
) -> dict[str, list[str]]:
    question_text = question.question
    entity_constraint_query = " ".join(query_parts.get("entity_constraint") or [])
    entity_relation_query = " ".join(query_parts.get("entity_relation") or [])
    source_hints = classify_source_hints(question_text)
    return {
        "rrf_summary_metadata_relation": rrf_fuse_rankings(
            [
                ranked_ids(score_query(indexes["summary_only_text"], [question_text], limit=limit)),
                ranked_ids(score_query(indexes["baseline_metadata_text"], [question_text], limit=limit)),
                ranked_ids(score_query(indexes["entity_relation_projection"], query_parts["entity_relation"], limit=limit)),
            ],
            limit=limit,
        ),
        "multi_probe_rrf": rrf_fuse_rankings(
            multi_probe_rankings(question_text, indexes=indexes, query_parts=query_parts, limit=limit),
            limit=limit,
        ),
        "pseudo_late_interaction": pseudo_late_interaction_rank(
            indexes=indexes,
            question_text=question_text,
            entity_constraint_query=entity_constraint_query,
            entity_relation_query=entity_relation_query,
            limit=limit,
        ),
        "constraint_exact_boost": constraint_exact_boost_rank(
            base_index=indexes["hybrid_projection_text"],
            field_text=field_texts["hybrid_projection_text"],
            question_text=question_text,
            query_parts=query_parts,
            limit=limit,
        ),
        "source_hint_boost": source_hint_boost_rank(
            docs=docs,
            base_index=indexes["hybrid_projection_text"],
            question_text=question_text,
            source_hints=source_hints,
            limit=limit,
        ),
        "prf_metadata_expansion": pseudo_relevance_feedback_rank(
            indexes=indexes,
            field_texts=field_texts,
            question_text=question_text,
            limit=limit,
        ),
        "hybrid_rrf_exact_source": hybrid_rrf_exact_source_rank(
            docs=docs,
            field_texts=field_texts,
            indexes=indexes,
            question_text=question_text,
            query_parts=query_parts,
            source_hints=source_hints,
            limit=limit,
        ),
    }


def ranked_ids(ranked: list[tuple[str, float]]) -> list[str]:
    return [doc_id for doc_id, _score in ranked]


def multi_probe_rankings(
    question_text: str,
    *,
    indexes: dict[str, dict[str, Any]],
    query_parts: dict[str, list[str]],
    limit: int,
) -> list[list[str]]:
    probes = [
        [question_text],
        query_parts.get("entity_constraint") or [question_text],
        query_parts.get("entity_relation") or [question_text],
    ]
    fields = [
        "summary_only_text",
        "baseline_metadata_text",
        "entity_relation_projection",
        "hybrid_projection_text",
    ]
    rankings = []
    for field in fields:
        for probe in probes:
            rankings.append(ranked_ids(score_query(indexes[field], probe, limit=limit)))
    return rankings


def rrf_fuse_rankings(
    rankings: list[list[str]],
    *,
    limit: int,
    k: int = 60,
    weights: list[float] | None = None,
) -> list[str]:
    scores: dict[str, float] = collections.defaultdict(float)
    for ranking_index, ranking in enumerate(rankings):
        weight = weights[ranking_index] if weights and ranking_index < len(weights) else 1.0
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += weight / (k + rank)
    return [doc_id for doc_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def pseudo_late_interaction_rank(
    *,
    indexes: dict[str, dict[str, Any]],
    question_text: str,
    entity_constraint_query: str,
    entity_relation_query: str,
    limit: int,
) -> list[str]:
    channels = [
        ("summary_only_text", [question_text], 0.7),
        ("baseline_metadata_text", [question_text], 1.0),
        ("entity_constraint_projection", [entity_constraint_query or question_text], 0.85),
        ("entity_relation_projection", [entity_relation_query or question_text], 1.2),
        ("hybrid_projection_text", [question_text, entity_relation_query], 1.0),
    ]
    scores: dict[str, float] = collections.defaultdict(float)
    for field, query_texts, weight in channels:
        raw_scores = score_query_scores(indexes[field], query_texts)
        max_score = max(raw_scores.values(), default=1.0) or 1.0
        for doc_id, score in raw_scores.items():
            scores[doc_id] += weight * (score / max_score)
    return [doc_id for doc_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def constraint_exact_boost_rank(
    *,
    base_index: dict[str, Any],
    field_text: dict[str, str],
    question_text: str,
    query_parts: dict[str, list[str]],
    limit: int,
) -> list[str]:
    scores = normalized_scores(score_query_scores(base_index, [question_text, *query_parts.get("entity_relation", [])]))
    exact_terms = exact_recall_terms(question_text, query_parts=query_parts)
    for doc_id, text in field_text.items():
        overlap = exact_overlap_score(text, exact_terms)
        if overlap:
            scores[doc_id] = scores.get(doc_id, 0.0) + overlap
    return [doc_id for doc_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def source_hint_boost_rank(
    *,
    docs: list[Document],
    base_index: dict[str, Any],
    question_text: str,
    source_hints: set[str],
    limit: int,
) -> list[str]:
    scores = normalized_scores(score_query_scores(base_index, [question_text]))
    source_by_doc = {doc.doc_id: doc.source_type for doc in docs}
    for doc_id, source_type in source_by_doc.items():
        if source_type in source_hints:
            scores[doc_id] = scores.get(doc_id, 0.0) + 0.22
    return [doc_id for doc_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def pseudo_relevance_feedback_rank(
    *,
    indexes: dict[str, dict[str, Any]],
    field_texts: dict[str, dict[str, str]],
    question_text: str,
    limit: int,
) -> list[str]:
    seed_ids = ranked_ids(score_query(indexes["summary_only_text"], [question_text], limit=min(8, limit)))
    expansion = feedback_terms(
        seed_ids,
        field_text=field_texts["baseline_metadata_text"],
        original_query=question_text,
        limit=16,
    )
    expanded_query = " ".join([question_text, *expansion])
    return rrf_fuse_rankings(
        [
            ranked_ids(score_query(indexes["summary_only_text"], [question_text], limit=limit)),
            ranked_ids(score_query(indexes["baseline_metadata_text"], [expanded_query], limit=limit)),
            ranked_ids(score_query(indexes["hybrid_projection_text"], [expanded_query], limit=limit)),
        ],
        limit=limit,
        weights=[0.8, 1.0, 1.1],
    )


def hybrid_rrf_exact_source_rank(
    *,
    docs: list[Document],
    field_texts: dict[str, dict[str, str]],
    indexes: dict[str, dict[str, Any]],
    question_text: str,
    query_parts: dict[str, list[str]],
    source_hints: set[str],
    limit: int,
) -> list[str]:
    rrf_ids = rrf_fuse_rankings(
        [
            ranked_ids(score_query(indexes["summary_only_text"], [question_text], limit=limit)),
            ranked_ids(score_query(indexes["baseline_metadata_text"], [question_text], limit=limit)),
            ranked_ids(score_query(indexes["entity_relation_projection"], query_parts["entity_relation"], limit=limit)),
            ranked_ids(score_query(indexes["hybrid_projection_text"], [question_text, *query_parts["entity_relation"]], limit=limit)),
        ],
        limit=limit,
        weights=[0.7, 1.0, 1.1, 1.2],
    )
    source_by_doc = {doc.doc_id: doc.source_type for doc in docs}
    exact_terms = exact_recall_terms(question_text, query_parts=query_parts)
    scores: dict[str, float] = {}
    for rank, doc_id in enumerate(rrf_ids, start=1):
        score = 1.0 / rank
        score += exact_overlap_score(field_texts["hybrid_projection_text"].get(doc_id, ""), exact_terms)
        if source_by_doc.get(doc_id) in source_hints:
            score += 0.12
        scores[doc_id] = score
    return [doc_id for doc_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def feedback_terms(
    seed_ids: list[str],
    *,
    field_text: dict[str, str],
    original_query: str,
    limit: int,
) -> list[str]:
    query_tokens = set(tokens(original_query))
    counts: collections.Counter[str] = collections.Counter()
    for doc_id in seed_ids:
        counts.update(tokens(field_text.get(doc_id, "")))
    values = []
    for token, count in counts.most_common(80):
        if token in query_tokens or token in STOPWORDS or len(token) < 3:
            continue
        if count < 2 and not is_identifier_or_measurement(token):
            continue
        values.append(token)
        if len(values) >= limit:
            break
    return values


def exact_recall_terms(question_text: str, *, query_parts: dict[str, list[str]]) -> list[str]:
    values = []
    for item in [question_text, *(query_parts.get("entity_relation") or [])]:
        for token in tokens(item):
            if is_identifier_or_measurement(token) or token not in STOPWORDS and len(token) >= 5:
                values.append(token)
    return dedupe(values)[:48]


def exact_overlap_score(text: str, exact_terms: list[str]) -> float:
    if not exact_terms:
        return 0.0
    text_tokens = set(tokens(text))
    score = 0.0
    for term in exact_terms:
        if term in text_tokens:
            score += 0.045 if is_identifier_or_measurement(term) else 0.018
    return min(score, 0.45)


def is_identifier_or_measurement(token: str) -> bool:
    return any(char.isdigit() for char in token) or "_" in token or "-" in token or "/" in token or "." in token


def classify_source_hints(question_text: str) -> set[str]:
    lowered = question_text.lower()
    hints = set()
    for source_type, terms in SOURCE_HINTS.items():
        if any(term in lowered for term in terms):
            hints.add(source_type)
    return hints


def normalized_scores(scores: dict[str, float]) -> dict[str, float]:
    max_score = max(scores.values(), default=1.0) or 1.0
    return {doc_id: score / max_score for doc_id, score in scores.items()}


def build_doc_texts(docs: list[Document], mode: str) -> dict[str, str]:
    texts: dict[str, str] = {}
    for index, doc in enumerate(docs, start=1):
        if index == 1 or index % 5000 == 0:
            LOGGER.info("building %s projection %s/%s", mode, index, len(docs))
        summary = profile_summary_text(doc)
        metadata = profile_metadata_text(doc)
        entities = profile_entity_texts(doc)
        constraints = profile_constraint_texts(doc)
        relations = profile_relation_texts(doc)
        if mode == "summary":
            text = summary
        elif mode == "metadata":
            text = metadata
        elif mode == "entity_constraint":
            text = "\n".join([doc.title, *entities, *constraints])
        elif mode == "entity_relation":
            text = "\n".join([doc.title, *entities, *constraints, *relations])
        elif mode == "hybrid":
            text = "\n".join([metadata, *entities, *constraints, *relations])
        else:
            raise ValueError(f"unknown mode: {mode}")
        texts[doc.doc_id] = text
    return texts


def profile_summary_text(doc: Document) -> str:
    return "\n".join(
        value
        for value in [
            doc.title,
            str(doc.profile.get("semantic_summary") or ""),
        ]
        if value
    )


def profile_metadata_text(doc: Document) -> str:
    profile = doc.profile
    values: list[str] = [doc.source_type, doc.title]
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
            values.append(str(value))
    for key in ("secondary_topics", "search_terms", "folder_hints"):
        values.extend(str(item) for item in (profile.get(key) or []) if item)
    return "\n".join(values)


def profile_entity_texts(doc: Document) -> list[str]:
    values: list[str] = []
    for entity in doc.profile.get("entities") or []:
        if not isinstance(entity, dict):
            values.append(str(entity))
            continue
        row = [entity.get("name"), entity.get("type")]
        row.extend(entity.get("aliases") or [])
        values.append(" | ".join(str(item) for item in row if item))
    return values


def profile_relation_texts(doc: Document) -> list[str]:
    values: list[str] = []
    for relation in doc.profile.get("relations") or []:
        if not isinstance(relation, dict):
            values.append(str(relation))
            continue
        row = [relation.get("subject"), relation.get("relation"), relation.get("object")]
        row.extend(relation.get("evidence_terms") or [])
        values.append(" | ".join(str(item) for item in row if item))
    return values


def profile_constraint_texts(doc: Document) -> list[str]:
    text = "\n".join(
        [
            str(doc.profile.get("semantic_summary") or ""),
            str(doc.profile.get("action_or_event") or ""),
            "\n".join(str(item) for item in (doc.profile.get("search_terms") or [])),
            "\n".join(profile_relation_texts(doc)),
        ]
    )
    return extract_constraints(text)


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
    scores = score_query_scores(index, query_texts)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]


def score_query_scores(index: dict[str, Any], query_texts: list[str]) -> dict[str, float]:
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
    return dict(scores)


def strategy_result(expected_doc_ids: list[str], ranked_ids: list[str]) -> dict[str, Any]:
    expected = set(expected_doc_ids)
    ranks = [rank for rank, doc_id in enumerate(ranked_ids, start=1) if doc_id in expected]
    return {
        "hit_at_1": bool(ranks and min(ranks) <= 1),
        "hit_at_3": bool(ranks and min(ranks) <= 3),
        "hit_at_5": bool(ranks and min(ranks) <= 5),
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
        "hit@1": round(sum(result["hit_at_1"] for result in results) / total, 4),
        "hit@3": round(sum(result["hit_at_3"] for result in results) / total, 4),
        "hit@5": round(sum(result["hit_at_5"] for result in results) / total, 4),
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


def dedupe(values: Iterable[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


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
        "| strategy | hit@1 | hit@3 | hit@5 | hit@10 | hit@20 | hit@50 | hit@100 | MRR | misses@100 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, row in result["summary"].items():
        lines.append(
            f"| `{strategy}` | {row['hit@1']:.4f} | {row['hit@3']:.4f} | "
            f"{row['hit@5']:.4f} | {row['hit@10']:.4f} | {row['hit@20']:.4f} | "
            f"{row['hit@50']:.4f} | {row['hit@100']:.4f} | {row['mrr']:.4f} | "
            f"{len(row['misses'])} |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
