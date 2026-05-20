# Entity / Relation Projection Experiment Design

## Goal

Validate whether entity, relation, and constraint projections fix the weakness
of one-vector-per-document retrieval on the full EnterpriseRAG workspace.

The target is not chunk-level RAG. The target is file-level recall for an agentic
filesystem:

```text
question
  -> query projections: entities, relations, constraints, answer type
  -> projection indexes
  -> aggregated file candidates / folder candidates
  -> agent opens a small number of full leaf documents with cat
```

## Hypothesis

Pooling a document into one embedding loses fine-grained evidence. Enterprise
questions often hinge on:

- exact entities: API names, customers, repos, owners, projects, tickets;
- relations: system X enforces limit Y, customer A requested feature B;
- constraints: numeric defaults, dates, SLAs, versions, states, thresholds.

If these are indexed as separate projections and then aggregated back to the
document, recall should improve without exposing chunk-level context to the
agent.

## Strategies

Use the same full workspace and question set for all strategies.

| strategy | document-side index text | query-side text | purpose |
|---|---|---|---|
| `baseline_metadata_vector` | current metadata composite text | raw question | current measured baseline |
| `summary_only_vector` | `summary` only | raw question | test the user's intended doc-level summary embedding |
| `entity_constraint_index` | entity and constraint projection rows | extracted entities and constraints | isolate whether explicit objects/rules improve recall |
| `entity_relation_index` | entity, relation, and constraint rows | extracted entities, relations, constraints | test graph-like retrieval without building a graph DB |
| `hybrid_projection_vector` | metadata vector plus projection rows | raw question plus extracted projections | likely production candidate if recall improves |

## Projection Shape

Each document can produce multiple projection rows:

```json
{
  "file_ref": "file_...",
  "external_id": "dsid_...",
  "projection_type": "entity | relation | constraint | summary | metadata",
  "projection_text": "multipart upload | default file limit | 10 MiB",
  "source_field": "entities | constraints | generated_relation",
  "confidence": 1.0
}
```

The projection index is not source of truth. It can be rebuilt from the PIFS
catalog, file metadata, and raw text/PageIndex tree artifacts.

## Query Parsing

First implementation should compare two query-side approaches:

1. deterministic parser from question text;
2. cheap LLM parser that returns:

```json
{
  "entities": [],
  "relations": [],
  "constraints": [],
  "expected_answer_type": ""
}
```

The LLM parser is allowed only at query time in the experiment. Document-side
generation must not use benchmark gold answers or expected document ids.

## Metrics

Offline retrieval first:

- `hit@10`
- `hit@20`
- `hit@50`
- `hit@100`
- MRR
- average query latency
- average candidates returned
- answerable recall, excluding questions whose expected docs are missing from
  the registered workspace

Promotion threshold:

- `hybrid_projection_vector` should reach at least `hit@50 >= 0.90` on
  answerable EnterpriseRAG full workspace questions.
- It should beat current metadata/fulltext union at lower candidate counts.
- It should return a compact enough candidate set to feed a shell-like PIFS
  command without dumping hundreds of file rows into the agent context.

## Agent Phase

Only after offline recall passes the threshold:

1. expose projection recall behind a research command, not default core;
2. convert deep file candidates into compact folder/group rows;
3. run agent with `MAX_SECONDS=60`;
4. measure `cat` count and document hit rate.

Do not run expensive agent evaluations before offline recall proves the index is
worth using.
