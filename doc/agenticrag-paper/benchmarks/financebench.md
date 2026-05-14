# FinanceBench Benchmark

## What It Tests

FinanceBench tests open-book financial question answering over public company
filings. It is a long-document evidence-extraction benchmark: each question is
associated with a specific financial document, and the answer often requires
locating figures or statements in long PDFs and sometimes performing financial
reasoning.

The AgenticRAG paper uses 150 open-source questions and reports answer
correctness.

## Data Shape

The public FinanceBench repository contains:

- `/data/financebench_open_source.jsonl`
- `/data/financebench_document_information.jsonl`
- `/pdfs/`
- `/results/`

Question fields include:

- `financebench_id`
- `question`
- `answer`
- `evidence`
- `justification`
- `question_type`
- `question_reasoning`
- `company`
- `doc_name`

Each evidence item includes:

- `evidence_text`
- `evidence_doc_name`
- `evidence_page_num`
- `evidence_text_full_page`

Document metadata fields include:

- `doc_name`
- `doc_type`
- `doc_period`
- `doc_link`
- `company`
- `company_sector_gics`

The AgenticRAG paper describes the benchmark as 150 queries over 84 ground
documents, with average document length around 143 pages and 117K tokens.

## Metric

The AgenticRAG paper uses answer correctness judged by LLM and manually
reviewed. FinanceBench's public data also provides gold answers and evidence
strings/pages, which are useful for retrieval diagnostics.

## PageIndex Fit

FinanceBench is the closest benchmark to PageIndex's original strength:
reasoning over long PDFs. The gap is not corpus size but reliable
in-document navigation and evidence extraction.

Required FileSystem behavior:

- import PDFs as documents keyed by `doc_name`
- preserve company, period, doc type, and sector metadata
- preserve page numbers and page text
- support precise page/window retrieval
- support evidence snippets tied to page numbers
- optionally support table-aware extraction where PDF text quality is weak

Recommended PageIndex approach:

1. Use metadata to route the question to the known company/document family.
2. Use PageIndex document tree for the target PDF.
3. Use `find()` over OCR/page text for financial terms, metrics, and dates.
4. Use `open()` / `get_page_content()` over nearby pages.
5. Generate answer with cited document/page evidence.

Unlike EnterpriseRAG-Bench and BRIGHT, FinanceBench does not primarily require
discovering one document from hundreds of thousands. It requires that the
document-tree layer be accurate, cheap enough to run over long PDFs, and able
to expose evidence at page granularity.

