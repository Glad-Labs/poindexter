"""Contract tests for the :func:`find_repo_root` sentinel-walking helper.

Pins the behaviour introduced in poindexter#722 to replace the brittle
``Path(__file__).resolve().parents[5]`` idiom that breaks when the
bind-mount depth inside a container differs from the host checkout depth.

Three assertions:

1. ``find_repo_root()`` returns a directory that contains both
   ``pyproject.toml`` and ``src/`` — the two sentinels the helper uses.
2. The result equals ``Path(__file__).resolve().parents[5]`` on the
   standard host layout (5 levels deep), so host runs are byte-for-byte
   identical to the old behaviour.
3. The ``repo_root`` pytest fixture exposes the same path for test
   functions that prefer fixture injection over a module-level call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.conftest import find_repo_root


class TestFindRepoRoot:
    def test_result_contains_pyproject_toml(self):
        root = find_repo_root(Path(__file__))
        assert (root / "pyproject.toml").exists(), (
            f"find_repo_root returned {root!r} but pyproject.toml is absent"
        )

    def test_result_contains_src_directory(self):
        root = find_repo_root(Path(__file__))
        assert (root / "src").is_dir(), (
            f"find_repo_root returned {root!r} but src/ is absent"
        )

    def test_result_matches_parents_index_for_deep_files(self):
        """For files 6 levels deep (e.g. unit/services/<file>.py), the sentinel
        walk and parents[5] must agree on the standard host checkout layout."""
        # Pick a known deep file that was migrated; use its path to verify
        # that the sentinel walk and the old hard-coded index agree.
        deep_file = (
            Path(__file__).parent
            / "services"
            / "test_brain_alert_dispatcher.py"
        )
        by_sentinel = find_repo_root(deep_file)
        by_index = deep_file.resolve().parents[5]
        assert by_sentinel == by_index, (
            f"sentinel walk ({by_sentinel!r}) != parents[5] ({by_index!r}); "
            "layout assumption has changed"
        )

    def test_raises_for_rootless_start(self, tmp_path):
        """A path that doesn't have an ancestor with pyproject.toml + src/
        causes a clear RuntimeError rather than an IndexError."""
        orphan = tmp_path / "orphan" / "file.py"
        orphan.parent.mkdir(parents=True)
        orphan.touch()
        with pytest.raises(RuntimeError, match="repo root not found"):
            find_repo_root(orphan)


class TestRepoRootFixture:
    def test_fixture_returns_same_as_helper(self, repo_root):
        assert repo_root == find_repo_root(Path(__file__))

    def test_fixture_contains_pyproject_toml(self, repo_root):
        assert (repo_root / "pyproject.toml").exists()

    def test_fixture_contains_src(self, repo_root):
        assert (repo_root / "src").is_dir()
