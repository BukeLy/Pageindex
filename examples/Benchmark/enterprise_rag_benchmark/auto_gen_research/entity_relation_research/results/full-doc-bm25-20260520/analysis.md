# Full-Doc BM25 Analysis

## Setup

- Dataset path: `examples/Benchmark/enterprise_rag_benchmark/dataset/`
- Documents parquet: `data/documents/test.parquet`
- Questions parquet: `data/questions/test.parquet`
- Indexed document universe: `511,958`
- Evaluated questions with expected documents: `470`
- Runtime: `2662.346s`
- Index: local SQLite FTS5/BM25 over full document text.
- No metadata generation, embeddings, or gold-answer leakage were used during indexing.

## Main Result

| strategy | hit@1 | hit@10 | hit@20 | hit@50 | hit@100 | MRR |
|---|---:|---:|---:|---:|---:|---:|
| `content_bm25` | 0.4957 | 0.6745 | 0.7170 | 0.7979 | 0.8298 | 0.5603 |
| `title_boost_bm25` | 0.4957 | 0.6745 | 0.7170 | 0.7979 | 0.8298 | 0.5603 |
| `source_hint_bm25` | 0.2532 | 0.3426 | 0.3638 | 0.4021 | 0.4149 | 0.2850 |
| `query_projection_rrf` | 0.3830 | 0.6404 | 0.6936 | 0.7809 | 0.8191 | 0.4706 |
| `hybrid_title_source_rrf` | 0.4213 | 0.6723 | 0.7170 | 0.7957 | 0.8255 | 0.5021 |

## Per-Source Hit@100

Using `content_bm25`:

| source | hit@100 |
|---|---:|
| confluence | 0.930 |
| jira | 0.900 |
| linear | 0.897 |
| gmail | 0.855 |
| google_drive | 0.833 |
| hubspot | 0.824 |
| github | 0.817 |
| fireflies | 0.800 |
| slack | 0.759 |

## Per-Question-Type Hit@100

Using `content_bm25`:

| question_type | hit@100 |
|---|---:|
| project_related | 1.000 |
| conflicting_info | 1.000 |
| intra_document_reasoning | 0.975 |
| constrained | 0.967 |
| miscellaneous | 0.950 |
| completeness | 0.900 |
| basic | 0.891 |
| semantic | 0.552 |

## Interpretation

Full-text BM25 is a useful fallback but is not enough as the primary agent
filesystem search layer. It reaches only `0.6745` hit@10 and `0.8298` hit@100
on the full 511k-document corpus. That means an agent limited to a small `cat`
budget will still miss too often, and pushing top-100 candidates into context is
not acceptable.

The biggest failure mode is semantic questions: hit@100 is only `0.552`, while
the lexical and project-linked question types are much stronger. This matches
the earlier vector evidence: full-corpus recall is not solved by one pooled
doc-level representation or by raw FTS alone.

The `source_hint_bm25` variant is actively harmful here. Source type should not
be used as a hard or strong automatic reranking signal unless it is explicit and
trusted. In this full-corpus run it drops hit@100 from `0.8298` to `0.4149`.

The next full-corpus experiment should not be another prompt-only or raw FTS
rerun. It should build generated document metadata for the full corpus, then
evaluate:

1. exact metadata narrowing,
2. browseable folder projections over that metadata,
3. semantic recall over generated retrieval summaries and entity/relation
   projections,
4. RRF fusion across metadata summary, entity/relation projection, and lexical
   FTS candidates.
