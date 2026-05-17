# WixQA Benchmark Adapter

This folder keeps WixQA registration and agent evaluation separate.

1. Register the Wix KB corpus into a PIFS workspace:

```bash
PYTHONPATH=. uv run python examples/Benchmark/wixqa_benchmark/register_wixqa_pifs_dataset.py --reset
```

2. Run agent evaluation against the existing workspace:

```bash
PYTHONPATH=. uv run python examples/Benchmark/wixqa_benchmark/run_wixqa_pifs_agent.py --max-questions 20
```

The official-compatible prediction file is written as JSONL rows containing
only:

```json
{"question":"...","answer":"...","article_ids":["..."]}
```

Debug traces and local metrics are written separately under `runs/`, which is
ignored by git.
