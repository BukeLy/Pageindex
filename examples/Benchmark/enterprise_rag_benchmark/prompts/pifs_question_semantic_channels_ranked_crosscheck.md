Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: ranked semantic channel cross-check.
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

- `search-summary`: short natural-language gist of the requested fact.
- `search-entity`: named objects and exact terms only: customers, products,
  projects, people, identifiers, configs, fields, build names, dates, SKUs,
  GL codes, contract addresses, or error codes.
- `search-relation`: relation-shaped sentence such as `customer requested
  package`, `system enforces limit`, `build compared baseline to optimized`,
  or `certification has validity period`.

Candidate selection after the three searches:

- Preserve rank priority. A low-ranked cross-channel overlap is not better than
  a rank-1 result from summary or entity.
- If the same document is rank 1 in both summary and entity, inspect it first
  even if relation does not return it.
- If a document appears in the top 3 of two channels, inspect it before any
  document that appears only once.
- If no top-3 overlap exists, inspect the best summary candidate and the best
  entity candidate before finalizing. Inspect a relation-only candidate only if
  the question is explicitly about an action, comparison, request, ownership,
  requirement, or cause.
- Do not choose a relation candidate merely because it overlaps at lower rank;
  relation is supporting evidence unless it is top 3 or the question is clearly
  relation-shaped.

Only after this ranked three-channel cross-check may you use
`grep <query> <ref>` for line evidence and `cat <ref|doc|path> --all` for the
final candidate document. Do not answer before a successful `cat --all` call.
If the opened document does not contain the requested field/value, continue
with another high-ranked candidate from a different semantic channel.

Use `grep -R` only if you need hybrid grep: vector preselection from the grep
query itself followed by real keyword matching inside those candidates. Use
`find --name` or `find --relation` only as extra bash-like aliases after the
mandatory explicit semantic searches.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of search/grep/find output. Do not include file_ref values or
rewritten ids.

Use the configured structured output schema.
