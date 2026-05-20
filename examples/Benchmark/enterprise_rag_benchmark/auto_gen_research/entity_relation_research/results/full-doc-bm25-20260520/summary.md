# Full-Doc BM25 Recall

This evaluates retrieval over the full EnterpriseRAG documents parquet
using local SQLite FTS5/BM25. It does not use metadata generation,
embeddings, or benchmark gold answers during indexing.

## Document Universe

```json
{
  "documents": 511958,
  "source_counts": {
    "slack": 285605,
    "gmail": 121390,
    "linear": 35308,
    "google_drive": 25108,
    "hubspot": 15017,
    "fireflies": 10173,
    "github": 8052,
    "jira": 6119,
    "confluence": 5186
  }
}
```

## Summary

| strategy | hit@1 | hit@3 | hit@5 | hit@10 | hit@20 | hit@50 | hit@100 | MRR | misses@100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `content_bm25` | 0.4957 | 0.5936 | 0.6404 | 0.6745 | 0.7170 | 0.7979 | 0.8298 | 0.5603 | 80 |
| `title_boost_bm25` | 0.4957 | 0.5936 | 0.6404 | 0.6745 | 0.7170 | 0.7979 | 0.8298 | 0.5603 | 80 |
| `source_hint_bm25` | 0.2532 | 0.2957 | 0.3255 | 0.3426 | 0.3638 | 0.4021 | 0.4149 | 0.2850 | 275 |
| `query_projection_rrf` | 0.3830 | 0.5149 | 0.5745 | 0.6404 | 0.6936 | 0.7809 | 0.8191 | 0.4706 | 85 |
| `hybrid_title_source_rrf` | 0.4213 | 0.5362 | 0.6085 | 0.6723 | 0.7170 | 0.7957 | 0.8255 | 0.5021 | 82 |
