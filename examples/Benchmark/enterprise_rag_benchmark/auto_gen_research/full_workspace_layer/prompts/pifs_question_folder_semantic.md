Question ID: {question_id}
Source types: {source_types}
Question: {question}

Retrieval mode: folder.

Use the PageIndex virtual shell only. Command output is shell-like plain text.
Use only folder navigation and text search for candidate discovery: `ls`,
`tree`, `grep -R`, `grep <query> <ref>`, `cat`, and `stat`.
Do not use `find --where`, metadata DSL, `stat --schema`, `ls --where`, or
`tree --where`.

Use the semantic folder tree first. Start with:
1. `ls /semantic`
2. `ls /semantic/source=<source_type>` when Source types has one value.
3. Use `tree <path> --depth 2` only after choosing a source folder.

Full-corpus rule:
- Do not run `grep -R` at `/` or at `/semantic`.
- Run `grep -R` only inside a narrowed source folder such as
  `/semantic/source=github`, `/semantic/source=gmail`, `/semantic/source=slack`,
  or a deeper topic/type/customer folder.
- Use compact 1 to 5 word probes, not the full question.
- Prefer source-like identifiers, product names, default names, metric names,
  fields, units, issue keys, repo names, customer names, and exact values.

When `grep -R` returns folder matches, choose a narrower folder and run
`grep -R` again. When it returns refs, inspect refs with `grep <query> <ref>`
for line evidence and then `cat <ref> --all` for the final candidate leaf
documents.

Refs look like ref_1, ref_2, and so on; use refs directly, not as path suffixes.
Only after refs appear should you use `grep` on a ref for line evidence and
then `cat <ref> --all` for the final candidate leaf documents. Do not answer
before a successful `cat --all` call.

Use the configured structured output schema.
Only include exact dsid_* document_ids copied from a document_id line or the
second column of ls/grep output. Do not include file_ref values or rewritten ids.
