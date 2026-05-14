# Recommended PageIndex FileSystem Architecture

## Executive Recommendation

Build PageIndex FileSystem as a corpus catalog and retrieval-planning layer
inside the current PageIndex project first. Do not start with a separate repo.
Create a clean package boundary, such as `pageindex/filesystem/`, with local
storage adapters. In the current repository this should mean a Python module
surface, not a hosted HTTP API or a separate service. Split into a standalone
repo only after the module contract stabilizes and multiple independent
projects need to depend on it.

## 1. Scaling To Millions Of Documents

The system must avoid showing or scanning a flat document list.

Use a layered plan:

1. Catalog layer: database tables or local catalog files for documents,
   folders, schemas, metadata
   fields, and indexed metadata values.
2. Candidate selection layer: push down folder scope, metadata filters,
   full-text/BM25/document-description search, and pagination.
3. Virtual tree layer: generate query-time grouping nodes when candidates are
   still too broad.
4. PageIndex retrieval layer: open document-level PageIndex trees only for the
   selected candidate set.
5. Feedback layer: record traversal traces, zero-result queries, and selected
   documents so virtual nodes and metadata hints improve offline.

For millions of documents, JSON metadata scans are not enough. Hot fields need
typed indexes or generated/functional indexes.

## 2. Folder Modeling

Use stable folder IDs as identity. Paths are derived display strings.

Recommended physical model:

```text
Folder
  id
  namespace_id
  parent_id
  name
  description
  sort_order
  depth
  materialized_path or path_key
  created_at
  updated_at

FolderClosure
  ancestor_id
  descendant_id
  depth
```

Use either `FolderClosure` or an indexed materialized path. Plain recursive
adjacency traversal should not be the only production mechanism.

Documents should have one canonical physical folder:

```text
Document.folder_id
```

Virtual membership should be separate:

```text
DocumentFolderView
  doc_id
  virtual_node_id
  reason
  score
```

This lets a document appear under multiple metadata/semantic views without
corrupting physical folder semantics.

## 3. Metadata Design

Keep metadata flat and typed for the first stable version.

Recommended schema:

```text
MetadataSchema
  id
  namespace_id
  scope_folder_id nullable
  version
  status

MetadataField
  id
  schema_id
  name
  type
  description
  indexed
  faceted
  sortable
  cardinality_hint
```

Types should include at least:

- string
- number
- boolean
- date
- datetime

Store original metadata as JSON for fidelity, but maintain a typed side index
for queryable fields:

```text
DocumentMetadataValue
  document_id
  field_id
  value_string
  value_number
  value_bool
  value_datetime
```

Add indexes by `(namespace_id, field_id, value_*)` for filterable/faceted
fields.

## 4. Query Capability

Do not let the LLM produce SQL. Let the LLM produce a validated
`FileSystemQuery` AST:

```json
{
  "scope": {"folder_id": "root", "recursive": true},
  "metadata_filter": {"year": {"$gte": 2024}},
  "text_query": "lease termination",
  "sort": {"effective_date": -1},
  "aggregates": {"n": {"$count": {}}},
  "group_by": ["counterparty"],
  "limit": 20
}
```

The backend should:

1. validate schema and types
2. compile to SQL using bound parameters
3. execute indexed candidate selection
4. return documents, groups, aggregates, or virtual nodes
5. call PageIndex document-tree retrieval only after the candidate set is small

This preserves the strong idea from compute/chat while avoiding direct
Query-to-SQL risk.

## 5. Storage Choice

Recommended split:

- Local/open-source PageIndex: SQLite catalog plus local files/object-store
  adapter.
- Cloud PageIndex: Postgres/MySQL/PlanetScale catalog plus S3-compatible object
  storage.
- Raw PDFs, page images, OCR artifacts, PageIndex trees: object store.
- Folder/document/schema/metadata indexes: database.
- JSON flat files: examples, export/import, and backward compatibility only.

SQLite is acceptable for local SDK use and can handle large local catalogs with
proper indexes. It should not be the only cloud storage model. S3-like paths
are useful for blobs, not as the authoritative folder database.

## 6. Mirage: Borrow And Avoid

Borrow:

- `Workspace`-style abstraction with mounted storage adapters.
- `Resource` interface for local, S3, and future backends.
- Programmatic filesystem operations similar to `WorkspaceFS`.
- Separate file cache and index/listing cache.
- Fingerprint/revision concepts for ingestion idempotency and cache
  invalidation.

Do not copy directly:

- Bash as the primary retrieval API.
- Provider path as stable identity.
- Resource-specific metadata without a PageIndex schema registry.
- Agent-run snapshot as the core corpus storage model.

Mirage is an excellent adapter and VFS reference, not a PageIndex retrieval
planner.

## 7. Final Architecture

```text
PageIndex FileSystem module
  create_folder, move_document, list, browse, search, aggregate, get_schema

Query Planner
  schema-aware LLM plan validation
  folder and metadata pushdown
  virtual node generation
  candidate-set budgeting

Agentic Tool Facade
  search across the FileSystem catalog
  find within a referenced document
  open a bounded page/line/tree window
  summarize evidence while preserving references

Catalog
  Folder
  FolderClosure or materialized path
  Document
  MetadataSchema
  MetadataField
  DocumentMetadataValue
  VirtualNode / DocumentVirtualMembership

Artifact Store
  source files
  page text/images
  PageIndex document trees
  OCR outputs

Retrieval Executor
  candidate documents
  PageIndex tree search per document
  answer assembly with citations

Adapters
  SQLite + local files for OSS/local
  SQL DB + S3-compatible object store for cloud
```

For the immediate open-source PageIndex direction, the SQLite + local files
path should be the default. The cloud DB + S3-compatible path is a future
deployment target, not a reason to add service/API packaging now.

The AgenticRAG paper adds one important requirement: the FileSystem should not
only return a one-shot candidate set. It should also support iterative
`search/find/open/summarize` tool use so benchmarks like BRIGHT, WixQA, and
FinanceBench can run on top of the same primitives.

## 8. Repo Decision

Implement first in the current PageIndex repo.

Reason:

- The open-source package already owns the document-level PageIndex primitive.
- The current local workspace needs a migration path.
- A package boundary can keep the design clean without creating a new repo too
  early.
- EnterpriseRAG-Bench can be supported as an example/research workflow with a
  local catalog and local artifacts; it does not require a hosted FileSystem
  service.
- Chat and compute can consume the same API/spec once stable.

Create a new repo later only if PageIndex FileSystem becomes an independently
deployed service with its own lifecycle, SDK, and release cadence.

## 9. Confidence And Remaining Unknowns

Confidence is high for the architectural direction because the same shape is
supported by:

- the PageIndex FileSystem blog
- current compute metadata and Folder implementation
- current chat MCP discovery tools
- Mirage's proven VFS adapter pattern

Remaining unknowns:

- exact enterprise deployment constraints
- final DB choice for cloud
- expected folder depth and fanout in production data
- whether PageIndex wants virtual nodes to be persisted, query-time only, or
  both

Those unknowns affect implementation details, not the core recommendation.
