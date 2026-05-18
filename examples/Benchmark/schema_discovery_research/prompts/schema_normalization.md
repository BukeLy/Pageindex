Normalize a proposed metadata schema for PageIndex FileSystem.

Rules:
- Keep only retrieval-useful fields.
- Convert field names to snake_case.
- Merge duplicate or near-duplicate fields.
- Reject dataset ids, benchmark ids, file ids, article ids, document ids, filenames, file paths, URLs, storage URIs, gold-answer fields, and expected-answer fields.
- Prefer a small stable schema over many narrow fields.
- All fields must use type string for the first experiment.
- Return strict JSON only.
