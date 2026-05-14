# Folder Structure

## Tools

`browse_documents` returns a mixed page of:

- immediate subfolders
- documents

`get_folder_structure` returns a recursive tree, similar to `tree -d`.

## Scope Behavior

`server/mcp/utils/folder.ts` defines a clear behavior matrix:

```text
effectiveFolderId undefined + any recursive -> whole library
root/null + recursive=false                -> direct root docs
root/null + recursive=true                 -> whole library
folder id + recursive=false                -> direct folder docs
folder id + recursive=true                 -> folder subtree
```

This is a good API contract and should be preserved.

## Human-Readable Paths

Results include display paths like:

```text
Research/Papers/2024
```

These paths are built by loading folder rows and walking parent links. They are
helpful for LLM navigation and disambiguation, but they should not be used as
stable identity because folders can be renamed or moved.

## Scope Validation

The code supports:

- unrestricted all-library scope
- root-only scope
- folder-subtree scope

The subtree check loads folder rows and walks parent links in memory. This is
fine for current team/user sizes but should be replaced by a hierarchy index at
large scale.

## Design Takeaway

PageIndex should keep `folder_id` as the execution identity and expose `path`
as display/context. For millions of documents, folder subtree expansion must be
indexed.

