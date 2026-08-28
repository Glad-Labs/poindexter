"""Unit tests for scripts/ci/docs_link_rot_lint.py.

The lint replaces `Mintlify Validation (gladlabs) - link-rot`, a REQUIRED check
on Glad-Labs/poindexter that has never once reported. These tests pin the two
things that made the first draft of the lint unusable — it matched link syntax
inside code fences and inline code, and reported ~300 of the docs' own examples
as rot.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

LINT_PATH = Path(__file__).resolve().parents[5] / "scripts" / "ci" / "docs_link_rot_lint.py"


def _load(tmp_root: Path):
    """Import the lint with REPO_ROOT / DOCS_* pointed at a throwaway tree."""
    spec = importlib.util.spec_from_file_location("_docs_link_rot_lint", LINT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_docs_link_rot_lint"] = mod
    spec.loader.exec_module(mod)
    mod.REPO_ROOT = tmp_root
    mod.DOCS_JSON = tmp_root / "docs.json"
    mod.DOCS_DIR = tmp_root / "docs"
    return mod


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs.json").write_text(
        '{"navigation": {"tabs": [{"tab": "T", "groups": '
        '[{"group": "G", "pages": ["docs/real"]}]}]}}',
        encoding="utf-8",
    )
    (tmp_path / "docs" / "real.md").write_text("# Real\n", encoding="utf-8")
    return tmp_path


@pytest.mark.unit
def test_clean_tree_passes(tree: Path) -> None:
    mod = _load(tree)
    assert mod.check_navigation() == ([], 1)
    assert mod.check_internal_links() == ([], 1)


@pytest.mark.unit
def test_dangling_navigation_entry_is_reported(tree: Path) -> None:
    (tree / "docs.json").write_text(
        '{"navigation": {"tabs": [{"tab": "T", "groups": '
        '[{"group": "G", "pages": ["docs/real", "docs/vanished"]}]}]}}',
        encoding="utf-8",
    )
    mod = _load(tree)
    problems, checked = mod.check_navigation()
    assert checked == 2
    assert len(problems) == 1
    assert "docs/vanished" in problems[0]


@pytest.mark.unit
def test_dead_relative_link_is_reported(tree: Path) -> None:
    (tree / "docs" / "real.md").write_text(
        "See [the other](./gone.md) for detail.\n", encoding="utf-8"
    )
    mod = _load(tree)
    problems, _ = mod.check_internal_links()
    assert len(problems) == 1
    assert "./gone.md" in problems[0]


@pytest.mark.unit
def test_live_relative_link_passes(tree: Path) -> None:
    (tree / "docs" / "other.md").write_text("# Other\n", encoding="utf-8")
    (tree / "docs" / "real.md").write_text("See [the other](./other.md).\n", encoding="utf-8")
    mod = _load(tree)
    assert mod.check_internal_links()[0] == []


@pytest.mark.unit
def test_link_syntax_inside_a_code_fence_is_not_a_link(tree: Path) -> None:
    """The docs explain markdown link syntax; those examples are not rot."""
    (tree / "docs" / "real.md").write_text(
        "Example:\n\n```markdown\n[Title](url)\n[x](./nope.md)\n```\n",
        encoding="utf-8",
    )
    mod = _load(tree)
    assert mod.check_internal_links()[0] == []


@pytest.mark.unit
def test_link_syntax_inside_inline_code_is_not_a_link(tree: Path) -> None:
    """anti-hallucination.md discusses `[Title](url)` in prose — not a link."""
    (tree / "docs" / "real.md").write_text(
        "Citations shaped like `[Title](url)` are draft artifacts.\n",
        encoding="utf-8",
    )
    mod = _load(tree)
    assert mod.check_internal_links()[0] == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "target",
    [
        "https://example.com/x",
        "mailto:a@b.c",
        "#anchor",
        "/go/mercury",  # deployed-site route, not a repo path
        "/docs/whatever",
        "{url}",  # placeholder in prose
        "<code>",
        "...",
    ],
)
def test_out_of_scope_targets_are_skipped(tree: Path, target: str) -> None:
    (tree / "docs" / "real.md").write_text(f"[x]({target})\n", encoding="utf-8")
    mod = _load(tree)
    assert mod.check_internal_links()[0] == []


@pytest.mark.unit
def test_anchor_is_stripped_before_resolving(tree: Path) -> None:
    (tree / "docs" / "other.md").write_text("# Other\n", encoding="utf-8")
    (tree / "docs" / "real.md").write_text("See [there](./other.md#a-heading).\n", encoding="utf-8")
    mod = _load(tree)
    assert mod.check_internal_links()[0] == []


@pytest.mark.unit
def test_extensionless_link_resolves_like_mintlify(tree: Path) -> None:
    (tree / "docs" / "other.md").write_text("# Other\n", encoding="utf-8")
    (tree / "docs" / "real.md").write_text("[there](./other)\n", encoding="utf-8")
    mod = _load(tree)
    assert mod.check_internal_links()[0] == []


@pytest.mark.unit
def test_missing_docs_json_is_reported_not_skipped(tmp_path: Path) -> None:
    """The floor case: no docs.json must fail, never pass over nothing."""
    (tmp_path / "docs").mkdir()
    mod = _load(tmp_path)
    problems, checked = mod.check_navigation()
    assert checked == 0
    assert problems and "docs.json not found" in problems[0]
