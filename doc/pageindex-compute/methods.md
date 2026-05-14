# Methods

## API Surface

Folder:

- `POST /folder`
- `GET /folders`

Document listing and search:

- `GET /docs`
  - `folder_id`
  - `recursive`
  - `metadata_filter`
  - `sort`
  - `aggregates`
  - `group_by`
- `POST /docs/search`
  - semantic search plus optional `metadata_filter`
  - no sort, aggregate, or group-by support

Metadata:

- Upload metadata on `POST /doc`, `POST /tree`, `POST /doc/s3`,
  `POST /tree/s3`.
- Replace metadata with `PUT /doc/{doc_id}/metadata` and
  `PUT /tree/{doc_id}/metadata`.
- Manage schema through `/folder/{folder_id}/metadata/schema`.
- Read effective schema through `/metadata/schema`.

## Storage

The current backend stores document metadata in:

```text
FilePageIndex.metadata JSON
```

Folder hierarchy is stored in:

```text
Folder.id
Folder.folderParentId
Folder.userId
Folder.teamspaceId
Folder.scope
Folder.mode
```

Schema is stored in:

```text
MetadataSchema(userId, folderId, schemaJson)
```

## Query Compilation

`server/lib/db/metadata.py` translates a constrained metadata DSL into SQL.
It binds JSON paths and values as parameters. Operators are limited to:

- `$eq`, short form `{field: value}`
- `$ne`
- `$in`
- `$gt`, `$gte`, `$lt`, `$lte`
- `$and`, `$or`

This is much safer than allowing the LLM to generate SQL directly.

## Response Shapes

`GET /docs` has two response shapes:

- list mode:

```json
{
  "documents": [],
  "total": 123,
  "limit": 50,
  "offset": 0,
  "aggregates": {"n": 123}
}
```

- group mode:

```json
{
  "groups": [],
  "total": 10,
  "truncated": false
}
```

The shape switch is useful, but clients must treat it as a discriminated
response, not a single document-list response.

## Implementation Gaps

- Metadata filters over JSON are not indexed.
- List mode clamps to a small implementation cap in `planet.py`, while API
  docs expose larger limits. This should be reconciled before exposing as a
  stable FileSystem API.
- Recursive folder scope should move from repeated tree walking to a dedicated
  hierarchy index.
- Schema changes need versioning or migration semantics before large-scale
  adoption.

