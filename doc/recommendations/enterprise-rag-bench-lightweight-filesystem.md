# EnterpriseRAG-Bench Lightweight FileSystem Recommendation

## Executive Recommendation

For EnterpriseRAG-Bench, PageIndex should implement FileSystem as a lightweight
local catalog plus retrieval planner inside the current PageIndex repository.
Do not build a hosted API and do not create a new repository for the first
version.

The immediate target is:

```text
benchmark JSON/txt corpus
  -> local FileSystem catalog
  -> folder + metadata + FTS candidate selection
  -> PageIndex document-tree retrieval on selected docs
  -> JSONL answers with minimal document_ids
```

## 1. Scaling To Millions Of Documents

The system should not generate full LLM-built PageIndex trees for all 500k+
documents before retrieval. That would turn indexing into the bottleneck and
would not solve corpus-level routing.

Use a staged scale strategy:

1. Catalog every document cheaply:
   - `dataset_doc_uuid`
   - source type
   - physical path
   - title
   - declared content fields
   - source-specific metadata
2. Build cheap searchable text:
   - title
   - content fields
   - selected metadata text
3. Use SQLite FTS or equivalent local full-text index for first-pass recall.
4. Use typed metadata columns/tables for high-value filters and facets.
5. Build deterministic shallow document trees for short/structured documents.
6. Build richer PageIndex trees lazily for long documents or top candidates.

This gives PageIndex a million-document path without abandoning the current
repository's lightweight style.

## 2. Folder Modeling

Use the benchmark's source tree as the physical folder tree:

```text
source_type / source-native grouping / optional nested path
```

Examples:

- `slack/eng`
- `gmail/grace_oconnor`
- `github/redwood`
- `jira/customer-support`
- `linear/design`
- `confluence/architecture-and-standards`
- `google_drive/shared_drives/engineering/platform-engineering`

Each document should have one canonical physical folder. Do not encode every
conceptual relationship as a physical folder.

Add virtual folders/views for:

- project or initiative
- customer/account
- person
- repo/channel/team
- time period
- document kind
- conflict/version lineage

Virtual folders can be generated from metadata and cached. They are retrieval
views, not source-of-truth storage paths.

## 3. Metadata Design

Use a flat typed metadata model. Keep the original raw JSON for fidelity, but
index only fields that help retrieval.

Common fields:

- `doc_id`
- `source_type`
- `title`
- `physical_path`
- `created_at`
- `updated_at`
- `owner_or_author`
- `people`
- `project`
- `customer_or_account`
- `status`
- `labels`

Source-specific field groups:

- Slack: `channel`, `thread_ts`, `participants`
- Gmail: `mailbox_owner`, `participants_internal`,
  `participants_external`, `first_email_at`, `last_email_at`,
  `related_account`, `deal_id`, `has_attachments`
- GitHub: `repo`, `pr_number`, `state`, `reviewers`, `linked_linear`,
  `linked_jira`, `ci_status`
- Jira/Linear: `key`, `assignee`, `priority`, `severity`, `due_date`,
  `cycle`, `components`
- HubSpot: `company_name`, `owner`, `stage`, `account_tier`,
  `interested_products`, `blockers`
- Google Drive/Confluence: `owner`, `author`, `space`, `path`, `tags`,
  `status`, `collaborators`

The PageIndex metadata schema should be explicitly declared or inferred once at
ingest time, then validated. LLM-generated SQL should not be the primary query
interface. If an LLM is used, it should output a validated query AST.

## 4. Query Capability

Use a two-stage query model:

1. LLM or deterministic planner produces `FileSystemQuery`:

```json
{
  "text": "rollback checklist after quantization profile latency spike",
  "scope": {"source_type": ["jira", "slack", "confluence"]},
  "metadata_filter": {
    "customer_or_account": {"$contains": "LexiHealth"},
    "status": {"$in": ["In Progress", "published"]}
  },
  "limit": 50
}
```

2. The local executor validates and runs:
   - folder/path constraints
   - metadata filters
   - FTS/BM25 text search
   - grouping/facets if the query is broad

The output should be candidate documents plus enough explanations for the final
PageIndex retrieval agent to decide what to open.

For final evidence extraction, keep the current PageIndex style:

- `get_document`
- `get_document_structure`
- `get_page_content`

The FileSystem layer only chooses candidates and navigation context; it should
not replace document-level PageIndex reasoning.

## 5. Storage Choice

Recommended first implementation for this benchmark:

- SQLite catalog for folders, documents, metadata indexes, and FTS.
- Local artifact directory for raw JSON, exported text, and document-tree
  cache.
- Optional JSONL manifests for import/export and debugging.

Do not use a single giant JSON file as the authoritative index. It will make
filtering, pagination, and incremental rebuilds fragile at 500k+ documents.

Do not use S3-like path layout as the authoritative folder model for local OSS
PageIndex. S3-style object keys are useful for artifact storage later, but the
retrieval planner needs typed metadata and indexed folder relationships.

## 6. Mirage: What To Borrow

Borrow:

- adapter boundary between logical filesystem and storage provider
- mount/resource abstraction
- cached directory listings
- fingerprint/revision concepts for idempotent ingest
- path-like user mental model for agents

Do not directly copy:

- bash/shell as the primary PageIndex retrieval interface
- path as stable identity
- snapshot/replay as the core corpus index
- resource-specific metadata with no PageIndex schema registry

Mirage is a good implementation reference for virtual filesystem ergonomics.
PageIndex still needs a retrieval-specific catalog and query planner.

## 7. Final Architecture

```text
PageIndex local workspace
  _meta.json or manifest pointer
  filesystem.sqlite
  raw/
    sources/...
  artifacts/
    doc_trees/
    exported_text/

pageindex.filesystem
  ingest_json_sources()
  build_catalog()
  browse()
  search()
  get_schema()
  get_document_tree()

Catalog tables
  folders
  documents
  metadata_schema
  metadata_values or typed generated columns
  fts_documents
  virtual_views
  tree_artifacts

Retrieval flow
  question
  -> query plan
  -> catalog candidate selection
  -> PageIndex document tools
  -> answer + document_ids
```

## 7.1 AgenticRAG Benchmark Extension

After reading `AgenticRAG: Agentic Retrieval for Enterprise Knowledge Bases`,
the FileSystem should expose an additional local agentic tool facade:

```text
search(query | queries, scope?, metadata_filter?, limit?)
find(reference_id, patterns, mode?)
open(reference_id, location?)
summarize(evidence_state, preserve_refs)
```

This facade lets the same FileSystem run the AgenticRAG paper's benchmark
families:

- BRIGHT: ranked long-document retrieval
- WixQA: multi-document support QA
- FinanceBench: long-PDF evidence extraction

This does not change the storage recommendation. It changes the retrieval
interface from one-shot candidate selection to iterative candidate discovery
plus in-document navigation.

The implementation should still be local and lightweight:

```text
Benchmark adapter
  -> Agentic tool facade
  -> FileSystem catalog
  -> PageIndex document trees / page windows
  -> benchmark-specific output
```

The important adjustment is that `find` and `open` should be first-class
FileSystem operations, not ad hoc helper calls hidden inside one benchmark
runner.

## 8. Repo Decision

Implement in the current PageIndex repository first.

Reason:

- the benchmark can be expressed as an example workflow, not a service
- the document-tree retrieval primitives already live here
- a local SQLite catalog fits the repo's lightweight design
- a separate repo would add coordination cost before the FileSystem contract is
  stable

Only create `pageindex-filesystem` as a separate repo if it becomes a shared
library used independently by `pageindex-chat`, `pageindex-compute`, and other
projects with separate release cycles.

## 9. Confidence Notes

The strongest conclusion is that PageIndex needs a file-level catalog before
document-level tree retrieval. The benchmark data, PageIndex blog direction,
pageindex-chat/pageindex-compute metadata work, and Mirage all point to the
same boundary:

- catalog and folder/metadata filters choose candidate documents
- PageIndex trees extract evidence inside selected documents

The uncertain part is not the architecture boundary. The remaining empirical
work is ranking quality: how much BM25/FTS, source routing, metadata filtering,
and lazy PageIndex tree extraction each contributes to final benchmark score.
