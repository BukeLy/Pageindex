# WixQA Benchmark

## What It Tests

WixQA is an enterprise RAG benchmark built from Wix support and help-center
data. It is closer to real customer-support RAG than BRIGHT because the corpus
is a knowledge base and the questions are support/troubleshooting scenarios.

The AgenticRAG paper uses the Expert Written and Simulated splits for
multi-document procedural QA.

## Data Shape

The Hugging Face dataset has four relevant configs:

- `wixqa_expertwritten`
  - 200 authentic tickets with expert step-by-step answers.
- `wixqa_simulated`
  - 200 concise answers distilled from user-expert chats.
- `wixqa_synthetic`
  - about 6.2K article-derived QA pairs.
- `wix_kb_corpus`
  - about 6.2K Wix Help Center articles.

Q-A fields:

- `question`
- `answer`
- `article_ids`

KB corpus fields:

- `id`
- `url`
- `contents`
- `article_type`

`article_ids` are ground-truth document IDs and map directly to KB corpus
article IDs.

## Metric

The AgenticRAG paper uses the same LLM-based factuality metric as WixQA. It
reports 0.96 factuality on the Expert Written split with GPT-5-mini.

Because WixQA includes `article_ids`, PageIndex can also measure retrieval
recall against required KB articles, but the paper emphasizes factuality of the
generated answer.

## PageIndex Fit

WixQA stresses multi-document synthesis over a medium-size support corpus.
Unlike FinanceBench, documents are not extremely long. Unlike BRIGHT, the
answer quality matters, not just ranked retrieval.

Required FileSystem behavior:

- import the KB corpus as one collection
- treat URL path, article type, and article ID as metadata
- support article-level folder organization derived from URL/category if
  available
- retrieve multiple articles per question
- preserve article IDs for evaluation
- support answer synthesis grounded in selected articles

Recommended PageIndex approach:

1. Catalog every KB article as a document.
2. Use FTS/BM25 plus metadata/article-type filters for first-pass recall.
3. Use PageIndex shallow trees for article headings and procedural sections.
4. Allow the retrieval agent to open multiple articles before answering.
5. Return both the answer and selected `article_ids` for offline analysis.

WixQA is the best of the three AgenticRAG benchmarks for validating the
FileSystem's ability to support multi-document procedural answers without
needing a massive corpus.

