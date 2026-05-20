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

## Current Recommendation

For PIFS doc-level recall, the next default candidate should be:

1. exact metadata filters when the agent has a precise filter,
2. `rrf_summary_metadata_relation` for doc-level candidate ranking,
3. optional source-hint boosting only when source type is explicitly available
   or reliably inferred from query/tool context,
4. final evidence through `cat/open`, not through vector snippets.

Do not promote:

- summary-only as the only recall index,
- entity/constraint-only projection,
- pseudo relevance feedback,
- deep top-1000 candidate exposure to the agent.

The missing experiment is a full-workspace run of
`rrf_summary_metadata_relation` and `hybrid_rrf_exact_source` against a
decoy-heavy corpus. The current worktree does not contain the full workspace
SQLite or dataset directory needed to run that exact test now.
