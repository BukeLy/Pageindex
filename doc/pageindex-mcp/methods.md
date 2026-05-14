# Methods

## Remote Tools

`src/server.ts` initializes a local stdio MCP server. On `ListTools`, it:

1. connects to the remote PageIndex MCP server
2. calls `client.listTools()`
3. converts remote JSON schemas to Zod
4. registers local proxy handlers

`src/tools/remote-proxy.ts` excludes internal upload helpers:

- `get_signed_upload_url`
- `submit_document`

and exposes the rest as pass-through remote tool calls.

## Remote Resources

`src/resources/remote-proxy.ts` proxies:

- `listResources`
- `readResource`
- `listResourceTemplates`

There is no local resource catalog or Folder implementation here.

## Local Upload Tool

The main local tool is `process_document`. It supports upload flow by calling
remote upload tools and submitting the document.

## Design Takeaway

Do not model PageIndex FileSystem after this repository. Treat it as a client
distribution layer. The FileSystem semantics should live in PageIndex core /
compute / chat, then be surfaced through this proxy.

