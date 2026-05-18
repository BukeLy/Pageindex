Extract metadata for one document according to the frozen workspace schema.

Rules:
- Use only the document text.
- Do not use filenames, paths, URLs, storage URIs, benchmark questions, gold answers, or outside knowledge.
- Return exactly the schema field names.
- If a field is not grounded in the text, return an empty string.
- Lists must be returned as short comma-separated text because the current PIFS metadata DSL stores string fields.
- Return strict JSON only.

Frozen schema:
{schema_json}

Document:
{document_text}
