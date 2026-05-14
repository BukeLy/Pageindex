# Auto-Gen FileSystem Research Summary

- Run: `20260515-0035-10docs-agent-eval`
- Questions: `qst_0001, qst_0002, qst_0003, qst_0004, qst_0005, qst_0006, qst_0007, qst_0008, qst_0009, qst_0010`
- Documents: `10`
- Agent model: `gpt-4.1-mini`
- Generation model: `gpt-4.1-mini`
- Base URL: `https://api.openai.com/v1`

## Result Table

| Combo | Doc Hits | Hit Rate | Avg Tool Calls | Avg Seconds | Errors |
| --- | ---: | ---: | ---: | ---: | --- |
| dag_fixed_schema__entropy_folder | 10/10 | 1.00 | 3.20 | 20.29 |  |
| entity_relation_fixed_schema__topic_count_folder | 10/10 | 1.00 | 3.80 | 20.12 |  |
| dag_fixed_schema__topic_count_folder | 10/10 | 1.00 | 3.90 | 15.57 |  |
| entity_relation_fixed_schema__entropy_folder | 9/10 | 0.90 | 4.10 | 27.25 | qst_0002 |

## Folder Plans

### Topic Count

```json
{
  "cluster_counts": {
    "API Streaming and Multipart Handling Enhancements": 2,
    "Design and Frontend UI Enhancements": 1,
    "Cloud Marketplace Onboarding and Billing Integration": 1,
    "Regional Failover, Data Residency, and Compliance": 2,
    "Bias Analysis and Model Behavior Investigation": 1,
    "Release Engineering and Shiproom Operations": 1,
    "Commercial Negotiations and Licensing Strategies": 1,
    "Observability, Alerting, and SLO Attribution": 1
  },
  "ranked_clusters": {
    "API Streaming and Multipart Handling Enhancements": 1,
    "Regional Failover, Data Residency, and Compliance": 2,
    "Bias Analysis and Model Behavior Investigation": 3,
    "Cloud Marketplace Onboarding and Billing Integration": 4,
    "Commercial Negotiations and Licensing Strategies": 5,
    "Design and Frontend UI Enhancements": 6,
    "Observability, Alerting, and SLO Attribution": 7,
    "Release Engineering and Shiproom Operations": 8
  }
}
```

### Entropy

```json
{
  "field_entropy": {
    "topic_cluster": 2.9219,
    "primary_topic": 3.3219,
    "source_type": 2.171,
    "doc_type": 2.9219,
    "time_period": 2.9219,
    "product_or_system": 3.1219,
    "communication_channel": 2.9219
  },
  "field_order": [
    "primary_topic",
    "product_or_system",
    "communication_channel",
    "doc_type",
    "time_period",
    "topic_cluster",
    "source_type"
  ]
}
```

## Current Interpretation

The current best combo is `dag_fixed_schema__entropy_folder` by hit rate, then tool calls, then runtime.
Treat this as a small-corpus signal only; the next step is to add distractor documents and rerun.
