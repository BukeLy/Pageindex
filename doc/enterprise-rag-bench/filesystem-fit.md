# PageIndex FileSystem Fit

## What The Benchmark Requires

EnterpriseRAG-Bench requires PageIndex to solve a corpus-level routing problem
before using document-level PageIndex trees.

The retrieval system must:

- ingest about 500k documents without LLM-generating a rich tree for every
  document up front
- preserve source hierarchy as folders
- preserve source JSON fields as metadata
- search title/content cheaply
- route by source, folder, project, person, repo, customer, ticket, date, and
  status
- load or build document trees only for a small candidate set
- output a small document ID set to avoid invalid-extra penalties

## Why A Flat Document List Fails

A flat list cannot represent the high-value routing cues in the dataset:

- `slack/eng/...` versus `slack/postmortems/...`
- `github/redwood/...` versus `github/eval-harness/...`
- Gmail mailbox owner and thread participants
- Jira project/customer/severity/status
- HubSpot company/account fields
- Confluence space and labels
- Google Drive team/path/status

The FileSystem layer should expose those cues as browsable folders and typed
metadata filters so the retrieval planner can narrow from 500k documents to a
few hundred or fewer before PageIndex document-tree reasoning begins.

## Recommended Benchmark Retrieval Flow

1. Parse the question text only.
2. Infer a lightweight retrieval plan:
   - source/folder hints
   - metadata filters
   - lexical terms
   - expected answer shape
3. Search the local catalog:
   - FTS over title/content/selected metadata text
   - metadata filter pushdown
   - folder/path prefix pushdown
4. Rerank and diversify candidates:
   - keep enough candidates for multi-hop/completeness questions
   - penalize likely distractors before final document selection
5. Open PageIndex document trees:
   - use existing `get_document`, `get_document_structure`, and
     `get_page_content` style retrieval over the selected documents
   - build missing document trees lazily when needed
6. Produce:
   - final answer
   - minimal `document_ids`

## Document Trees

The benchmark's source documents are often field-structured JSON converted to
plain text. Full LLM tree construction for all 500k documents is not the right
first move.

Use three tiers instead:

- Short documents:
  - single-node or shallow deterministic tree
  - title, source-specific fields, and body
- Structured medium documents:
  - deterministic nodes based on JSON fields, headings, email messages, PR
    sections, ticket comments, or bullet sections
- Long documents / final candidates:
  - PageIndex's richer document-tree generation, cached after first use

This keeps the first benchmark implementation light while still preserving the
PageIndex interface for final evidence extraction.

## Folder Model

The physical folder tree should mirror `generated_data/sources/`:

```text
root
  confluence/<space>/<optional topic>
  slack/<channel>
  gmail/<mailbox_owner>
  github/<repo>
  google_drive/<drive_area>/<path...>
  hubspot/<entity type>
  jira/<project>
  linear/<team>
  fireflies/<meeting category>
```

Virtual folders should be query-time or materialized views:

- by project/initiative
- by customer/account
- by person
- by repo/channel/team
- by time window
- by document kind
- by conflict/version lineage

Physical folders should answer "where did this come from?" Virtual folders
should answer "which conceptual set might answer this question?"

## Metadata Model

Use a common core plus source-specific fields.

Common fields:

- `doc_id`
- `source_type`
- `title`
- `physical_path`
- `created_at`
- `updated_at`
- `owner_or_author`
- `people`
- `project`
- `customer_or_account`
- `status`
- `labels`

Source-specific fields should remain available when valuable:

- Slack: channel, thread timestamps, participants
- Gmail: mailbox owner, internal/external participants, first/last email time,
  attachments, related account/deal
- GitHub: repo, PR number, branch, reviewers, linked tickets, CI state
- Jira/Linear: key, assignee, due date, priority, severity, components, cycle
- HubSpot: company, owner, stage, product interests, blockers, ARR range
- Google Drive/Confluence: owner, collaborators, path/space, status, tags

For the main leaderboard, metadata improves routing and precision. For
`extra_questions.jsonl`, metadata becomes the primary answer substrate.

## Benchmark-Specific Caution

The question file contains labels such as `source_types` and `question_type`.
Those should not be used by the retriever if the goal is a fair benchmark
system. They are useful for offline error analysis and ablations only.

