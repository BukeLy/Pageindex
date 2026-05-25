Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: semantic metadata pipeline smoke.

Use the PageIndex virtual shell only. The workspace was materialized from the
semantic metadata pipeline artifacts:
- folder browse from folder_plan.json
- metadata DSL from approved canonical fields
- semantic channels from whichever projection indexes the workspace exposes
- grep/FTS as lexical search only

Start inside the listed source type roots. Source roots are mounted as
`/source_type=<source-type>`, with underscores converted to hyphens. For
example, source type `google_drive` is `/source_type=google-drive`, and
`github` is `/source_type=github`.

Use ls/tree to understand browse structure. Use stat --schema if you need to
see metadata fields available to find --where. Use find --where only for exact
or canonical fields such as source_type, content_type, doc_type, domain, topic,
or extension fields discovered by the pipeline.

Use only the semantic search commands listed in the runtime workspace
capabilities. Choose the available projection space whose description best
matches the clue.

Semantic search returns candidates, not literal text matches. After candidate
refs appear, inspect promising candidates with grep <query> <ref> when useful,
then cat <ref> --all before answering.

Use grep -R only for recursive lexical/FTS search. It must return real matching
lines. If grep -R reports that the folder is too broad, switch to
one of the suggested available semantic commands, then verify the final
candidate with cat. If no semantic command is available, narrow with
ls/tree/find --where.

Only include exact dsid_* document_ids copied from command output. Use the
configured structured output schema.
