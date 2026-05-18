You are designing a metadata schema for a virtual filesystem workspace.

Rules:
- Use only the provided sample documents.
- Do not use benchmark questions, gold answers, expected ids, filenames, file paths, URLs, storage URIs, or dataset identifiers.
- Prefer fields that help an agent filter documents before opening full text.
- Avoid fields that are unique identifiers or artifact provenance.
- All field types must be string, number, or boolean.
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
      "example_values": ["short grounded examples"],
      "source_evidence": "short phrase showing this came from the samples"
    }}
  ]
}}

Sample documents:
{sample_json}
