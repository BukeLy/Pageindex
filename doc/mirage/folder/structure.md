# Folder Structure

## Mount Tree

Mirage's top-level "folders" are mount prefixes:

```text
/data
/s3
/slack
/github
```

Each mounted `Resource` is responsible for its own internal path semantics.

## Path Resolution

`MountRegistry.resolve(path)`:

1. normalizes the path
2. finds the longest matching mount prefix
3. strips the mount prefix into a `PathSpec`
4. returns the matching resource and mount mode

This is a clean abstraction for a multi-backend filesystem.

## S3-Like Folders

S3 does not have real folders. Mirage implements directory behavior by listing
objects with:

- `Prefix`
- `Delimiter: "/"`

It then synthesizes directory entries from `CommonPrefixes` and objects from
`Contents`.

This is directly relevant to PageIndex storage: S3/object-store paths are good
for blobs and artifacts, but logical Folder identity should live in the catalog
database. S3 prefixes should not be the source of truth for user-facing folder
operations.

## Difference From PageIndex Needs

Mirage path organization is backend-first:

```text
mount -> backend path -> file bytes
```

PageIndex FileSystem needs retrieval-first organization:

```text
tenant/workspace -> folder or virtual view -> document candidate -> PageIndex tree
```

The abstraction is useful, but the semantics need to be PageIndex-specific.

