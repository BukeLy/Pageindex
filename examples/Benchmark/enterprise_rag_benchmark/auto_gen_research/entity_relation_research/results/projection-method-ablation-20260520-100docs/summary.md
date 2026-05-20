# Entity Relation Projection Preflight

This is a cheap offline recall preflight. It uses lexical BM25-style scoring
over projection text, not embeddings. The purpose is to verify whether the
entity/relation projection direction has signal before spending API tokens
on projection embeddings.

## Config

```json
{
  "question_limit": 0,
  "question_ids": "",
  "candidate_limit": 100,
  "selected_questions_json": "/Users/chengjie/.codex/worktrees/6d00/PageIndex/examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/results/20260515-100docs-agent-eval/selected_questions.json",
  "selected_documents_json": "/Users/chengjie/.codex/worktrees/6d00/PageIndex/examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/results/20260515-100docs-agent-eval/selected_documents.json",
  "profiles_json": "/Users/chengjie/.codex/worktrees/6d00/PageIndex/examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/generated/doc_profiles.json"
}
```

## Document Universe

```json
{
  "documents": 100,
  "expected_documents_present": 100,
  "source_counts": {
    "linear": 16,
    "google_drive": 14,
    "jira": 13,
    "github": 11,
    "gmail": 11,
    "slack": 11,
    "confluence": 10,
    "fireflies": 7,
    "hubspot": 7
  }
}
```

## Summary

| strategy | hit@10 | hit@20 | hit@50 | hit@100 | MRR | misses@100 |
|---|---:|---:|---:|---:|---:|---:|
| `baseline_metadata_text` | 0.9900 | 0.9900 | 1.0000 | 1.0000 | 0.9716 | 0 |
| `summary_only_text` | 0.9900 | 0.9900 | 1.0000 | 1.0000 | 0.9412 | 0 |
| `entity_constraint_projection` | 0.9400 | 0.9500 | 0.9500 | 0.9500 | 0.8592 | 5 |
| `entity_relation_projection` | 0.9800 | 0.9800 | 1.0000 | 1.0000 | 0.9502 | 0 |
| `hybrid_projection_text` | 0.9800 | 0.9900 | 1.0000 | 1.0000 | 0.9645 | 0 |
| `rrf_summary_metadata_relation` | 0.9900 | 1.0000 | 1.0000 | 1.0000 | 0.9768 | 0 |
| `multi_probe_rrf` | 0.9900 | 1.0000 | 1.0000 | 1.0000 | 0.9718 | 0 |
| `pseudo_late_interaction` | 0.9800 | 0.9900 | 1.0000 | 1.0000 | 0.9662 | 0 |
| `constraint_exact_boost` | 0.9800 | 0.9900 | 1.0000 | 1.0000 | 0.9644 | 0 |
| `source_hint_boost` | 0.9900 | 1.0000 | 1.0000 | 1.0000 | 0.9527 | 0 |
| `prf_metadata_expansion` | 0.9900 | 0.9900 | 1.0000 | 1.0000 | 0.9226 | 0 |
| `hybrid_rrf_exact_source` | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9733 | 0 |
