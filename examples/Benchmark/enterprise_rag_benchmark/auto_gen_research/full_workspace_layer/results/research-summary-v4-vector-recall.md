# Full Workspace Vector Recall Research v4

## Goal

Find a local vector-database retrieval shape that can hit at least 90% file
recall on the current EnterpriseRAG full workspace without timing out. This is
research only; no LLM reranker or vector command is added to PIFS core yet.

## Workspace

- Workspace: `auto_gen_research/full_workspace_layer/workspaces/structured-metadata-v1/workspace`
- Files: 511,958
- Vector backend: local `sqlite-vec`
- Embedding model: `text-embedding-3-small`
- Dimensions: 256
- Built indexes:
  - `metadata-256-full`
  - `fulltext-256-2k`

## Main Results

| experiment | answerable recall | hit@50 | avg seconds/question | p95 seconds/question | notes |
|---|---:|---:|---:|---:|---|
| metadata vector top200 | ~0.777 | 0.672 | 0.101 | 0.432 | Fast but not enough recall. |
| fulltext 2k vector top200 | 0.796 | 0.709 | 0.098 | 0.421 | Slightly better than metadata; head-only text misses later evidence. |
| metadata+fulltext union top200 | 0.811 | 0.704 | 0.207 | 0.855 | Better than either single index but still below target. |
| metadata+fulltext union top500 | 0.872 | 0.706 | 0.211 | 0.876 | Still below target. |
| metadata+fulltext union top750 | 0.891 | 0.704 | 0.208 | 0.874 | Near target. |
| metadata+fulltext union top1000 | 0.906 | 0.706 | 0.217 | 0.863 | First configuration above 90% recall without timeout. |

## Group Compression Probe

Deep vector recall should not dump 1000 file rows into the agent context. The
better shape is:

1. query embedding
2. search multiple rebuildable vector indexes with source filters
3. union top1000 candidates
4. aggregate candidates into metadata/folder-like groups
5. show compact group rows, then let the agent narrow and open evidence

Results:

| group fields | answerable group hit@5 | answerable group hit@10 | answerable group hit@20 | note |
|---|---:|---:|---:|---|
| `source_bucket,topic,customer,repo,project,channel,space,doc_type` | 0.966 | 0.985 | 0.996 | Inflated by broad `doc_type` groups. Useful as fallback but too coarse alone. |
| `source_bucket,topic,customer,repo,project,channel,space` | 0.811 | 0.868 | 0.904 | More realistic folder projection; top20 crosses 90%. |

## Interpretation

- The current local vector DB is fast enough at this corpus size.
- The blocker is not vector latency; it is returning a usable, small tool output
  from a deep recall pool.
- Top1000 union is the first measured setting that reaches the 90% file-recall
  threshold on answerable questions.
- A shell-like PIFS command should not expose top1000 directly. It should expose
  compact group rows plus a small number of top refs per group.
- `doc_type` is too broad to be the primary folder projection signal. It can be
  a fallback or secondary label, but `source_bucket/topic/customer/repo/project/
  channel/space` are closer to useful navigable folders.
- The cheap LLM reranker experiment on 100 docs did not improve accuracy and was
  slow. It remains a research-only optional stage, not a default core component.

## Stopped Experiment

Started a full `sampled_fulltext` index using salient lines plus text windows.
It was stopped at about 58k/512k files because:

- metadata+fulltext union top1000 already crossed the 90% recall threshold;
- sampled indexing was much slower and would take several more hours;
- the next higher-leverage problem is candidate compression, not more
  document-level embedding variants.

The useful code change from that experiment remains: sampled text extraction now
preserves paragraph boundaries instead of collapsing source text before salience
selection.

## Recommended Next PIFS Research Shape

Do not add LLM reranker as default core. The next experimental command shape
should be:

```text
search --semantic "<query>" <scope>
```

Internally:

1. build/query rebuildable sqlite-vec indexes for metadata recall text and
   source-text preview;
2. apply source/folder/metadata filters before or during vector search;
3. retrieve a deep pool, default around 1000 at current corpus scale;
4. aggregate by folder-like metadata groups;
5. return shell-like rows:
   - group path or group key;
   - candidate count;
   - best score;
   - top refs/document_ids;
   - short snippets only, never full text.

Agent flow:

```text
search --semantic "rollback time to recover staging" /google_drive
ls /generated/source_bucket=users/project=release-engineering
grep -R "time-to-recover" /generated/source_bucket=users/project=release-engineering
cat ref_... --all
```

This keeps SQLite as catalog source of truth and treats vector indexes as
rebuildable semantic indexes, consistent with the current PIFS direction.
