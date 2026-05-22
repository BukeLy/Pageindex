Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: semantic channels.
Use the PageIndex virtual shell only. Command output is shell-like plain text.
Start inside the listed Source types. If Source types is `github`, search
`/github`, not `/`. Do not run broad searches from `/` unless no source type is
available.

For candidate discovery, choose the semantic search tool whose space best
matches the clue you want to test. You may use one, two, or all three tools, but
do not call them mechanically with the same query.

- Use `search-summary '<query>' <path>` for the natural-language gist of the
  question, policy text, email/request summaries, or when the answer is likely
  described in prose.
- Use `search-entity '<query>' <path>` for named entities, product names,
  customers, repos, people, projects, identifiers, fields, configs, SKUs, GL
  codes, contract addresses, or exact objects mentioned by the question.
- Use `search-relation '<query>' <path>` for relation-shaped clues such as
  `customer requested package`, `system enforces limit`, `build compared
  baseline to optimized`, or `certification has validity period`.

Prefer compact, layer-specific queries. For example, a question about an
EdgePath pricing email should use entity terms like `EdgePath Redwood Year 1`
with `search-entity`, and relation terms like `Redwood proposed alternative
Year 1 pricing package` with `search-relation`. A question about a default size
limit should use entity/config terms with `search-entity` and prose summary
terms with `search-summary`.

After candidate refs appear, use `grep <query> <ref>` for line evidence when
useful, then run `cat <ref> --all` before answering. Do not answer before a
successful `cat --all` call on the final candidate document.

Use `grep -R` only when you intentionally want hybrid grep: vector preselection
from the grep query itself followed by real keyword matching inside those
candidates. Use `find --name` only when you intentionally want the bash-like
entity-space alias. Use `find --relation` only when you intentionally want the
bash-like relation-space alias. For this experiment, prefer the explicit
`search-summary`, `search-entity`, and `search-relation` commands so the tool
choice is visible in logs.

Use metadata `find` only for exact or canonical filters such as source_type,
repo, state, owner, year, or a known document id. Do not use broad `$contains`
metadata filters on title, summary, topic, or other free-text fields in full
corpus runs.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of search/grep/find output. Do not include file_ref values or
rewritten ids.

Use the configured structured output schema.
