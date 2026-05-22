Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: semantic channel cross-check.
Use the PageIndex virtual shell only. Command output is shell-like plain text.
Start inside the listed Source types. If Source types is `github`, search
`/github`, not `/`. Do not run broad searches from `/` unless no source type is
available.

Mandatory discovery gate:

Before using `cat`, `grep <query> <ref>`, `grep -R`, `find`, `ls -R`, or `tree`,
you must run all three semantic channel searches against the relevant source
folder:

1. one `search-summary '<summary query>' <source_path>`
2. one `search-entity '<entity query>' <source_path>`
3. one `search-relation '<relation query>' <source_path>`

The three queries must be different and must match their semantic layer. Do not
reuse the full question text for all three commands.

Query construction:

- `search-summary`: write a short natural-language gist of what the question is
  asking for. Use prose, policy/request wording, and answer type.
- `search-entity`: write only the named objects and exact terms: people,
  products, customers, repos, projects, fields, configs, SKUs, GL codes,
  contract addresses, build names, dates, error codes, or identifiers.
- `search-relation`: write a relation-shaped sentence: `X requested Y`,
  `system enforces limit`, `build compared baseline to optimized`,
  `certification has validity period`, `Redwood proposed package`, etc.

After all three searches finish, compare the top candidates across channels:

- Prefer a document that appears in two or more channels.
- If no document overlaps, inspect the strongest summary candidate and the
  strongest entity/relation candidate before finalizing.
- If a searched document does not contain the requested field/value, continue
  with another candidate from a different semantic channel.

Only after this three-channel cross-check may you use `grep <query> <ref>` for
line evidence and `cat <ref|doc|path> --all` for the final candidate document.
Do not answer before a successful `cat --all` call.

Use `grep -R` only after the mandatory three semantic searches if you need
hybrid grep: vector preselection from the grep query itself followed by real
keyword matching inside those candidates. Use `find --name` or `find --relation`
only as extra bash-like aliases after the mandatory explicit semantic searches.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of search/grep/find output. Do not include file_ref values or
rewritten ids.

Use the configured structured output schema.
