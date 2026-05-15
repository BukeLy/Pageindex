# Semantic Folder V2

This experiment keeps PageIndex FileSystem core unchanged.

Flow:

1. Register selected EnterpriseRAG documents into a local PIFS workspace.
2. Register a fixed, dataset-insensitive semantic metadata schema.
3. Generate document-level semantic metadata from document text only.
4. Canonicalize metadata labels across the selected corpus.
5. Deterministically materialize semantic folders from canonical labels.

The LLM metadata step receives only:

```json
{
  "dataset_doc_uuid": "...",
  "text": "..."
}
```

The prompt receives only `text`; provenance fields such as `source_path`,
`source_type`, raw dataset metadata, questions, and gold answers are not passed
to the LLM.

Run:

```bash
env PYTHONPATH=. uv run python examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/v2/run_semantic_folder_v2.py \
  --target-docs 10 \
  --run-name 20260515-10docs-semantic-v2-smoke \
  --metadata-workers 2 \
  --reset
```

Outputs:

- `v2/results/<run-name>/semantic_metadata.json`
- `v2/results/<run-name>/canonicalization.json`
- `v2/results/<run-name>/folder_plan.json`
- `v2/results/<run-name>/summary.json`
- `v2/workspaces/<run-name>/semantic-metadata-folders-v2/`
