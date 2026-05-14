# Summary

The standalone `VectifyAI/pageindex-mcp` repository does not currently contain
Folder or metadata FileSystem logic.

It is a local stdio MCP wrapper around the remote PageIndex MCP server at
`https://chat.pageindex.ai`. It dynamically fetches remote tools and exposes
them locally. It also proxies MCP resources.

Therefore, for FileSystem design, this repository is not the source of truth.
The relevant implementation is in `pageindex-chat/server/mcp`, where tools such
as `browse_documents`, `get_folder_structure`, `search_documents`, and
`get_metadata_schema` are implemented.

