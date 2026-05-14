# EnterpriseRAG Benchmark Adapter

This folder contains only the EnterpriseRAG data adapter for PageIndex
FileSystem ingestion.

There is intentionally no standalone smoke runner here. Benchmark execution
should use the PageIndex FileSystem Agent path:

```text
EnterpriseRAG data -> PageIndexFileSystem -> PIFS bash commands -> Agent loop
```

Generated benchmark outputs, SQLite databases, and local artifacts should stay
under `runs/`, which is ignored by git.
