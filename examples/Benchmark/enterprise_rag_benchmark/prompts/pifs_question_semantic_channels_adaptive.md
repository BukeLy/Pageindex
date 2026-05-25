Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: adaptive semantic channels.
Use the PageIndex virtual shell only. Command output is shell-like plain text.
Start inside the listed Source types. If Source types is `github`, search
`/github`, not `/`. Do not run broad searches from `/` unless no source type is
available.

Use semantic channels adaptively. Semantic commands are dynamic; use only the
commands listed in the runtime workspace capabilities. Do not invent or call
semantic command names that are absent from that list. Do not run every listed
semantic command mechanically at the start. Pick one available channel, inspect
the best candidate, and switch channels if the opened document is not the exact
evidence.

Channel selection:

- Use a summary/prose channel first when the question is mostly natural
  language, asks for a fact in a policy/email/page, or does not contain a
  strong exact identifier.
- Use an entity/exact-object channel when the question names customers,
  products, repos, people, projects, fields, configs, SKUs, GL codes, contract
  addresses, build names, dates, error codes, or identifiers.
- Use a relation/action channel after summary/prose or exact-object candidates
  are only similar, or when the question is about an action, comparison,
  request, ownership, requirement, cause, or relationship.

Adaptive recovery protocol:

1. Start with the most likely available channel. If unsure and a summary/prose
   channel is listed, start there.
2. Open the strongest candidate with `cat <ref|doc|path> --all`.
3. Verify the opened document against the question's exact constraints:
   entity names, source type, customer/project/build names, requested field,
   answer type, dates, numbers, and relation.
4. If the document is only topically similar or does not contain the requested
   exact evidence, do not answer from it. Switch to a different semantic
   channel and rewrite the query for that channel.
5. If summary/prose search fails and an entity/exact-object channel is listed,
   try exact nouns/identifiers from the question. If that also returns
   similar-but-wrong candidates and a relation/action channel is listed, try a
   verb phrase such as `X proposed Y`, `build compared baseline to optimized`,
   `certification has validity period`, or `customer requested package`.
6. Do not repeat the same channel with near-identical query text unless you have
   learned a new exact term from an opened document.
7. Limit yourself to at most two `cat --all` calls per channel before switching
   channels.

Examples:

- For an EdgePath pricing email, if summary/prose search opens a similar
  pricing email for a different customer, reject it and use an entity/exact
  channel with `EdgePath Redwood Year 1 pricing`, then a relation/action channel
  with `Redwood proposed alternative Year 1 pricing package` if those channels
  are listed.
- For a model regression comparison, if summary opens a general regression
  note that does not contain the requested first comparison run and score
  change, switch to entity terms such as `baseline build optimized build triage
  rubric`, then relation terms such as `baseline compared to optimized score
  change`.
- For a certification validity question, if summary/prose and exact-object
  channels return the same exact bootcamp document, open it and answer; do not
  let a lower-ranked action-only candidate override it.

After refs appear, you may use `grep <query> <ref>` for line evidence. Do not
answer before a successful `cat --all` call on the final evidence document.

Use `grep -R` only when you want recursive lexical/FTS search with real
matching lines. It does not use semantic or vector preselection. Use semantic
aliases such as metadata-free name or relation search only when those aliases
are listed in the runtime workspace capabilities. Prefer explicit
runtime-listed semantic commands so the semantic choice is visible in logs.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of search/grep/find output. Do not include file_ref values or
rewritten ids.

Use the configured structured output schema.
