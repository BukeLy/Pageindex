# Full Workspace Layer Research v2

## What Changed Since v1

I tested term-based semantic folder projection because v1 canonical folders only hit 5/10 timeout-heavy questions.

Experiments:

| projection | timeout10 folder recall | wrong5 folder recall | folder count | memberships | verdict |
|---|---:|---:|---:|---:|---|
| v1 canonical folders | 5/10 | not run | 91,062 | 3.25M total | too weak recall |
| v2 first-term folders | 8/10 after probe fix | not run | 311,559 | 6.32M total | better recall, too noisy |
| v3 scored terms | 9/10 | not run | 1,352,813 | 8.37M total | recall up, structure failed |
| v4 field-quota terms | 10/10 | 4/5 | 1,309,600 | 8.15M total | strong recall, not acceptable as folder |

## Main Insight

Term recall works, but term folders are the wrong physical abstraction.

The high-recall versions create hundreds of thousands to more than one million folders. That breaks the FileSystem experience:

- `ls/tree` become noisy.
- `grep -R` child-folder ranking becomes expensive because one folder can have enormous child fanout.
- Updating or rebuilding the projection is expensive; deleting the v3 semantic folders alone took more than a minute before projection could start.
- The folder tree stops being a human/agent-browsable hierarchy and becomes an inverted index disguised as directories.

So the architecture should split these responsibilities:

1. Folder: low-cardinality, browseable corpus-level projection.
2. Metadata DSL: exact/canonical filtering only.
3. Semantic recall index: term/summary/constraint recall and ranking.

## Evidence

### v4 Timeout10

v4 reached 10/10 folder-probe recall on timeout-heavy questions, including previously missed cases:

- qst_0028 found by `annotated+signed` / release-tag terms.
- qst_0047 found through `fewshot` alias normalization.
- qst_0056 found once summary/intent terms like `nccl` were no longer displaced by numeric constraint terms.

### v4 Wrong5

v4 reached 4/5 on the wrong-doc sample. The miss was qst_0017:

Question asks about a maritime logistics SaaS customer, dedicated GPU capacity, uptime SLA, and service credit percentages. The expected HubSpot document has those facts in summary/notes, but the folder terms selected for it were mostly account identifiers and infra/security IDs:

- `horizon`
- `infra-9021`
- `sec-1173`
- `p95`
- `p99`
- `rps`

This is exactly the failure mode of hard-selecting a few folder terms: the right semantic words can exist in metadata but not be chosen as folder names.

## Decision

Do not keep v4 as the default folder design despite its 10/10 timeout10 probe recall.

The next implementation direction should be:

- Keep canonical folders only, with strict cardinality gates.
- Add a dedicated semantic recall index over generated metadata text.
- Use folder browsing as a narrowing/browsing UI, not as a token index.
- Teach agent flow: exact metadata filter -> semantic recall index -> folder browse if useful -> cat only for final candidates.

## Concrete Next Step

Implement `semantic_terms` / `semantic_recall` as a rebuildable index table instead of folders:

```text
semantic_terms(
  term,
  file_ref,
  source_type,
  field,
  weight
)
```

Query plan:

1. Parse the question into terms.
2. Require `source_type` when available.
3. Lookup terms by exact normalized token/alias, not `%LIKE%` over EAV.
4. Rank candidate files by summed field weights.
5. Return refs with short snippets/provenance.

Folder generation should then return to v1-style low-cardinality folders, plus clustered canonical topics only if the cluster count is capped.
