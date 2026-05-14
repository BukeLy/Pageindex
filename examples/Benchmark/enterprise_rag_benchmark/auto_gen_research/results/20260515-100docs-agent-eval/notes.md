# 100-Doc Agent Eval Notes

Run: `20260515-100docs-agent-eval`

This run evaluates four automatically generated PageIndex FileSystem layouts on
the same 100-question / 100-document EnterpriseRAG subset.

## Ranking

| Rank | Combo | Doc Hits | Hit Rate | Avg Tool Calls | Avg Seconds |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | `dag_fixed_schema__entropy_folder` | 93/100 | 0.93 | 4.83 | 47.38 |
| 2 | `entity_relation_fixed_schema__entropy_folder` | 89/100 | 0.89 | 4.41 | 47.95 |
| 3 | `dag_fixed_schema__topic_count_folder` | 89/100 | 0.89 | 4.85 | 39.98 |
| 4 | `entity_relation_fixed_schema__topic_count_folder` | 82/100 | 0.82 | 5.64 | 53.34 |

## Caveats

- This is a retrieval document-hit evaluation, not final answer grading.
- `entity_relation_fixed_schema__entropy_folder` includes four transport/API
  failures: `qst_0087`, `qst_0088`, `qst_0089`, and `qst_0090`. The raw result
  is preserved as-is; do not interpret those four misses as pure FileSystem
  layout failures.
- Most other recorded errors are `MaxTurnsExceeded`, which is useful signal for
  agent navigation cost and folder/metadata discoverability.

## Read

The strongest signal from this run is that entropy-ordered folders beat
topic-count folders on this 100-document corpus. The best layout combines
directed graph-style fixed metadata with entropy folder levels:
`dag_fixed_schema__entropy_folder`.
