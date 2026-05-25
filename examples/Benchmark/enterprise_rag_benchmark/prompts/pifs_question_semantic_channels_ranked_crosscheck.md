Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: ranked semantic channel cross-check.
Use the PageIndex virtual shell only. Command output is shell-like plain text.
Start inside the listed Source types. If Source types is `github`, search
`/github`, not `/`. Do not run broad searches from `/` unless no source type is
available.

Discovery gate:

Before using `cat`, `grep <query> <ref>`, `grep -R`, `find`, `ls -R`, or
`tree`, inspect the runtime workspace capabilities and run the listed semantic
candidate commands against the relevant source folder. Semantic commands are
dynamic; some workspaces may expose fewer than three channels or none at all.
Do not invent or call semantic command names that are absent from the runtime
list.

If all summary/prose, entity/exact-object, and relation/action channels are
listed, run one query in each channel before opening candidates. If only one or
two semantic commands are listed, run those listed commands before opening
candidates. If no semantic commands are listed, fall back to folder browsing,
metadata filtering, and lexical grep. Queries for different channels must be
different and must match their semantic layer. Do not reuse the full question
text for all commands.

Query construction:

- For a summary/prose channel, write a short natural-language gist of the
  requested fact.
- For an entity/exact-object channel, write named objects and exact terms only:
  customers, products, projects, people, identifiers, configs, fields, build
  names, dates, SKUs, GL codes, contract addresses, or error codes.
- For a relation/action channel, write a relation-shaped sentence such as
  `customer requested package`, `system enforces limit`, `build compared
  baseline to optimized`, or `certification has validity period`.

Candidate selection after the available semantic searches:

- Preserve rank priority. A low-ranked cross-channel overlap is not better than
  a rank-1 result from summary or entity.
- If the same document is rank 1 in both summary/prose and exact-object
  channels, inspect it first even if the action channel does not return it.
- If a document appears in the top 3 of two channels, inspect it before any
  document that appears only once.
- If no top-3 overlap exists, inspect the best summary/prose candidate and the
  best exact-object candidate before finalizing when those channels are
  available. Inspect an action-only candidate only if the question is explicitly
  about an action, comparison, request, ownership, requirement, or cause.
- Do not choose an action-channel candidate merely because it overlaps at lower
  rank; action evidence is supporting evidence unless it is top 3 or the
  question is clearly relation-shaped.

Only after this ranked available-channel cross-check may you use
`grep <query> <ref>` for line evidence and `cat <ref|doc|path> --all` for the
final candidate document. Do not answer before a successful `cat --all` call.
If the opened document does not contain the requested field/value, continue
with another high-ranked candidate from a different semantic channel.

Use `grep -R` only if you need recursive lexical/FTS search with real matching
lines. It does not use semantic or vector preselection. Use semantic aliases
such as metadata-free name or relation search only when those aliases are listed
in the runtime workspace capabilities.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of search/grep/find output. Do not include file_ref values or
rewritten ids.

Use the configured structured output schema.
