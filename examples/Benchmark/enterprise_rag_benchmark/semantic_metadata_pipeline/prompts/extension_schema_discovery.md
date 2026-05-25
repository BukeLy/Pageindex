You discover extension metadata fields for a virtual filesystem.

Goal:
Find canonical fields that help metadata DSL filtering or folder browsing. Do not propose semantic text fields.

Rules:
- Use only sample documents and existing normalized metadata.
- Do not use benchmark questions, gold answers, expected ids, filenames, file paths, URLs, storage URIs, or outside knowledge.
- Do not propose ids, document UUIDs, paths, URLs, artifact provenance, summary, entities, relations, constraints, or retrieval_cues.
- Prefer fields with good coverage, low/medium cardinality, clear canonical values, and useful browse/filter behavior.
- A field may be suitable_for_dsl, suitable_for_folder, or both.
- Return strict JSON only.

Return:
{
  "fields": [
    {
      "name": "snake_case_name",
      "description": "",
      "why_queryable": "",
      "coverage_estimate": 0.0,
      "canonical_values": [],
      "synonyms": {},
      "suitable_for_dsl": true,
      "suitable_for_folder": true,
      "cardinality_expectation": "low",
      "empty_policy": "",
      "example_values": [],
      "source_evidence": ""
    }
  ]
}

Sample:
{sample_json}
