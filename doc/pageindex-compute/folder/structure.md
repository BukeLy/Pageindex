# Folder Structure

## Current Model

`pageindex-compute` models folders as an adjacency list:

```text
Folder
  id
  name
  description
  folderParentId
  scope
  userId
  sortOrder
  mode
  teamspaceId
  createdByUserId
```

Documents point to a folder through `FilePageIndex.folderId`.
Root-level documents use `folderId IS NULL`.

## Semantics

The API uses `folder_id = "root"` as a user-facing sentinel.

Behavior:

```text
folder_id omitted          -> historical full-library behavior
folder_id="root"           -> direct root documents
folder_id="root", recursive=true -> all documents
folder_id=<id>             -> direct documents in that folder
folder_id=<id>, recursive=true -> documents in the folder subtree
```

## Recursive Implementation

`get_folder_ids_recursive(folder_id, user_id)` walks descendants and then
document queries use `folderId IN (...)`.

This is acceptable for small to medium folder trees but should not be the final
model for millions of documents or deep folder structures. A production
FileSystem should add one of:

- closure table: `folder_closure(ancestor_id, descendant_id, depth)`
- materialized path with indexed prefix/range query
- database-native tree path type if the chosen DB supports it

## Recommendation

Keep stable folder IDs and parent IDs, but add a hierarchy index. Folder paths
should be derived and cached for display, not treated as stable identity.

