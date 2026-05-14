# PageIndex FileSystem Research Notes

This directory records the research notes for designing a PageIndex FileSystem.
The notes are split by research object so each conclusion can be traced back to
the source project, branch, file, or public article that informed it.

## Navigation

- `pageindex-filesystem/`
  - `index.md`: final design navigation.
  - `final-design.md`: final PageIndex FileSystem design draft. This is the
    current canonical design document.
- `pageindex-filesystem-blog/`
  - `summary.md`: design intent from the public PageIndex FileSystem blog.
  - `methods.md`: inferred mechanics: file-level tree, virtual nodes, dynamic
    flattening, and query-time traversal.
- `pageindex-current/`
  - `summary.md`: current open-source PageIndex package and workspace behavior.
  - `methods.md`: current `_meta.json` and in-memory retrieval implementation.
- `pageindex-compute/`
  - `summary.md`: backend Folder and metadata implementation on `origin/dev`.
  - `methods.md`: API, DB, validation, and query surface.
  - `folder/structure.md`: Folder table and recursive scope behavior.
  - `metadata/schema.md`: schema declaration and validation behavior.
  - `metadata/query.md`: filter, sort, aggregate, and group-by query mechanics.
- `pageindex-chat/`
  - `summary.md`: MCP-facing Folder and metadata UX on
    `origin/feat/metadata-filter`.
  - `methods.md`: tool-level behavior and backend integration.
  - `folder/structure.md`: `browse_documents` and `get_folder_structure`.
  - `metadata/query.md`: `get_metadata_schema` and
    `search_documents(mode="metadata")`.
- `pageindex-mcp/`
  - `summary.md`: standalone MCP client repository status.
  - `methods.md`: remote proxy behavior and why Folder/metadata logic is not
    implemented there.
- `mirage/`
  - `summary.md`: Mirage as a unified virtual filesystem for agents.
  - `methods.md`: Workspace, mount, resource, ops, cache, and snapshot model.
  - `folder/structure.md`: path and mount organization.
  - `metadata/query.md`: what Mirage does and does not provide for metadata.
- `enterprise-rag-bench/`
  - `summary.md`: benchmark goal, corpus shape, question types, and scoring.
  - `methods.md`: source JSON fields, exported text layout, evaluation format,
    and baseline retrievers.
  - `filesystem-fit.md`: what PageIndex FileSystem must provide to compete on
    this benchmark without becoming a separate service/API.
- `agenticrag-paper/`
  - `summary.md`: understanding of the AgenticRAG paper and why it matters to
    PageIndex FileSystem.
  - `methods.md`: search/find/open/summarize tool harness and context
    management design.
  - `benchmarks/bright.md`: BRIGHT long-context retrieval benchmark notes.
  - `benchmarks/wixqa.md`: WixQA enterprise support QA benchmark notes.
  - `benchmarks/financebench.md`: FinanceBench financial PDF QA benchmark
    notes.
  - `filesystem-fit.md`: additional FileSystem requirements implied by these
    three benchmarks.
- `recommendations/`
  - `pageindex-filesystem-architecture.md`: recommended PageIndex FileSystem
    architecture and migration plan.
  - `enterprise-rag-bench-lightweight-filesystem.md`: benchmark-oriented,
    lightweight architecture for the current PageIndex repo.

## Source Snapshots

- PageIndex current repo: current worktree at
  `/Users/chengjie/.codex/worktrees/6d00/PageIndex`.
- `pageindex-compute`: exported from `origin/dev`
  (`82eece7815c0345f785b7eb2d448660ee7a7a80e`) into
  `/private/tmp/pageindex-compute-origin-dev-20260514`.
- `pageindex-chat`: exported from `origin/feat/metadata-filter`
  (`23292bb95907540f286951223c7521ae0bcc3e3a`) into
  `/private/tmp/pageindex-chat-feat-metadata-filter-20260514`.
- `pageindex-mcp`: temporary read-only clone from public GitHub `master`
  (`fca18e35eb47b8bb0068bef245f7cd746b7fa1ec`) at
  `/tmp/pageindex-mcp-research-20260514`.
- Mirage: public repository clone at `/private/tmp/mirage-src`, latest observed
  commit `c39b14e` from 2026-05-13.
- EnterpriseRAG-Bench: public repository clone at
  `/private/tmp/EnterpriseRAG-Bench-20260514`, latest observed commit
  `d36685e273713975ee20299bbf1ab64165575b3c` from 2026-05-07.
- AgenticRAG paper: arXiv `2605.05538v1`, submitted 2026-05-07.
  Supporting benchmark sources consulted: BRIGHT website/GitHub/Hugging Face,
  WixQA Hugging Face dataset card, and FinanceBench GitHub/benchmark pages.

## Current Design Stance

The PageIndex FileSystem blog is treated as product direction, not as evidence
that PageIndex already has an enterprise filesystem implementation. For the
current open-source PageIndex repo, the first useful implementation should stay
lightweight: a local catalog/retrieval-planning layer plus document-tree
artifacts, not a new hosted API service.

The canonical final design is now
`doc/pageindex-filesystem/final-design.md`.
