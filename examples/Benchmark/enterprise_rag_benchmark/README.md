# EnterpriseRAG PIFS Benchmark Adapter

This adapter runs selected EnterpriseRAG questions against an existing
PageIndex FileSystem workspace and records both the final answer rows and the
PIFS tool trace used by the agent.

The runner does not register documents, rebuild summaries, modify gold answers,
or change the workspace. It assumes the workspace already contains imported
EnterpriseRAG documents and projection indexes.

Example:

```bash
cd /Users/chengjie/Projects/PageIndex
source .env

PYTHONPATH=/Users/chengjie/Projects/PageIndex \
python examples/Benchmark/enterprise_rag_benchmark/run_enterprise_rag_pifs_agent.py \
  --dataset /Users/chengjie/Projects/pifs-data/enterprise-rag-benchmark/dataset \
  --workspace examples/Benchmark/enterpriseragbenchmark_workspace \
  --run-name selected6-hybrid \
  --model gpt-5.4-mini \
  --stream-mode tools \
  --max-seconds 60 \
  --max-turns 40 \
  --agent-retries 0
```

By default the selected set is:

- `qst_0001`
- `qst_0176`
- `qst_0341`
- `qst_0411`
- `qst_0431`
- `qst_0481`

Outputs are written under `examples/Benchmark/enterprise_rag_benchmark/runs/<run-name>/`:

- `answers.jsonl`: EnterpriseRAG submit shape with `question_id`, `answer`, and
  `document_ids`.
- `results.jsonl`: local debug trace with prompt mode, tool calls, expected
  document ids, answer, errors, and timing.
- `summary.json`: aggregate recall and negative-control metrics.
- `progress_summary.json`: rewritten after each completed question.

The primary metric used for this selected set is expected-document recall:
`expected_doc_hits / expected_doc_total`. The selected questions contain eight
expected document IDs across six questions, so a `6/8` run means six expected
documents were recovered.
