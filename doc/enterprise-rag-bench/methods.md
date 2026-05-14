# Methods

## Source Documents

The raw benchmark data is JSON. Each document contains source-specific
metadata, a title field, one or more content fields, and a `dataset_doc_uuid`.
The JSON also declares:

- `title_field_name`
- `content_field_names`

That means a PageIndex importer does not need to guess which fields are body
text. It can read the declared title/content fields and treat all remaining
top-level fields as metadata candidates.

Examples observed:

- Confluence:
  - `title`, `space`, `author`, `owner_team`, `status`, `created_at`,
    `last_updated`, `reviewers`, `labels`, `content`,
    `dataset_doc_uuid`
- Slack:
  - `channel`, `thread_ts`, `participants`, `messages`,
    `dataset_doc_uuid`
- Gmail:
  - `thread_id`, `mailbox_owner`, `subject`, internal/external participants,
    `first_email_at`, `last_email_at`, attachments, links, `messages`,
    `dataset_doc_uuid`
- GitHub:
  - `repo`, `pr_number`, `title`, `author`, dates, state, reviewers,
    labels, linked tickets, description, review conversation, release notes,
    `dataset_doc_uuid`
- Linear:
  - `key`, `team`, `title`, status, priority, assignee, project, cycle,
    due date, labels, tasks, acceptance criteria, dependencies,
    `dataset_doc_uuid`
- Jira:
  - key, project, issue type, status, severity, customer/company fields,
    components, labels, description, investigation, comments, resolution,
    `dataset_doc_uuid`
- HubSpot:
  - company/account fields, owner, stage, requirements, linked artifacts,
    activities, notes, recommendations, `dataset_doc_uuid`
- Google Drive:
  - title, owner, drive area, path, doc type, collaborators, team, status,
    tags, linked artifacts, content, `dataset_doc_uuid`

## Exported Text Format

The repository's export script can convert JSON documents into `.txt` files.
The exported filename has the form:

```text
{dataset_doc_uuid}__{original_json_basename}.txt
```

The default text body is:

```text
title

content field 1
content field 2
...
```

This is useful for compatibility with generic RAG systems, but PageIndex should
not rely only on exported `.txt` because the source JSON carries the metadata
and folder cues needed for high-precision retrieval.

## Evaluation

A submitted answer file is JSONL. Each row maps a question to an answer and a
document ID list:

```json
{"question_id": "qst_0001", "answer": "...", "document_ids": ["dsid_abc"]}
```

The evaluator compares the answer against gold answers and answer facts, and it
compares submitted `document_ids` against expected documents. It also judges
candidate documents as required, valid, or invalid. Extra documents are not
free; irrelevant extras count against the system.

## Baseline Retrieval Approaches

The repository documents three answer-generation approaches:

- Vector search:
  - embed each document as title + content
  - minimal chunking
  - retrieve top 10 by cosine similarity
- BM25 keyword search:
  - index title + content as one text field
  - retrieve top 10 by lexical match
- Agent-based retrieval:
  - agent navigates the source directory with shell tools
  - tools include command execution, document reading, and document selection
  - the source tree itself is part of the retrieval substrate

The agent baseline is especially relevant to PageIndex FileSystem. It proves
that directory structure and source-specific fields help the model navigate a
large corpus, but it is expensive and slow. PageIndex should turn the same
navigation advantages into a deterministic local catalog and bounded
query-planning flow.

