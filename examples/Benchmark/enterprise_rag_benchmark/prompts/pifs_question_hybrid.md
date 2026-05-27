Question ID: {question_id}
Question type: {question_type}
Source types: {source_types}
Question: {question}

Retrieval mode: bounded semantic-folder hybrid.

Use this sequence:
1. Choose the source root(s) from Source types. Start there, not at /, unless no
   source type is available.
2. Inspect ordinary folders briefly only when folder names are likely to be
   useful. If the next folder is not clearly relevant, stop browsing and run
   semantic candidate discovery in the current root.
3. Run search-summary with a compact expanded query on the normal source root.
   Use --limit 20 for broad
   questions or when multiple documents may be needed.
4. Verify the best candidates with cat. For long documents, first use
   cat <target> --structure, then read relevant nodes/pages. For text artifacts,
   use cat <target> --all or focused grep on that target.
5. If evidence is partial, continue with another candidate or another source
   type before answering.

Do not combine a large tree/ls command with search-summary or cat in the same
tool call. Keep folder browse, candidate search, and evidence reads separate.

Benchmark-specific hints:
- Use exact terms from the question plus likely aliases. Do not grep the full
  question.
- For GPU booking questions, try H200 80GB, h200-80, dedicated clusters,
  eu-central-1, ap-south-1, booking, reservation.
- For Proxima 429 / priority routing questions, try Proxima Bank,
  PROXIMA-ENT-014, priority routing, protected route, hot-route, admission
  control, over_budget, route SLO, chat+embeddings, us-east.
- For multipart upload limit questions, try multipart upload, max_file_size,
  max_total_request_size, 10 MiB, 50 MiB, OpenAI-compatible. If semantic
  folders are available, the topic facet
  /semantic/source_type=github/facets/topic=multipart-form-data-handling-and-validation
  is a precise partition for this family of documents.
- For Streamly dedicated-pool questions, try Streamly, dp-132-usw,
  priority=high, burst credits, reserved, 30%, 20%.
- For Serving Runtime rollback questions, try Serving Runtime, rollback,
  emergency, Hosted, Dedicated, runtime release, known-good, re-pin.

For conflict/completeness/multi-source questions:
- Inspect at least two plausible candidates when the question implies a conflict
  or asks for combined evidence.
- For procedure/completeness questions, inspect the top two clearly relevant
  candidates if search-summary returns both a runbook and a pipeline/procedure
  page.
- For incident plus verification questions, search separately for the incident
  document and the SLO/policy document used to verify the mitigation. For
  Proxima-style 429 questions, after locating the incident doc, also search
  /confluence for "Hosted API SLOs Enterprise Route Tiers hot-route capacity
  protection" or "enterprise route SLO protected route".
- For Serving Runtime rollback questions, search /confluence for both
  "Serving Runtime emergency rollback Hosted Dedicated" and "Runtime release
  pipeline rollback procedure"; inspect both if they appear.
- Prefer the latest/current policy when documents conflict, but cite the
  document that establishes the conflict if it is needed for the answer.

For not-found questions:
- If the exact requested account list, allowlist, or budget values are absent,
  say the evidence is incomplete.
- Return document_ids: [] for not-found answers even if partial context
  documents were inspected. Do not cite partial documents that only prove the
  values live elsewhere or are missing.

Final answer requirements:
- Answer only from verified PIFS evidence.
- Fill document_ids with exact dsid_* IDs for verified supporting documents.
- Do not include candidate-only document_ids.
