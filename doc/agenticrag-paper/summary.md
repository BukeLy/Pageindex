# Summary

The AgenticRAG paper is directly relevant to PageIndex FileSystem because it
argues for the same high-level boundary we are converging on: a fast retrieval
backend should provide broad candidate discovery, while a reasoning model
iteratively decides which documents to inspect more deeply.

The paper's system is a lightweight inference-time harness layered on top of
existing enterprise search infrastructure. It does not require model
fine-tuning, graph construction, custom embeddings, or heavy corpus-specific
preprocessing. The harness gives the LLM four tools:

- `search`: enterprise-wide document discovery
- `find`: in-document targeted search
- `open`: bounded-window full document reading
- `summarize`: context compaction while preserving references

This is close to PageIndex's intended direction, but not identical. AgenticRAG
uses a generic search stack plus line/window reading. PageIndex should use the
FileSystem catalog for corpus-level discovery and PageIndex document trees for
in-document navigation.

## Headline Results

The paper evaluates on three public benchmarks:

- BRIGHT long-context retrieval:
  - AgenticRAG reports 49.6% recall@1 with Claude Sonnet 4.5.
  - This is +21.8 percentage points above the best embedding baseline cited in
    the paper.
- WixQA enterprise support QA:
  - AgenticRAG reports 0.96 factuality on the Expert Written split with
    GPT-5-mini.
- FinanceBench financial document QA:
  - AgenticRAG reports 92% answer correctness on 150 questions, within 2 points
    of an oracle setting in the paper.

The most important ablation result is qualitative for PageIndex: moving from
single-shot retrieval to iterative tool use is the main source of improvement.
Multi-query search and in-document navigation matter because they let the model
recover when the first candidate set is incomplete or ambiguous.

## Implications For PageIndex

This paper expands our benchmark target beyond EnterpriseRAG-Bench. PageIndex
FileSystem should also run:

- BRIGHT long-context retrieval
- WixQA support KB QA
- FinanceBench PDF QA

These benchmarks stress different parts of the FileSystem:

- BRIGHT: reasoning-intensive corpus retrieval over many candidate documents
- WixQA: multi-document support answer synthesis over a compact KB
- FinanceBench: precise evidence extraction inside very long PDFs

The FileSystem should therefore expose not just `search`, but also:

- stable document references
- folder/source scopes
- metadata filters
- in-document find
- windowed or tree-based open
- citation/reference tracking
- context compaction or evidence summarization

