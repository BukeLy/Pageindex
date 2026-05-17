Question ID: {question_id}
Question: {question}

Retrieval mode: folder.
Use only folder navigation and text search for candidate discovery: `ls`,
`tree`, `grep -R`, `grep <query> <ref>`, `cat`, and `stat`.
Do not use metadata DSL, `stat --schema`, or `find --where`.

Use the configured structured output schema.
Return:
- answer: a concise support answer grounded in opened articles.
- article_ids: exact WixQA article ids copied from PIFS output.
