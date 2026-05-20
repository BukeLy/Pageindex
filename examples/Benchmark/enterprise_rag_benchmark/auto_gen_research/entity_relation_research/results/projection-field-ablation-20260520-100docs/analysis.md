# Projection Field Ablation Analysis

This run evaluates five document-level projection strategies over the selected
100-doc EnterpriseRAG profile universe. It is offline and API-free: scoring is
lexical BM25-style over projection text, not embeddings.

## Main Result

| strategy | hit@10 | hit@50 | MRR | misses@100 |
|---|---:|---:|---:|---:|
| `baseline_metadata_text` | 0.99 | 1.00 | 0.9716 | 0 |
| `summary_only_text` | 0.99 | 1.00 | 0.9412 | 0 |
| `entity_constraint_projection` | 0.94 | 0.95 | 0.8592 | 5 |
| `entity_relation_projection` | 0.98 | 1.00 | 0.9502 | 0 |
| `hybrid_projection_text` | 0.98 | 1.00 | 0.9645 | 0 |

## Insight

The strongest baseline is still the generated metadata text. In this selected
100-doc universe, `semantic_summary` plus existing profile fields already carry
almost all question-answering signal.

`entity_constraint_projection` is not sufficient by itself. It loses recall on
GitHub, Slack, Fireflies, and HubSpot questions because the projection strips
away too much context around why an entity or constraint matters.

`entity_relation_projection` recovers the missing recall. This supports the
original hypothesis that entity embeddings alone are too pooled and lossy, while
entity + relation + constraint text preserves enough retrieval semantics to be a
useful semantic index candidate.

`hybrid_projection_text` is the safest candidate for a future semantic recall
adapter because it keeps metadata text and appends entity/relation/constraint
signals. It has no hit@100 misses, but it does not outperform
`baseline_metadata_text` on MRR in this small universe.

## Failure Cases

`qst_0090` is the hard case across metadata, summary, relation, and hybrid. It
asks for a HubSpot call summarization turnaround time. The expected document is
ranked between 21 and 29 depending on projection, so top-10 agent behavior would
likely miss it without additional narrowing.

`qst_0082` is sensitive to relation projection. It is a Fireflies meeting
question about the cause of an early December latency incident. Relation-only
projection ranks it worse than summary text, and hybrid still leaves it at rank
12.

## Recommendation

Do not promote entity-only or entity+constraint-only indexing. If we add a
rebuildable semantic index to PIFS, the candidate should be either:

1. `metadata_summary` embedding for the cheapest first pass, or
2. `hybrid_projection_text` embedding for a relation-aware pass.

The next meaningful experiment needs a decoy-heavy/full workspace. This 100-doc
set is useful for checking whether a projection has signal, but it is too easy
to prove scale behavior because every document is selected from benchmark
expected documents.
