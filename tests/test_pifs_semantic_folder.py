from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest


class MetadataGenerator:
    def __init__(self, values_by_title: dict[str, dict[str, Any]]):
        self.values_by_title = values_by_title
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def generate(self, request, *, fields):
        self.calls.append((request.title, tuple(fields)))
        values = self.values_by_title[request.title]
        return {field: values[field] for field in fields}


class TitlePlanner:
    def __init__(self, paths_by_title: dict[str, list[str]], *, template=None):
        self.paths_by_title = paths_by_title
        self.template = template or ["domain", "topic"]
        self.payloads: list[dict[str, Any]] = []

    def plan(self, payload):
        self.payloads.append(payload)
        canonical_values = [
            {"field": "domain", "display": "Finance", "slug": "finance"},
            {"field": "domain", "display": "Technology", "slug": "technology"},
            {"field": "topic", "display": "Rates", "slug": "rates"},
            {"field": "topic", "display": "GPU Accelerators", "slug": "gpu-accelerators"},
            {"field": "topic", "display": "Credit", "slug": "credit"},
        ]
        memberships = []
        skipped = []
        for item in payload["items"]:
            paths = self.paths_by_title.get(item["title"], [])
            if paths:
                memberships.append({"item_id": item["item_id"], "paths": paths, "confidence": 0.91})
            else:
                skipped.append({"item_id": item["item_id"], "reason": "missing first field"})
        return {
            "template": self.template,
            "partial_path_policy": "template_prefix",
            "canonical_values": canonical_values,
            "memberships": memberships,
            "skipped": skipped,
            "finalized": payload.get("planning_stage") == "registry_finalization",
        }


class BadThenTopicPlanner:
    def __init__(self):
        self.payloads: list[dict[str, Any]] = []

    def plan(self, payload):
        self.payloads.append(payload)
        if len(self.payloads) == 1:
            first_item = payload["items"][0]
            return {
                "template": ["domain", "topic"],
                "canonical_values": [
                    {"field": "domain", "display": "Machine Learning", "slug": "machine-learning"},
                    {
                        "field": "topic",
                        "display": "Attention Residuals",
                        "slug": "attention-residuals-attnres-block-attnres-deep-transformer-residuals-memory-communication-cross-stage-caching",
                    },
                    {
                        "field": "topic",
                        "display": "Earth Movers Distance Similarity Search",
                        "slug": (
                            "earth-movers-distance-similarity-search-min-cost-flow-scalable-knn-"
                            "filter-and-refinement-progressive-bounding-dynamic-refinement-ordering-sia"
                        ),
                    },
                ],
                "memberships": [
                    {
                        "item_id": first_item["item_id"],
                        "paths": [
                            "/domain/machine-learning",
                            "topic/attention-residuals-attnres-block-attnres-deep-transformer-residuals-memory-communication-cross-stage-caching",
                        ],
                        "confidence": 0.9,
                    }
                ],
                "skipped": [],
            }

        assert payload["aggregate"]["fields"]["topic"]["high_cardinality"] is True
        assert "file_ref" not in json.dumps(payload)
        if payload.get("planning_stage") != "registry_finalization":
            assert payload["retry"]["validation_error"]
            assert payload["retry"]["invalid_plan"]["memberships"][0]["paths"][0].startswith("/")
        topics = ["earnings-results", "corporate-governance", "strategic-transactions"]
        return {
            "template": ["topic"],
            "partial_path_policy": "template_prefix",
            "canonical_values": [
                {"field": "topic", "display": "Earnings Results", "slug": "earnings-results"},
                {"field": "topic", "display": "Corporate Governance", "slug": "corporate-governance"},
                {"field": "topic", "display": "Strategic Transactions", "slug": "strategic-transactions"},
            ],
            "memberships": [
                {
                    "item_id": item["item_id"],
                    "paths": [f"topic/{topics[index % len(topics)]}"],
                    "confidence": 0.85,
                }
                for index, item in enumerate(payload["items"])
            ],
            "skipped": [],
            "finalized": payload.get("planning_stage") == "registry_finalization",
        }


class OmittingThenCompletePlanner:
    def __init__(self):
        self.payloads: list[dict[str, Any]] = []

    def plan(self, payload):
        self.payloads.append(payload)
        topics = ["earnings-results", "corporate-governance"]
        if len(self.payloads) == 1:
            first_item = payload["items"][0]
            return {
                "template": ["topic"],
                "partial_path_policy": "template_prefix",
                "canonical_values": [
                    {"field": "topic", "display": "Earnings Results", "slug": "earnings-results"},
                ],
                "memberships": [
                    {
                        "item_id": first_item["item_id"],
                        "paths": ["topic/earnings-results"],
                        "confidence": 0.82,
                    }
                ],
                "skipped": [],
                "finalized": False,
            }

        if payload.get("planning_stage") != "registry_finalization":
            assert "omitted build item" in payload["retry"]["validation_error"]
        return {
            "template": ["topic"],
            "partial_path_policy": "template_prefix",
            "canonical_values": [
                {"field": "topic", "display": "Earnings Results", "slug": "earnings-results"},
                {"field": "topic", "display": "Corporate Governance", "slug": "corporate-governance"},
            ],
            "memberships": [
                {
                    "item_id": item["item_id"],
                    "paths": [f"topic/{topics[index % len(topics)]}"],
                    "confidence": 0.86,
                }
                for index, item in enumerate(payload["items"])
            ],
            "skipped": [],
            "finalized": payload.get("planning_stage") == "registry_finalization",
        }


@dataclass
class Candidate:
    document_id: str
    score: float = 0.8
    snippet: str = ""
    sources: list[dict[str, Any]] | None = None


class BrowseBackend:
    semantic_tool_channels = ("summary",)

    def __init__(self, document_ids):
        self.document_ids = document_ids

    def available_channels(self):
        return ("summary",)

    def search_channel(self, channel, query, *, limit, filters=None):
        rows = []
        for document_id in self.document_ids:
            rows.append(Candidate(document_id=document_id, sources=[{"distance": 0.25}]))
        return rows[:limit]


def _filesystem(tmp_path, values_by_title=None):
    from pageindex.filesystem import PageIndexFileSystem

    return PageIndexFileSystem(
        tmp_path / "workspace",
        metadata_generator=MetadataGenerator(values_by_title or {}),
        summary_projection_index=False,
    )


def _register_generated_file(filesystem, title, *, folder="/documents", external_id=None):
    values = filesystem.metadata_generator.values_by_title
    values.setdefault(
        title,
        {
            "summary": f"Summary for {title}",
            "domain": "Finance",
            "topic": "Rates",
        },
    )
    return filesystem.register_file(
        storage_uri=f"file:///tmp/{title}.txt",
        folder_path=folder,
        external_id=external_id or title.lower().replace(" ", "_"),
        title=title,
        content=f"{title} evidence about rates and GPUs.",
        content_type="text/plain",
        metadata_policy={
            "fields": {
                "summary": True,
                "doc_type": False,
                "domain": True,
                "topic": True,
                "entity": False,
                "relation": False,
            },
            "projection_indexes": {"summary": False},
            "batch": False,
        },
    )


def test_semantic_folder_build_materializes_scope_relative_mount_and_memberships(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Rates": {"summary": "Central bank rate summary", "domain": "Finance", "topic": "Rates"},
            "GPU": {"summary": "Accelerator summary", "domain": "Technology", "topic": "GPU Accelerators"},
        },
    )
    rates_ref = _register_generated_file(filesystem, "Rates", external_id="doc_rates")
    gpu_ref = _register_generated_file(filesystem, "GPU", folder="/documents/sec-filings", external_id="doc_gpu")
    planner = TitlePlanner(
        {
            "Rates": ["domain/finance/topic/rates"],
            "GPU": ["domain/technology/topic/gpu-accelerators"],
        }
    )

    result = filesystem.build_semantic_folder("/", planner=planner)
    finalized_contract_hash = result.pop("finalized_contract_hash")

    assert result == {
        "source": "/",
        "mount": "/semantic",
        "template": "domain/topic",
        "files": 2,
        "memberships": 2,
        "skipped": 0,
        "metadata_cached": 4,
        "metadata_generating": 0,
        "metadata_failed": 0,
        "metadata_deferred": 0,
        "planning": "generated",
        "publish_status": "published",
        "explicit_skips": 0,
        "alignment_failures": 0,
        "stale_items": 0,
        "drift_items": 0,
        "display_collision_groups": 0,
        "display_disambiguated_files": 0,
        "partial_success": False,
        "warnings": [],
        "planning_stages": 2,
        "materialization_chunks": 1,
    }
    assert finalized_contract_hash.startswith("sha256:")
    assert filesystem.store.resolve_file_ref("/semantic/domain/finance/topic/rates/Rates") == rates_ref
    assert (
        filesystem.store.resolve_file_ref(
            "/semantic/domain/technology/topic/gpu-accelerators/GPU"
        )
        == gpu_ref
    )
    assert filesystem.store.get_file(rates_ref).file_ref == rates_ref
    memberships = filesystem.store.folder_memberships(rates_ref)
    assert sorted(folder["path"] for folder in memberships) == [
        "/documents",
        "/semantic/domain/finance/topic/rates",
    ]

    payload_item = planner.payloads[0]["items"][0]
    assert set(payload_item) == {"item_id", "title", "summary", "domain", "topic"}
    assert "file_ref" not in json.dumps(planner.payloads)
    assert "storage_uri" not in json.dumps(planner.payloads)
    assert "/documents" not in json.dumps(planner.payloads)


def test_semantic_folder_build_uses_scope_relative_mount_and_rejects_conflict(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Report": {"summary": "Report summary", "domain": "Finance", "topic": "Credit"},
        },
    )
    _register_generated_file(filesystem, "Report", folder="/documents/sec-filings")
    planner = TitlePlanner({"Report": ["domain/finance/topic/credit"]})

    result = filesystem.build_semantic_folder("/documents/sec-filings", planner=planner)

    assert result["mount"] == "/documents/sec-filings/semantic"
    assert filesystem.store.folder_info("/documents/sec-filings/semantic")["kind"] == "generated"

    filesystem.create_folder("/documents/manual/semantic")
    with pytest.raises(FileExistsError, match="non-generated"):
        filesystem.build_semantic_folder(
            "/documents/manual",
            planner=TitlePlanner({"Report": ["domain/finance/topic/credit"]}),
        )


def test_semantic_folder_rebuild_is_atomic_and_replaces_only_own_mount(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Report": {"summary": "Report summary", "domain": "Finance", "topic": "Rates"},
        },
    )
    _register_generated_file(filesystem, "Report", external_id="doc_report")
    filesystem.build_semantic_folder(
        "/",
        planner=TitlePlanner({"Report": ["domain/finance/topic/rates"]}),
    )
    assert filesystem.store.resolve_file_ref("/semantic/domain/finance/topic/rates/Report")

    class InvalidPlanner:
        def plan(self, payload):
            return {
                "template": ["domain"],
                "canonical_values": [{"field": "domain", "display": "Finance", "slug": "finance"}],
                "memberships": [{"item_id": payload["items"][0]["item_id"], "paths": ["/domain/finance"]}],
                "skipped": [],
            }

    with pytest.raises(ValueError, match="must be relative"):
        filesystem.build_semantic_folder("/", planner=InvalidPlanner())
    assert filesystem.store.resolve_file_ref("/semantic/domain/finance/topic/rates/Report")

    filesystem.build_semantic_folder(
        "/",
        planner=TitlePlanner({"Report": ["domain/finance"]}, template=["domain"]),
    )
    assert filesystem.store.resolve_file_ref("/semantic/domain/finance/Report")
    with pytest.raises(KeyError):
        filesystem.store.folder_info("/semantic/domain/finance/topic/rates")


def test_semantic_source_scan_excludes_descendant_semantic_mounts(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Report": {"summary": "Report summary", "domain": "Finance", "topic": "Rates"},
        },
    )
    file_ref = _register_generated_file(filesystem, "Report", external_id="doc_report")
    filesystem.build_semantic_folder(
        "/",
        planner=TitlePlanner({"Report": ["domain/finance/topic/rates"]}),
    )

    entries = filesystem.store.semantic_source_file_entries("/")

    assert [entry.file_ref for entry in entries] == [file_ref]
    with pytest.raises(ValueError, match="semantic mount path"):
        filesystem.build_semantic_folder(
            "/semantic/domain",
            planner=TitlePlanner({"Report": ["domain/finance/topic/rates"]}),
        )


def test_semantic_folder_generates_missing_candidate_metadata_without_overwriting_canonicalization(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Report": {
                "summary": "Cached report summary",
                "domain": "Financial Services",
                "topic": "Central Bank Rates",
            },
        },
    )
    filesystem.register_file(
        storage_uri="file:///tmp/report.txt",
        folder_path="/documents",
        external_id="doc_report",
        title="Report",
        content="Report evidence",
        content_type="text/plain",
        metadata_policy={
            "fields": {
                "summary": True,
                "doc_type": False,
                "domain": False,
                "topic": False,
                "entity": False,
                "relation": False,
            },
            "projection_indexes": {"summary": False},
            "batch": False,
        },
    )

    filesystem.build_semantic_folder(
        "/",
        planner=TitlePlanner({"Report": ["domain/finance/topic/rates"]}),
    )

    metadata = filesystem.store.get_file(filesystem.store.resolve_file_ref("doc_report")).metadata
    assert metadata["domain"] == "Financial Services"
    assert metadata["topic"] == "Central Bank Rates"
    assert ("Report", ("summary",)) in filesystem.metadata_generator.calls
    assert ("Report", ("domain", "topic")) in filesystem.metadata_generator.calls


def test_browse_inside_semantic_folder_returns_navigation_local_locators(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Report": {"summary": "Report summary", "domain": "Finance", "topic": "Rates"},
        },
    )
    file_ref = _register_generated_file(filesystem, "Report", external_id="doc_report")
    filesystem.build_semantic_folder(
        "/",
        planner=TitlePlanner({"Report": ["domain/finance/topic/rates"]}),
    )
    filesystem.semantic_retrieval_backend = BrowseBackend([file_ref])

    result = filesystem.browse_semantic_files(
        "/semantic/domain/finance",
        "rates",
        recursive=True,
    )

    assert result["data"][0]["path"] == "/semantic/domain/finance/topic/rates/Report"
    assert filesystem.store.resolve_file_ref(result["data"][0]["path"]) == filesystem.store.resolve_file_ref(
        "/documents/Report"
    )


def test_grep_and_stat_inside_semantic_folder_return_navigation_local_paths(tmp_path):
    from pageindex.filesystem.commands import PIFSCommandExecutor

    filesystem = _filesystem(
        tmp_path,
        {
            "Report": {"summary": "Report summary", "domain": "Finance", "topic": "Rates"},
        },
    )
    _register_generated_file(filesystem, "Report", external_id="doc_report")
    filesystem.build_semantic_folder(
        "/",
        planner=TitlePlanner({"Report": ["domain/finance/topic/rates"]}),
    )
    executor = PIFSCommandExecutor(filesystem)

    grep_output = executor.execute('grep -R "rates" /semantic/domain/finance/topic/rates')
    stat_output = executor.execute("stat /semantic/domain/finance/topic/rates/Report")
    field_output = executor.execute("stat --field domain /semantic/domain/finance/topic/rates/Report")

    assert "/semantic/domain/finance/topic/rates/Report:1:" in grep_output
    assert "/documents/Report" not in grep_output
    assert "target: /semantic/domain/finance/topic/rates/Report" in stat_output
    assert "/documents/Report" not in stat_output
    assert field_output.startswith("/semantic/domain/finance/topic/rates/Report:\n")


def test_semantic_folder_listing_order_is_alphabetical(tmp_path):
    from pageindex.filesystem.commands import PIFSCommandExecutor

    filesystem = _filesystem(
        tmp_path,
        {
            "Zeta": {"summary": "Zeta summary", "domain": "Finance", "topic": "Rates"},
            "Alpha": {"summary": "Alpha summary", "domain": "Finance", "topic": "Rates"},
        },
    )
    _register_generated_file(filesystem, "Zeta", folder="/documents", external_id="doc_zeta")
    _register_generated_file(filesystem, "Alpha", folder="/documents", external_id="doc_alpha")
    filesystem.build_semantic_folder(
        "/",
        planner=TitlePlanner(
            {
                "Zeta": ["domain/finance/topic/rates"],
                "Alpha": ["domain/finance/topic/rates"],
            }
        ),
    )
    executor = PIFSCommandExecutor(filesystem)

    root_listing = executor.execute("ls /")
    documents_listing = executor.execute("ls /documents")
    semantic_listing = executor.execute("ls /semantic/domain/finance/topic/rates")

    assert root_listing.index("/documents/") < root_listing.index("/semantic/")
    assert documents_listing.index("/documents/Alpha") < documents_listing.index("/documents/Zeta")
    assert semantic_listing.index("/semantic/domain/finance/topic/rates/Alpha") < (
        semantic_listing.index("/semantic/domain/finance/topic/rates/Zeta")
    )


def test_openai_semantic_folder_response_format_requires_nullable_confidence():
    from pageindex.filesystem.semantic_folder import OpenAISemanticFolderPlanner

    schema = OpenAISemanticFolderPlanner._response_format()["json_schema"]["schema"]
    membership_schema = schema["properties"]["memberships"]["items"]

    assert "confidence" in membership_schema["required"]
    assert membership_schema["properties"]["confidence"]["type"] == ["number", "null"]


def test_semantic_folder_display_names_disambiguate_same_title_memberships(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Report": {"summary": "Report summary", "domain": "Finance", "topic": "Rates"},
        },
    )
    first_ref = _register_generated_file(
        filesystem,
        "Report",
        folder="/first",
        external_id="doc_first",
    )
    second_ref = _register_generated_file(
        filesystem,
        "Report",
        folder="/second",
        external_id="doc_second",
    )

    result = filesystem.build_semantic_folder(
        "/",
        planner=TitlePlanner({"Report": ["domain/finance/topic/rates"]}),
    )

    assert result["display_collision_groups"] == 1
    assert result["display_disambiguated_files"] == 2
    listing = filesystem.browse("/semantic/domain/finance/topic/rates")
    paths = sorted(f"{item['folder_path']}/{item['title']}" for item in listing["files"])
    assert paths == [
        f"/semantic/domain/finance/topic/rates/Report [{first_ref.replace('file_', '')[:8]}]",
        f"/semantic/domain/finance/topic/rates/Report [{second_ref.replace('file_', '')[:8]}]",
    ]
    assert filesystem.store.resolve_file_ref(paths[0]) in {first_ref, second_ref}
    assert filesystem.store.resolve_file_ref(paths[1]) in {first_ref, second_ref}
    assert filesystem.store.resolve_file_ref(paths[0]) != filesystem.store.resolve_file_ref(paths[1])
    with filesystem.store.connect() as conn:
        row = conn.execute(
            "SELECT manifest_json FROM semantic_folder_manifests ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    manifest = json.loads(row["manifest_json"])
    display_ledger = manifest["materialization"]["display_ledger"]
    assert len(display_ledger) == 2
    assert all(item["base_display_name"] == "Report" for item in display_ledger)
    assert all(item["suffix"] for item in display_ledger)


def test_semantic_folder_validation_rejects_taxonomy_repairs_and_limits():
    from pageindex.filesystem.semantic_folder import (
        SEGMENT_MAX_CHARS,
        validate_semantic_folder_plan,
    )

    base = {
        "template": ["domain"],
        "canonical_values": [
            {"field": "domain", "display": "Finance", "slug": "finance"},
        ],
        "memberships": [{"item_id": "item_0001", "paths": ["domain/finance"]}],
        "skipped": [],
    }
    assert validate_semantic_folder_plan(base, item_file_refs={"item_0001": "file_a"}).memberships

    with pytest.raises(ValueError, match="collision"):
        validate_semantic_folder_plan(
            {
                **base,
                "canonical_values": [
                    {"field": "domain", "display": "Finance", "slug": "finance"},
                    {"field": "domain", "display": "Financial Services", "slug": "finance"},
                ],
            },
            item_file_refs={"item_0001": "file_a"},
        )
    with pytest.raises(ValueError, match="limit exceeded"):
        validate_semantic_folder_plan(
            {
                **base,
                "canonical_values": [
                    {"field": "domain", "display": "Finance", "slug": "finance"},
                    {"field": "domain", "display": "Technology", "slug": "technology"},
                    {"field": "domain", "display": "Healthcare", "slug": "healthcare"},
                    {"field": "domain", "display": "Energy", "slug": "energy"},
                ],
                "memberships": [
                    {
                        "item_id": "item_0001",
                        "paths": [
                            "domain/finance",
                            "domain/technology",
                            "domain/healthcare",
                            "domain/energy",
                        ],
                    }
                ],
            },
            item_file_refs={"item_0001": "file_a"},
        )
    with pytest.raises(ValueError, match="unknown"):
        validate_semantic_folder_plan(
            {**base, "memberships": [{"item_id": "item_0001", "paths": ["domain/unknown"]}]},
            item_file_refs={"item_0001": "file_a"},
        )
    with pytest.raises(ValueError, match="must be relative"):
        validate_semantic_folder_plan(
            {**base, "memberships": [{"item_id": "item_0001", "paths": ["/domain/finance"]}]},
            item_file_refs={"item_0001": "file_a"},
        )
    with pytest.raises(ValueError, match="field/value segments"):
        validate_semantic_folder_plan(
            {**base, "memberships": [{"item_id": "item_0001", "paths": ["domain"]}]},
            item_file_refs={"item_0001": "file_a"},
        )
    with pytest.raises(ValueError, match="does not match selected template"):
        validate_semantic_folder_plan(
            {
                **base,
                "template": ["domain", "topic"],
                "memberships": [{"item_id": "item_0001", "paths": ["topic/finance"]}],
            },
            item_file_refs={"item_0001": "file_a"},
        )
    with pytest.raises(ValueError, match="Unsafe Semantic Folder domain slug"):
        validate_semantic_folder_plan(
            {
                **base,
                "canonical_values": [
                    {
                        "field": "domain",
                        "display": "Overlong",
                        "slug": "a" * (SEGMENT_MAX_CHARS + 1),
                    },
                ],
                "memberships": [{"item_id": "item_0001", "paths": ["domain/" + "a" * (SEGMENT_MAX_CHARS + 1)]}],
            },
            item_file_refs={"item_0001": "file_a"},
        )
    with pytest.raises(ValueError, match="omitted build item"):
        validate_semantic_folder_plan(
            base,
            item_file_refs={"item_0001": "file_a", "item_0002": "file_b"},
        )
    with pytest.raises(ValueError, match="both place and skip"):
        validate_semantic_folder_plan(
            {**base, "skipped": [{"item_id": "item_0001", "reason": "duplicate"}]},
            item_file_refs={"item_0001": "file_a"},
        )


def test_semantic_folder_planner_retries_invalid_patterns_and_can_canonicalize_high_cardinality_topic(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            f"Paper {index}": {
                "summary": f"Research paper {index}",
                "domain": "Machine Learning",
                "topic": f"Specific paper-only mechanism {index}",
            }
            for index in range(1, 7)
        },
    )
    for index in range(1, 7):
        _register_generated_file(
            filesystem,
            f"Paper {index}",
            folder="/documents",
            external_id=f"doc_{index}",
        )
    planner = BadThenTopicPlanner()

    result = filesystem.build_semantic_folder("/documents", planner=planner)

    assert result["template"] == "topic"
    assert len(planner.payloads) == 3
    assert planner.payloads[0]["aggregate"]["fields"]["topic"]["high_cardinality"] is True
    assert planner.payloads[1]["retry"]["validation_error"]
    assert planner.payloads[2]["planning_stage"] == "registry_finalization"
    listing = filesystem.browse("/documents/semantic/topic")
    assert [folder["name"] for folder in listing["folders"]] == [
        "corporate-governance",
        "earnings-results",
        "strategic-transactions",
    ]
    for path in (
        "/documents/semantic/topic/earnings-results",
        "/documents/semantic/topic/corporate-governance",
        "/documents/semantic/topic/strategic-transactions",
    ):
        for file in filesystem.browse(path)["files"]:
            assert file["folder_path"] == path


def test_semantic_folder_planner_retries_omitted_items_instead_of_silently_skipping(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Q1 Results": {
                "summary": "Quarterly earnings release",
                "domain": "Finance",
                "topic": "Detailed Q1 earnings results",
            },
            "Proxy Vote": {
                "summary": "Shareholder governance vote",
                "domain": "Finance",
                "topic": "Board and shareholder governance proposals",
            },
            "Annual Results": {
                "summary": "Annual earnings release",
                "domain": "Finance",
                "topic": "Detailed annual earnings results",
            },
        },
    )
    for title in ("Q1 Results", "Proxy Vote", "Annual Results"):
        _register_generated_file(filesystem, title, folder="/documents")
    planner = OmittingThenCompletePlanner()

    result = filesystem.build_semantic_folder("/documents", planner=planner)

    assert result["template"] == "topic"
    assert result["skipped"] == 0
    assert len(planner.payloads) == 3
    assert "omitted build item" in planner.payloads[1]["retry"]["validation_error"]
    assert planner.payloads[2]["planning_stage"] == "registry_finalization"
    listing = filesystem.browse("/documents/semantic/topic")
    assert [folder["name"] for folder in listing["folders"]] == [
        "corporate-governance",
        "earnings-results",
    ]


def test_semantic_folder_large_scope_uses_batch_membership_and_finalization(tmp_path):
    titles = [f"Doc {index:02d}" for index in range(30)]
    filesystem = _filesystem(
        tmp_path,
        {
            title: {"summary": f"{title} summary", "domain": "Finance", "topic": "Rates"}
            for title in titles
        },
    )
    for index, title in enumerate(titles):
        _register_generated_file(filesystem, title, folder="/documents", external_id=f"doc_{index}")
    planner = TitlePlanner({title: ["domain/finance/topic/rates"] for title in titles})

    result = filesystem.build_semantic_folder("/documents", planner=planner)

    assert result["files"] == 30
    assert result["memberships"] == 30
    assert result["planning_stages"] == 3
    assert result["materialization_chunks"] == 1
    assert [payload["planning_stage"] for payload in planner.payloads] == [
        "sample_planning",
        "membership_planning",
        "registry_finalization",
    ]
    assert len(planner.payloads[0]["items"]) == 25
    assert len(planner.payloads[1]["items"]) == 30
    finalization_contract = planner.payloads[2]["planning_contract"]
    assert "membership_intent_draft" not in finalization_contract
    assert "skipped_draft" not in finalization_contract
    assert "registry_lifecycle_contract" in finalization_contract
    assert "file_ref" not in json.dumps(planner.payloads)
    with filesystem.store.connect() as conn:
        row = conn.execute(
            "SELECT lifecycle_status, contract_hash FROM semantic_folder_builds WHERE contract_hash = ?",
            (result["finalized_contract_hash"],),
        ).fetchone()
    assert dict(row) == {
        "lifecycle_status": "published",
        "contract_hash": result["finalized_contract_hash"],
    }


def test_semantic_folder_registry_finalization_contract_is_bounded_for_large_synthetic_ledger():
    from pageindex.filesystem.core import PageIndexFileSystem

    source_count = 50_000
    draft_memberships = [
        {
            "item_id": f"item_{index:04d}",
            "relative_path": "topic/enterprise-operations",
            "canonical_segments": [
                {
                    "field": "topic",
                    "display": "Enterprise Operations",
                    "slug": "enterprise-operations",
                }
            ],
            "confidence": 0.8,
        }
        for index in range(1, source_count + 1)
    ]
    draft_skipped = [
        {"item_id": f"skip_{index:04d}", "reason": "missing first field"}
        for index in range(1, source_count + 1)
    ]
    payload_items = [
        {
            "item_id": f"item_{index:04d}",
            "title": f"Document {index}",
            "summary": "bounded example evidence",
            "domain": None,
            "topic": None,
        }
        for index in range(1, 200)
    ]

    contract = PageIndexFileSystem._semantic_folder_registry_lifecycle_contract(
        draft_canonical_values=[
            {
                "field": "topic",
                "display": "Enterprise Operations",
                "slug": "enterprise-operations",
            }
        ],
        draft_memberships=draft_memberships,
        draft_skipped=draft_skipped,
        payload_items=payload_items,
        aggregate={"item_count": source_count},
        stage_records=[
            {"stage": "sample_planning", "attempts": 1, "items": 25},
            {"stage": "membership_planning", "attempts": 1, "items": source_count},
        ],
    )
    payload_text = json.dumps(contract, sort_keys=True)

    assert len(payload_text) < 50_000
    assert "membership_intent_draft" not in payload_text
    assert "skipped_draft" not in payload_text
    assert "item_50000" not in payload_text
    assert contract["coverage"]["membership_intent_count"] == source_count
    assert contract["coverage"]["explicit_skip_count"] == source_count
    assert (
        contract["candidate_registry"]["topic"]["included_candidates"][0]["membership_count"]
        == source_count
    )


def test_semantic_folder_large_missing_metadata_is_deferred_to_planner(tmp_path):
    titles = [f"Deferred {index:02d}" for index in range(11)]
    filesystem = _filesystem(
        tmp_path,
        {
            title: {
                "summary": f"{title} summary",
                "domain": "Finance",
                "topic": "Rates",
            }
            for title in titles
        },
    )
    for title in titles:
        filesystem.register_file(
            storage_uri=f"file:///tmp/{title}.txt",
            folder_path="/documents",
            external_id=title.lower().replace(" ", "_"),
            title=title,
            content=f"{title} evidence.",
            content_type="text/plain",
            metadata_policy={
                "fields": {
                    "summary": True,
                    "doc_type": False,
                    "domain": False,
                    "topic": False,
                    "entity": False,
                    "relation": False,
                },
                "projection_indexes": {"summary": False},
                "batch": False,
            },
        )
    planner = TitlePlanner({title: ["domain/finance/topic/rates"] for title in titles})

    result = filesystem.build_semantic_folder("/documents", planner=planner)

    assert result["metadata_deferred"] == 22
    assert result["metadata_generating"] == 0
    assert result["warnings"] == ["large_missing_metadata_set_deferred_to_planner"]
    assert not any(fields == ("domain", "topic") for _, fields in filesystem.metadata_generator.calls)
    assert result["memberships"] == 11


def test_semantic_folder_finalization_barrier_blocks_publish(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Report": {"summary": "Report summary", "domain": "Finance", "topic": "Rates"},
        },
    )
    _register_generated_file(filesystem, "Report", external_id="doc_report")

    class NeverFinalizingPlanner(TitlePlanner):
        def plan(self, payload):
            plan = super().plan(payload)
            plan["finalized"] = False
            return plan

    with pytest.raises(ValueError, match="finalized=true"):
        filesystem.build_semantic_folder(
            "/",
            planner=NeverFinalizingPlanner({"Report": ["domain/finance/topic/rates"]}),
        )

    with pytest.raises(KeyError):
        filesystem.store.folder_info("/semantic")
    with filesystem.store.connect() as conn:
        row = conn.execute(
            "SELECT lifecycle_status, error FROM semantic_folder_builds ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row["lifecycle_status"] == "failed"
    assert "finalized=true" in row["error"]


def test_semantic_folder_registry_finalization_retries_missing_active_refs(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Report": {"summary": "Report summary", "domain": "Finance", "topic": "Rates"},
        },
    )
    _register_generated_file(filesystem, "Report", external_id="doc_report")

    class MissingRegistryThenCompletePlanner(TitlePlanner):
        def plan(self, payload):
            if payload.get("planning_stage") != "registry_finalization":
                return super().plan(payload)
            self.payloads.append(payload)
            if "retry" not in payload:
                return {
                    "template": ["domain", "topic"],
                    "partial_path_policy": "template_prefix",
                    "canonical_values": [
                        {"field": "domain", "display": "Finance", "slug": "finance"},
                    ],
                    "memberships": [],
                    "skipped": [],
                    "finalized": True,
                }
            assert "undeclared canonical value" in payload["retry"]["validation_error"]
            return {
                "template": ["domain", "topic"],
                "partial_path_policy": "template_prefix",
                "canonical_values": [
                    {"field": "domain", "display": "Finance", "slug": "finance"},
                    {"field": "topic", "display": "Rates", "slug": "rates"},
                ],
                "memberships": [],
                "skipped": [],
                "finalized": True,
            }

    planner = MissingRegistryThenCompletePlanner({"Report": ["domain/finance/topic/rates"]})

    result = filesystem.build_semantic_folder("/", planner=planner)

    assert result["publish_status"] == "published"
    stages = [payload.get("planning_stage") for payload in planner.payloads]
    assert stages[0] == "sample_planning"
    assert stages.count("membership_disposition") == 3
    assert stages.count("registry_finalization") == 2
    for payload in planner.payloads:
        if payload.get("planning_stage") == "registry_finalization":
            assert "membership_intent_draft" not in payload["planning_contract"]
            assert "skipped_draft" not in payload["planning_contract"]
    assert planner.payloads[-1]["retry"]["validation_error"]
    assert filesystem.store.resolve_file_ref("/semantic/domain/finance/topic/rates/Report")


def test_semantic_folder_inactive_registry_refs_use_localized_disposition(tmp_path):
    titles = [f"Doc {index:02d}" for index in range(30)]
    filesystem = _filesystem(
        tmp_path,
        {
            title: {"summary": f"{title} summary", "domain": "Ops", "topic": "Legacy"}
            for title in titles
        },
    )
    for index, title in enumerate(titles):
        _register_generated_file(filesystem, title, external_id=f"doc_{index}")

    class DispositionPlanner:
        def __init__(self):
            self.payloads: list[dict[str, Any]] = []

        def plan(self, payload):
            self.payloads.append(payload)
            stage = payload.get("planning_stage")
            if stage in {"sample_planning", "membership_planning"}:
                return {
                    "template": ["topic"],
                    "partial_path_policy": "template_prefix",
                    "canonical_values": [
                        {"field": "topic", "display": "Legacy Topic", "slug": "legacy-topic"},
                    ],
                    "memberships": [
                        {
                            "item_id": item["item_id"],
                            "paths": ["topic/legacy-topic"],
                            "confidence": 0.7,
                        }
                        for item in payload["items"]
                    ],
                    "skipped": [],
                    "finalized": False,
                }
            if stage == "registry_finalization":
                contract = payload["planning_contract"]
                assert "membership_intent_draft" not in contract
                assert "skipped_draft" not in contract
                return {
                    "template": ["topic"],
                    "partial_path_policy": "template_prefix",
                    "canonical_values": [
                        {
                            "field": "topic",
                            "display": "Enterprise Operations",
                            "slug": "enterprise-operations",
                        },
                    ],
                    "memberships": [],
                    "skipped": [],
                    "finalized": True,
                    "lifecycle_outcomes": [
                        {
                            "field": "topic",
                            "display": "Legacy Topic",
                            "slug": "legacy-topic",
                            "lifecycle": "rejected",
                            "reason": "too narrow for the finalized registry",
                        }
                    ],
                    "canonical_mappings": [
                        {
                            "field": "topic",
                            "source_slug": "legacy-topic",
                            "target_slug": "enterprise-operations",
                            "lifecycle": "merged_away",
                            "reason": "localized disposition should remap affected intents",
                        }
                    ],
                }
            if stage == "membership_disposition":
                assert payload["planning_contract"]["affected_membership_intents"]
                assert payload["planning_contract"]["final_active_registry"] == [
                    {
                        "field": "topic",
                        "display": "Enterprise Operations",
                        "slug": "enterprise-operations",
                        "lifecycle": "accepted",
                    }
                ]
                return {
                    "template": ["topic"],
                    "partial_path_policy": "template_prefix",
                    "canonical_values": [
                        {
                            "field": "topic",
                            "display": "Enterprise Operations",
                            "slug": "enterprise-operations",
                        },
                    ],
                    "memberships": [
                        {
                            "item_id": item["item_id"],
                            "paths": ["topic/enterprise-operations"],
                            "confidence": 0.82,
                        }
                        for item in payload["items"]
                    ],
                    "skipped": [],
                    "finalized": False,
                }
            raise AssertionError(stage)

    planner = DispositionPlanner()

    result = filesystem.build_semantic_folder("/documents", planner=planner)

    assert result["memberships"] == 30
    assert [payload["planning_stage"] for payload in planner.payloads] == [
        "sample_planning",
        "membership_planning",
        "registry_finalization",
        "membership_disposition",
    ]
    assert filesystem.store.resolve_file_ref(
        "/documents/semantic/topic/enterprise-operations/Doc 00"
    )


def test_semantic_folder_disposition_omission_blocks_publish(tmp_path):
    titles = [f"Doc {index:02d}" for index in range(30)]
    filesystem = _filesystem(
        tmp_path,
        {
            title: {"summary": f"{title} summary", "domain": "Ops", "topic": "Legacy"}
            for title in titles
        },
    )
    for index, title in enumerate(titles):
        _register_generated_file(filesystem, title, external_id=f"doc_{index}")

    class OmittingDispositionPlanner:
        def plan(self, payload):
            stage = payload.get("planning_stage")
            if stage in {"sample_planning", "membership_planning"}:
                return {
                    "template": ["topic"],
                    "partial_path_policy": "template_prefix",
                    "canonical_values": [
                        {"field": "topic", "display": "Legacy Topic", "slug": "legacy-topic"},
                    ],
                    "memberships": [
                        {
                            "item_id": item["item_id"],
                            "paths": ["topic/legacy-topic"],
                            "confidence": 0.7,
                        }
                        for item in payload["items"]
                    ],
                    "skipped": [],
                    "finalized": False,
                }
            if stage == "registry_finalization":
                return {
                    "template": ["topic"],
                    "partial_path_policy": "template_prefix",
                    "canonical_values": [
                        {
                            "field": "topic",
                            "display": "Enterprise Operations",
                            "slug": "enterprise-operations",
                        },
                    ],
                    "memberships": [],
                    "skipped": [],
                    "finalized": True,
                }
            if stage == "membership_disposition":
                return {
                    "template": ["topic"],
                    "partial_path_policy": "template_prefix",
                    "canonical_values": [
                        {
                            "field": "topic",
                            "display": "Enterprise Operations",
                            "slug": "enterprise-operations",
                        },
                    ],
                    "memberships": [
                        {
                            "item_id": item["item_id"],
                            "paths": ["topic/enterprise-operations"],
                            "confidence": 0.82,
                        }
                        for item in payload["items"][:-1]
                    ],
                    "skipped": [],
                    "finalized": False,
                }
            raise AssertionError(stage)

    with pytest.raises(ValueError, match="omitted build item"):
        filesystem.build_semantic_folder("/documents", planner=OmittingDispositionPlanner())

    with pytest.raises(KeyError):
        filesystem.store.folder_info("/documents/semantic")


def test_semantic_folder_registry_finalization_ignores_stray_memberships(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Report": {"summary": "Report summary", "domain": "Finance", "topic": "Rates"},
        },
    )
    _register_generated_file(filesystem, "Report", external_id="doc_report")

    class VerboseFinalizationPlanner(TitlePlanner):
        def plan(self, payload):
            plan = super().plan(payload)
            if payload.get("planning_stage") == "registry_finalization":
                plan["memberships"] = [
                    {"item_id": "item_0001", "paths": ["domain/finance/topic/rates"], "confidence": 0.5}
                ]
            return plan

    result = filesystem.build_semantic_folder(
        "/",
        planner=VerboseFinalizationPlanner({"Report": ["domain/finance/topic/rates"]}),
    )

    assert result["publish_status"] == "published"
    assert filesystem.store.resolve_file_ref("/semantic/domain/finance/topic/rates/Report")


def test_semantic_folder_publish_cas_failure_preserves_visible_generation(tmp_path):
    filesystem = _filesystem(
        tmp_path,
        {
            "Report": {"summary": "Report summary", "domain": "Finance", "topic": "Rates"},
        },
    )
    file_ref = _register_generated_file(filesystem, "Report", external_id="doc_report")
    result = filesystem.build_semantic_folder(
        "/",
        planner=TitlePlanner({"Report": ["domain/finance/topic/rates"]}),
    )
    assert filesystem.store.resolve_file_ref("/semantic/domain/finance/topic/rates/Report") == file_ref

    with pytest.raises(RuntimeError, match="CAS failed"):
        filesystem.store.apply_semantic_folder_build(
            source_scope="/",
            mount_path="/semantic",
            memberships=[],
            manifest={
                "build_id": "semantic_folder_stale",
                "finalized_contract_hash": "sha256:stale",
            },
            expected_generation="stale-generation",
        )

    assert filesystem.store.resolve_file_ref("/semantic/domain/finance/topic/rates/Report") == file_ref
    assert (
        filesystem.store.semantic_mount_generation(source_scope="/", mount_path="/semantic")
        != "semantic_folder_stale"
    )
    assert result["publish_status"] == "published"


def test_cli_semantic_folder_build_is_user_surface_not_agent_surface(monkeypatch, capsys, tmp_path):
    from pageindex.filesystem import cli
    from pageindex.filesystem.commands import PIFSCommandError, PIFSCommandExecutor

    class FakeFileSystem:
        def __init__(self, workspace):
            self.workspace = workspace

        def configure_existing_projection_retrieval(self):
            return False

        def build_semantic_folder(self, source_scope="/"):
            return {
                "source": source_scope,
                "mount": "/documents/semantic",
                "template": "domain/topic",
                "files": 3,
                "memberships": 4,
                "skipped": 1,
                "metadata_cached": 5,
                "metadata_generating": 1,
                "metadata_failed": 0,
                "planning": "generated",
            }

    monkeypatch.setattr(cli, "PageIndexFileSystem", FakeFileSystem)

    status = cli.main(["--workspace", str(tmp_path), "semantic-folder", "build", "/documents"])

    assert status == 0
    output = capsys.readouterr().out
    assert "source: /documents" in output
    assert "mount: /documents/semantic" in output
    assert "metadata: cached=5 generating=1 failed=0" in output
    executor = PIFSCommandExecutor(FakeFileSystem(tmp_path))
    assert "semantic-folder" not in executor.allowed_commands()
    with pytest.raises(PIFSCommandError, match="Unsupported command"):
        executor.execute("semantic-folder build /documents")
