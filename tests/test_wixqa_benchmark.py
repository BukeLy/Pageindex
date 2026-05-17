import json
import tempfile
import unittest
from pathlib import Path


class WixQABenchmarkTest(unittest.TestCase):
    def test_loader_registration_and_official_prediction_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            kb_path = tmp_path / "kb.jsonl"
            qa_path = tmp_path / "qa.jsonl"
            kb_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "id": "article_1",
                                "url": "https://support.wix.com/en/article/site-editor/connect-a-domain",
                                "contents": "Connect a domain from the site editor.",
                                "article_type": "help_center",
                            }
                        ),
                        json.dumps(
                            {
                                "id": "article_2",
                                "url": "https://support.wix.com/en/article/billing/refunds",
                                "contents": "Refunds are handled from billing settings.",
                                "article_type": "help_center",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            qa_path.write_text(
                json.dumps(
                    {
                        "question": "How do I connect a domain?",
                        "answer": "Open the site editor and connect the domain.",
                        "article_ids": ["article_1"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            from examples.Benchmark.wixqa_benchmark.wixqa import (
                WixQABenchmark,
                load_articles,
                load_questions,
                write_official_predictions,
            )
            from pageindex.filesystem import PageIndexFileSystem

            articles = load_articles(kb_path)
            questions = load_questions(qa_path, split="expert")
            filesystem = PageIndexFileSystem(workspace=tmp_path / "workspace")
            refs = WixQABenchmark(filesystem).ingest_articles(articles)
            write_official_predictions(
                tmp_path / "predictions.jsonl",
                [
                    {
                        "question": questions[0].question,
                        "answer": "Use the site editor.",
                        "article_ids": ["article_1"],
                    }
                ],
            )

            stat = filesystem._stat("article_1")
            folders = filesystem.browse("/kb/help_center/site-editor", recursive=True)
            prediction = json.loads((tmp_path / "predictions.jsonl").read_text(encoding="utf-8"))

            self.assertEqual(len(refs), 2)
            self.assertEqual(questions[0].question_id, "expert_0001")
            self.assertEqual(questions[0].expected_article_ids, ["article_1"])
            self.assertEqual(stat["external_id"], "article_1")
            self.assertEqual(stat["metadata"]["category"], "site-editor")
            self.assertEqual(folders["files"][0]["external_id"], "article_1")
            self.assertEqual(
                set(prediction),
                {"question", "answer", "article_ids"},
            )

    def test_runner_refuses_unregistered_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            from examples.Benchmark.wixqa_benchmark.run_wixqa_pifs_agent import ensure_workspace

            with self.assertRaises(SystemExit):
                ensure_workspace(Path(tmp) / "missing")


if __name__ == "__main__":
    unittest.main()
