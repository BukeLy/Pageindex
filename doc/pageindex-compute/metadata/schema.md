# Metadata Schema

## Current Schema Shape

`MetadataSchema.schemaJson` uses:

```json
{
  "fields": {
    "year": {
      "type": "number",
      "description": "Publication year"
    },
    "company": {
      "type": "string",
      "description": "Company name"
    }
  }
}
```

Allowed types:

- `string`
- `number`
- `boolean`

Rules:

- top-level object must contain only `fields`
- field names match `^[a-zA-Z][a-zA-Z0-9_]*$`
- each field spec has required `type`
- optional `description`
- schema size <= 16KB

## Scope Resolution

Schema is keyed by `(userId, folderId)`.

- `folderId = "root"` means user-level schema.
- A top-level folder can declare a workspace schema.
- A subfolder inherits its top-level workspace schema.
- Workspace schema wins over user-level schema.
- Root documents use the user-level schema.

## Write Semantics

Schema is immutable:

- first `PUT` succeeds
- second `PUT` returns conflict
- caller must `DELETE` then `PUT` to change it

This makes accidental schema drift less likely, but a large FileSystem will
need explicit schema versioning and migration strategy.

## Values Injection

`folder_metadata_values.py` can enrich schema fields with observed values:

- string: distinct values, capped
- number: min/max range
- boolean: distinct observed values

This is useful for LLM query generation because it reduces invalid filters and
helps the model choose exact values.

## Recommendation

Keep the schema registry concept, but evolve it to:

- include `date`/`datetime` and optional enum hints
- assign stable field IDs
- version schemas
- mark fields as indexed, faceted, sortable, or display-only
- record observed values and cardinality statistics

