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

- `baseline_metadata_text`: hit@50 `0.99`
- `summary_only_text`: hit@50 `0.99`
- `entity_constraint_projection`: hit@50 `0.91`
- `entity_relation_projection`: hit@50 `0.98`
- `hybrid_projection_text`: hit@50 `1.00`

Interpretation:

- Pure entity/constraint projection is too lossy.
- Adding relation text recovers much of the missing signal.
- Hybrid projection is the only tested variant with no misses at hit@100 in the
  100-doc profile universe.
- This is still a small profile-based smoke, not full-corpus proof.

## Boundary

- The catalog remains SQLite-backed PIFS and stays the source of truth.
- The entity/relation/constraint index is rebuildable recall infrastructure.
- Results must aggregate back to `file_ref`, `external_id`, `source_path`, and
  folder memberships.
- No benchmark questions, gold answers, or expected ids may be used during
  index generation.
- Do not expose large candidate lists directly to the agent. The target user
  interface is still shell-like `ls` / `find` / `grep` / `cat` behavior.
