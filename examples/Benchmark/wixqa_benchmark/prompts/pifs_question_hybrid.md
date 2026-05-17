Question ID: {question_id}
Question: {question}

Retrieval mode: hybrid.
Use metadata and folders together. Start with `stat --schema /` or folder
inspection, use `find -type d --where` when metadata can narrow the KB, use
`grep -R` to locate candidate articles, then open useful refs with
`cat <ref> --all`.

Use the configured structured output schema.
Return:
- answer: a concise support answer grounded in opened articles.
- article_ids: exact WixQA article ids copied from PIFS output.
