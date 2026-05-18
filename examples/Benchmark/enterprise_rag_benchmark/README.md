# EnterpriseRAG Benchmark Adapter

This folder keeps EnterpriseRAG data registration and retrieval evaluation as
separate steps.

1. Register the dataset into a PageIndex FileSystem catalog:

```bash
python register_enterprise_rag_pifs_dataset.py --reset --question-ids qst_0001,qst_0002
```

For a full-corpus workspace:

```bash
python register_enterprise_rag_pifs_dataset.py \
  --workspace runs/full-pifs-workspace/workspace \
  --all-documents \
  --source-json-artifacts \
  --batch-size 1000
```

Registration skips existing `external_id` rows by default, so the same command
can resume. Use `--refresh-existing` only when you intentionally want to
overwrite catalog rows/artifacts.

2. Run retrieval against the existing catalog:

```bash
python run_enterprise_rag_pifs_agent.py --question-ids qst_0001,qst_0002
```

For the official core questions:

```bash
python run_enterprise_rag_pifs_agent.py \
  --workspace runs/full-pifs-workspace/workspace \
  --run-name full-pifs-agent \
  --all-questions \
  --max-seconds 60
```

The runner writes two outputs:

- `answers.jsonl`: official EnterpriseRAG format with `question_id`, `answer`,
  and `document_ids`.
- `results.jsonl`: local debug output with tool logs, errors, timing, and
  optional citations/provenance.

To update metadata in an existing workspace without rebuilding it:

```bash
python update_enterprise_rag_pifs_metadata.py \
  --workspace runs/full-pifs-workspace/workspace \
  --schema-json path/to/schema.json \
  --metadata-jsonl path/to/metadata.jsonl
```

The runner reads model configuration from environment variables loaded from the
repo `.env` first, then this folder's `.env`:

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
PIFS_AGENT_MODEL=gpt-4.1-mini
PIFS_MAX_SECONDS=60
```

`OPENAI_BASE_URL` is optional for the default OpenAI endpoint, but should be set
for OpenAI-compatible providers.

The runner only checks that `runs/pifs-workspace/workspace/filesystem.sqlite`
already exists, loads questions, and calls the PIFS agent. It must not register
files, infer metadata, reset the catalog, or modify benchmark data/gold answers.

Generated benchmark outputs, SQLite databases, and local artifacts should stay
under `runs/`, which is ignored by git.
