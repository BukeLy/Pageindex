# Summary

`pageindex-chat` is where the MCP-facing FileSystem behavior currently lives.
The standalone `pageindex-mcp` package only proxies remote tools; the real
Folder and metadata UX is implemented in `server/mcp` in this repository.

The branch `origin/feat/metadata-filter` adds:

- `browse_documents` as the unified discovery entry.
- `get_folder_structure` for tree navigation.
- `search_documents` with two modes:
  - `mode="query"` for keyword precision.
  - `mode="metadata"` for metadata filters, sort, aggregates, and group-by.
- `get_metadata_schema` so the model can inspect schema before building a
  structured query.
- Folder scope validation for direct MCP, API proxy, and teamspace contexts.
- Human-readable folder paths attached to results.

This branch gives a strong product shape for AI-facing FileSystem operations.
It should influence PageIndex's API design, but the long-term implementation
should move the shared semantics into a lower-level FileSystem module rather
than keeping them only as MCP tool code.

## Strong Ideas To Keep

- A browse-first discovery funnel.
- `get_folder_structure()` before targeted browsing.
- Explicit `recursive` semantics.
- Metadata query as a separate structured mode.
- Schema-first instructions for LLM-generated metadata filters.
- Group-by support for counts and distributions.

## Gaps

- The upload schema in chat does not yet expose metadata even though compute
  supports upload-time metadata.
- The MCP metadata query max for `group_by` differs from backend docs/code.
- Chat depends on compute's JSON metadata implementation, so it inherits the
  backend scale limitations.
- Folder path strings are useful to the model, but IDs must remain the stable
  execution target.

