# PageIndex Core Bridge for PIFS Register

## Current State

PIFS registration is currently a catalog operation over already-extracted text.
`PageIndexFileSystem.register_file()` and `register_files()` accept caller-supplied
`content`, write it to `artifacts/text`, index that text into SQLite FTS, and
record raw registration provenance in `artifacts/raw`. `open`, `cat`, and
`grep` read from the stored text artifact; recursive `grep -R` is still lexical
search over registered text and metadata, not a semantic or PageIndex tree
reader.

The original PageIndex Core flow is separate:

- `PageIndexClient.index(file_path, mode="auto")` detects `.pdf`, `.md`, and
  `.markdown`.
- PDF inputs call `page_index(...)`, which builds a PageIndex document tree and
  can add node summaries, node text, node ids, and a document description.
  The client then extracts per-page text with `PyPDF2` and persists document
  JSON when a client workspace is configured.
- Markdown inputs call `md_to_tree(...)`, which parses Markdown headings into a
  tree and can optionally generate node summaries and a document description.
- Other inputs fail with `ValueError("Unsupported file format ...")`.

The PIFS store already has bridge-shaped fields: `pageindex_doc_id`,
`pageindex_tree_status`, and an `artifacts/pageindex_trees` directory. Today
`_prepare_file_record()` always sets `pageindex_doc_id=None` and
`pageindex_tree_status="not_built"`, so those fields are reserved but not wired
to PageIndex Core.

## Gap

There is no public PIFS API that takes a source path, detects whether it is PDF,
Markdown, or plain text/JSON/JSONL, extracts text for grep/FTS, optionally builds
a PageIndex tree, and registers the resulting artifacts into the PIFS catalog.
Callers must pre-extract text before registration. Wiring `register_file()`
directly to PageIndex Core would also be risky because the current Core defaults
can trigger LLM calls for summaries and descriptions.

## Target API

Add a source-path API instead of changing the existing text-registration
contract:

```python
file_ref = filesystem.register_path(
    "/data/report.pdf",
    folder_path="/reports",
    metadata={"source_type": "local"},
    external_id="report-2026",
    pageindex_policy={
        "tree": "lazy",
        "summaries": "disabled",
    },
)
```

Equivalent names such as `register_source_path(...)` are acceptable, but the API
should make the difference from `register_file(content=...)` obvious:

- `register_file(s)` remains the low-level catalog call for known text
  artifacts.
- `register_path(...)` owns local file detection, raw artifact/provenance,
  text extraction, and optional PageIndex tree materialization.
- Batch-oriented code should get a `register_paths(...)` equivalent once the
  single-path behavior is stable.

## Format Detection Policy

The bridge should accept:

- `.pdf` or `application/pdf`: PageIndex Core candidate.
- `.md`, `.markdown`, `text/markdown`, `text/x-markdown`, or
  `application/markdown`: PageIndex Core candidate.
- `.txt`, `.text`, or `text/plain`: direct text artifact candidate.
- `.json` or `application/json`: direct text artifact candidate.
- `.jsonl`, `.ndjson`, `application/jsonl`, `application/x-jsonlines`, or
  `application/x-ndjson`: direct text artifact candidate.

Unsupported extensions or content types should fail before registration with a
clear message listing supported inputs. Detection must be a pure step and should
not read files, index PDFs, call LLMs, or write PIFS state.

## Eager, Lazy, and Pending Policy

Default behavior for large corpora should not eagerly run LLM-backed PageIndex
tree generation. Recommended policy:

- Text/JSON/JSONL: eagerly write the text artifact and FTS row because this is
  cheap and preserves current `grep`/`cat` behavior.
- PDF: extract a text artifact for grep/FTS if a cheap parser is available;
  set `pageindex_tree_status="not_built"` unless the caller explicitly requests
  tree build.
- Markdown: parse or copy enough text for grep/FTS eagerly; build the tree lazily
  by default unless the caller opts into a no-summary structural tree.
- `tree="pending"` should register the file and enqueue or mark work without
  running Core inline.
- `tree="eager"` must be explicit, bounded, and should default summary/doc
  description generation to disabled for corpus-scale ingestion.

## Artifact Model

PIFS should store three artifact classes separately:

- Text artifact: normalized text used by `grep`, SQLite FTS, and current
  `open`/`cat` line windows.
- PageIndex tree artifact: JSON tree stored under `artifacts/pageindex_trees`
  for deep reading, structure navigation, and page/section lookup.
- Raw artifact/provenance: source URI, source path, original content type,
  extraction options, parser version, and failures stored in `artifacts/raw`.

The catalog fields should point to or summarize this state:

- `text_artifact_path`: always present for searchable registered files.
- `raw_artifact_path`: provenance for replay/debugging.
- `pageindex_doc_id`: Core document id or tree artifact id when built.
- `pageindex_tree_status`: `not_built`, `pending`, `building`, `built`, or
  `failed`.

## Future Read-Only Agent Surface

The Agent-facing PIFS surface should stay read-only. Do not restore `cp`,
`mkdir`, or registration commands to the command executor.

Future read APIs can expose richer evidence without mutation commands:

- `cat --structure <ref>` or `structure <ref>` for a compact tree.
- `cat --pages 5-7 <ref>` for page windows.
- `cat --node <node_id> <ref>` for bounded section text.

These should read cached artifacts or clearly report that structure is pending
or unavailable.

## Metadata Boundary

PageIndex extraction is document structure and content extraction. It is not the
same as PIFS retrieval metadata generation.

- PageIndex tree fields describe headings, pages, nodes, and section text.
- PIFS derived metadata fields such as `summary`, `doc_type`, `domain`, `topic`,
  `entity`, and `relation` drive retrieval, exact metadata filters, semantic
  channels, and folder projection.
- A PageIndex document description can be useful provenance or a candidate input
  to metadata generation, but it should not silently satisfy PIFS metadata
  policy.

Keep these jobs explicit so a corpus can build cheap text/structure artifacts
without accidentally paying for retrieval metadata summaries.

## Risks

- Cost: current `PageIndexClient.index()` requests summaries and document
  descriptions for PDF and Markdown, which can be expensive at corpus scale.
- Memory and latency: PDF extraction and tree building may load large documents
  and can be slow for long files.
- Summary defaults: eager summary generation is not a safe default for bulk PIFS
  ingestion.
- Parser limits: `PyPDF2` text extraction may miss OCR-only or complex-layout
  PDFs; PageIndex PDF structure depends on TOC and parser quality.
- Failure recovery: tree build failures must not corrupt the text artifact or
  remove catalog searchability.
- Semantics drift: PageIndex Core extraction should not change the shell-like
  lexical contract of `grep -R`.

## Recommended Implementation Phases

1. Add pure detection and policy types for supported path/content-type inputs.
2. Add `register_path(..., tree="lazy")` for text/JSON/JSONL only, preserving the
   existing `register_file()` behavior internally.
3. Add cheap PDF and Markdown text-artifact extraction with `pageindex_tree_status`
   left as `not_built` or `pending`.
4. Add no-summary Markdown structural tree build and cache it under
   `artifacts/pageindex_trees`.
5. Add explicit PDF tree build behind an opt-in policy, with failures recorded
   as `failed` while keeping text search available.
6. Add read-only structure/page surfaces after cached artifacts exist.
7. Add corpus-scale tests and benchmarks before enabling any eager LLM summary
   or retrieval metadata generation defaults.
