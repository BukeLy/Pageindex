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

## Boundary

- The catalog remains SQLite-backed PIFS and stays the source of truth.
- The entity/relation/constraint index is rebuildable recall infrastructure.
- Results must aggregate back to `file_ref`, `external_id`, `source_path`, and
  folder memberships.
- No benchmark questions, gold answers, or expected ids may be used during
  index generation.
- Do not expose large candidate lists directly to the agent. The target user
  interface is still shell-like `ls` / `find` / `grep` / `cat` behavior.
