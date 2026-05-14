# Methods

## Workspace

`Workspace` receives a map of mount prefixes to resources:

```ts
new Workspace({
  "/data": new RAMResource(),
  "/s3": new S3Resource(...),
  "/slack": new SlackResource(...)
})
```

It constructs:

- `MountRegistry`
- `OpsRegistry`
- `SessionManager`
- `WorkspaceFS`
- file cache
- observer/session history

`Workspace.resolve(path)` asks the mount registry to find the resource for a
path, opens the resource lazily, and returns:

```text
Resource, PathSpec, MountMode
```

## Mount Registry

`MountRegistry` normalizes mount prefixes and sorts them longest-prefix first.
This lets nested mounts work correctly.

It supports:

- `mount(prefix, resource, mode)`
- `unmount(prefix)`
- `mountFor(path)`
- `resolve(path)`
- `childMountNames(parentPath)`
- `descendantMounts(path)`

## Resource Interface

`Resource` is a backend adapter interface. It can expose:

- `readFile`
- `writeFile`
- `readdir`
- `stat`
- `mkdir`
- `rmdir`
- `unlink`
- `rename`
- `find`
- `glob`
- `fingerprint`
- `ops`
- `commands`

This is the main adapter pattern PageIndex should borrow.

## Ops Registry

`OpsRegistry` dispatches operations by:

```text
operation name + resource kind + optional filetype
```

This allows generic operations like `read` or `readdir` to call resource-
specific implementations, and filetype-specific handlers to override behavior.

## WorkspaceFS

`WorkspaceFS` is a programmatic API over the virtual filesystem:

- `readFile`
- `writeFile`
- `readdir`
- `stat`
- `exists`
- `mkdir`
- `unlink`
- `rename`

This is closer to what PageIndex should expose than Mirage's full shell.

## Cache

Mirage separates:

- file cache: raw bytes for paths
- index cache: stat/listing entries for resources

This is useful for PageIndex because document trees, page manifests, folder
listings, and metadata query results have different invalidation behavior.

## Snapshot

Mirage snapshot/replay captures workspace state, touched cache bytes, and
fingerprints or revisions for remote reads. This is valuable for reproducing
agent runs, but it is not the same problem as PageIndex corpus indexing.

PageIndex can borrow fingerprint/revision ideas for document ingestion and
cache invalidation, not the full snapshot model as the core FileSystem design.

