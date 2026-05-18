Question ID: {question_id}
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

After `find` returns refs, you may use `grep <query> <ref>` only inside those
candidate refs for line evidence. You must use `cat <ref> --all` before
answering. If several refs look plausible, open the top 1 to 3 candidates and
choose the article that directly answers the question.

Use the configured structured output schema.
Return:
- answer: a concise support answer grounded in opened articles.
- article_ids: exact WixQA article ids copied from PIFS output.
