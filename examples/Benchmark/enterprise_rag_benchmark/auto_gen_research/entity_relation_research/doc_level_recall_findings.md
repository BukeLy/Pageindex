# Doc-Level Recall Findings

This note keeps the current doc-level recall conclusions in one place so future
work does not mix results from different experiment scopes.

## Existing Full-Workspace Vector Results

Existing OpenAI vector probes under
`../full_workspace_layer/results/` are the strongest evidence that full
workspace recall is much harder than the selected 100-doc experiments.

| result file | scope | method | key result |
|---|---|---|---|
| `vector-probe-metadata-openai-20260520-021455.json` | 500 questions, 470 answerable | metadata embedding | hit@10 `0.474`, hit@50 `0.632`, hit@limit `0.730` |
| `vector-probe-fulltext-openai-20260520-031319.json` | 500 questions, 470 answerable | fulltext preview embedding | hit@10 `0.532`, hit@50 `0.666`, hit@limit `0.748` |
| `vector-union-openai-20260520-033955.json` | 500 questions, 470 answerable | metadata + fulltext union | hit@10 `0.530`, hit@50 `0.662`, hit@limit `0.838` |
| `vector-rerank-metadata-openai-20260520-013220.json` | 30 questions | metadata vector + LLM rerank | vector hit@10 `1.000`, rerank hit@10 `1.000` |

Interpretation:

- Full-workspace vector recall is not solved by a single doc-level pooled
  embedding.
- Fulltext preview embedding beats metadata embedding, but not enough.
- Deep candidate pools can contain the answer, but exposing hundreds of
  candidates to an agent is not acceptable.
- The 30-question rerank result is promising but too small and does not prove
  full benchmark behavior.

## Selected 100-Doc Profile Results

The selected 100-doc experiments are useful for method ablations, but they are
not full-corpus proof because the universe is curated around expected docs.

Latest method ablation:

| strategy | hit@1 | hit@10 | MRR |
|---|---:|---:|---:|
| `baseline_metadata_text` | 0.96 | 0.99 | 0.9716 |
| `summary_only_text` | 0.91 | 0.99 | 0.9412 |
| `rrf_summary_metadata_relation` | 0.97 | 0.99 | 0.9768 |
| `hybrid_rrf_exact_source` | 0.96 | 1.00 | 0.9733 |
| `prf_metadata_expansion` | 0.89 | 0.99 | 0.9226 |

Interpretation:

- Summary-only is not bad in the 100-doc universe. It is strong but lower MRR
  than metadata and RRF fusion.
- RRF over summary, metadata, and entity-relation is the cleanest local
  improvement.
- Hybrid RRF + exact/source hints fixes the only top-10 miss in this universe,
  but the source-hint part must be validated on a larger workspace.
- Pseudo relevance feedback is harmful here and should not be promoted.

## Full-Doc BM25 Results

The full EnterpriseRAG documents parquet has now been restored under
`examples/Benchmark/enterprise_rag_benchmark/dataset/` and indexed with local
SQLite FTS5/BM25.

Run:

`results/full-doc-bm25-20260520/`

Scope:

- `511,958` indexed documents.
- `470` answerable questions.
- no generated metadata,
- no embeddings,
- no benchmark gold answers during indexing.

| strategy | hit@1 | hit@10 | hit@20 | hit@50 | hit@100 | MRR |
|---|---:|---:|---:|---:|---:|---:|
| `content_bm25` | 0.4957 | 0.6745 | 0.7170 | 0.7979 | 0.8298 | 0.5603 |
| `title_boost_bm25` | 0.4957 | 0.6745 | 0.7170 | 0.7979 | 0.8298 | 0.5603 |
| `source_hint_bm25` | 0.2532 | 0.3426 | 0.3638 | 0.4021 | 0.4149 | 0.2850 |
| `query_projection_rrf` | 0.3830 | 0.6404 | 0.6936 | 0.7809 | 0.8191 | 0.4706 |
| `hybrid_title_source_rrf` | 0.4213 | 0.6723 | 0.7170 | 0.7957 | 0.8255 | 0.5021 |

Interpretation:

- Full-text BM25 is better than the previous full-workspace pooled embedding
  probes at hit@10, but it still only reaches `0.6745` hit@10 and `0.8298`
  hit@100.
- The hard part is semantic questions: `content_bm25` hit@100 is `0.552` for
  semantic questions, while lexical/project-linked categories are much higher.
- Source hinting is unsafe as an automatic reranking signal. It cuts hit@100
  from `0.8298` to `0.4149`.
- Full-doc evidence confirms that selected 100-doc results overstate recall.
  They are useful for method ablation, not final proof.

## Current Recommendation

For PIFS doc-level recall, the next default candidate should be:

1. exact metadata filters when the agent has a precise filter,
2. generated retrieval metadata as the workspace-level narrowing layer,
3. browseable folders as corpus-level projections over generated metadata,
4. semantic recall over generated summaries and entity/relation projections,
5. RRF fusion between semantic candidates and lexical FTS candidates,
6. final evidence through `cat/open`, not through vector snippets.

Do not promote:

- summary-only as the only recall index,
- entity/constraint-only projection,
- pseudo relevance feedback,
- automatic source-type reranking unless the source is explicit and trusted,
- raw full-text BM25 as the only retrieval layer,
- deep top-1000 candidate exposure to the agent.

The missing experiment is now narrower: build full-corpus generated metadata
and test metadata narrowing plus semantic/folder projections on the same
511k-document workspace. Raw full-doc FTS alone is measured and is not enough.
