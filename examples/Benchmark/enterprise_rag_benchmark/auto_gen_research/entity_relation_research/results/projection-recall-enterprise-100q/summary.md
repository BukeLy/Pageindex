# Projection Recall

Offline retrieval eval only. No agent prompt, no benchmark runner, no fulltext/BM25 strategy.

## Config

- dataset: `/Users/chengjie/.codex/worktrees/6d00/PageIndex-datasets/enterprise_rag_benchmark/dataset`
- index_dir: `/Users/chengjie/Projects/PageIndex/examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/entity_relation_research/cache/projection_indexes/enterprise_full_v1`
- query_extractor: `heuristic`
- embedding_model: `text-embedding-3-small`
- embedding_dimensions: `256`
- question_start: `1`
- question_limit: `100`
- source_type_filter: `True`

## Results

| strategy | hit@1 | hit@3 | hit@5 | hit@10 | hit@20 | hit@50 | hit@100 | MRR | avg candidates | misses@100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_metadata_vector` | 0.2900 | 0.3900 | 0.4600 | 0.5400 | 0.5900 | 0.7000 | 0.7800 | 0.3691 | 100.0 | 22 |
| `summary_only_vector` | 0.3400 | 0.4600 | 0.5200 | 0.5800 | 0.6500 | 0.7100 | 0.7700 | 0.4200 | 100.0 | 23 |
| `entity_constraint_index` | 0.2000 | 0.3400 | 0.3800 | 0.4300 | 0.5100 | 0.5500 | 0.5900 | 0.2854 | 105.1 | 41 |
| `entity_relation_index` | 0.2700 | 0.4400 | 0.5000 | 0.6000 | 0.6200 | 0.6900 | 0.7100 | 0.3787 | 184.3 | 29 |
| `hybrid_entity_relation_vector` | 0.3000 | 0.4300 | 0.5300 | 0.6200 | 0.6700 | 0.7300 | 0.7900 | 0.4054 | 241.5 | 21 |
