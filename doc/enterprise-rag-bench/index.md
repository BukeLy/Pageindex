# EnterpriseRAG-Bench Research Notes

## Files

- `summary.md`
  - benchmark purpose
  - corpus scale and source mix
  - question categories
  - scoring implications for PageIndex
- `methods.md`
  - source JSON document shape
  - exported text format
  - answer JSONL format
  - baseline retrievers and agent retriever behavior
- `filesystem-fit.md`
  - concrete FileSystem requirements for this benchmark
  - folder, metadata, document tree, and query planning implications

## Source Snapshot

- Repository: `onyx-dot-app/EnterpriseRAG-Bench`
- Hugging Face leaderboard:
  <https://huggingface.co/spaces/onyx-dot-app/EnterpriseRAG-Bench-Leaderboard>
- Local clone: `/private/tmp/EnterpriseRAG-Bench-20260514`
- Observed commit: `d36685e273713975ee20299bbf1ab64165575b3c`
- Observed date: 2026-05-07

## Main Files Read

- `README.md`
- `methodology.md`
- `questions.jsonl`
- `extra_questions.jsonl`
- `generated_data/source_tree.txt`
- `generated_data/company_overview.md`
- `generated_data/initiatives.md`
- representative JSON files under `generated_data/sources/`
- `src/scripts/data_gen_stage_4_data_export/export_data.py`
- `src/scripts/answer_generation/README.md`
