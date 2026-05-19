# Full Workspace Layer Research Summary

## Current Result

Built a derived full EnterpriseRAG PIFS workspace from the existing registered corpus:

- Files: 511,958
- Queryable metadata rows: 6,885,119
- Compact FTS rows: 511,958
- Semantic folder memberships: 2,737,401
- Semantic folders: 91,062

This did not use benchmark gold answers for generation.

## Key Findings

1. Root cause is confirmed: full-corpus root grep is not viable. The previous full run timed out heavily because the agent searched physical source roots across hundreds of thousands of files.
2. SQLite EAV `$contains` is not viable as the semantic recall layer at this scale. Even source-filtered local probes over `metadata_values` exceeded 60s. Metadata DSL should be used for exact/canonical filters, not broad semantic contains.
3. Semantic folder projection is useful but currently only partially covers question language. On the timeout10 probe set, folder-name matching found expected-doc folders for 5/10 questions.
4. The current deterministic folder generator is too high-cardinality and too shallow semantically: 91k folders, with many one-file topic folders plus very large buckets like `/semantic/source=slack/type/slack-thread`.
5. Compact FTS over generated metadata is necessary for semantic recall, but naive multi-query probing with BM25 sorting is also too slow if we fan out many query variants. It needs a controlled query planner and/or precomputed token index, not blind query expansion.

## Probe Evidence

Folder probe on timeout-heavy 10 questions:

| question | source | folder hit | best folder |
|---|---|---:|---|
| qst_0007 | google_drive | yes | `/semantic/source=google-drive/topic/regression` files=96 |
| qst_0009 | gmail | yes | `/semantic/source=gmail/customer/edgepath` files=1 |
| qst_0011 | confluence | yes | `/semantic/source=confluence/topic/training` files=5 |
| qst_0018 | linear | yes | `/semantic/source=linear/topic/kvcache` files=65 |
| qst_0021 | confluence | yes | `/semantic/source=confluence/topic/access-control` files=74 |
| qst_0028 | slack | no | none |
| qst_0030 | google_drive | no | none |
| qst_0047 | gmail | no | none |
| qst_0051 | slack | no | none |
| qst_0056 | slack | no | none |

## Immediate Next Experiments

Do not run full benchmark yet. Next single-variable experiments should be:

1. `folder_v2_alias_projection`: add alias folders from normalized question-independent lexical variants in metadata (`kv-cache`/`kvcache`, `google-drive`/`drive`, plural/singular, hyphen removal). Measure folder probe hit rate on timeout10 and wrong5.
2. `folder_v3_clustered_topics`: cap topic cardinality by clustering topic slugs into corpus-level canonical topics per source. Target fewer than 5k semantic folders and no source/type folder with more than 10k files unless it has children.
3. `metadata_exact_only`: keep EAV DSL only for exact fields (`source_type`, `repo`, `project`, `channel`, `customer`, `year`, `month`, `status`) and remove prompt reliance on `$contains` for free text.
4. `semantic_recall_index`: implement a bounded inverted-token table or FTS planner over generated metadata terms, with source_type required and one query per turn, not many fan-out probes.

## Recommendation

PIFS full workspace should be three-layered:

1. Flat catalog in SQLite remains source of truth.
2. Metadata schema is split by purpose:
   - exact/canonical fields in `metadata_values` for DSL filtering;
   - semantic text fields in a dedicated recall index, not EAV `$contains`.
3. Folder is a browseable projection over canonical metadata and semantic clusters. It should be generated from metadata, but must include alias/canonicalization and folder cardinality gates.

The current v1 layer proves the direction but is not yet good enough for full benchmark scoring.
