# Methods

## System Shape

AgenticRAG is an inference-time harness. It keeps the existing enterprise
search backend and adds an LLM-driven loop around it.

The loop is bounded by:

- a maximum iteration count
- token-budget monitoring
- forced final answer when the model runs out of iterations
- context summarization when accumulated tool outputs approach the context
  limit

The paper's default maximum iteration count is 15. The context-management
threshold described in the paper is based on a 128K-token context budget, with
warnings near 90% usage and forced summarization at threshold.

## Retrieval Tools

### Search

`search` performs broad corpus discovery. In the default configuration, the
model can issue up to five reformulated queries in one call. Each query returns
up to 10 results. Results are deduplicated and assigned stable reference IDs
that later `find` and `open` calls can use.

Returned information includes:

- title
- snippet
- filename
- file type
- available metadata
- reference ID

PageIndex equivalent:

- FileSystem `search()` over local catalog, FTS, metadata, and folder scopes.
- Return stable references that map to PageIndex document IDs and corpus
  artifact paths.

### Find

`find` searches inside one referenced document. The paper describes
case-insensitive lexical substring matching, with optional semantic find. It
returns up to two matching passages per pattern and is bounded by token limits.

PageIndex equivalent:

- For lightweight benchmark support, provide deterministic in-document lexical
  search over title/content/text pages.
- For PageIndex-native support, map find results to document tree nodes or page
  ranges.

### Open

`open` retrieves a fixed line window from a referenced document. The paper uses
line-numbered content with a default window of 1,800 lines and a header
indicating the current viewing range and total document length.

PageIndex equivalent:

- For text/JSON benchmarks, expose line/window access.
- For PDFs, expose page-window access.
- For PageIndex documents, expose tree-node/page-range access through
  `get_document_structure` and `get_page_content`.

### Summarize

`summarize` is a context-management tool. The model records current findings
and chooses which references to preserve. Tool messages not associated with
preserved references can be removed to free context.

PageIndex equivalent:

- Track selected candidate documents and evidence snippets outside the prompt.
- Summarize evidence state without losing document IDs, node IDs, page ranges,
  or line ranges.

## What To Borrow

Borrow:

- the coarse-to-fine tool contract: search -> find/open -> answer
- multi-query search in one tool call
- stable reference IDs for later navigation
- bounded outputs for every tool
- reference-preserving context summarization
- forced finalization when a query runs too long

Do not copy directly:

- generic line windows as the only in-document navigation model
- dependency on an external enterprise search service
- lack of a folder/metadata schema as a first-class query contract

PageIndex's stronger version should use FileSystem for candidate selection and
PageIndex document trees for evidence extraction.

