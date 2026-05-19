# Full Workspace Layer Research v3: Term Index Check

## Added Experiment

Built `semantic_terms` as a separate recall index instead of representing every term as a folder.

Index size:

- Files indexed: 511,958
- Term rows: 20,477,661
- Unique terms: 1,287,095
- Source-specific term stats rows: 1,452,075
- Build time: ~8.5 minutes for terms, ~32 seconds for source-specific df stats

This avoids polluting the folder tree, unlike v4 term folders with 1.3M folders.

## Probe Results

| method | timeout10 recall | wrong5 recall | latency |
|---|---:|---:|---:|
| term index v1, no df | 5/10 | 3/5 | fast |
| term index v2, source-specific df rank | 7/10 | 3/5 | fast |
| v4 term folders | 10/10 | 4/5 | folder tree unacceptable |

## Diagnosis

The term index is the right storage shape, but the current ranking is too lexical.

Miss examples:

- qst_0007 expected doc ranked 315 even though it matched `regression` and `rolling`.
- qst_0030 expected doc ranked 232 despite matching `console`, `a-b`, `cohort`, and `trace`.
- qst_0056 expected doc did not reach top1000 because it only matched broad terms like `gpu` and `nccl`; many Slack threads share those terms.
- qst_0017 expected HubSpot doc did not reach top1000 because the question asks via descriptive concepts (`maritime logistics SaaS`, `uptime SLA`, `service credit`) and the document's selected terms emphasize account/infra identifiers.

## Conclusion

The winning architecture is not `term folders` and not raw lexical term-index ranking.

The next best candidate is:

1. Keep low-cardinality folders only.
2. Keep exact metadata DSL for canonical fields.
3. Use a semantic recall index backed by generated metadata text, but rank with embedding/reranker or phrase-aware scoring.
4. Use term index as a fast candidate generator, not final ranking.

Recommended next experiment:

- Store one embedding per file over generated `summary + intent + entities + constraints + title`.
- For each question, source-filter first, vector-recall top 100-300, then optionally rerank with term/metadata boosts.
- Compare against current folder probe and source-grep baseline on timeout10, wrong5, then 30 mixed questions.

This is now the strongest evidence-backed path: embeddings are not a replacement for FileSystem; they are the semantic candidate generator underneath the agent-facing FS tools.
