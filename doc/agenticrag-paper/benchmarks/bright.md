# BRIGHT Benchmark

## What It Tests

BRIGHT is a reasoning-intensive retrieval benchmark. It is designed for cases
where lexical or simple semantic similarity is not enough to identify relevant
documents. Queries come from real human sources such as StackExchange,
LeetCode, TheoremQA, and math competitions.

The AgenticRAG paper uses BRIGHT's long-context setting, where documents are
entire web pages rather than short passages. The task is to retrieve the full
relevant document or documents for each query.

## Data Shape

The BRIGHT Hugging Face dataset exposes several subsets, including:

- `examples`
- `documents`
- `long_documents`
- model-generated reasoning subsets

The paper's long-context evaluation covers eight splits:

- Biology
- Earth Science
- Economics
- Psychology
- Robotics
- Stack Overflow
- Sustainable Living
- Pony

The paper reports 861 total queries and 5,650 long documents across those eight
splits. Average document length is about 16K tokens, with Stack Overflow
averaging more than 40K tokens.

Important fields:

- `query`
- `reasoning`
- `id`
- `excluded_ids`
- `gold_ids_long`
- `gold_ids`
- `gold_answer`

For the long-context setting, `gold_ids_long` is the important target because
it points to full documents, while `gold_ids` points to shorter passage-style
documents.

## Metric

The AgenticRAG paper reports Recall@1 for the long-context setting.

The public BRIGHT leaderboard normally reports nDCG@10 across 12 datasets, but
the AgenticRAG paper's specific setup is the eight-domain long-context
Recall@1 evaluation.

## PageIndex Fit

BRIGHT is a strong test of PageIndex FileSystem's candidate-selection layer.
It is not mainly a metadata benchmark. It asks whether the system can identify
the right long document from semantically overlapping candidates.

Required FileSystem behavior:

- import BRIGHT splits as folders/domains
- preserve full document IDs from the dataset
- index long documents without chunk-only identity
- expose `search()` over query/title/content
- expose `find()` inside candidate long documents
- expose `open()` by line or PageIndex tree node
- output ranked document IDs for Recall@1 / nDCG@10 evaluation

Recommended PageIndex approach:

1. Use SQLite FTS/BM25 or a pluggable search adapter for first-pass candidates.
2. Use PageIndex tree summaries or deterministic line sections for long docs.
3. Let an agent issue multi-query search, then open/find promising documents.
4. Score cited documents and return a ranked list.

The main change from EnterpriseRAG-Bench is that BRIGHT needs ranked retrieval
evaluation, not final answer JSONL with minimal `document_ids`.

