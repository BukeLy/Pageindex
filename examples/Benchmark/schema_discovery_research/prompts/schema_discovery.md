You are designing a metadata schema for a virtual filesystem workspace.

Rules:
- Use only the provided sample documents.
- Do not use benchmark questions, gold answers, expected ids, filenames, file paths, URLs, storage URIs, or dataset identifiers.
- Prefer fields that help an agent filter documents before opening full text.
- Avoid fields that are unique identifiers or artifact provenance.
- All field types must be string, number, or boolean.
- For `hybrid_v2_extension`, the base schema is already fixed. Return extension fields only. Do not repeat or rename base fields.
- For extension fields, only propose fields that appear likely to cover at least 30% of the sample, can be canonicalized into a small value set, and are useful for metadata DSL filtering.
- Return strict JSON only.

Strategy: {strategy}

Base schema, if any:
{base_schema_json}

Maximum generated fields: {max_fields}

Return:
{{
  "fields": [
    {{
      "name": "snake_case_name",
      "type": "string",
      "description": "what this field captures",
      "why_queryable": "why an agent would filter on this field",
      "coverage_estimate": 0.3,
      "canonical_values": ["small", "stable", "value", "set"],
      "synonyms": {{"canonical_value": ["alternate wording"]}},
      "empty_policy": "when this should be empty",
      "example_values": ["short grounded examples"],
      "source_evidence": "short phrase showing this came from the samples"
    }}
  ]
}}

Sample documents:
{sample_json}
