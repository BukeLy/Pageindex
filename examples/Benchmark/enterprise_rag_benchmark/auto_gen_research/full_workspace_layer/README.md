# Full Workspace Layer Research

This directory contains controlled experiments for improving PageIndex
FileSystem behavior on the full EnterpriseRAG workspace without changing
benchmark questions, gold answers, or core PIFS APIs.

Current hypothesis:

1. Full-corpus source grep is the failure mode, not the solution.
2. The catalog already has enough raw document metadata, but only a tiny schema
   is registered and queryable.
3. A generated workspace layer should add queryable metadata and semantic folder
   memberships above the flat file catalog.

The runner creates a derived workspace by copying only `filesystem.sqlite` from
the full registered workspace, then adds:

- a compact, source-aware metadata schema;
- deterministic metadata extracted from document JSON and text fields;
- semantic folder memberships rooted at `/semantic/source=<source_type>`.

It deliberately avoids benchmark expected document ids during generation.
