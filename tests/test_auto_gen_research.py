import unittest

from examples.Benchmark.enterprise_rag_benchmark.auto_gen_research.run_auto_gen_research import (
    merge_cluster_batches,
    summarize_combo,
)


class AutoGenResearchTest(unittest.TestCase):
    def test_merge_cluster_batches_normalizes_label_map(self):
        class Message:
            content = '{"label_map":{"Alpha":"Merged","Beta":"Merged"}}'

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        class Completions:
            def create(self, **_kwargs):
                return Response()

        class Chat:
            completions = Completions()

        class FakeClient:
            chat = Chat()

        docs = [
            {"dataset_doc_uuid": "d1", "source_type": "email", "title": "Doc 1"},
            {"dataset_doc_uuid": "d2", "source_type": "email", "title": "Doc 2"},
        ]
        profiles = {
            "d1": {"primary_topic": "alpha", "doc_type": "memo", "semantic_summary": "alpha doc"},
            "d2": {"primary_topic": "beta", "doc_type": "memo", "semantic_summary": "beta doc"},
        }
        batches = [{"doc_cluster": {"d1": "Alpha", "d2": "Beta"}}]

        merged = merge_cluster_batches(FakeClient(), "fake", docs, profiles, batches)

        self.assertEqual(merged["doc_cluster"], {"d1": "Merged", "d2": "Merged"})
        self.assertEqual(merged["raw_label_map"], {"Alpha": "Merged", "Beta": "Merged"})

    def test_summarize_combo_marks_register_only_runs_as_skipped(self):
        results = [
            {
                "question_id": "qst_0001",
                "doc_hit": False,
                "tool_calls": 0,
                "seconds": 0,
                "error": None,
                "skipped": True,
            }
        ]

        summary = summarize_combo("combo", results)

        self.assertTrue(summary["skipped"])
        self.assertEqual(summary["errors"], [])


if __name__ == "__main__":
    unittest.main()
