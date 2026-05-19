Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: hybrid.
Use the PageIndex virtual shell only. Command output is shell-like plain text.
Start inside the listed Source types. If Source types is `github`, inspect and
search `/github`, not `/`. Do not run broad grep from `/` unless no source type
is available.

Start with folder inspection using `ls` or `tree` on the relevant source folder.
When metadata fields clearly apply, use `find <path> -type d --where '<DSL>'`
to find folders whose subtrees contain matching files. When `grep -R` on a
folder returns folder matches, choose a narrower folder and run `grep -R` again
there.
Do not use `ls --where` or `tree --where`.

Use metadata `find` only for exact or canonical filters such as source_type,
repo, state, owner, year, or a known document id. Do not use broad `$contains`
metadata filters on title, summary, topic, or other free-text fields in full
corpus runs; use `grep -R` for free-text discovery.

Never grep the full question text. Use compact 1 to 5 word queries. For
implementation-detail questions, try source-like terms from the question:
snake_case names, config names, metric names, HTTP fields, units, error codes,
or quoted phrases that are likely to appear in the document. For example, a
question about default size limits should lead to searches for terms like
`multipart upload`, `max_file_size`, `max_total_request_size`, `10MiB`, or
`50MiB`.

For exact-value questions asking for defaults, numeric limits, metric names,
JSON schema fields, error payload fields, headers, config keys, queue token
formats, contract addresses, GL codes, SKUs, or similar implementation facts,
you must run at least one grep using a source-like identifier, code-like token,
or unit before finalizing. Do not answer from merely similar documents. If the
opened document does not contain the exact requested field/value, keep searching
with a more specific grep.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only after refs appear should you use `grep` on a ref for line evidence and
then `cat <ref> --all` for the final candidate leaf documents. Do not answer
before a successful `cat --all` call.

Use the configured structured output schema.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of ls/grep/find output. Do not include file_ref values or
rewritten ids.
