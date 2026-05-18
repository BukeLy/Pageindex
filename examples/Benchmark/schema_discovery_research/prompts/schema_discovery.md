You are designing a metadata schema for a virtual filesystem workspace.

Rules:
- Use only the provided sample documents.
- Do not use benchmark questions, gold answers, expected ids, filenames, file paths, URLs, storage URIs, or dataset identifiers.
- Prefer fields that help an agent filter documents before opening full text.
- Avoid fields that are unique identifiers or artifact provenance.
- All field types must be string, number, or boolean.
- For `hybrid_v2_extension`, the base schema is already fixed. Return extension fields only. Do not repeat or rename base fields.
- For extension fields, only propose fields that appear likely to cover at least 30% of the sample, can be canonicalized into a small value set, and are useful for metadata DSL filtering.
- For `hybrid_v3_problem_extensions`, prefer problem-oriented retrieval fields such as `source_channel`, `system_area`, `problem_type`, `action_type`, `metric_or_limit`, `failure_symptom`, `policy_or_rule_type`, and `customer_or_team`.
- For `hybrid_v3_alias_rich`, every proposed extension field must include useful `canonical_values` and `synonyms` that map alternate wording onto those canonical values.
- For `hybrid_v3_filter_power_gate`, prefer fields whose values split the corpus into useful buckets for retrieval, not fields that are nearly always the same or nearly unique per document.
- For `hybrid_v3_retrieval_cues`, still return only normal extension fields here; the runner will add a fixed `retrieval_cues` field after discovery.
- `default_workspace` uses the same schema profile as `hybrid_v3_retrieval_cues`: base 7 fields plus bounded workspace extensions, with a fixed `retrieval_cues` field added by the runner.
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
