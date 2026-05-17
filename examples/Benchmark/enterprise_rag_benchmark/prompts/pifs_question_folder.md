Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: folder.
Use the PageIndex virtual shell only. Command output is shell-like plain text.
Use only folder navigation and text search for candidate discovery: `ls`,
`tree`, `grep -R`, `grep <query> <ref>`, `cat`, and `stat`.
Do not use `find --where`, metadata DSL, `stat --schema`, `ls --where`, or
`tree --where`.
Start with `ls` or `tree`, narrow through folders with `grep -R`, then inspect
refs with `grep <query> <ref>` and `cat <ref> --all`.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only after refs appear should you use `grep` on a ref for line evidence and
then `cat <ref> --all` for the final candidate leaf documents. Do not answer
before a successful `cat --all` call.

Use the configured structured output schema.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of ls/grep output. Do not include file_ref values or rewritten ids.
