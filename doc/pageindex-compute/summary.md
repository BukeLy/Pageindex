# Summary

`pageindex-compute` on `origin/dev` contains the most complete backend
implementation of PageIndex Folder and document metadata.

It implements:

- Folder CRUD basics through `/folder` and `/folders`.
- Folder-scoped document listing and semantic search through `folder_id` and
  `recursive`.
- Document-level metadata stored on `FilePageIndex.metadata`.
- Metadata validation for flat primitive values.
- A MongoDB/Chroma-like metadata filter DSL compiled into MySQL JSON SQL.
- Sort, aggregate, and group-by support on `GET /docs`.
- Per-workspace metadata schema stored in a dedicated `MetadataSchema` table.
- User-level schema fallback using `folderId = "root"`.
- Metadata value summaries for schema introspection.

This implementation is a good MVP for product semantics, but it is not yet the
right scale architecture for millions of documents because hot metadata fields
remain in a JSON column without indexes and recursive folder queries are built
from adjacency traversal.

## Strong Ideas To Keep

- Flat typed metadata with strict validation.
- A structured filter DSL instead of unconstrained SQL.
- Schema introspection before LLM-generated filters.
- Folder scoping with explicit `recursive` behavior.
- Group-by and aggregate support for structural questions.

## Main Scale Risks

- `FilePageIndex.metadata` JSON scans are O(N) within the user/workspace scope.
- Recursive folder expansion uses folder ID enumeration instead of a closure
  table or materialized path index.
- Current folder tree is single-parent physical hierarchy only.
- Metadata schema is immutable and unversioned from the query user's
  perspective.
- Schema supports only `string`, `number`, and `boolean`; date/time and enum
  semantics are missing.

