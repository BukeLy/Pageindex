# Semantic Vector Field Ablation

| field mode | indexed docs | hit@1 | hit@5 | hit@10 | hit@50 | hit@limit | mean best rank |
|---|---:|---:|---:|---:|---:|---:|---:|
| fulltext | 100 | 0.933 | 1.000 | 1.000 | 1.000 | 1.000 | 1.1 |
| metadata | 100 | 0.900 | 1.000 | 1.000 | 1.000 | 1.000 | 1.1 |
| summary | 100 | 0.900 | 0.967 | 1.000 | 1.000 | 1.000 | 1.2 |

## Interpretation Rule

- `summary` tests whether one compact generated summary is enough.
- `metadata` tests the current recommended recall text: title, summary, intent, entities, constraints, topic, and semantic equivalents.
- `fulltext` tests raw source-text preview embedding without benchmark question/gold leakage.
