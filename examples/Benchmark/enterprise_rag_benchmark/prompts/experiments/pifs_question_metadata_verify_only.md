Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: metadata.
Use the PageIndex virtual shell only. Command output is shell-like plain text.
Use only metadata DSL for candidate discovery. Start with `stat --schema /`,
then run `find / --where '<DSL>'` or `find / -type d --where '<DSL>'`.
Use `$contains` for substring matching when exact values are uncertain.
Do not use `ls`, `tree`, or `grep -R <query> <folder>` for candidate discovery.
After `find` returns refs, you may use `grep <query> <ref>` for line evidence
and must use `cat <ref> --all` before answering.

Single experimental change: verify candidates early.
- Once a `find` returns plausible refs, do not keep searching metadata forever.
- Open the top 1 to 3 plausible refs with `cat <ref> --all`.
- Choose the document that directly answers the question from the opened refs.
- If the first opened ref does not answer the question, open the next plausible
  ref from the same `find` result before writing the final answer.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only after refs appear should you use `grep` on a ref for line evidence and
then `cat <ref> --all` for the final candidate leaf documents. Do not answer
before a successful `cat --all` call.

Use the configured structured output schema.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of find output. Do not include file_ref values or rewritten ids.
