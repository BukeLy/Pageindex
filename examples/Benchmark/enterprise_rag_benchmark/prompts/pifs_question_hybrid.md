Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: hybrid.
Use the PageIndex virtual shell only. Command output is shell-like plain text.
Start with folder inspection using `ls` or `tree`. When metadata fields clearly
apply, use `find <path> -type d --where '<DSL>'` to find folders whose subtrees
contain matching files. When `grep -R` on a folder returns folder matches,
choose a narrower folder and run `grep -R` again there.
Do not use `ls --where` or `tree --where`.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only after refs appear should you use `grep` on a ref for line evidence and
then `cat <ref> --all` for the final candidate leaf documents. Do not answer
before a successful `cat --all` call.

Use the configured structured output schema.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of ls/grep/find output. Do not include file_ref values or
rewritten ids.
