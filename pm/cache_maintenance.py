"""Maintenance for the Hermes-owned uv cache.

PM creates `cache/uv/` (`pm.packages.uv_cache_path()`) and reuses it as the warm cache for
every `uv sync` / `uv run`, but nothing outside the CI build path ever reaps it: the directory
only grows (issue #148719, item 3). Pruning it is uv's supported maintenance, and
`pm.uv_cache_prune.prune_uv_cache_to_lock` already enforces the exactness contract for
bundles — the gap was that no consumer existed for a live machine. This module is that
consumer: it prunes to the repo's `uv.lock`, so what is dropped is only what no environment
on this machine can resolve again, and what is kept includes everything the current lock
pins (the warm-cache contract for the next install stays intact). No uv invocation, no
network, no toolchain.
"""
from __future__ import annotations

from pathlib import Path


def prune_machine_uv_cache(
    source_repo: Path | None = None,
    *,
    cache: Path | None = None,
) -> dict:
    """Prune the machine uv cache down to what *source_repo*'s `uv.lock` resolves.

    Returns ``{"cache": path, "pruned_entries": n, "freed_bytes": int, "skipped": reason|None}``.
    The cache is never created, and without a lock there is no exactness contract to prune by,
    so both states are reported through `skipped` with nothing removed.
    """
    from hermes_constants import scratch_dir_usage_bytes

    from pm.packages import uv_cache_path
    from pm.paths import repo_root
    from pm.uv_cache_prune import prune_uv_cache_to_lock

    target = Path(cache) if cache is not None else uv_cache_path()
    if not target.is_dir():
        return {"cache": target, "pruned_entries": 0, "freed_bytes": 0,
                "skipped": "no cache directory yet — nothing to reclaim"}
    repo = Path(source_repo) if source_repo is not None else repo_root()
    if not (repo / "uv.lock").is_file():
        return {"cache": target, "pruned_entries": 0, "freed_bytes": 0,
                "skipped": f"no uv.lock in {repo} — pruning needs the exactness contract"}

    before = scratch_dir_usage_bytes(target)
    pruned = prune_uv_cache_to_lock(target, repo)
    return {"cache": target, "pruned_entries": pruned,
            "freed_bytes": max(before - scratch_dir_usage_bytes(target), 0), "skipped": None}
