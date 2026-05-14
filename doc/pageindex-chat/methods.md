# Methods

## API Client

`PageIndexApiService` wraps backend calls:

- `searchDocs()` calls `POST /docs/search`.
- `listDocs()` calls `GET /docs` and JSON-stringifies:
  - `metadata_filter`
  - `sort`
  - `aggregates`
  - `group_by`
- `getMetadataSchema()` calls `GET /metadata/schema`.
- `replaceDocMetadata()` calls `PUT /doc/{id}/metadata`.
- `setFolderSchema()` calls `PUT /folder/{folderId}/metadata/schema`.
- `deleteFolderSchema()` calls `DELETE /folder/{folderId}/metadata/schema`.

## Prisma Model Alignment

`prisma/schema.prisma` already includes:

- `FilePageIndex.metadata Json?`
- `FilePageIndex.folderId`
- `Folder` adjacency fields
- `MetadataSchema(userId, folderId, schemaJson)`

This mirrors the compute-side schema.

## Tool Layer

The MCP tool layer is more opinionated than the raw backend:

- It tells agents to browse before concluding a document does not exist.
- It forces structured metadata fields under a top-level `metadata` object.
- It encourages `recursive=true` for library-wide metadata queries.
- It handles plan-gating and folder-scope boundaries before making backend
  calls.
- It attaches human-readable paths to folder/document results.

## Design Takeaway

The chat implementation should be treated as a product prototype for the
PageIndex FileSystem UX. The shared core should be pulled down into reusable
FileSystem APIs so chat, MCP, SDK, and compute do not drift.

