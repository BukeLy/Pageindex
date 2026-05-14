# Summary

EnterpriseRAG-Bench is a synthetic enterprise corpus benchmark built around a
fictional company, Redwood Inference. Its scale and shape are directly relevant
to PageIndex FileSystem because it tests whether a retrieval system can find
the right few documents from a 500k-document internal-company corpus.

The released corpus contains slightly over 500,000 documents and 500 main
questions. The README also exposes an extra 100 metadata-dependent questions
that are not part of the main leaderboard but are useful for validating a
metadata-aware design.

## Corpus Shape

The corpus is organized under `generated_data/sources/` by source system:

- Slack: roughly 275k documents
- Gmail: roughly 120k documents
- Linear: roughly 35k documents
- Google Drive: roughly 25k documents
- HubSpot: roughly 15k documents
- Fireflies: roughly 10k documents
- GitHub: roughly 8k documents
- Jira: roughly 6k documents
- Confluence: roughly 5k documents

The source tree is not a decorative detail. It encodes useful retrieval
structure: Slack channels, Gmail mailbox owners, GitHub repositories,
Confluence spaces, Google Drive shared-drive paths, Jira projects, HubSpot
entities, and Linear teams. A PageIndex FileSystem that flattens everything
into one document list would throw away one of the benchmark's strongest
routing signals.

## Question Shape

The benchmark uses question records with:

- `question_id`
- `question_type`
- `source_types`
- `question`
- `expected_doc_ids`
- `gold_answer`
- `answer_facts`

The answer submission format is JSONL with:

```json
{"question_id": "qst_0001", "answer": "...", "document_ids": ["dsid_..."]}
```

For leaderboard-style integrity, the retrieval system should treat
`question_type`, `source_types`, `expected_doc_ids`, `gold_answer`, and
`answer_facts` as evaluation labels, not retrieval inputs. The useful runtime
input is the natural-language question.

## Scoring Implications

The metrics are:

- correctness
- completeness
- document recall
- invalid extra documents

This creates a specific pressure on PageIndex:

- high recall is not enough if the system returns many irrelevant documents
- the output document set must be small and defensible
- multi-document questions need deliberate evidence collection
- "not found" questions need a calibrated abstention path
- metadata and folder structure matter even when the final question is not a
  pure metadata question

## Main Takeaway

EnterpriseRAG-Bench is not primarily a single-document reasoning benchmark. It
is a corpus navigation benchmark. PageIndex already has a strong document-tree
retrieval story, but this benchmark needs a file-level tree and catalog in
front of it so the system can decide which document trees to open.

