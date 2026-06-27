# PIFS Core Alignment Spec

## Purpose

Align PageIndex FileSystem (PIFS) core with the current pageindex-chat document
navigation semantics while keeping PIFS as a BashLike agent tool surface.

PIFS must not become a typed MCP tool server. The alignment target is capability
semantics: folder-scoped pruning, document discovery, structure-first reading,
and evidence reads.

## Scope

This spec covers the first PIFS core alignment pass.

In scope:

- Keep PIFS BashLike commands as the agent-facing interface.
- Align command semantics with pageindex-chat document navigation behavior.
- Remove duplicate, misleading, or legacy command paths.
- Make command output JSON-only.
- Update `agent.py` command descriptions and policy to match the aligned funnel.
- Use ponytail review after implementation to delete remaining command bloat.

Out of scope:

- Replacing PIFS with MCP typed tools.
- Reintroducing Semantic Folder v1.
- Adding plan gating, mode policy, API proxy behavior, or upload/delete tools.
- Adding benchmark-specific prompts, result rows, or scoring logic to PIFS core.
- Adding future roadmap scaffolding.

## Source Alignment

pageindex-chat's current prompt and MCP surface establish these product
semantics:

- Folder-aware discovery starts with folder structure, then browse, then search
  escalation only if needed.
- Document reading is structure-first for long documents.
- Folder scope controls the visible document set.
- Legacy aliases such as `recent_documents`, `find_relevant_documents`, and
  `list_folders` are not the primary public path.
- `create_folder` is not part of the current MCP tool surface.

PIFS maps those semantics to BashLike commands rather than adopting MCP tool
names.

## Final Command Surface

PIFS exposes five agent-facing command names:

| Command | Role | pageindex-chat capability |
|---|---|---|
| `tree` | Folder structure orientation | `get_folder_structure` |
| `browse` | Sole document discovery command | `browse_documents` |
| `stat` | Single document identity, status, metadata | `get_document` |
| `cat` | Structure and page content reads | `get_document_structure`, `get_page_content` |
| `grep` | Single-document lexical evidence fallback | No primary MCP equivalent |

`ls` remains only as an exact alias for `tree -L 1`.

## Deleted Command Surface

Remove these from the agent-facing surface and implementation where practical:

- Independent `ls` logic.
- `ls -R`.
- `find`.
- `find -type d`.
- `find -type f`.
- `find --name`.
- `find --where`.
- `find --relation`.
- `find -maxdepth`.
- `grep <query> <folder>`.
- `grep -R`.
- Piped command support, including `... | grep ...`.
- `grep --where`.
- `stat --schema`.
- `stat --field`.
- Batch `stat`.
- Implicit `cat <file>` default reads.
- `cat --all` and `cat --range` for PIFS document reads.
- `browse --space`.
- Entity and relation projection retrieval.
- Semantic Folder CLI and Semantic Folder v1 code.
- Text-file specific PIFS reads as a supported final surface.

Deletion is preferred over compatibility shims.

## Command Semantics

### `tree`

`tree` is the structural orientation command.

Syntax:

```text
tree <folder> [-L depth]
ls <folder>
```

Rules:

- `tree` returns folders and folder counts, not semantic document candidates.
- `ls <folder>` is equivalent to `tree <folder> -L 1`.
- `ls` must not have separate implementation logic.
- Recursive file enumeration is not exposed.
- Agents use `tree` before choosing where to browse.

### `browse`

`browse` is the only document discovery command.

Syntax:

```text
browse <folder> "<query>" [--page N] [--where JSON] [-R]
```

Rules:

- Query is required.
- Results are relevance-ranked documents inside the folder scope.
- Default browse is non-recursive.
- `-R` expands the folder scope to descendants and is an escalation step, not
  the default first move.
- `--page` uses fixed page windows.
- `--where` is optional metadata pruning.
- Folder paths are positional command targets, not metadata filters.
- `--space` is removed.
- Summary projection is the only supported retrieval projection in this pass.
- Entity and relation projections are removed for now.

If `browse` misses, escalation is done by changing folder, changing query, or
using broader recursive browse. Do not add a separate search command.

### `stat`

`stat` inspects exactly one document.

Syntax:

```text
stat <file>
```

Rules:

- `stat` returns identity, status, page count if available, folder membership,
  and metadata.
- `stat` is not a content discovery command.
- `stat` is not a schema browser.
- `stat` is not a batch metadata API.

### `cat`

`cat` reads document structure or page evidence explicitly.

Syntax:

```text
cat <file> --structure
cat <file> --page N[-M]
```

Rules:

- PIFS documents must be read structure-first.
- `cat --page` must only be used after `cat --structure` for the same file
  locator in the current agent run.
- Page reads are targeted evidence reads.
- Implicit reads are removed.
- Text-specific reads are not part of the aligned final surface.
- PIFS should stop designing around `.txt` support.

### `grep`

`grep` is a single-document lexical fallback.

Syntax:

```text
grep <query> <file>
```

Rules:

- `grep` can only target a resolved file locator.
- `grep` is not document discovery.
- Folder grep, recursive grep, grep pipes, and metadata grep are removed.
- Agents should use `grep` only after `browse` has selected a candidate file.

## JSON Output

All command output is JSON. Shell text rendering and `--json` mode are removed.

Success envelope:

```json
{
  "success": true,
  "data": {},
  "next_steps": []
}
```

Error envelope:

```json
{
  "success": false,
  "error": {
    "code": "invalid_command",
    "message": "browse requires a query"
  },
  "next_steps": []
}
```

Rules:

- `data` keeps domain hierarchy.
- Do not flatten folders, documents, pages, metadata, or pagination into one
  top-level object.
- `next_steps` must contain BashLike commands, not MCP tool names.
- Do not copy pageindex-chat's top-level tool-specific JSON shape. PIFS uses a
  smaller stable envelope.

Expected payload shapes:

| Command | `data` shape |
|---|---|
| `tree` | `{ "tree": {...}, "total_folders": 0, "depth": 0, "truncated": false }` |
| `browse` | `{ "documents": [...], "pagination": {...}, "scope": {...} }` |
| `stat` | `{ "document": {...} }` |
| `cat --structure` | `{ "document": {...}, "structure": {...}, "pagination": {...} }` |
| `cat --page` | `{ "document": {...}, "requested_pages": "...", "returned_pages": [...], "content": {...} }` |
| `grep` | `{ "document": {...}, "matches": [...] }` |

## Agent Policy

`agent.py` must present PIFS as a read-only BashLike virtual filesystem.

The default agent funnel is:

1. Use `tree` to understand folder structure.
2. Choose the most relevant folder scope.
3. Use `browse <folder> "<query>"` for document discovery.
4. If browse misses, inspect unvisited folders, rephrase the query, or widen
   scope with recursive browse.
5. Use `stat <file>` only when identity, status, or metadata matters.
6. Use `cat <file> --structure` before any page read.
7. Use `cat <file> --page N[-M]` for targeted evidence.
8. Use `grep <query> <file>` only as a single-document lexical fallback.

The agent policy must explicitly forbid:

- `find`.
- Recursive grep.
- Folder grep.
- Piped commands.
- Schema browsing through `stat`.
- Batch metadata commands.
- Browse spaces.
- Implicit cat reads.
- General-knowledge fallback when workspace evidence is not found.

## Persistence Protocol

When relevant documents are not found:

1. Browse the most likely folder.
2. Use `tree` to inspect sibling or child folders not yet visited.
3. Browse another plausible folder.
4. Rephrase the query and browse again.
5. Use recursive browse from a broader folder.
6. Only after those steps may the agent say the workspace lacks evidence.

Do not add `find`, `search`, or recursive grep as escalation commands.

## Data And Identity

Preserve the current PIFS identity model:

- `external_id` is the product/evaluation-facing document id.
- Command payloads expose `external_id` as `document_id`.
- `file_ref` is the internal stable file locator.
- `pageindex_doc_id` is the cached PageIndex document id for structure and page
  reads.

Do not invent benchmark-only document ids.

## Verification

Implementation must leave focused checks for changed behavior:

- `ls` calls the same path as `tree -L 1`.
- Removed commands fail with structured JSON errors.
- `browse` rejects missing query and removed `--space`.
- `browse` only exposes summary retrieval.
- `stat` accepts one target and rejects schema, field, and batch modes.
- `cat --page` requires prior structure-first policy at the agent/tool policy
  level.
- Command output is always JSON with `success`, `data`, and `next_steps`.
- Agent instructions mention only the aligned command surface.

After implementation, run a ponytail review over the command list:

- Delete any command path that duplicates `tree`, `browse`, `stat`, `cat`, or
  single-file `grep`.
- Delete any renderer branch kept only for old shell output.
- Delete compatibility branches for removed commands unless a current test proves
  they are still required.
