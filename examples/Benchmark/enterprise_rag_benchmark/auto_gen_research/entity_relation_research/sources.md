# Source Notes For Entity / Relation Projection Research

Checked on 2026-05-20.

This document records the idea sources behind the entity/relation projection
experiment so later writeups can cite the right papers or system references.

## Primary PIFS Sources

### PageIndex File System blog

- Source: PageIndex Team. "PageIndex File System: Massive-Scale Document
  Search." 2026-05-03.
- URL: https://pageindex.ai/blog/pageindex-filesystem
- Relevant ideas:
  - Classic vector RAG can miss relevant evidence when the right text is not
    semantically close to the user query.
  - The filesystem layer should scale single-document tree navigation to many
    documents.
  - A plain folder tree is insufficient because enterprise corpora are often
    flat, one-dimensional, or badly labeled.
  - Virtual nodes can be synthesized from metadata, topics, entities, and other
    semantic signals.
  - One document may appear under multiple virtual ancestors.
- How this maps to the experiment:
  - Entity/relation/constraint projections are a candidate implementation of
    "virtual node" signals.
  - The experiment keeps the document as the leaf unit and avoids chunk-level
    context injection.
- What not to copy directly:
  - Do not assume query-dependent virtual-tree construction is solved in core.
    First validate projection recall offline.

### Mirage virtual filesystem

- Source: Strukto. "mirage: A Unified Virtual Filesystem Workspace."
- URL: https://www.strukto.ai/mirage
- Relevant ideas:
  - Agents work well when heterogeneous data is exposed through a single
    filesystem and shell-like interface.
  - Resource operations, mounted paths, file caches, and index caches should be
    separated.
  - A bash-like surface can hide backend complexity.
- How this maps to the experiment:
  - PIFS should continue exposing shell-like `ls`, `find`, `grep`, and `cat`,
    while projection indexes remain internal recall infrastructure.
- What not to copy directly:
  - Mirage is about mounted multi-backend execution. PIFS is about corpus
    retrieval, metadata filtering, document provenance, and PageIndex tree
    navigation.

### AgenticRAG

- Source: Suresh, Susheel, Hazel Mak, Shangpo Chou, Fred Kroon, and Sahil
  Bhatnagar. "AgenticRAG: Agentic Retrieval for Enterprise Knowledge Bases."
  arXiv:2605.05538, 2026.
- URL: https://arxiv.org/abs/2605.05538
- Relevant ideas:
  - A reasoning model should not be locked into one fixed top-k candidate set.
  - Search, find, open, and summarize tools let the model iterate over evidence.
  - The paper reports that switching from single-shot retrieval to agentic tool
    use is the largest improvement source in its ablation.
- How this maps to the experiment:
  - Projection indexes should improve the `search/find` layer, not replace the
    agent loop.
  - Final evidence must still come from opening files, not from embedding text.
- What not to copy directly:
  - Do not expose generic enterprise search JSON blobs to the model. Preserve
    PIFS shell-like commands and provenance.

## Retrieval Papers

### ColBERT

- Source: Khattab, Omar, and Matei Zaharia. "ColBERT: Efficient and Effective
  Passage Search via Contextualized Late Interaction over BERT." arXiv:2004.12832,
  2020.
- URL: https://arxiv.org/abs/2004.12832
- Relevant ideas:
  - Late interaction keeps finer-grained similarity signals than a single pooled
    document embedding while still allowing offline document representation.
  - This supports the concern that one pooled vector can lose entity-level and
    relation-level evidence.
- How this maps to the experiment:
  - PIFS will not implement ColBERT directly in this experiment.
  - Entity/relation/constraint projections are a lightweight approximation of
    multi-vector evidence without token-level indexing.

### SPLADE v2

- Source: Formal, Thibault, Carlos Lassance, Benjamin Piwowarski, and Stephane
  Clinchant. "SPLADE v2: Sparse Lexical and Expansion Model for Information
  Retrieval." arXiv:2109.10086, 2021.
- URL: https://arxiv.org/abs/2109.10086
- Relevant ideas:
  - Learned sparse retrieval preserves exact-term behavior from inverted indexes
    while adding semantic expansion.
  - Exact lexical evidence remains important for identifiers, numbers, field
    names, and enterprise-specific terms.
- How this maps to the experiment:
  - Constraint and entity projections should keep exact strings and aliases.
  - Vector recall should be combined with exact-match bonuses, not used alone.

### Multi-hop Dense Retrieval

- Source: Xiong, Wenhan, Xiang Lorraine Li, Srini Iyer, Jingfei Du, Patrick
  Lewis, William Yang Wang, Yashar Mehdad, Wen-tau Yih, Sebastian Riedel, Douwe
  Kiela, and Barlas Oguz. "Answering Complex Open-Domain Questions with
  Multi-Hop Dense Retrieval." arXiv:2009.12756, 2020.
- URL: https://arxiv.org/abs/2009.12756
- Relevant ideas:
  - Retrieval can be iterative and multi-hop without relying on corpus-specific
    hyperlinks or manually provided entity markers.
- How this maps to the experiment:
  - Query-side entity/relation extraction can drive multiple focused retrieval
    probes before aggregating back to files.
- What not to copy directly:
  - PIFS should not become a QA-specific multi-hop retriever. The projection
    index is only a filesystem recall backend.

## Graph / Relation RAG Sources

### GraphRAG

- Source: Edge, Darren, Ha Trinh, Newman Cheng, Joshua Bradley, Alex Chao,
  Apurva Mody, Steven Truitt, Dasha Metropolitansky, Robert Osazuwa Ness, and
  Jonathan Larson. "From Local to Global: A Graph RAG Approach to Query-Focused
  Summarization." arXiv:2404.16130, 2024.
- URL: https://arxiv.org/abs/2404.16130
- Relevant ideas:
  - LLM-derived entity graphs and community summaries can represent corpus-level
    structure that ordinary RAG retrieval misses.
- How this maps to the experiment:
  - Entity/relation projection rows borrow the entity-graph idea but keep the
    storage model simpler than a full graph database.
  - We aggregate projections back to files/folders instead of generating global
    corpus summaries.
- What not to copy directly:
  - Do not build community summaries or a full GraphRAG pipeline before proving
    projection recall on EnterpriseRAG.

### HybridRAG

- Source: Sarmah, Bhaskarjit, Benika Hall, Rohan Rao, Sunil Patel, Stefano
  Pasquali, and Dhagash Mehta. "HybridRAG: Integrating Knowledge Graphs and
  Vector Retrieval Augmented Generation for Efficient Information Extraction."
  arXiv:2408.04948, 2024.
- URL: https://arxiv.org/abs/2408.04948
- Relevant ideas:
  - Combining graph-style retrieval with vector retrieval can outperform either
    alone on complex information extraction tasks.
- How this maps to the experiment:
  - The `hybrid_projection_vector` strategy intentionally combines metadata
    vector recall with entity/relation/constraint recall.
- What not to copy directly:
  - Do not make a knowledge graph the PIFS source of truth. PIFS catalog remains
    SQLite; projection indexes are rebuildable.

## Agentic Retrieval Sources

### ReAct

- Source: Yao, Shunyu, Jeffrey Zhao, Dian Yu, Nan Du, Izhak Shafran, Karthik
  Narasimhan, and Yuan Cao. "ReAct: Synergizing Reasoning and Acting in Language
  Models." arXiv:2210.03629, 2022.
- URL: https://arxiv.org/abs/2210.03629
- Relevant ideas:
  - Interleaving reasoning and tool actions lets a model update its plan based
    on external observations.
- How this maps to the experiment:
  - PIFS should keep agent-facing commands small and interpretable so the agent
    can decide the next search/open action.

### IRCoT

- Source: Trivedi, Harsh, Niranjan Balasubramanian, Tushar Khot, and Ashish
  Sabharwal. "Interleaving Retrieval with Chain-of-Thought Reasoning for
  Knowledge-Intensive Multi-Step Questions." arXiv:2212.10509, 2022.
- URL: https://arxiv.org/abs/2212.10509
- Relevant ideas:
  - One-shot retrieve-and-read is insufficient for multi-step questions; what to
    retrieve depends on what has already been found.
- How this maps to the experiment:
  - Query projections should support multiple focused recall attempts rather
    than one root-level broad grep.

## Local Implementation References

These are not public citation sources unless the code is published in the same
state. They are recorded so the implementation lineage is auditable.

### pageindex-compute doc-level semantic search

- Local repo: `/Users/chengjie/Projects/pageindex-compute`
- Snapshot checked: `origin/dev` at
  `bea3fe4ab3e6b55e8839553f36dddb33cc9f1d84`
- Relevant files:
  - `server/api/api.py`: `/docs/search` embeds the query and calls document
    search.
  - `server/lib/db/planet.py`: searches `FilePageIndex.descriptionEmbedding`
    with cosine distance and optional folder/metadata filters.
  - `server/tasks/document.py`: generates and stores document description
    embeddings after document processing.
- Lesson:
  - Exact filters before vector search are useful.
  - A single document-description embedding is useful but too coarse for the
    PIFS full-workspace target.

### pageindex-compute Chroma node search

- Local repo: `/Users/chengjie/Projects/pageindex-compute`
- Snapshot checked: `origin/dev` at
  `bea3fe4ab3e6b55e8839553f36dddb33cc9f1d84`
- Relevant files:
  - `server/lib/vecdb/chroma.py`: Chroma handler.
  - `server/api/retrieval/pfile_agent.py`: `node_search_embedding_plus`
    searches node chunks, aggregates distances to node ids, and combines node
    search results with LLM node search.
  - `server/tasks/retrieval.py` and `server/tasks/document.py`: Chroma write
    paths for node/chunk retrieval.
- Lesson:
  - Chunk/node vector search can help in-document navigation, but copying this
    as PIFS corpus-level retrieval would collapse the design back into
    traditional RAG.

### Current PIFS semantic index adapter

- Local repo: this PageIndex worktree.
- Relevant files:
  - `pageindex/filesystem/semantic_index.py`: rebuildable local `sqlite-vec`
    adapter.
  - `examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/full_workspace_layer/run_full_workspace_layer_research.py`:
    full-workspace vector field ablation and group compression probes.
  - `examples/Benchmark/enterprise_rag_benchmark/auto_gen_research/full_workspace_layer/results/research-summary-v4-vector-recall.md`:
    current full-workspace vector recall summary.
- Lesson:
  - `metadata+fulltext` union can reach high recall only with a deep candidate
    pool, which is not acceptable as direct agent output.
  - Projection rows should improve precision before candidate compression.
