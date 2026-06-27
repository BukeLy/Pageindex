# PageIndex FileSystem

This context defines the product language for PageIndex FileSystem (PIFS), an agent-facing virtual filesystem for navigating document workspaces.

## Language

**PIFS Browse**:
A relevance-ranked view of files inside one PIFS folder scope for a required query. It returns files only, not folders. Recursive browse expands the file scope and returns a single globally ranked file page, not directory groups. It is not plain directory listing; without a query there is no relevance ordering and the request is invalid. Browse results are paginated rather than caller-sized.
_Avoid_: search-summary, search-entity, search-relation, semantic-grep, unqualified browse

**PIFS File Locator**:
An idempotent path-like resource locator returned by PIFS commands that can be used directly by file inspection commands. It must identify one file without relying on title-derived paths that may collide.
_Avoid_: display path, title path, source_path, storage_uri

**Navigation-Local File Locator**:
A PIFS File Locator returned in the folder context where the user or agent is currently navigating. Returned file locators should remain inside that navigation context so the next `cat`, `grep`, or `stat` follows the same filesystem mental model.
_Avoid_: jumping back to original folder locator, physical source path

**PIFS Add Target**:
The virtual destination supplied when adding a physical file to PIFS. It may be a full virtual file path or an existing/new virtual folder path; PIFS creates missing target folders. When the target is a folder path, PIFS uses the physical file name and rejects the add if that file name already exists in the target folder.
_Avoid_: implicit overwrite

**PIFS Added File**:
A physical file copied into the PIFS workspace artifacts and registered at a virtual destination. The workspace owns the copied artifact rather than depending on the original physical path after add completes. Add is synchronous and atomic: success means the file is ready for its supported PIFS reads and default summary browse, and failure leaves no registered partial file.
_Avoid_: external-only reference

**Supported Added File Type**:
A file type that PIFS can synchronously make ready during add. The supported set is PDF, Markdown, and plain text; other file types are rejected rather than registered partially.
_Avoid_: best-effort add

**PIFS Listing**:
A structural view of folders and files without relevance ranking. Listing is used to understand folder shape before choosing where to browse for files.
_Avoid_: browse

**Query-Dependent Tree Construction**:
The PIFS behavior where the navigable tree can be shaped by the current question, so the agent sees the folder and virtual-node structure that is useful for that query rather than one permanent corpus taxonomy.
_Avoid_: one fixed tree for every question, flat corpus search

**Dynamic Flattening**:
The PIFS behavior where low-value intermediate navigation levels are collapsed during query-time navigation so the agent can move directly to useful file groups or evidence candidates.
_Avoid_: forcing every physical or generated level into the agent's traversal path

**PIFS Virtual Node**:
A query-time navigable node in the PageIndex File System tree, derived from a metadata axis value or another non-persisted corpus view rather than from stored file-folder membership. A PIFS Virtual Node is path-addressable by PIFS commands, so agents can navigate it like a folder while the underlying file memberships remain virtual.
_Avoid_: Semantic Folder, metadata folder, facet folder, Metadata Virtual Node, Topic Virtual Node

**PIFS Virtual Segment**:
The path segment form that addresses one PIFS Virtual Node. It uses `@axis=value`, where one segment names exactly one metadata axis predicate applied to the parent navigation scope.
_Avoid_: unmarked metadata path, mixed-condition segment

**PIFS Browse Page**:
A stable window of 10 results inside a relevance-ranked PIFS Browse result set. Page numbers are user-facing and start at 1; the caller does not choose an arbitrary result limit or offset.
_Avoid_: limit-sized browse, offset-sized browse

**PIFS Browse Record**:
The JSON representation of one browsed file inside the PIFS command envelope. It appears under `data.documents[]` and carries document identity, locator, rank, similarity, summary, metadata, and folder context.
_Avoid_: shell key-value block, browse table

**Browse Similarity**:
A score shown with each PIFS Browse Record so the agent can judge whether the query is well aligned with the selected space. Low similarity means the result is weakly related, not that browsing failed.
_Avoid_: treating low-similarity results as no results

**PIFS Browse Space**:
The relevance lens used by PIFS Browse to rank files for the query. In the current core alignment pass, PIFS Browse exposes only the summary projection; entity and relation projection retrieval are intentionally absent from the agent-facing surface.
_Avoid_: hidden vector tool, separate search command, entity/relation browse space

**Unavailable Browse Space**:
A requested non-summary browse projection. It is an invalid command in the current core alignment pass; PIFS does not silently fall back to another space.
_Avoid_: silent fallback, browse --space

**PIFS Benchmark Success**:
The product success condition for PIFS benchmarks has two layers: document discovery and evidence localization. Document discovery measures whether PIFS finds the right file with fewer candidate files and tokens. Evidence localization measures whether PIFS reaches the right page or span with fewer page reads and tokens. Final answer correctness is a guardrail for both layers.
_Avoid_: measuring only final answer accuracy, measuring only vector top-k recall, treating document discovery and evidence localization as competing goals

**PIFS Benchmark Corpus**:
The first PIFS benchmark corpus is a finance, PDF-first document workspace at roughly S&P 500 scale across 10-20 years, with multi-axis metadata such as company, ticker, sector, industry, form type, and report year. Its purpose is to create many plausible wrong documents so query-dependent navigation can be measured.
_Avoid_: switching to another domain before the finance PDF benchmark is exhausted, treating a tiny corpus as proof

**PIFS-Benchmark Boundary**:
PIFS is the agent-facing filesystem under test. The benchmark is an external evaluation harness that builds datasets, queries, labels, prompts, and result rows around PIFS. Benchmark choices must not be treated as PIFS product concepts unless they already exist in PIFS.
_Avoid_: designing PIFS APIs around benchmark-only fields

**PIFS Benchmark Harness**:
The PIFS benchmark harness evaluates PIFS from the outside. It may prepare datasets, generate queries, run prompts, and score results, but it must not smuggle benchmark-only behavior into PIFS core.
_Avoid_: changing filesystem behavior just to make a benchmark pass

**PIFS Financial Benchmark**:
The first PIFS benchmark harness lives under `examples/Benchmark/pifs_financial_benchmark/` and targets the finance PDF corpus. It is separate from PIFS core and from the existing EnterpriseRAG benchmark work.
_Avoid_: placing benchmark runner code in `pageindex/filesystem`, mixing the financial benchmark into EnterpriseRAG benchmark code

**PIFS Financial Benchmark File Set**:
The first financial benchmark implementation starts with `SPEC.md`, `generate_queries.py`, and `run_benchmark.py`. Dataset artifacts, generated queries, workspaces, and results live under `pifs-data` rather than in the repo.
_Avoid_: adding scaffolding files before the first benchmark run needs them

**PIFS Financial Benchmark Data Root**:
The financial benchmark data root is `/Users/chengjie/Projects/pifs-data/pifs-financial-benchmark/`, with corpus, queries, workspaces, and results stored under that root.
_Avoid_: committing generated benchmark artifacts into the PageIndex repo

**PIFS Financial Benchmark Corpus Scope**:
The first financial benchmark corpus uses an S&P 500 constituent snapshot, AnnualReports.com annual report PDFs, and report years 2015-2024. The target size is roughly 5,000 PDFs.
_Avoid_: expanding to 20 years before the first 10-year benchmark run works

**PIFS Financial Benchmark Company Snapshot**:
The financial benchmark may use Wikipedia to collect the current S&P 500 constituent table once, but the benchmark must save the crawl date and complete company/ticker snapshot under `pifs-data`. Future runs use the frozen snapshot rather than live index membership.
_Avoid_: benchmark results changing because the S&P 500 membership changed online

**PIFS Financial Benchmark Missing Reports**:
If AnnualReports.com does not provide a company or year PDF, the benchmark records the missing report and reason instead of filling the gap from another source. The first corpus uses successfully downloaded AnnualReports PDFs only.
_Avoid_: mixing SEC, investor-relations sites, or other PDF sources just to reach the target count

**PIFS Core Retrieval Agent**:
The agent inside PIFS core is an experience-oriented retrieval agent for users to inspect and answer questions over a workspace. It is not the benchmark runner.
_Avoid_: benchmark-specific prompts, scoring, or output contracts in the core retrieval agent

**PIFS Benchmark Agent**:
A benchmark-owned agent that drives PIFS only for evaluation. It may have benchmark-specific prompts and output contracts, but those belong to the benchmark harness rather than PIFS core.
_Avoid_: mixing benchmark behavior into the core retrieval agent

**PIFS Benchmark Label Set**:
A benchmark query may carry three labels: expected document id, evidence line or page, and golden answer. Expected document id is the first required label because the first PIFS benchmark must prove corpus-level document discovery. Evidence location and golden answer may be left empty until the dataset can support reliable annotation.
_Avoid_: blocking document-discovery benchmarking on full answer/span annotation

**PIFS Benchmark Query Generation**:
The first benchmark query set uses rules to generate canonical queries and expected document ids, then uses an LLM by default to rewrite the query into more human-like language. The LLM may change wording only; deterministic validation must still map the rewritten query back to the same expected document id.
_Avoid_: using LLM-generated ground truth, accepting rewritten queries that no longer have a unique expected document

**PIFS Benchmark Query Rewrite Check**:
LLM query rewriting has only a minimal acceptance check: the rewritten query must be non-empty and must not change the expected document id.
_Avoid_: complex retry or fallback machinery for simple query rewriting

**PIFS Benchmark Query Count**:
The first document-discovery benchmark run uses 1,000 generated queries sampled from the financial corpus.
_Avoid_: running the whole corpus before the first benchmark loop is stable

**PIFS Benchmark Query Mix**:
The first 1,000 document-discovery queries are sampled across three query families: company-year, industry-year, and topic-company-year. The initial mix is 40% company-year, 30% industry-year, and 30% topic-company-year.
_Avoid_: a benchmark made only of ticker/year template queries

**PIFS Benchmark First Run**:
The first benchmark run measures PIFS only. Baselines are deferred until PIFS can produce stable document-discovery results on the finance PDF corpus.
_Avoid_: blocking the first run on baseline implementation

**PIFS Financial Benchmark Exclusions**:
The first financial benchmark excludes TopicModeling and ChatIndex-style topic trees.
_Avoid_: treating TopicModeling as a required or planned benchmark component

**PIFS Benchmark Result Row**:
The first benchmark result row records only query id, query, expected document id, found document id, and hit. More detailed trace fields can be derived later from logs if needed.
_Avoid_: adding rank, token, trace, or evidence fields before the first document-discovery run needs them

**PIFS Benchmark Hit**:
A first-run benchmark hit is an exact document-id match: `expected_doc_id == found_doc_id`. The agent must return a PIFS file locator or manifest document id that the evaluator can resolve to one document.
_Avoid_: title fuzzy matching, manual result judgment

**PIFS Benchmark Agent Output**:
The benchmark agent prompt requires the final answer to end with one machine-readable line: `FOUND_DOC_ID=<doc_id>`. The evaluator parses that line as the found document id.
_Avoid_: parsing free-form explanations as benchmark output

**PIFS Benchmark Document Id**:
The benchmark's expected and found document ids are evaluation-side values resolved against the PIFS file `external_id`, which PIFS command payloads also expose as `document_id`. PIFS keeps its existing identity model: `file_ref` is the internal stable file locator, and `pageindex_doc_id` is the cached PageIndex document id for structure/page reads.
_Avoid_: changing PIFS identity for benchmark needs, inventing a benchmark-only PIFS id, using title or virtual path as the expected document id

## Flagged Ambiguities

**Withdrawn Semantic Folder terminology**:
Resolved as retired product language. Do not describe current or future PIFS navigation with Semantic Folder terms; use Blog-aligned virtual nodes and query-dependent views instead.
_Avoid_: PIFS Semantic Folder, semantic-folder build, semantic mount, semantic folder membership

**Browse without query**:
Resolved as invalid command usage. If the caller wants unranked folder contents, they mean PIFS Listing, not PIFS Browse.

**Browse command phrase**:
Resolved as a required positional query after the folder path, for example `browse /documents "vector database"`. Multi-word queries must be quoted.
_Avoid_: browse --query

**Browse result sizing**:
Resolved as fixed-size pagination, not caller-specified limits or offsets. PIFS Browse returns 10 results per page and should expose page-number movement plus the next page command when more results exist.

**Folder retrieval**:
Resolved as structural exploration, not semantic browse. PIFS Browse does not retrieve folders; agents use PIFS Listing to inspect folder shape and then browse files inside a chosen folder scope.

**Semantic file retrieval command surface**:
Resolved as PIFS Browse only. Other commands such as find do not expose semantic vector-space aliases.
_Avoid_: find --name as semantic search, find --relation as semantic search

**Browse metadata filter**:
An exact metadata filter applied before or during PIFS Browse ranking. It uses fields from the metadata schema and does not express folder paths.
_Avoid_: path filter in where

## Example Dialogue

Developer: "Should the agent run browse /documents first?"

Domain expert: "Only if it has a query. browse /documents --query 'vector database' means rank files in /documents by relevance to that query. For plain folder contents, use listing."

Developer: "Can the agent ask for browse --limit 100?"

Domain expert: "No. Browse returns 10 ranked results per page and tells the agent how to request the next page with --page 2."
