# PageIndex PIFS

PageIndex PIFS is a virtual filesystem context for registering documents into a workspace, enriching them with retrieval metadata, and exposing bash-like retrieval tools to agents.

## Language

**PIFS Workspace**:
A registered document corpus that an agent can browse, filter, search, and open through PageIndex filesystem commands.
_Avoid_: dataset, benchmark run, index

**Registration**:
The act of adding documents to a PIFS Workspace so they become addressable filesystem entries with Raw Metadata and Default Retrieval Metadata.
_Avoid_: ingestion, eval setup, metadata-free insert

**Pending Registration**:
A registration that has accepted documents but has not yet completed Default Retrieval Metadata generation, usually because a large corpus is waiting for batch submission or collection.
_Avoid_: registered file, completed registration

**Metadata Generation Policy**:
A product-level policy that states which derived metadata fields PIFS should generate for registered documents, such as summary, entity, relation, doc_type, domain, and topic.
_Avoid_: benchmark script options, experiment knobs

**Default Retrieval Metadata**:
The default Metadata Generation Policy for PIFS: summary, doc_type, domain, and topic are generated; entities, relations, constraints, and retrieval cues are opt-in.
_Avoid_: all metadata, full schema

**Derived Metadata**:
Metadata produced from document understanding, normally by an LLM, and stored with provenance before it is used for filtering, browsing, or semantic projection.
_Avoid_: raw metadata, source metadata, heuristic tags

**Raw Metadata**:
Ordinary source-provided document metadata available at registration time, such as source type, title, content type, timestamps, and storage location.
_Avoid_: derived metadata, generated metadata

**Projection Index**:
A rebuildable retrieval index derived from registered documents and trusted metadata; it helps find candidates but is not the source of truth for the workspace.
_Avoid_: workspace, catalog

**Summary Projection Index**:
The default Projection Index built from generated summaries so `search-summary` is available when Registration is complete.
_Avoid_: optional benchmark index, composite vector

**Retrieval Capability**:
A search or browse ability that is actually available in a PIFS Workspace because the required metadata and indexes exist.
_Avoid_: hardcoded tool list, unavailable command

**Lexical Recursive Search**:
The `grep -R` retrieval capability that searches document text and returns real matching lines.
_Avoid_: vector prefilter, semantic candidate search

**Recursive Search Guard**:
A configurable implementation guard that may limit broad `grep -R` execution for a workspace backend without changing the lexical meaning of the command.
_Avoid_: grep semantics, semantic grep

**Semantic Folder Projection**:
An automatic multi-mount folder layout derived from trusted metadata so documents can be browsed through semantic categories without leaving their existing folders.
_Avoid_: move to folder, relocation, source bucket

**Semantic Folder Field**:
A metadata field allowed to participate in Semantic Folder Projection: doc_type, domain, topic, or an LLM-discovered Extension Field intended for browsing.
_Avoid_: summary, entity, relation, retrieval cue, constraint, provenance field

**Explicit Folder**:
A folder placement supplied during Registration, before any Semantic Folder Projection is applied.
_Avoid_: semantic folder, generated folder

**Multi-mount**:
The ability for one registered document to appear in more than one folder path in the same PIFS Workspace.
_Avoid_: copy, duplicate file

**Extension Schema Discovery**:
A product-level opt-in LLM capability that identifies canonical non-text metadata fields suitable for exact filtering or folder browsing from document understanding. It requires provider participation; offline code may only prepare prompts or mark discovery pending. When enabled, discovered Extension Fields become active Retrieval Capabilities without an interactive approval step.
_Avoid_: field profiler, heuristic discovery, statistical audit

**Extension Field**:
A canonical metadata field outside the base metadata set that can support exact filtering or folder browsing, such as a repository, channel, project, customer, or status when grounded in the corpus.
_Avoid_: source bucket, unique id, benchmark field

## Example Dialogue

Developer: Should this EnterpriseRAG script decide whether summaries are generated?
Domain expert: No. The Metadata Generation Policy is a PIFS product concept. The benchmark may choose a policy, but it should not define the concept.

Developer: Should PIFS generate every possible metadata field by default?
Domain expert: No. Default Retrieval Metadata is summary plus doc_type, domain, and topic. More expensive or specialized fields are opt-in.

Developer: Is Extension Schema Discovery a folder operation?
Domain expert: No. It is an opt-in Metadata Generation Policy capability during Registration. Semantic Folder Projection is a separate workspace layout operation.

Developer: Is metadata generation outside Registration just because a benchmark uses BatchMode?
Domain expert: No. Registration includes Default Retrieval Metadata. BatchMode is an execution mode for large registrations, not a different product concept.

Developer: Can a document be considered registered before Default Retrieval Metadata is ready?
Domain expert: No. It is a Pending Registration until the default metadata is generated and attached.

Developer: Is Registration complete before summary semantic search is ready?
Domain expert: No. The Summary Projection Index is part of completed Registration when summary metadata is enabled.

Developer: Should an agent see entity or relation search commands when the workspace has no entity or relation projection?
Domain expert: No. Agent-visible retrieval tools are loaded from Retrieval Capabilities.

Developer: Does a broad-folder guard change what `grep -R` means?
Domain expert: No. `grep -R` remains Lexical Recursive Search. The guard only controls whether a backend will execute an expensive broad search.

Developer: Can a Projection Index replace the workspace catalog?
Domain expert: No. It only supplies candidates. The PIFS Workspace remains the corpus that commands operate on.

Developer: Does Semantic Folder Projection move a document out of its original folder?
Domain expert: No. It adds browseable folder memberships. The document remains the same registered document.

Developer: Can summaries or entities be used directly as semantic folder names?
Domain expert: No. Semantic Folder Projection uses Semantic Folder Fields, not text-heavy retrieval projections.

Developer: Can extension fields be discovered by counting field coverage and cardinality?
Domain expert: No. Extension Schema Discovery is a document-understanding concept that requires an LLM/provider. Code may audit forbidden fields and JSON shape, but it must not choose fields with coverage, cardinality, field-name regex, or source-type heuristics.

Developer: Does an SDK user need to approve discovered extension fields interactively?
Domain expert: No. Extension Schema Discovery is opt-in; when enabled, its fields become active capabilities for the workspace.
