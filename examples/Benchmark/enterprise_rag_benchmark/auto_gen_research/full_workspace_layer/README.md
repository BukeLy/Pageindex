# Full Workspace Layer Research

This directory contains controlled experiments for improving PageIndex
FileSystem behavior on the full EnterpriseRAG workspace without changing
benchmark questions, gold answers, or core PIFS APIs.

Current hypothesis:

1. Full-corpus source grep is the failure mode, not the solution.
2. The catalog already has enough raw document metadata, but only a tiny schema
   is registered and queryable.
3. A generated workspace layer should add queryable metadata and semantic folder
   memberships above the flat file catalog.
4. Semantic recall should be a rebuildable index, not a high-cardinality folder
   explosion.

The runner creates a derived workspace by copying only `filesystem.sqlite` from
the full registered workspace, then adds:

- a compact, source-aware metadata schema;
- deterministic metadata extracted from document JSON and text fields;
- semantic folder memberships rooted at `/semantic/source=<source_type>`.
- optional local semantic vector indexes under
  `workspace/semantic_vector_indexes/`.

It deliberately avoids benchmark expected document ids during generation.

## Semantic Vector Index

The local vector adapter is `pageindex.filesystem.semantic_index.SQLiteVecSemanticIndex`.
It uses `sqlite-vec`, stores vectors in a local SQLite database file, and keeps
the main PIFS catalog as the source of truth. `source_type` is stored as a
`vec0` partition key so benchmark-known source filters can narrow KNN before
ranking rather than post-filtering a global top-k list.

The current field ablation compares:

- `summary`: generated `summary` or `semantic_summary` only.
- `metadata`: retrieval metadata text from title, summary, intent, entities,
  constraints, topic, and semantic equivalents.
- `fulltext`: source text preview from raw document content fields.

Local smoke without API spend:

```bash
uv run python examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/full_workspace_layer/run_full_workspace_layer_research.py \
  compare-vector-fields \
  --workspace examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/v2/workspaces/20260515-10docs-semantic-v2-smoke/semantic-metadata-folders-v2 \
  --embedding-provider hash \
  --embedding-model hash-256 \
  --field-modes summary,metadata,fulltext \
  --question-ids qst_0001 \
  --max-docs 10 \
  --candidate-limit 10 \
  --reset
```

Real embedding experiment:

```bash
uv run python examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/full_workspace_layer/run_full_workspace_layer_research.py \
  compare-vector-fields \
  --workspace examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/v2/workspaces/20260517-100docs-semantic-v2-gpt54mini/semantic-metadata-folders-v2 \
  --embedding-provider openai \
  --embedding-model text-embedding-3-small \
  --field-modes summary,metadata,fulltext \
  --question-set timeout10 \
  --candidate-limit 50 \
  --reset
```

The experiment writes `summary.json`, `summary.md`, and per-mode probe files
under `results/vector-field-ablation-*`.
