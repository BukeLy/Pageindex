# Methods

## File-Level Tree

The blog describes a layer above document trees:

```text
Corpus/FileSystem tree
  Folder or virtual node
    Folder or virtual node
      Document leaf
        PageIndex document tree
          Section/page nodes
```

The document leaf is still the entry point into the existing PageIndex
retrieval API. The new part is the corpus tree used to select which documents
should be searched deeply.

## Virtual Nodes

The strongest blog idea is virtual organization. A document may need to appear
under more than one logical branch:

- physical folder placement, such as `Contracts/2024`
- metadata-driven placement, such as `Counterparty/Acme`
- query-specific grouping, such as "termination clauses after 2023"
- generated semantic clusters, such as "supply chain risk"

This means PageIndex should not model FileSystem as a strict single-parent
directory tree only. It needs a physical folder tree plus query-time or
materialized virtual views.

The blog specifically names topic models, LLM-driven grouping, LLM-inferred
metadata, summaries, categories, and key entities as inputs for synthesizing
these virtual internal nodes.

## Dynamic Flattening

The blog's scaling idea is to flatten dynamically when a branch is too wide or
too shallow:

- If a folder has too many children, summarize or group children before showing
  them to the model.
- If metadata can narrow the set cheaply, push down metadata filters before
  invoking expensive LLM traversal.
- If a user asks for a global attribute query, bypass literal folders and build
  a temporary metadata tree or grouped result.

This is closer to a query planner than a file browser.

## Query-Time Traversal

The LLM should not emit arbitrary SQL. It should emit a constrained retrieval
plan:

```json
{
  "scope": {"folder_id": "root", "recursive": true},
  "metadata_filter": {"year": {"$gte": 2024}},
  "text_query": "lease renewal termination",
  "sort": {"effective_date": -1},
  "limit": 20
}
```

The backend validates and executes the plan. PageIndex then opens selected
document trees.

The blog also says traversal feedback should improve future virtual nodes and
metadata. That implies PageIndex FileSystem should record query traces and use
them as offline signals, not just serve one-off searches.

## Design Takeaway

The FileSystem layer should be a first-class corpus index with both physical
folders and virtual, metadata-backed branches. It should feed PageIndex
document-level retrieval with a small, defensible candidate set.
