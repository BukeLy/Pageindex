Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: metadata.

Use the PageIndex virtual shell only. Command output is shell-like plain text.
This run is metadata-only: candidate discovery must use metadata DSL through
`find`. Do not use `grep -R <query> <folder>` for candidate discovery, and do
not use broad full-text search over folders.

Start with:
1. `stat --schema /`
2. Build a short metadata search plan from the schema and the question.
3. Run `find / --where '<DSL>' --limit 10`.

Full-corpus rule:
- Always include `source_type` when Source types has one value.
- If the question names a source-native object, also use that field:
  `repo`, `project`, `channel`, `space`, `customer`, `owner`, `status`,
  `doc_type`, `source_bucket`, `year`, or `month`.
- Use `$contains` for semantic fields: `topic`, `entities`, `constraints`,
  `summary`, `intent`.
- Use `$eq` only for controlled fields like `source_type`, `repo`, `project`,
  `channel`, `doc_type`, `year`, `month`, or exact customer/account names.

Budget:
- Use at most two metadata conditions per `find`.
- Run at most five candidate-discovery `find` commands before opening refs.
- If a query returns no refs, broaden immediately by dropping one condition or
  switching to `entities`, `topic`, or `constraints`.
- Do not repeat an empty pattern.

Useful query pattern:
- First filter by source type plus the strongest entity/system/product name.
- Then source type plus the mechanism, metric, policy, default, limit, time
  window, customer, channel, project, issue key, repo, or failure symptom.
- Then source type plus a broader `topic` or `summary` phrase.

After `find` returns refs, you may use `grep <query> <ref>` only inside those
candidate refs for line evidence. You must use `cat <ref> --all` before
answering. Open the top 1 to 3 candidates and choose the document that directly
answers the question.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only after refs appear should you use `grep` on a ref for line evidence and
then `cat <ref> --all` for the final candidate leaf documents. Do not answer
before a successful `cat --all` call.

Use the configured structured output schema.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of find output. Do not include file_ref values or rewritten ids.
