# EnterpriseRAG Benchmark Adapter

This folder keeps EnterpriseRAG data registration and retrieval evaluation as
separate steps.

1. Register the dataset into a PageIndex FileSystem catalog:

```bash
python register_enterprise_rag_pifs_dataset.py --reset --question-ids qst_0001,qst_0002
```

2. Run retrieval against the existing catalog:

```bash
python run_enterprise_rag_pifs_agent.py --question-ids qst_0001,qst_0002
```

The runner only checks that `runs/pifs-workspace/workspace/filesystem.sqlite`
already exists, loads questions, and calls the PIFS agent. It must not register
files, infer metadata, reset the catalog, or modify benchmark data/gold answers.

Generated benchmark outputs, SQLite databases, and local artifacts should stay
under `runs/`, which is ignored by git.
