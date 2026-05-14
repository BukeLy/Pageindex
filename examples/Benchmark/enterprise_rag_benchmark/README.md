# EnterpriseRAG Benchmark Smoke

This folder contains a lightweight, reproducible runner for testing the current
PageIndex FileSystem prototype against EnterpriseRAG multi-file retrieval.

## Run the first 5 questions

Put an OpenAI-compatible Gemini key in the repository root `.env`:

```bash
OPENAI_API_KEY=your_gemini_key
```

Optional overrides:

```bash
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
PAGEINDEX_ANSWER_MODEL=gemini-2.5-flash
```

Answer generation uses the same OpenAI Agents SDK path as
`examples/agentic_vectorless_rag_demo.py`. Install the optional dependency
before running without `--skip-llm`:

```bash
pip3 install openai-agents
```

Run:

```bash
python3 examples/Benchmark/enterprise_rag_benchmark/run_smoke.py \
  --bench-root /private/tmp/EnterpriseRAG-Bench-20260514 \
  --limit 5
```

For a faster fixed 5-question smoke that avoids the very large Gmail/Slack
sources, run:

```bash
python3 examples/Benchmark/enterprise_rag_benchmark/run_smoke.py \
  --bench-root /private/tmp/EnterpriseRAG-Bench-20260514 \
  --question-ids qst_0001,qst_0002,qst_0004,qst_0011,qst_0012 \
  --workspace examples/Benchmark/enterprise_rag_benchmark/runs/smoke-small/workspace \
  --results-path examples/Benchmark/enterprise_rag_benchmark/runs/smoke-small/results.jsonl \
  --summary-path examples/Benchmark/enterprise_rag_benchmark/runs/smoke-small/summary.json
```

If the configured OpenAI-compatible endpoint is unavailable, add `--skip-llm`
to evaluate retrieval-only metrics from the same FileSystem index.

The default `--source-scope question-source-types` only indexes the source
types mentioned by the selected questions. This is useful for a quick local
smoke test, but it is not a leaderboard-fair run because the benchmark question
metadata narrows the corpus. For a fairer retrieval run, use:

```bash
python3 examples/Benchmark/enterprise_rag_benchmark/run_smoke.py \
  --bench-root /private/tmp/EnterpriseRAG-Bench-20260514 \
  --limit 5 \
  --source-scope all
```

Generated PageIndex artifacts are written under this benchmark folder by default:

- `examples/Benchmark/enterprise_rag_benchmark/runs/smoke/results.jsonl`
- `examples/Benchmark/enterprise_rag_benchmark/runs/smoke/summary.json`
- `examples/Benchmark/enterprise_rag_benchmark/runs/smoke/workspace/`

The `runs/` directory is ignored by git because it contains local SQLite
databases, text artifacts, and generated JSON result files.
