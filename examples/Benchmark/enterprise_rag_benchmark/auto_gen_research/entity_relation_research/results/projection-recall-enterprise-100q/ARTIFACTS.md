# EnterpriseRAG Projection Recall Artifacts

Generated on 2026-05-21 for EnterpriseRAG questions 1-100 offline retrieval eval.

Keep the full-corpus projection index databases, not only the metric summary.
These files are rebuildable semantic indexes, but the rebuild is expensive.

## Index Databases To Preserve

Index directory:

`examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/entity_relation_research/cache/projection_indexes/enterprise_full_v1`

Total size at creation: `25G`

SQLite files:

| file | size | notes |
|---|---:|---|
| `metadata_composite_vector.sqlite` | 1.2G | baseline metadata vector index |
| `summary_only_vector.sqlite` | 1.2G | summary-only vector index |
| `entity_vectors.sqlite` | 4.8G | entity projection vector index |
| `constraint_vectors.sqlite` | 4.8G | constraint projection vector index |
| `relation_vectors.sqlite` | 4.8G | relation projection vector index |
| `embedding_cache.sqlite` | 8.3G | OpenAI embedding cache used during build and probe |
| `query_projection_cache.sqlite` | 108K | cached question/query projections |

## Eval Outputs

Result directory:

`examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/entity_relation_research/results/projection-recall-enterprise-100q`

Files:

| file | size | notes |
|---|---:|---|
| `summary.md` | 1.3K | human-readable metrics table |
| `summary.json` | 4.6M | full per-strategy eval details and misses |

## Gate Decision

`hybrid_entity_relation_vector hit@50 = 0.73`, below the `0.90` threshold.
Do not run the agent stage from this run.
