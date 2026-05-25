# PIFS Structural Read Tools Backed by PageIndex Core

## Framing

PIFS is a PageIndex filesystem and retrieval layer. PageIndex Core is the
single-document structure and page-content engine that can back PIFS read tools
when a structural artifact is available.

The natural agent surface stays bash-like and read-only:

```bash
stat dsid_report
tree /reports --depth 2
cat --all dsid_report
cat --structure dsid_report
cat --node 0007 dsid_report
cat --page 5-7 dsid_report
```

This keeps folder hierarchy under `tree`, file content views under `cat`, and
metadata/status under `stat`. It does not restore mutation commands such as
`cp`, `mkdir`, or agent-facing registration.

## Reference: Agentic Vectorless RAG Demo

`examples/agentic_vectorless_rag_demo.py` already shows the PageIndex tool
pattern:

- `get_document()` returns document metadata such as status and page count.
- `get_document_structure()` returns the document tree without text so the agent
  can pick relevant sections.
- `get_page_content(pages="5-7")` returns tight page or line ranges instead of
  dumping a whole document.

The agent prompt instructs the model to inspect metadata first, inspect the
structure second, then fetch only tight page ranges. That maps directly to the
PIFS command shape:

- `stat <file>` for catalog metadata.
- `cat --structure <file>` for PageIndex document structure.
- `cat --node <node_id> <file>` for a bounded structural section.
- `cat --page <range> <file>` for tight page windows when page content is
  cached.

PIFS should preserve this retrieval discipline while making it feel like a
filesystem, not a separate SDK.

## Current State

`PageIndexFileSystem.register_file()` and `register_files()` currently accept
already-extracted text, write it to `artifacts/text`, index it into SQLite FTS,
and record raw registration provenance under `artifacts/raw`. Existing `open`,
`cat --all`, and `grep` read the text artifact.

The store already reserves PageIndex structural state:

- `pageindex_doc_id`
- `pageindex_tree_status`

PDF and Markdown registration must populate those fields synchronously. PIFS
first checks the normal `PageIndexClient` workspace under
`<pifs workspace>/artifacts/pageindex_client`; when `_meta.json` already has a
document whose canonical path matches the registered file, PIFS reuses that
`doc_id`. On cache miss, registration calls `PageIndexClient.index(source_path)`
and records the returned `pageindex_doc_id` with
`pageindex_tree_status="built"`. If indexing raises, registration records a
clear failed status so reads can report unavailability without retrying work.

Metadata generation is intentionally out of scope for this PR. Existing
`metadata_generation` policy/status behavior can remain `pending_generate`
unless generated metadata is supplied by the caller.

## Product Gap

PIFS has shell-like text reads but lacks a PageIndex document structure read
path. The command surface should make the file format boundary explicit:

- Text files use `cat --all`.
- PDF/Markdown files use `cat --structure`, `cat --page`, and `cat --node`.
- Unsupported formats fail clearly before reading an artifact.

The registration path owns PageIndex tree creation. Read tools are consumers of
the pointer recorded at registration time.

## Read-Only Command Semantics

### `stat <ref>`

Returns the file catalog metadata and status. For PageIndex-backed files this
must include `pageindex_doc_id` and `pageindex_tree_status`, so an agent can see
whether a cached structural read is available before asking for deep content.

### `tree <folder> [--depth N]`

Keeps the existing folder tree behavior only. Do not extend `tree` to file
internal PageIndex structure: native `tree` means directory hierarchy, and using
it for a PDF/Markdown document structure makes folder navigation and document
navigation too easy to confuse.

### `cat --structure <file>`

Returns a cached PageIndex document structure for a PDF or Markdown file. This
uses PageIndex Core's original terminology. It is a read-only textual view of
file content, so it belongs under `cat` rather than the directory-oriented
`tree` command. Text fields should be removed from the structure view so this
stays a navigation surface.

If no cached structure exists for the file, return a clear unavailable result
with the current `pageindex_tree_status`. Do not build the structure inline or
link a cache entry from a read command.

Use `--structure`, not `--index`: "index" is ambiguous between PageIndex tree
artifacts, metadata indexes, SQLite FTS, and semantic retrieval indexes.

### `cat --node <node_id> <file>`

Returns text for a cached PageIndex node in a PDF or Markdown file. The response
should include the node metadata and bounded text. If the node is missing or the
artifact has no node text, return a clear read-only miss.

### `cat --page <pages> <file>`

Returns cached page or line text for tight ranges such as `5-7`, `3,8`, or
`12` in a PDF or Markdown file. This mirrors the demo's
`get_page_content(pages=...)` tool. If page content is not cached, report that
it is unavailable rather than reparsing the PDF or Markdown source.

Use `--page`, not `--page-content`: `cat` already means content output, so the
option only needs to say which page range to read.

### `cat --all <file>`

Reads the full text artifact for txt/text files only. PDF and Markdown files
should use `cat --structure`, `cat --page`, or `cat --node` so agents keep the
PageIndex structural workflow instead of dumping whole extracted documents.

## Artifact Model

PIFS should keep artifact responsibilities separate:

- Text artifact: line-oriented content for `cat --all`, `grep`, and SQLite FTS.
  The `cat --all` command is only exposed for txt/text files.
- PageIndexClient workspace: canonical PageIndex document cache under
  `artifacts/pageindex_client`, using the same `_meta.json` and `<doc_id>.json`
  files as `PageIndexClient(workspace=...)`.
- SQLite pointers: PIFS stores only `pageindex_doc_id` and
  `pageindex_tree_status` for PageIndex structure/page availability.
- Raw artifact/provenance: source URI/path, extraction options, parser version,
  and failure details under `artifacts/raw`.

The structural read tools should call `PageIndexClient.get_document_structure`
and `PageIndexClient.get_page_content`. They should not read a custom PIFS
`pageindex_trees` JSON schema. They should also not call
`PageIndexClient.index()`, `page_index(...)`, `md_to_tree(...)`, or any LLM
during read commands.

## Ingestion Boundary

Ingestion can later detect `.pdf`, `.md`, `.markdown`, text, JSON, and JSONL,
but that is an implementation detail behind PIFS registration or background
jobs. It should not define the agent-facing product surface.

Large corpus defaults should remain explicit:

- PDF and Markdown registration eagerly builds the PageIndex Core structure
  unless a matching PageIndexClient cache entry already exists.
- Do not add a mode flag that disables this default behavior.
- Preserve text search even when structural extraction fails.
- Resolve the PageIndexClient cache before indexing: if
  `_meta.json` already contains a document whose canonical absolute `path`
  matches the registered source file, reuse that `doc_id`, update the SQLite
  pointer, and do not index again.
- Read commands never call `PageIndexClient.index()` and never repair missing
  SQLite pointers. If a legacy or corrupted record lacks `pageindex_doc_id`,
  reads return unavailable even if the PageIndexClient workspace contains a
  matching document.

## Format Detection

The read tools should gate commands using the smallest available local signal:

- Prefer explicit `content_type` when it is specific, such as `application/pdf`,
  `text/markdown`, or `application/json`.
- Treat the current default `text/plain` as a weak signal because older PIFS
  registrations did not always carry precise original formats.
- Use `source_path` suffixes as a practical fallback and override for that weak
  default: `.txt`/`.text` for `cat --all`, and `.pdf`, `.md`, `.markdown` for
  PageIndex structural reads.
- Treat existing `pageindex_doc_id` or non-`not_built` `pageindex_tree_status`
  as a PageIndex structural signal for legacy records that lack a precise
  content type or suffix.

Unsupported combinations should fail clearly. For example, `cat --all` on a PDF
should point the agent to `cat --structure`/`--page`/`--node`, while
`cat --structure` on a txt file should point it back to `cat --all`.

## Metadata Boundary

PageIndex structure extraction is not the same as PIFS retrieval metadata
generation.

- PageIndex Core describes document structure, page ranges, nodes, and section
  text.
- PIFS metadata generation produces retrieval fields such as `summary`,
  `doc_type`, `domain`, `topic`, `entity`, and `relation`.
- A PageIndex document description can be an input to later metadata generation,
  but it should not silently satisfy the PIFS metadata policy.

Future metadata-generation PR semantics:

- Default non-batch register should synchronously generate requested metadata
  fields such as `summary`, `doc_type`, `domain`, and `topic`.
- Batch mode should leave files pending and expose a separate bulk generation
  API/function.
- PDF/Markdown metadata generation should use PageIndex-extracted text as its
  document body, not raw PDF bytes, title/path/URL/storage URI values, benchmark
  ids, or document ids as semantic evidence.
- Summary projection indexing should only expose `search-summary` when a real
  summary semantic index/capability exists.

## Recommended Phases

1. Add cached-only `cat --structure`, `cat --node`, and `cat --page` skeletons
   that never build or mutate artifacts inline.
2. Add tests proving the command surface remains read-only and missing
   structural artifacts fail clearly.
3. Add a no-summary Markdown structure writer through PageIndex Core if a
   cheaper default registration path is needed later.
4. Add cheap PDF page-text extraction for the text artifact while keeping the
   PageIndexClient workspace as the only structural cache.
5. Add explicit monitoring and retry handling for failed PDF/Markdown
   registration builds.
6. Add agent guidance to prefer `stat`, `cat --structure`, and tight
   `cat --node`/`cat --page` reads over `cat --all`.
