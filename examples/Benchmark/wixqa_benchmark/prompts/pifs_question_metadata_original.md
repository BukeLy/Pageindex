Question ID: {question_id}
Question: {question}

Retrieval mode: metadata.
Use only metadata DSL for candidate discovery. Start with `stat --schema /`,
then run `find / --where '<DSL>'` or `find / -type d --where '<DSL>'`.
Use `$contains` when exact values are uncertain. Do not use `ls`, `tree`, or
`grep -R <query> <folder>` for candidate discovery. After `find` returns refs,
you may use `grep <query> <ref>` for line evidence and must use
`cat <ref> --all` before answering.

Use the configured structured output schema.
Return:
- answer: a concise support answer grounded in opened articles.
- article_ids: exact WixQA article ids copied from PIFS output.
