# Auto Generation Method

This experiment treats schema selection and value generation as separate
problems.

## Metadata Generation

`gen_auto_metadata` should not ask the LLM to invent a schema. PageIndex owns the
schema. The LLM receives a fixed extraction contract and fills values from the
PageIndex tree when available, or from raw document text when no tree exists.

The experiment uses one intermediate LLM output:

```text
DocumentSemanticProfile
  doc_type
  primary_topic
  secondary_topics
  topic_cluster_hint
  time_period
  communication_channel
  product_or_system
  primary_entity
  action_or_event
  entities[]: name, type, salience, aliases
  relations[]: subject, relation, object, direction, evidence_terms
  semantic_summary
  search_terms
  folder_hints
```

Then it projects that profile into two fixed metadata schemas:

1. `entity_relation_fixed_schema`
   - Keeps entity and relation fields as document-level retrieval descriptors.
   - Useful for metadata DSL queries such as topic/entity/product/source filters.
   - Fields include `primary_entity`, `entities`, `relations`,
     `action_or_event`, `semantic_summary`, and `search_terms`.

2. `dag_fixed_schema`
   - Still fixed schema. The difference is that values encode directed graph
     structure.
   - Fields include `graph_nodes`, `graph_edges`, `edge_directions`,
     `root_entities`, `leaf_entities`, `relation_path`, and `evidence_terms`.
   - This tests whether explicit directionality helps the agent choose better
     evidence paths than unordered entity/relation text.

## Folder Level Generation

`gen_auto_folder_level` is corpus-level, not only document-level. It first needs
the semantic profile for each document, then builds a tree.

The experiment compares two folder generators:

1. `topic_count_folder`
   - Summarize each document into `primary_topic`.
   - Cluster related topics across the selected corpus.
   - Count documents per cluster.
   - Folder path starts with the largest cluster rank:
     `/topic_count/r01_<cluster>/source_<source_type>/type_<doc_type>`.

2. `entropy_folder`
   - Candidate dimensions: `topic_cluster`, `primary_topic`, `source_type`,
     `doc_type`, `time_period`, `product_or_system`, `communication_channel`.
   - Compute Shannon entropy for each dimension across the selected corpus.
   - Higher-entropy dimensions go higher in the tree because they split the
     search space most strongly.
   - Folder path shape:
     `/entropy/<highest_entropy_field=value>/<next_field=value>/...`.

## Evaluation

Each metadata schema is crossed with each folder generator, producing four PIFS
workspaces. The same 10 EnterpriseRAG questions are run through the same agent
loop. Primary score is `doc_hit_rate`; tie-breakers are average tool calls and
average seconds.
