# Auto-Gen FileSystem Research Summary

- Run: `20260515-100docs-agent-eval`
- Selection: `100docs-6c92112755`
- Questions: `qst_0001, qst_0002, qst_0003, qst_0004, qst_0005, qst_0006, qst_0007, qst_0008, qst_0009, qst_0010, qst_0011, qst_0012, qst_0013, qst_0014, qst_0015, qst_0016, qst_0017, qst_0018, qst_0019, qst_0020, qst_0021, qst_0022, qst_0023, qst_0024, qst_0025, qst_0026, qst_0027, qst_0028, qst_0029, qst_0030, qst_0031, qst_0032, qst_0033, qst_0034, qst_0035, qst_0036, qst_0037, qst_0038, qst_0039, qst_0040, qst_0041, qst_0042, qst_0043, qst_0044, qst_0045, qst_0046, qst_0047, qst_0048, qst_0049, qst_0050, qst_0051, qst_0052, qst_0053, qst_0054, qst_0055, qst_0056, qst_0057, qst_0058, qst_0059, qst_0060, qst_0061, qst_0062, qst_0063, qst_0064, qst_0065, qst_0066, qst_0067, qst_0068, qst_0069, qst_0070, qst_0071, qst_0072, qst_0073, qst_0074, qst_0075, qst_0076, qst_0077, qst_0078, qst_0079, qst_0080, qst_0081, qst_0082, qst_0083, qst_0084, qst_0085, qst_0086, qst_0087, qst_0088, qst_0089, qst_0090, qst_0091, qst_0092, qst_0093, qst_0094, qst_0095, qst_0096, qst_0097, qst_0098, qst_0099, qst_0100`
- Documents: `100`
- Agent model: `gpt-4.1-mini`
- Generation model: `gpt-4.1-mini`
- Base URL: `https://api.openai.com/v1`
- Cluster cache: `/Users/chengjie/.codex/worktrees/6d00/PageIndex/examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/generated/topic_clusters_100docs-6c92112755.json`

## Result Table

| Combo | Doc Hits | Hit Rate | Avg Tool Calls | Avg Seconds | Errors |
| --- | ---: | ---: | ---: | ---: | --- |
| dag_fixed_schema__entropy_folder | 93/100 | 0.93 | 4.83 | 47.38 | qst_0016, qst_0057, qst_0062 |
| entity_relation_fixed_schema__entropy_folder | 89/100 | 0.89 | 4.41 | 47.95 | qst_0002, qst_0039, qst_0065, qst_0087, qst_0088, qst_0089, qst_0090 |
| dag_fixed_schema__topic_count_folder | 89/100 | 0.89 | 4.85 | 39.98 | qst_0002, qst_0027, qst_0082, qst_0097 |
| entity_relation_fixed_schema__topic_count_folder | 82/100 | 0.82 | 5.64 | 53.34 | qst_0006, qst_0022, qst_0034, qst_0058, qst_0071, qst_0074, qst_0090, qst_0094, qst_0099 |

## Folder Plans

### Topic Count

```json
{
  "cluster_counts": {
    "API Input Handling and Streaming Enhancements": 2,
    "Design System and UI/UX Specifications": 5,
    "Marketplace and Subscription Management": 3,
    "Regional Failover and Reliability Automation": 2,
    "Product Features and Engineering Design Documents": 11,
    "Software Release and Runtime Incident Management": 7,
    "Service Assurance, Pricing, and Procurement Coordination": 6,
    "Telemetry, Observability, and Metrics Engineering": 6,
    "Telemetry, Observability, and Compliance for Private Deployments": 7,
    "Network Incidents and VPN Connectivity Issues": 4,
    "Security Incident Response and Penetration Testing": 6,
    "Upgrade and Rollback Support for Redwood Private": 3,
    "Customer Profiles and Sales Discovery": 3,
    "Access Governance and Entitlements Management": 4,
    "SDK and API Enhancements and Compatibility": 4,
    "Authentication and Access Control Incidents": 4,
    "Customer Onboarding, Workshops, and Usage Metrics": 2,
    "Customer Evaluation and Proof of Concept for Redwood Platform": 3,
    "Kernel Lattice Scheduler and GPU Workspace Optimization": 2,
    "API Model Migration and Customer Onboarding": 1,
    "GPU and Network Incident Management and Operations": 2,
    "Runtime and SDK Enhancements for Redwood Inference": 2,
    "Customer Account Management and Billing Issues": 3,
    "Routing, Traffic Management, and Autoscaling": 4,
    "Internal Company Meetings and Culture": 2,
    "Customer Profiles and Sales Engagements": 2
  },
  "ranked_clusters": {
    "Product Features and Engineering Design Documents": 1,
    "Software Release and Runtime Incident Management": 2,
    "Telemetry, Observability, and Compliance for Private Deployments": 3,
    "Security Incident Response and Penetration Testing": 4,
    "Service Assurance, Pricing, and Procurement Coordination": 5,
    "Telemetry, Observability, and Metrics Engineering": 6,
    "Design System and UI/UX Specifications": 7,
    "Access Governance and Entitlements Management": 8,
    "Authentication and Access Control Incidents": 9,
    "Network Incidents and VPN Connectivity Issues": 10,
    "Routing, Traffic Management, and Autoscaling": 11,
    "SDK and API Enhancements and Compatibility": 12,
    "Customer Account Management and Billing Issues": 13,
    "Customer Evaluation and Proof of Concept for Redwood Platform": 14,
    "Customer Profiles and Sales Discovery": 15,
    "Marketplace and Subscription Management": 16,
    "Upgrade and Rollback Support for Redwood Private": 17,
    "API Input Handling and Streaming Enhancements": 18,
    "Customer Onboarding, Workshops, and Usage Metrics": 19,
    "Customer Profiles and Sales Engagements": 20,
    "GPU and Network Incident Management and Operations": 21,
    "Internal Company Meetings and Culture": 22,
    "Kernel Lattice Scheduler and GPU Workspace Optimization": 23,
    "Regional Failover and Reliability Automation": 24,
    "Runtime and SDK Enhancements for Redwood Inference": 25,
    "API Model Migration and Customer Onboarding": 26
  }
}
```

### Entropy

```json
{
  "field_entropy": {
    "topic_cluster": 4.4912,
    "primary_topic": 6.6439,
    "source_type": 3.1229,
    "doc_type": 5.1476,
    "time_period": 5.6397,
    "product_or_system": 6.5763,
    "communication_channel": 5.8201
  },
  "field_order": [
    "primary_topic",
    "product_or_system",
    "communication_channel",
    "time_period",
    "doc_type",
    "topic_cluster",
    "source_type"
  ]
}
```

## Current Interpretation

The current best combo is `dag_fixed_schema__entropy_folder` by hit rate, then tool calls, then runtime.
Treat this as a corpus-specific signal; rerun with a larger distractor set before choosing a default layout.
