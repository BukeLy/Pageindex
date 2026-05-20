# Projection Method Ablation Analysis

This run tests doc-level recall methods inspired by the previously recorded
retrieval papers and systems notes. It reuses the existing 100 selected
EnterpriseRAG documents and generated document profiles. It does not call an
external API and does not regenerate metadata.

## Methods

| method | idea source | implementation shape |
|---|---|---|
| `rrf_summary_metadata_relation` | Hybrid retrieval / RRF-style rank fusion | Fuse summary, metadata, and entity-relation rankings. |
| `multi_probe_rrf` | AgenticRAG / IRCoT multi-probe retrieval | Search multiple query projections and fuse results. |
| `pseudo_late_interaction` | ColBERT late interaction | Score separate field/query channels and combine normalized evidence. |
| `constraint_exact_boost` | SPLADE-style sparse exact evidence | Boost exact identifiers, numbers, constraints, and long terms. |
| `source_hint_boost` | Enterprise metadata filtering | Use query text heuristics to add a source-type prior without reading gold source labels. |
| `prf_metadata_expansion` | Pseudo relevance feedback | Expand the query from top summary hits, then rerank metadata/hybrid fields. |
| `hybrid_rrf_exact_source` | HybridRAG-style combined evidence | Fuse summary/metadata/relation ranks, then add exact-term and source-hint boosts. |

## Result

| strategy | hit@10 | hit@20 | hit@50 | hit@100 | MRR | misses@100 |
|---|---:|---:|---:|---:|---:|---:|
| `baseline_metadata_text` | 0.99 | 0.99 | 1.00 | 1.00 | 0.9716 | 0 |
| `summary_only_text` | 0.99 | 0.99 | 1.00 | 1.00 | 0.9412 | 0 |
| `entity_constraint_projection` | 0.94 | 0.95 | 0.95 | 0.95 | 0.8592 | 5 |
| `entity_relation_projection` | 0.98 | 0.98 | 1.00 | 1.00 | 0.9502 | 0 |
| `hybrid_projection_text` | 0.98 | 0.99 | 1.00 | 1.00 | 0.9645 | 0 |
| `rrf_summary_metadata_relation` | 0.99 | 1.00 | 1.00 | 1.00 | 0.9768 | 0 |
| `multi_probe_rrf` | 0.99 | 1.00 | 1.00 | 1.00 | 0.9718 | 0 |
| `pseudo_late_interaction` | 0.98 | 0.99 | 1.00 | 1.00 | 0.9662 | 0 |
| `constraint_exact_boost` | 0.98 | 0.99 | 1.00 | 1.00 | 0.9644 | 0 |
| `source_hint_boost` | 0.99 | 1.00 | 1.00 | 1.00 | 0.9527 | 0 |
| `prf_metadata_expansion` | 0.99 | 0.99 | 1.00 | 1.00 | 0.9226 | 0 |
| `hybrid_rrf_exact_source` | 1.00 | 1.00 | 1.00 | 1.00 | 0.9733 | 0 |

## Insight

`rrf_summary_metadata_relation` is the best pure rank-fusion method. It improves
MRR from the metadata baseline `0.9716` to `0.9768` without source heuristics.
This is the cleanest candidate for a default doc-level fusion layer.

`hybrid_rrf_exact_source` is the only method that gets every question into
top-10. The main improvement is `qst_0090`, a HubSpot call-summarization
question. It moves from rank 26 under metadata baseline to rank 6. This suggests
that a source-type prior can be useful, but it should be treated cautiously
because source classification must come from query text or agent context, not
from benchmark labels.

`multi_probe_rrf` is close to metadata baseline and helps the same hard HubSpot
case, moving `qst_0090` from rank 26 to rank 13. It is useful but weaker than
simple field RRF in this run.

`pseudo_late_interaction` does not beat RRF here. It helps `qst_0090` slightly
but hurts `qst_0082`, a Fireflies meeting incident question.

`constraint_exact_boost` does not improve this 100-doc profile universe. Exact
terms are still important for real PIFS behavior, but this specific boost is
not ready to promote.

`prf_metadata_expansion` is harmful. It drops MRR to `0.9226` and pushes
`qst_0090` from rank 26 to rank 37. Do not use pseudo-relevance feedback as a
default.

## Recommendation

Promote `rrf_summary_metadata_relation` as the next candidate local doc-level
ranking method. Keep `hybrid_rrf_exact_source` as an experimental variant until
source-hint extraction is validated on a larger and less curated workspace.

Do not promote:

- entity/constraint-only projection,
- pseudo relevance feedback,
- the current exact-boost-only variant.

The next full-workspace test should reuse the same method names and inputs, but
run against a decoy-heavy corpus. The current worktree does not contain a full
workspace SQLite or full dataset directory, so this run is limited to the saved
100-doc profile asset.
