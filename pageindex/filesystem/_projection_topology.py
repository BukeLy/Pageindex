from __future__ import annotations

from pathlib import Path


def projection_database_paths(index_dir: str | Path) -> tuple[Path, Path]:
    index_dir = Path(index_dir).expanduser()
    return index_dir / "summary.sqlite", index_dir / "embedding_cache.sqlite"


def projection_database_pair(
    index_dir: str | Path,
) -> tuple[Path, Path] | None:
    summary_path, cache_path = projection_database_paths(index_dir)
    summary_exists = summary_path.exists()
    cache_exists = cache_path.exists()
    if not summary_exists and not cache_exists:
        return None
    if not summary_exists or not cache_exists:
        raise RuntimeError(
            "PIFS Summary Projection topology is incomplete; migrate this workspace with "
            "pifs-data/scripts/migrate_pifs_workspace.py before opening it."
        )
    return summary_path, cache_path
