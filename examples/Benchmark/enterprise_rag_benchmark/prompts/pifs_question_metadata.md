Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: metadata.

Use the PageIndex virtual shell only. Command output is shell-like plain text.
This run is metadata-only: candidate discovery must use metadata DSL through
`find`. Do not use `ls`, `tree`, or `grep -R <query> <folder>` for candidate
discovery, and do not use broad full-text search over folders.

Start with `stat --schema /`, then build a short metadata search plan from the
schema and the question, then run `find / --where '<DSL>' --limit 10`.

Metadata DSL rules:
- Treat metadata as a coarse filter, not as exact full-text search.
- Prefer `$contains` over exact equality unless the question clearly gives a
  canonical value.
- Use short high-signal phrases, usually 1 to 4 words, not the full question.
- Prefer these fields when present: `entities`, `topic`, `constraints`,
  `summary`, `intent`, then corpus extension fields.
- Use at most two metadata conditions per `find`. Avoid long `$and` chains.
- If a `find` returns no refs, immediately broaden the next query by removing
  one condition or switching to a more general field.
- Do not repeat the same empty query pattern.
- Run at most four metadata candidate-discovery `find` commands before opening
  the best refs found.

Useful query pattern:
- First try a high-signal entity or system name.
- Then try the core mechanism, metric, policy, limit, time window, or failure
  symptom.
- Then try a broader `summary` or `topic` phrase.

After `find` returns refs, you may use `grep <query> <ref>` only inside those
candidate refs for line evidence. You must use `cat <ref> --all` before
answering. If several refs look plausible, open the top 1 to 3 candidates and
choose the document that directly answers the question.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only after refs appear should you use `grep` on a ref for line evidence and
then `cat <ref> --all` for the final candidate leaf documents. Do not answer
before a successful `cat --all` call.

Use the configured structured output schema.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of find output. Do not include file_ref values or rewritten ids.
