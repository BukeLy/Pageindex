# Methods

## Workspace Persistence

`pageindex/client.py` defines `META_INDEX = "_meta.json"` and stores local
documents in a workspace directory.

On index:

1. Generate a UUID `doc_id`.
2. Build a document tree using `page_index()` or `md_to_tree()`.
3. Store the full document payload as `<doc_id>.json`.
4. Store a lightweight entry in `_meta.json`.
5. Drop heavy fields from the in-memory object and lazy-load them later.

The lightweight metadata entry contains:

```json
{
  "type": "pdf",
  "doc_name": "...",
  "doc_description": "...",
  "path": "...",
  "page_count": 10
}
```

This is adequate for examples and local demos, but it does not support
efficient corpus-level queries.

## Retrieval Surface

`pageindex/retrieve.py` exposes three tool-style functions:

- `get_document(documents, doc_id)`
- `get_document_structure(documents, doc_id)`
- `get_page_content(documents, doc_id, pages)`

The functions assume the caller already knows the target `doc_id`.
They do not search or route across a corpus.

The examples reinforce the intended shape: a lightweight agent calls document
tools directly instead of going through a hosted service. For an
EnterpriseRAG-Bench path, the new FileSystem layer should therefore produce
candidate `doc_id`s and keep using these document-level tools for final
evidence extraction.

## Metadata Tutorial

`examples/tutorials/doc-search/metadata.md` currently recommends this flow:

1. Upload all documents into PageIndex and get `doc_id`s.
2. Store document metadata and `doc_id` in a SQL table.
3. Use an LLM to translate the user request into SQL.
4. Use the returned `doc_id`s to call PageIndex retrieval.

This confirms the product need, but direct LLM-to-SQL is too risky as the
long-term PageIndex FileSystem interface. The safer path is LLM-to-validated
query AST or DSL, compiled by PageIndex.

## Conclusion

The current repo should be treated as the natural home for the SDK/local
FileSystem abstraction, but the current workspace implementation should not be
extended directly as the production-scale catalog. It should be migrated behind
a storage adapter.

For the benchmark, this means adding a local catalog/retrieval planner around
the existing style, not replacing PageIndex with a server API.
