# EnterpriseRAG Auto-Gen FileSystem Research

This folder contains controlled experiments for PageIndex FileSystem automatic
metadata and folder generation.

The experiment fixes a small EnterpriseRAG subset, generates document-level
metadata from raw document text with the configured LLM, registers four PIFS
workspace variants, and evaluates them with the same agent retrieval loop.

The metadata comparison is not "fixed vs generated schema". Both metadata
variants use a fixed schema. The experiment compares two fixed-schema
projections of the same LLM-generated `DocumentSemanticProfile`:

- `entity_relation_fixed_schema`: document-level entities and relation triples.
- `dag_fixed_schema`: directed entity/relation graph fields such as nodes,
  edges, direction signatures, roots, leaves, and relation paths.

Folder levels are also generated from the same semantic profiles:

- `topic_count_folder`: summarize documents into topics, cluster topics, and put
  the largest clusters at the highest folder level.
- `entropy_folder`: compute entropy across candidate semantic dimensions and put
  the highest-entropy dimension at the highest folder level.

Matrix:

- `entity_relation_fixed_schema` + `topic_count_folder`
- `entity_relation_fixed_schema` + `entropy_folder`
- `dag_fixed_schema` + `topic_count_folder`
- `dag_fixed_schema` + `entropy_folder`

Run:

```bash
python run_auto_gen_research.py --reset
```

Generate and register a 100-document corpus without running the agent loop:

```bash
python run_auto_gen_research.py --reset --skip-agent --target-docs 100
```

By default the script selects questions in benchmark order until the number of
unique `expected_doc_ids` reaches `--target-docs` (default `10`). It then uses
those expected document ids as the fixed document subset. Passing
`--question-ids` overrides that automatic selection.

Run directories are timestamped by default, for example:

```text
results/20260515-003500-10docs-agent-eval/
workspaces/20260515-003500-10docs-agent-eval/
```

Environment is loaded from the repo `.env` first and the benchmark `.env`
second:

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
PIFS_AGENT_MODEL=gpt-4.1-mini
PIFS_PROFILE_WORKERS=4
PIFS_CLUSTER_BATCH_SIZE=25
```

Generated SQLite workspaces live under `workspaces/` and are ignored by git.
Research inputs, generated metadata, and result summaries are written under
this folder so each run is reproducible.

`generated/doc_profiles.json` is a per-document cache and can be reused across
different corpus sizes. Topic clusters are cached per selected document set as
`generated/topic_clusters_<selection-id>.json`, so a 100-document run does not
overwrite the 10-document cluster layout.
