# Metadata And Query

## FileStat Metadata

Mirage has `FileStat`:

```ts
{
  name,
  size,
  modified,
  fingerprint,
  type,
  extra
}
```

This is operational file metadata, not a typed document metadata catalog.

## Resource-Specific Metadata

Resources can put provider-specific data into `extra`, prompts, or custom
commands. This is flexible, but it is not a shared query schema.

## Query Style

Mirage query is shell-oriented:

```bash
grep alert /slack/general/*.json | wc -l
cat /s3/events.parquet | jq .user
```

This is powerful for agent operations, but it is not the same as PageIndex's
need for validated metadata filters, aggregate queries, and candidate
selection before document-tree retrieval.

## What PageIndex Should Borrow

- A common resource adapter interface.
- Programmatic filesystem operations.
- Separate file/listing caches.
- Fingerprint/revision metadata for consistency.
- Virtual path presentation for agents.

## What PageIndex Should Not Copy Directly

- Bash as the primary retrieval API.
- Treating provider path as document identity.
- Resource-specific metadata without a global schema registry.
- File byte access as the central abstraction.

PageIndex needs typed document metadata, indexed query execution, and a
retrieval planner.

