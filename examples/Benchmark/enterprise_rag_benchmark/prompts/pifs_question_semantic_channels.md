Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: semantic channels.
Use the PageIndex virtual shell only. Command output is shell-like plain text.
Start inside the listed Source types. If Source types is `github`, search
`/github`, not `/`. Do not run broad searches from `/` unless no source type is
available.

For candidate discovery, choose among the semantic search commands listed in
the runtime workspace capabilities. Semantic commands are dynamic; some
workspaces may expose only one channel or none at all. Do not invent or call
semantic command names that are absent from the runtime list.

When the runtime capability text describes multiple semantic channels, choose
the channel whose space best matches the clue you want to test. Use a
summary/prose channel for natural-language gist, policy text, emails, requests,
or answer descriptions. Use an entity/exact-object channel for product names,
customers, repos, people, projects, identifiers, fields, configs, SKUs, GL
codes, contract addresses, or exact objects. Use a relation/action channel for
clues such as `customer requested package`, `system enforces limit`, `build
compared baseline to optimized`, or `certification has validity period`.

Prefer compact, layer-specific queries. For example, a question about an
EdgePath pricing email should use exact-object terms like `EdgePath Redwood
Year 1` for an entity-style channel when that channel is listed, and relation
terms like `Redwood proposed alternative Year 1 pricing package` for a
relation-style channel when that channel is listed. A question about a default
size limit should use entity/config terms for an entity-style channel and prose
summary terms for a summary-style channel.

After candidate refs appear, use `grep <query> <ref>` for line evidence when
useful, then run `cat <ref> --all` before answering. Do not answer before a
successful `cat --all` call on the final candidate document.

Use `grep -R` only when you intentionally want recursive lexical/FTS search
with real matching lines. It does not use semantic or vector preselection. Use
semantic aliases such as metadata-free name or relation search only when those
aliases are listed in the runtime workspace capabilities. For this experiment,
prefer explicit runtime-listed semantic commands so the tool choice is visible
in logs.

Use metadata `find` only for exact or canonical filters such as source_type,
repo, state, owner, year, or a known document id. Do not use broad `$contains`
metadata filters on title, summary, topic, or other free-text fields in full
corpus runs.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of search/grep/find output. Do not include file_ref values or
rewritten ids.

Use the configured structured output schema.
