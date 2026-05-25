# EnterpriseRAG Semantic Metadata Pipeline

This directory contains the newer EnterpriseRAG / PIFS metadata -> extension schema -> folder -> semantic projection pipeline.

The design keeps four tool surfaces separate:

- metadata DSL: exact/canonical filters over approved fields
- folder browse: low-cardinality browseable projections
- semantic vector search: candidate discovery only
- grep/FTS/BM25: lexical text search with real matching lines

## Pipeline Steps

Run outputs are written to:

```text
examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/results/<run_name>/
```

1. Normalize metadata:

```bash
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/build_metadata.py \
  --run-name my-run \
  --metadata-source examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/generated/doc_profiles.json \
  --reuse-generated-only
```

For documents that do not have safe reusable LLM metadata, use the OpenAI Batch workflow instead of synchronous generation:

```bash
# Write OpenAI Batch JSONL files without submitting.
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/build_metadata.py \
  --run-name my-run \
  --metadata-source examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/generated/doc_profiles.json \
  --metadata-batch-mode prepare \
  --metadata-model gpt-5-nano

# Upload the prepared JSONL files and create Batch API jobs.
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/build_metadata.py \
  --run-dir examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/results/my-run \
  --metadata-batch-mode submit

# After OpenAI marks the batches completed, download results and merge them.
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/build_metadata.py \
  --run-dir examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/results/my-run \
  --metadata-batch-mode collect
```

This writes:

- `config.json`
- `metadata.normalized.jsonl`
- `metadata_reuse_report.json`
- `summary.md`
- `metadata_batch_manifest.json` and `metadata_batches/*.jsonl` when `--metadata-batch-mode` is used

2. Discover extension fields:

```bash
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/discover_extensions.py \
  --run-dir examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/results/my-run
```

This writes:

- `extension_schema.json`
- `extension_schema_audit.json`
- `extension_schema_prompt.sample.md`

3. Build folder projections:

```bash
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/build_folders.py \
  --run-dir examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/results/my-run \
  --mode both
```

This writes:

- `folder_plan.json`
- `folder_field_report.json`

4. Build semantic projection indexes:

```bash
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/build_projection_indexes.py \
  --run-dir examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/results/my-run \
  --embedding-provider openai \
  --embedding-model text-embedding-3-small \
  --embedding-dimensions 256
```

For local smoke tests without network, use `--embedding-provider hash`.

For full runs, use the OpenAI Batch workflow so embedding generation is asynchronous and cacheable:

```bash
# Write /v1/embeddings Batch JSONL for cache misses only.
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/build_projection_indexes.py \
  --run-dir examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/results/my-run \
  --embedding-provider openai \
  --embedding-batch-mode prepare \
  --embedding-model text-embedding-3-small \
  --embedding-dimensions 256

# Upload prepared JSONL files and create embedding Batch API jobs.
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/build_projection_indexes.py \
  --run-dir examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/results/my-run \
  --embedding-batch-mode submit

# After OpenAI marks the batches completed, download vectors into embedding_cache.sqlite and build indexes.
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/build_projection_indexes.py \
  --run-dir examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/results/my-run \
  --embedding-provider openai \
  --embedding-batch-mode collect
```

This writes:

- `projection_manifest.json`
- `sample_projection_rows.json`
- `projection_indexes/summary_only_vector.sqlite`
- `projection_indexes/entity_vectors.sqlite`
- `projection_indexes/relation_vectors.sqlite`
- `projection_indexes/embedding_cache.sqlite`
- `embedding_batch_manifest.json` and `embedding_batches/*.jsonl` when `--embedding-batch-mode` is used

5. Inspect artifacts:

```bash
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/inspect_artifacts.py \
  --run-dir examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/results/my-run
```

This writes:

- `inspection_report.json`

6. Materialize the pipeline into a PIFS workspace and run an agent smoke:

```bash
uv run python examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/run_agent_smoke.py \
  --run-dir examples/Benchmark/enterprise_rag_benchmark/semantic_metadata_pipeline/results/my-run \
  --question-ids qst_0007 \
  --model gpt-5.4-mini \
  --stream-mode all \
  --verbose
```

This registers only the documents from `metadata.normalized.jsonl`, creates
folders from `folder_plan.json`, attaches the summary/entity/relation projection
indexes, and runs the PIFS agent against that materialized workspace.

This writes under `agent_smoke/<timestamp>/`:

- `workspace_manifest.json`
- `results.jsonl`
- `answers.jsonl`
- `summary.json`
- `summary.md`

## Metadata Reuse Policy

The resolver separates system/provenance fields from LLM metadata.

System/provenance fields are used only for registration, evaluation, and traceability:

- `dataset_doc_uuid`
- `source_type`
- `source_path`
- `title`
- `content_type`
- `storage_uri`
- `created_at`
- `updated_at`

Base metadata fields are:

- `doc_type`
- `domain`
- `topic`
- `summary`
- `entities`
- `relations`
- `constraints`
- `retrieval_cues`

Accepted reuse:

- `semantic_summary` -> `summary`
- structured `entities` -> `entities`
- structured `relations` -> `relations`
- `search_terms` / `folder_hints` / `retrieval_cues` -> `retrieval_cues`
- profile fields such as `primary_topic`, `topic_cluster_hint`, `doc_type`, `product_or_system`, and `communication_channel` as base or extension candidates

Rejected reuse:

- `compact_summary(title, preview)`
- title + preview truncation
- `keyword_terms` rule entities
- `infer_predicate` rule relations
- fulltext preview embedding summaries

Every normalized field records provenance:

- `source_file`
- `source_field`
- `generation_method`
- `confidence`
- `notes`

## Extension Discovery Policy

Extension fields are not hardcoded as base schema. Fields such as repo, channel, project, customer, status, or source bucket must be discovered from candidate metadata and pass audit checks.

An extension field records:

- `name`
- `description`
- `why_queryable`
- `coverage_estimate`
- `canonical_values`
- `synonyms`
- `suitable_for_dsl`
- `suitable_for_folder`
- `cardinality_expectation`
- `empty_policy`
- `source_evidence`

Discovery rejects:

- unique ids, benchmark ids, paths, URLs, filenames, storage URIs
- `summary`, `entities`, `relations`, `constraints`, `retrieval_cues`
- high-cardinality fields
- low-coverage fields
- text-heavy fields that do not canonicalize cleanly

## Folder Field Policy

Folder generation may use only:

- `metadata_base.doc_type`
- `metadata_base.domain`
- `metadata_base.topic`
- extension fields with `suitable_for_folder=true`
- system `source_type` as an optional browse root

Folder generation must not use:

- `summary`
- `entities`
- `relations`
- `constraints`
- `retrieval_cues`
- `dataset_doc_uuid`
- `source_path`
- `storage_uri`
- unique ids

The folder builder computes coverage, cardinality, folder-size distribution, and singleton rate for each candidate. It supports facet/multimount folders and tree folders. Tree depth is configured by `--max-depth`; it is not a fixed semantic rule.

## Semantic Projection Policy

Only three vector channels are built:

- `summary_only_vector` from `metadata.summary`
- `entity_vectors` from `metadata.entities`
- `relation_vectors` from `metadata.relations`

The pipeline does not build:

- `constraint_vectors`
- `retrieval_cues_vectors`
- `metadata_composite_vector`

`retrieval_cues` are lexical payloads for FTS/BM25/grep. They are not embedded.

Embedding is batched:

- collect projection rows
- batch query embedding cache
- batch embed cache misses
- batch write sqlite/vector index
- record model, dimensions, batch size, input rows, skipped rows, cache hits, and metadata source path in `projection_manifest.json`

## Tool Semantics

Default command boundaries:

- `ls` / `tree`: folder browse
- `find --where`: exact/canonical metadata DSL filtering over allowed fields
- `grep -R '<query>' <path>`: recursive lexical/FTS/BM25 search returning real matching lines; broad deep folders may return a scope warning instead of running an expensive recursive SQLite FTS scan
- `search-summary '<query>' <path>`: summary vector candidate discovery
- `search-entity '<query>' <path>`: entity vector candidate discovery
- `search-relation '<query>' <path>`: relation vector candidate discovery
- `semantic-grep -R '<query>' <path>`: experimental semantic candidate discovery followed by real lexical/line matching

Default `grep -R` remains lexical because grep means text matching. A semantic candidate is useful for recall, but it is not evidence that the text occurs in the document. In the current SQLite-backed implementation, `grep -R` is intentionally limited on folders that are both deeper than two descendant levels and larger than ten files; the guard uses bounded threshold probes instead of exact recursive counts. The experimental `semantic-grep` tool keeps the semantic/text distinction explicit: it searches entity candidates, checks real lines, then searches relation candidates, checks real lines, and returns no matches if no line evidence exists.
