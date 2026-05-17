from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

from examples.Benchmark.wixqa_benchmark.wixqa import WixQABenchmark, load_articles
from pageindex.filesystem import PageIndexFileSystem


BENCHMARK_DIR = Path(__file__).resolve().parent


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(BENCHMARK_DIR / ".env")
    args = parse_args()
    workspace = (BENCHMARK_DIR / args.workspace).resolve()
    run_dir = (BENCHMARK_DIR / args.run_dir).resolve()
    if args.reset:
        for path in [workspace, run_dir]:
            if path.exists():
                shutil.rmtree(path)
    workspace.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    articles = load_articles(args.kb_jsonl or None, dataset_config=args.kb_config)
    if args.max_articles > 0:
        articles = articles[: args.max_articles]
    filesystem = PageIndexFileSystem(workspace=workspace)
    refs = WixQABenchmark(filesystem).ingest_articles(articles, batch_size=args.batch_size)
    summary = {
        "workspace": str(workspace),
        "kb_config": args.kb_config,
        "kb_jsonl": args.kb_jsonl,
        "article_count": len(articles),
        "registered_files": len(refs),
    }
    (run_dir / "registration_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register WixQA KB corpus into a PIFS workspace")
    parser.add_argument("--workspace", default="runs/pifs-workspace/workspace")
    parser.add_argument("--run-dir", default="runs/pifs-workspace")
    parser.add_argument("--kb-config", default="wix_kb_corpus")
    parser.add_argument("--kb-jsonl", default="")
    parser.add_argument("--max-articles", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--reset", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
