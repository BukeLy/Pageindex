Extract metadata for one document according to the frozen workspace schema.

Rules:
- Use only the document text.
- Do not use filenames, paths, URLs, storage URIs, benchmark questions, gold answers, or outside knowledge.
- Return exactly the schema field names.
- If a field is not grounded in the text, return an empty string.
- If a field defines `canonical_values`, choose the closest grounded canonical value whenever possible.
- If none of the canonical values are grounded, return an empty string for that field.
- Use `synonyms` only to map document wording onto a canonical value; do not invent values.
- For `retrieval_cues`, return 5 to 10 short grounded phrases that a metadata-only agent might use in `$contains` queries. Prefer exact names, metrics, limits, policies, failure symptoms, APIs, systems, and customer/team names.
- Lists must be returned as short comma-separated text because the current PIFS metadata DSL stores string fields.
- Return strict JSON only.

Frozen schema:
{schema_json}

Document:
{document_text}
