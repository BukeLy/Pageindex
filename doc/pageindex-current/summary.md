# Summary

The current open-source PageIndex package is centered on single-document tree
generation and tree-based retrieval. It can persist a small local workspace,
but that workspace is a convenience layer around individual document JSON files,
not a scalable FileSystem.

Current behavior:

- `PageIndexClient.index()` creates one PageIndex tree per PDF or Markdown
  file.
- `PageIndexClient(workspace=...)` persists each document as
  `<doc_id>.json`.
- `_meta.json` stores lightweight document metadata for reload.
- Retrieval tools operate over an in-memory `documents` dictionary keyed by
  `doc_id`.
- The metadata tutorial currently recommends an external SQL table and
  Query-to-SQL flow for metadata-based document selection.

The current package therefore has the right document-level primitive but lacks
the corpus-level catalog, folder model, metadata schema, query planner, and
storage abstraction needed for millions of documents.

## Key Gap

The existing `workspace` directory is not a FileSystem:

- no folder table or folder operations
- no metadata schema enforcement
- no metadata query DSL
- no indexed document listing
- no multi-document routing beyond user-provided `doc_id`
- no distinction between physical storage path and logical path

