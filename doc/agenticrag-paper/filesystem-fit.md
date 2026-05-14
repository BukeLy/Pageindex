# FileSystem Fit For AgenticRAG Benchmarks

## Combined Requirement

To run the AgenticRAG benchmark suite, PageIndex FileSystem needs to support
three benchmark modes:

1. ranked retrieval over long documents
2. multi-document enterprise support QA
3. long-PDF financial evidence extraction

This is broader than EnterpriseRAG-Bench. EnterpriseRAG-Bench mostly stresses
large-corpus document selection plus final answer/document IDs. AgenticRAG's
benchmarks additionally require:

- ranked retrieval output for BRIGHT
- factual answer generation for WixQA
- page-level evidence extraction for FinanceBench

## Tool Contract

The FileSystem should expose an AgenticRAG-compatible local tool layer:

```text
search(query | queries, scope?, metadata_filter?, limit?)
  -> reference_id, doc_id, title, snippet, path, metadata

find(reference_id, patterns, mode?)
  -> passages, line/page ranges, tree node ids

open(reference_id, loc?)
  -> bounded line/page/tree window

summarize(state, preserve_refs)
  -> compact evidence state with references preserved
```

This does not mean copying AgenticRAG's implementation. It means PageIndex
FileSystem should be able to present the same retrieval affordances to an agent
while internally using PageIndex trees and local catalog indexes.

## Storage Implications

The local FileSystem catalog should track benchmark-specific IDs:

- EnterpriseRAG-Bench: `dataset_doc_uuid`
- BRIGHT: dataset document ID / `gold_ids_long`
- WixQA: article `id`
- FinanceBench: `doc_name` and page numbers

Recommended catalog additions:

- `external_id`
- `benchmark_name`
- `split`
- `source_type`
- `path`
- `title`
- `metadata_json`
- `text_artifact_path`
- `tree_artifact_path`
- `page_count`
- `token_count`

This can still live in SQLite and local artifacts. It does not require a new
service.

## Folder Implications

Folder modeling should be benchmark-adaptive:

- EnterpriseRAG-Bench:
  - source system and native hierarchy
- BRIGHT:
  - domain split, query family, original web page path/topic
- WixQA:
  - help-center category/URL path/article type
- FinanceBench:
  - company, period, filing type, source PDF

The key is to separate physical source organization from virtual retrieval
views. For example, FinanceBench's physical document is a filing PDF, but a
virtual view may group by company, year, metric, or statement type.

## Metadata Implications

Metadata must support both corpus routing and answer constraints:

- BRIGHT:
  - domain, split, document ID, source path, long vs short document
- WixQA:
  - article ID, article type, URL/category
- FinanceBench:
  - company, document period, filing type, sector, page count
- EnterpriseRAG-Bench:
  - source type, people, project, customer/account, date, status, labels

The metadata schema should remain flat and typed. Lists can be normalized for
filter/facet use, but raw source metadata should be preserved.

## Query And Evaluation Modes

The runner should support multiple output modes:

- BRIGHT:
  - ranked document IDs and scores
  - Recall@1 / nDCG@10 compatible output
- WixQA:
  - generated answer
  - selected article IDs
  - optional evidence snippets
- FinanceBench:
  - generated answer
  - cited `doc_name`
  - page-level evidence references
- EnterpriseRAG-Bench:
  - JSONL answer with `question_id`, `answer`, and `document_ids`

This argues for a benchmark adapter layer above the same FileSystem primitives,
not separate bespoke retrieval systems for each benchmark.

## Architecture Adjustment

The current PageIndex FileSystem recommendation should add an explicit
"agentic tool facade":

```text
Benchmark Adapter
  loads questions
  normalizes expected IDs
  writes benchmark-specific outputs

Agentic Tool Facade
  search
  find
  open
  summarize

FileSystem Catalog
  folders
  documents
  metadata
  FTS / candidate retrieval

PageIndex Document Layer
  document trees
  page / line / node content
  evidence extraction
```

This keeps the repo lightweight while allowing PageIndex to run the same
benchmark families used in the AgenticRAG paper.

## Main Design Change From Previous Recommendation

The previous EnterpriseRAG-Bench plan focused on:

```text
catalog -> candidate docs -> PageIndex document tools -> answer + doc_ids
```

After reading AgenticRAG, the plan should become:

```text
catalog -> agentic search/find/open loop -> PageIndex document tools -> benchmark adapter output
```

The difference is not large architecturally, but it is important operationally:
the FileSystem must support iterative retrieval, not just one-shot candidate
selection.

