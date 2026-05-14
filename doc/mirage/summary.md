# Summary

Mirage is a unified virtual filesystem for AI agents. It lets an agent see
many backends - S3, Google Drive, Slack, Gmail, GitHub, Redis, local/RAM
storage, databases, and others - as one mounted filesystem tree. The agent can
then use Unix-like commands such as `cat`, `ls`, `grep`, `rg`, and pipelines
across services.

Mirage's central abstraction is:

```text
Workspace
  MountRegistry
    /data  -> RAMResource
    /s3    -> S3Resource
    /slack -> SlackResource
  OpsRegistry
  File cache
  Index cache
```

Mirage is useful to PageIndex mainly as an abstraction reference:

- mount-based resource adapters
- a unified virtual path surface
- resource-specific commands behind a common operation interface
- cache separation between raw bytes and listing/stat indexes
- snapshot/replay semantics for touched paths

It is not a direct PageIndex FileSystem replacement because it is optimized for
agent tool access across many live backends, not for typed document metadata
queries or PageIndex's corpus-to-document-tree retrieval flow.

## Key Inspiration

The part worth borrowing is not the bash shell. The part worth borrowing is the
clean separation between:

- logical path namespace
- mounted resources
- resource operations
- caching
- execution interface

PageIndex needs a similar separation, but the query planner and metadata
catalog should be PageIndex-specific.

