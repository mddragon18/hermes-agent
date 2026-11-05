"""`hermes pm cache-prune` reaps the Hermes-owned uv cache to what the lock resolves.

Regression for the lifecycle half of #148719 (item 3, and the pruning gap #128474 points at):
PM writes `cache/uv/` (`pm/packages.py::uv_cache_dir()`) and nothing outside the CI build path
ever reaps it, so the directory only grows. `pm/uv_cache_prune.py` already enforces the
exact-to-lock contract for shipped bundles, but it had no consumer for a live machine, so a
real machine never ran it. This adds that consumer, plus the marker-identification fix it
needed: uv names dist-info directories with hyphens, and the old pattern recognized none of
them, so a rolling cache silently kept superseded dists. The doctor's warning text for this
directory is #128474's own fix and is deliberately not touched here.
"""

from pathlib import Path

from pm.cache_maintenance import prune_machine_uv_cache

_LOCK = """\
# fixture lock: the packages a cache may keep
[[package]]
name = "httpx"
version = "0.28.1"

[[package]]
name = "rich"
version = "13.7.1"
"""


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "uv.lock").write_text(_LOCK, encoding="utf-8")
    return repo


def _wheel_bucket(cache: Path, family: str, dist: str, bytes_each: int = 4096) -> Path:
    bucket = cache / family / "pypi" / dist
    bucket.mkdir(parents=True)
    (bucket / "payload.whl").write_bytes(b"x" * bytes_each)
    return bucket


def _archive_bucket(cache: Path, dist_marker: str, bytes_each: int = 4096) -> Path:
    """A uv archive bucket is content-addressed and identified by the `.dist-info` marker inside
    it, not by its own directory name — so the directory name here is arbitrary and unique."""
    bucket = cache / "archive-v0" / dist_marker.replace(".", "-")
    bucket.mkdir(parents=True)
    (bucket / dist_marker).mkdir()
    (bucket / "payload").write_bytes(b"x" * bytes_each)
    return bucket


def _fill(cache: Path) -> dict:
    """A cache holding two locked dists, two superseded pins, and one unidentifiable bucket."""
    cache.mkdir(parents=True)
    kept_wheel = _wheel_bucket(cache, "wheels-v7", "httpx")
    stale_wheel = _wheel_bucket(cache, "wheels-v7", "old-dep")
    kept_sdist = _wheel_bucket(cache, "sdists-v10", "rich")
    stale_sdist = _wheel_bucket(cache, "sdists-v10", "retired-dep")
    kept_archive = _archive_bucket(cache, "httpx-0.28.1.dist-info")
    stale_archive = _archive_bucket(cache, "old-dep-9.9.9.dist-info")
    unknown = cache / "archive-v0" / "no-marker"
    unknown.mkdir()
    (unknown / "blob").write_bytes(b"x" * 4096)
    return {
        "kept_wheel": kept_wheel, "stale_wheel": stale_wheel, "kept_sdist": kept_sdist,
        "stale_sdist": stale_sdist, "kept_archive": kept_archive,
        "stale_archive": stale_archive, "unknown": unknown,
    }


def test_prune_drops_superseded_pins_and_keeps_everything_the_lock_resolves(tmp_path):
    repo = _repo(tmp_path)
    cache = tmp_path / "machine" / "cache" / "uv"
    parts = _fill(cache)

    result = prune_machine_uv_cache(repo, cache=cache)

    assert result["pruned_entries"] == 3
    assert result["freed_bytes"] > 0
    assert result["skipped"] is None
    for path in (parts["stale_wheel"], parts["stale_sdist"], parts["stale_archive"]):
        assert not path.exists()
    for path in (parts["kept_wheel"], parts["kept_sdist"], parts["kept_archive"], parts["unknown"]):
        assert path.exists()


def test_hyphenated_dist_names_are_identified_and_pruned(tmp_path):
    """`python-tabulate-0.9.0.dist-info` is a real uv marker and uv names dists with hyphens.

    A name pattern that excludes hyphens matched nothing for these dists, and pruning fails open,
    so a rolling cache kept every superseded hyphenated dist without a word. That is the bulk of
    what `cache/uv/` grows by on a real machine, so the machine pruner has to see them. Names are
    the contract, so the kept dist is a different hyphenated name the lock does resolve.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "uv.lock").write_text(
        '[[package]]\nname = "python-dateutil"\nversion = "2.9.0"\n', encoding="utf-8")
    cache = tmp_path / "cache" / "uv"
    stale = _archive_bucket(cache, "python-tabulate-0.9.0.dist-info")
    kept = _archive_bucket(cache, "python-dateutil-2.9.0.dist-info")

    result = prune_machine_uv_cache(repo, cache=cache)

    assert result["pruned_entries"] == 1
    assert not stale.exists() and kept.exists()


def test_prune_reports_a_missing_cache_without_creating_it(tmp_path):
    """A machine that has never run PM must not gain a cache directory from maintenance."""
    repo = _repo(tmp_path)
    absent = tmp_path / "machine" / "cache" / "uv"

    result = prune_machine_uv_cache(repo, cache=absent)

    assert not absent.exists()
    assert result["pruned_entries"] == 0
    assert result["freed_bytes"] == 0
    assert "no cache" in result["skipped"]


def test_prune_without_a_lock_changes_nothing(tmp_path):
    """No lock means no exactness contract, so maintenance refuses rather than guessing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    cache = tmp_path / "cache" / "uv"
    parts = _fill(cache)

    result = prune_machine_uv_cache(repo, cache=cache)

    assert result["pruned_entries"] == 0
    assert "uv.lock" in result["skipped"]
    assert parts["stale_wheel"].exists()


def test_the_machine_cache_path_is_the_one_seeding_uses(tmp_path, monkeypatch):
    """Maintenance and the seeded cache must be the same directory, or the command prunes a
    tree uv never reads. One definition (`uv_cache_path()`), readable without seeding."""
    from pm.packages import uv_cache_dir, uv_cache_path

    # conftest already points HERMES_HOME and Path.home() at the test's temp dir; the guard
    # refuses creating a `.hermes` of our own, so the fixture's home is the one under test.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

    assert uv_cache_path() == uv_cache_dir()
