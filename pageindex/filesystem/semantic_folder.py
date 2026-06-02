from __future__ import annotations

import json
import os
import re
import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol


CANDIDATE_FIELDS = ("domain", "topic")
MEMBERSHIP_LIMIT = 3
SEGMENT_MAX_CHARS = 127
SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")
PARTIAL_PATH_POLICY_TEMPLATE_PREFIX = "template_prefix"
PARTIAL_PATH_POLICIES = (PARTIAL_PATH_POLICY_TEMPLATE_PREFIX,)
CONTRACT_SCHEMA_VERSION = 1
LEDGER_SCHEMA_VERSION = 1


class SemanticFolderPlanError(ValueError):
    pass


@dataclass(frozen=True)
class SemanticFolderBuildItem:
    item_id: str
    title: str
    summary: str
    domain: Any = None
    topic: Any = None
    build_item_id: str = ""
    file_ref: str = ""


@dataclass(frozen=True)
class SemanticFolderCanonicalValue:
    canonical_id: str
    field: str
    display: str
    slug: str
    lifecycle: str = "accepted"
    origin: str = "current_build"


@dataclass(frozen=True)
class SemanticFolderMembership:
    item_id: str
    file_ref: str
    relative_path: str
    confidence: float | None = None
    canonical_segments: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SemanticFolderValidatedPlan:
    template: list[str]
    partial_path_policy: str
    canonical_values: list[dict[str, str]]
    memberships: list[SemanticFolderMembership]
    skipped: list[dict[str, str]]
    raw_plan: dict[str, Any]
    finalized: bool = False


class SemanticFolderPlanner(Protocol):
    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class OpenAISemanticFolderPlanner:
    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        request_timeout: float | None = None,
    ):
        self.model = (
            model
            or os.environ.get("PIFS_SEMANTIC_FOLDER_MODEL")
            or os.environ.get("PIFS_METADATA_MODEL")
            or "gpt-5-nano"
        )
        self.base_url = (
            base_url
            if base_url is not None
            else os.environ.get("PIFS_METADATA_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        )
        timeout_value = (
            request_timeout
            if request_timeout is not None
            else os.environ.get("PIFS_SEMANTIC_FOLDER_TIMEOUT")
        )
        self.request_timeout = float(timeout_value) if timeout_value is not None else 240.0

    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = (
            os.environ.get("PIFS_SEMANTIC_FOLDER_API_KEY")
            or os.environ.get("PIFS_METADATA_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if not api_key:
            raise SemanticFolderPlanError(
                "PIFS_SEMANTIC_FOLDER_API_KEY, PIFS_METADATA_API_KEY, or OPENAI_API_KEY "
                "is required for PIFS Semantic Folder planning"
            )

        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=self.base_url or None,
            timeout=self.request_timeout,
        )
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Plan a PIFS Semantic Folder from document-level metadata. "
                        "Use only the provided transient item ids, title, summary, domain, and topic. "
                        "Do not infer from storage paths or original folders. "
                        "Choose a useful navigation template using domain and topic metadata. "
                        "Treat observed cardinality as a navigation signal, not as a rejection rule. "
                        "If raw topic values are mostly unique, first try to canonicalize them into "
                        "short broad topic categories that would help users navigate the corpus. "
                        "Choose a domain-only template only if no useful, stable topic categories emerge. "
                        "All membership paths must be relative field/value segments with no leading "
                        "slash. Paths must match the selected template order: ['topic'] paths look like "
                        "topic/<topic-slug>, ['topic', 'domain'] paths look like "
                        "topic/<topic-slug>/domain/<domain-slug>, and ['domain', 'topic'] paths look "
                        "like domain/<domain-slug>/topic/<topic-slug>. Never emit a standalone "
                        "topic/... root when the selected template starts with domain. "
                        "Slugs must be path-safe and at most 127 characters. Canonicalize broad "
                        "display values and short slugs; do not use unknown/misc placeholders. Reduce "
                        "each document to at most three semantic memberships. Every input item must "
                        "appear in memberships or skipped; prefer at least one useful membership for "
                        "each item unless its selected first field is missing or no useful semantic "
                        "placement exists. Set partial_path_policy to template_prefix. For the "
                        "registry_finalization planning_stage, set finalized to true only after the "
                        "active registry has no pending references and the lifecycle ledger is closed. "
                        "For earlier stages, set finalized to false. If retry feedback is present, "
                        "regenerate a fully valid plan instead of explaining the error. "
                        "Return strict JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            response_format=self._response_format(),
        )
        return json.loads(response.choices[0].message.content or "{}")

    @staticmethod
    def _response_format() -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "pifs_semantic_folder_plan",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "template",
                        "partial_path_policy",
                        "canonical_values",
                        "memberships",
                        "skipped",
                        "finalized",
                    ],
                    "properties": {
                        "template": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(CANDIDATE_FIELDS)},
                        },
                        "partial_path_policy": {
                            "type": "string",
                            "enum": list(PARTIAL_PATH_POLICIES),
                        },
                        "canonical_values": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["field", "display", "slug"],
                                "properties": {
                                    "field": {"type": "string", "enum": list(CANDIDATE_FIELDS)},
                                    "display": {"type": "string"},
                                    "slug": {"type": "string"},
                                },
                            },
                        },
                        "memberships": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["item_id", "paths", "confidence"],
                                "properties": {
                                    "item_id": {"type": "string"},
                                    "paths": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "confidence": {"type": ["number", "null"]},
                                },
                            },
                        },
                        "skipped": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["item_id", "reason"],
                                "properties": {
                                    "item_id": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                            },
                        },
                        "finalized": {"type": "boolean"},
                    },
                },
            },
        }


def semantic_mount_path(source_scope: str) -> str:
    source_scope = _normalize_path(source_scope)
    return "/semantic" if source_scope == "/" else f"{source_scope}/semantic"


def validate_semantic_folder_plan(
    plan: dict[str, Any],
    *,
    item_file_refs: dict[str, str],
    expected_template: list[str] | None = None,
    require_memberships: bool = True,
    require_finalized: bool = False,
) -> SemanticFolderValidatedPlan:
    if not isinstance(plan, dict):
        raise SemanticFolderPlanError("Semantic Folder planner returned a non-object plan")
    template = _validate_template(plan.get("template"))
    if expected_template is not None and template != expected_template:
        raise SemanticFolderPlanError(
            "Semantic Folder template alignment failed: "
            f"expected {'/'.join(expected_template)}, got {'/'.join(template)}"
        )
    partial_path_policy = _validate_partial_path_policy(plan.get("partial_path_policy"))
    canonical_values = _validate_canonical_values(plan.get("canonical_values"))
    canonical_lookup = {
        (item["field"], item["slug"]): item for item in canonical_values
    }
    memberships: list[SemanticFolderMembership] = []
    seen_item_paths: set[tuple[str, str]] = set()
    per_item_count: dict[str, int] = {}
    for item in _required_list(plan.get("memberships"), "memberships"):
        if not isinstance(item, dict):
            raise SemanticFolderPlanError("Semantic Folder membership entries must be objects")
        item_id = str(item.get("item_id") or "").strip()
        if item_id not in item_file_refs:
            raise SemanticFolderPlanError(f"Unknown Semantic Folder build item: {item_id}")
        paths = item.get("paths")
        if not isinstance(paths, list):
            raise SemanticFolderPlanError(f"Semantic Folder membership {item_id} paths must be a list")
        confidence = _optional_float(item.get("confidence"))
        for raw_path in paths:
            relative_path, canonical_segments = _validate_membership_path(
                raw_path,
                template=template,
                partial_path_policy=partial_path_policy,
                canonical_lookup=canonical_lookup,
            )
            key = (item_id, relative_path)
            if key in seen_item_paths:
                raise SemanticFolderPlanError(
                    f"Duplicate Semantic Folder membership for {item_id}: {relative_path}"
                )
            seen_item_paths.add(key)
            per_item_count[item_id] = per_item_count.get(item_id, 0) + 1
            if per_item_count[item_id] > MEMBERSHIP_LIMIT:
                raise SemanticFolderPlanError(
                    f"Semantic Folder membership limit exceeded for {item_id}: "
                    f"max {MEMBERSHIP_LIMIT}"
                )
            memberships.append(
                SemanticFolderMembership(
                    item_id=item_id,
                    file_ref=item_file_refs[item_id],
                    relative_path=relative_path,
                    confidence=confidence,
                    canonical_segments=canonical_segments,
                )
            )
    skipped = _validate_skipped(plan.get("skipped"), item_file_refs)
    planned_item_ids = {membership.item_id for membership in memberships}
    skipped_item_ids = {item["item_id"] for item in skipped}
    overlap_item_ids = sorted(planned_item_ids & skipped_item_ids)
    if overlap_item_ids:
        preview = ", ".join(overlap_item_ids[:10])
        if len(overlap_item_ids) > 10:
            preview += f", ... {len(overlap_item_ids) - 10} more"
        raise SemanticFolderPlanError(
            f"Semantic Folder plan cannot both place and skip item(s): {preview}"
        )
    planned_or_skipped = planned_item_ids | skipped_item_ids
    missing_item_ids = sorted(set(item_file_refs) - planned_or_skipped)
    if missing_item_ids:
        preview = ", ".join(missing_item_ids[:10])
        if len(missing_item_ids) > 10:
            preview += f", ... {len(missing_item_ids) - 10} more"
        raise SemanticFolderPlanError(
            "Semantic Folder plan omitted build item(s); each item must be placed "
            f"or explicitly skipped: {preview}"
        )
    finalized = bool(plan.get("finalized") is True)
    if require_finalized and not finalized:
        raise SemanticFolderPlanError(
            "Semantic Folder registry finalization must explicitly return finalized=true"
        )
    if require_memberships and not memberships:
        raise SemanticFolderPlanError("No useful Semantic Folder hierarchy was planned")
    return SemanticFolderValidatedPlan(
        template=template,
        partial_path_policy=partial_path_policy,
        canonical_values=canonical_values,
        memberships=memberships,
        skipped=skipped,
        raw_plan=plan,
        finalized=finalized,
    )


def _validate_template(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise SemanticFolderPlanError("Semantic Folder plan template must select at least one field")
    template: list[str] = []
    for field in value:
        field = str(field)
        if field not in CANDIDATE_FIELDS:
            raise SemanticFolderPlanError(f"Unsupported Semantic Folder field: {field}")
        if field in template:
            raise SemanticFolderPlanError(f"Duplicate Semantic Folder template field: {field}")
        template.append(field)
    return template


def _validate_partial_path_policy(value: Any) -> str:
    if value is None:
        return PARTIAL_PATH_POLICY_TEMPLATE_PREFIX
    policy = str(value or "").strip()
    if policy not in PARTIAL_PATH_POLICIES:
        raise SemanticFolderPlanError(
            f"Unsupported Semantic Folder partial path policy: {policy}"
        )
    return policy


def _validate_canonical_values(value: Any) -> list[dict[str, str]]:
    rows = _required_list(value, "canonical_values")
    seen_slug: dict[tuple[str, str], str] = {}
    canonical_values: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise SemanticFolderPlanError("Semantic Folder canonical values must be objects")
        field = str(row.get("field") or "").strip()
        display = str(row.get("display") or "").strip()
        slug = str(row.get("slug") or "").strip()
        lifecycle = str(row.get("lifecycle") or "accepted").strip()
        if field not in CANDIDATE_FIELDS:
            raise SemanticFolderPlanError(f"Unsupported Semantic Folder canonical field: {field}")
        if lifecycle != "accepted":
            raise SemanticFolderPlanError(
                "Semantic Folder final registry cannot expose inactive canonical values: "
                f"{field}/{slug or '<missing>'} is {lifecycle}"
            )
        if not display:
            raise SemanticFolderPlanError("Semantic Folder canonical display value is required")
        _validate_segment(slug, label=f"{field} slug")
        key = (field, slug)
        previous = seen_slug.get(key)
        if previous is not None and previous != display:
            raise SemanticFolderPlanError(
                f"Semantic Folder segment collision for {field}/{slug}: "
                f"{previous!r} and {display!r}"
            )
        if previous is None:
            seen_slug[key] = display
            canonical_values.append({"field": field, "display": display, "slug": slug})
    return [
        {
            **row,
            "canonical_id": f"can_{index:04d}",
            "lifecycle": "accepted",
            "origin": "current_build",
        }
        for index, row in enumerate(canonical_values, 1)
    ]


def _validate_membership_path(
    value: Any,
    *,
    template: list[str],
    partial_path_policy: str,
    canonical_lookup: dict[tuple[str, str], dict[str, str]],
) -> tuple[str, list[dict[str, str]]]:
    raw_path = str(value or "").strip()
    if not raw_path:
        raise SemanticFolderPlanError("Semantic Folder membership path is required")
    if raw_path.startswith("/"):
        raise SemanticFolderPlanError(f"Semantic Folder membership path must be relative: {raw_path}")
    parts = raw_path.split("/")
    if len(parts) % 2:
        raise SemanticFolderPlanError(
            f"Semantic Folder membership path must use field/value segments: {raw_path}"
        )
    canonical_segments: list[dict[str, str]] = []
    fields = parts[0::2]
    values = parts[1::2]
    if partial_path_policy != PARTIAL_PATH_POLICY_TEMPLATE_PREFIX:
        raise SemanticFolderPlanError(
            f"Unsupported Semantic Folder partial path policy: {partial_path_policy}"
        )
    if fields != template[: len(fields)]:
        raise SemanticFolderPlanError(
            f"Semantic Folder membership path does not match selected template: {raw_path}"
        )
    for field, slug in zip(fields, values):
        _validate_segment(field, label="field segment")
        _validate_segment(slug, label=f"{field} value segment")
        if field not in CANDIDATE_FIELDS:
            raise SemanticFolderPlanError(f"Unsupported Semantic Folder field segment: {field}")
        canonical = canonical_lookup.get((field, slug))
        if canonical is None:
            raise SemanticFolderPlanError(
                f"Semantic Folder path uses undeclared canonical value: {field}/{slug}"
            )
        canonical_segments.append(
            {
                "field": canonical["field"],
                "canonical_id": canonical["canonical_id"],
                "display": canonical["display"],
                "slug": canonical["slug"],
            }
        )
    return "/".join(parts), canonical_segments


def _validate_segment(segment: str, *, label: str) -> None:
    if not segment or segment in {".", ".."}:
        raise SemanticFolderPlanError(f"Unsafe Semantic Folder {label}: {segment!r}")
    if "/" in segment or "\\" in segment or "=" in segment:
        raise SemanticFolderPlanError(f"Unsafe Semantic Folder {label}: {segment!r}")
    if segment.lower() in {"unknown", "misc", "uncategorized"}:
        raise SemanticFolderPlanError(
            f"Semantic Folder plan must skip missing values instead of using {segment!r}"
        )
    if not SEGMENT_RE.fullmatch(segment):
        raise SemanticFolderPlanError(f"Unsafe Semantic Folder {label}: {segment!r}")


def _validate_skipped(value: Any, item_file_refs: dict[str, str]) -> list[dict[str, str]]:
    skipped: list[dict[str, str]] = []
    for row in _required_list(value, "skipped"):
        if not isinstance(row, dict):
            raise SemanticFolderPlanError("Semantic Folder skipped entries must be objects")
        item_id = str(row.get("item_id") or "").strip()
        if item_id not in item_file_refs:
            raise SemanticFolderPlanError(f"Unknown skipped Semantic Folder build item: {item_id}")
        reason = str(row.get("reason") or "").strip() or "skipped"
        skipped.append({"item_id": item_id, "reason": reason})
    return skipped


def _required_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise SemanticFolderPlanError(f"Semantic Folder plan {name} must be a list")
    return value


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SemanticFolderPlanError("Semantic Folder confidence must be numeric") from exc


def _normalize_path(path: str) -> str:
    parts = [part for part in str(path or "/").replace("\\", "/").split("/") if part and part != "."]
    return "/" + "/".join(parts) if parts else "/"


def build_finalized_semantic_folder_contract(
    *,
    source_scope: str,
    mount_path: str,
    template: list[str],
    partial_path_policy: str,
    canonical_values: list[dict[str, Any]],
    memberships: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    source_snapshot: list[dict[str, Any]],
) -> dict[str, Any]:
    active_registry = [
        {
            "canonical_id": str(row["canonical_id"]),
            "field": str(row["field"]),
            "display": str(row["display"]),
            "slug": str(row["slug"]),
            "lifecycle": "accepted",
            "origin": "current_build",
        }
        for row in canonical_values
    ]
    final_memberships = [
        {
            "build_item_id": str(row["build_item_id"]),
            "item_id": str(row["item_id"]),
            "segments": [
                {
                    "field": str(segment["field"]),
                    "canonical_id": str(segment["canonical_id"]),
                }
                for segment in row.get("canonical_segments") or []
            ],
            "relative_path_snapshot": str(row["relative_path"]),
        }
        for row in memberships
    ]
    final_memberships.sort(
        key=lambda row: (row["build_item_id"], row["relative_path_snapshot"])
    )
    skipped_rows = [
        {
            "build_item_id": str(row.get("build_item_id") or ""),
            "item_id": str(row["item_id"]),
            "reason": str(row.get("reason") or "skipped"),
        }
        for row in skipped
    ]
    skipped_rows.sort(key=lambda row: (row["build_item_id"], row["item_id"]))
    snapshot_rows = [
        {
            "build_item_id": str(row["build_item_id"]),
            "item_id": str(row["item_id"]),
            "file_ref": str(row["file_ref"]),
            "title": str(row.get("title") or ""),
        }
        for row in source_snapshot
    ]
    snapshot_rows.sort(key=lambda row: row["build_item_id"])
    lifecycle_ledger = {
        "active_registry_finalized": True,
        "pending_refs": [],
        "accepted_canonical_ids": [
            row["canonical_id"] for row in sorted(active_registry, key=lambda item: item["canonical_id"])
        ],
        "closed": True,
    }
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "source_scope": _normalize_path(source_scope),
        "mount_path": _normalize_path(mount_path),
        "template": list(template),
        "partial_path_policy": partial_path_policy,
        "active_registry": active_registry,
        "final_memberships": final_memberships,
        "skipped": skipped_rows,
        "source_snapshot": snapshot_rows,
        "lifecycle_ledger": lifecycle_ledger,
    }


def semantic_folder_contract_hash(contract: dict[str, Any]) -> str:
    payload = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()
