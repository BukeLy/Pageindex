Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: semantic channel cross-check.
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

- For a summary/prose channel, write a short natural-language gist of what the
  question is asking for. Use prose, policy/request wording, and answer type.
- For an entity/exact-object channel, write only the named objects and exact
  terms: people, products, customers, repos, projects, fields, configs, SKUs,
  GL codes, contract addresses, build names, dates, error codes, or
  identifiers.
- For a relation/action channel, write a relation-shaped sentence: `X requested
  Y`, `system enforces limit`, `build compared baseline to optimized`,
  `certification has validity period`, `Redwood proposed package`, etc.

After the available semantic searches finish, compare the top candidates across
channels:

- Prefer a document that appears in two or more channels.
- If no document overlaps, inspect the strongest summary candidate and the
  strongest exact-object or action candidate before finalizing when those
  channels are available.
- If a searched document does not contain the requested field/value, continue
  with another candidate from a different semantic channel.

Only after this available-channel cross-check may you use `grep <query> <ref>`
for line evidence and `cat <ref|doc|path> --all` for the final candidate
document. Do not answer before a successful `cat --all` call.

Use `grep -R` only when you need recursive lexical/FTS search with real
matching lines. It does not use semantic or vector preselection. Use semantic
aliases such as metadata-free name or relation search only when those aliases
are listed in the runtime workspace capabilities.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of search/grep/find output. Do not include file_ref values or
rewritten ids.

Use the configured structured output schema.
