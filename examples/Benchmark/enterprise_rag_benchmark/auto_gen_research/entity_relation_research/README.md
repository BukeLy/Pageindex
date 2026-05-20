# Entity Relation Research

This directory is for the next PageIndex FileSystem retrieval experiment:
testing whether document-level entity, relation, and constraint projections can
improve recall without turning PIFS into chunk-level traditional RAG.

The experiment is research-only for now. It should not change PIFS core until
offline recall shows that the projection index is useful at full-workspace
scale.

## Files

- `experiment_design.md`: experiment goal, matrix, metrics, and promotion
  criteria.
- `sources.md`: papers, systems, and local implementation references that
  inspired the experiment. Keep this updated when adding new ideas so future
  writeups can cite the original source.
- `run_projection_recall.py`: local JSON/profile based offline recall preflight.
  It does not require DuckDB, a vector DB, or external APIs.

## Current Smoke

Run:

```bash
uv run python examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/entity_relation_research/run_projection_recall.py \
  --candidate-limit 100
```

Input:

- 100 selected EnterpriseRAG questions from
  `auto_gen_research/results/20260515-100docs-agent-eval/selected_questions.json`.
- 100 selected documents from
  `auto_gen_research/results/20260515-100docs-agent-eval/selected_documents.json`.
- Generated document profiles from `auto_gen_research/generated/doc_profiles.json`.

Latest result:

- `baseline_metadata_text`: hit@10 `0.99`, hit@50 `1.00`, MRR `0.9716`
- `summary_only_text`: hit@10 `0.99`, hit@50 `1.00`, MRR `0.9412`
- `entity_constraint_projection`: hit@10 `0.94`, hit@50 `0.95`, MRR `0.8592`
- `entity_relation_projection`: hit@10 `0.98`, hit@50 `1.00`, MRR `0.9502`
- `hybrid_projection_text`: hit@10 `0.98`, hit@50 `1.00`, MRR `0.9645`

Canonical result directory:

- `results/projection-field-ablation-20260520-100docs/`
- `results/projection-method-ablation-20260520-100docs/`

Interpretation:

- Pure entity/constraint projection is too lossy. It misses five questions even
  at hit@100.
- Adding relation text recovers the lost recall, which supports keeping
  relation-aware projection as a candidate semantic index.
- Hybrid projection is robust, but it does not beat full metadata text on MRR in
  this 100-doc universe.
- This result is not full-corpus proof because the document universe is the
  selected 100-doc benchmark profile set, not a large decoy-heavy workspace.

Method ablation update:

- `rrf_summary_metadata_relation` has the best MRR among tested local methods:
  `0.9768`.
- `hybrid_rrf_exact_source` is the only tested local method with hit@10 `1.00`.
- Pseudo relevance feedback hurts MRR in this dataset and should not be
  promoted.
- Entity/constraint-only remains rejected.

## Boundary

- The catalog remains SQLite-backed PIFS and stays the source of truth.
- The entity/relation/constraint index is rebuildable recall infrastructure.
- Results must aggregate back to `file_ref`, `external_id`, `source_path`, and
  folder memberships.
- No benchmark questions, gold answers, or expected ids may be used during
  index generation.
- Do not expose large candidate lists directly to the agent. The target user
  interface is still shell-like `ls` / `find` / `grep` / `cat` behavior.
