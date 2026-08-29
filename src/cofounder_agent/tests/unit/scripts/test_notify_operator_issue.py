"""Guards the CI issue notifier (``scripts/ci/notify_operator_issue.py``).

The notifier exists because three workflows filed one issue per failed RUN
rather than per incident: 20 issues for 3 real failures, each closed by hand.
These tests pin the two behaviours that make it dedupe, and the two ``gh``
traps that would silently un-fix it.

The ``gh`` boundary is injected, so nothing here touches the network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
SCRIPT = REPO_ROOT / "scripts" / "ci" / "notify_operator_issue.py"

TITLE = "⚠️ poindexter mirror sync FAILED"
REPO = "Glad-Labs/glad-labs-stack"


@pytest.fixture(scope="module")
def notify():
    assert SCRIPT.is_file(), f"notifier missing at {SCRIPT}"
    spec = importlib.util.spec_from_file_location("notify_operator_issue", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["notify_operator_issue"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeGh:
    """Records every gh invocation and replays a canned issue list."""

    def __init__(self, open_issues=None, list_rc=0, list_stdout=None):
        self.open_issues = open_issues or []
        self.list_rc = list_rc
        self.list_stdout = list_stdout
        self.calls: list[list[str]] = []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            if self.list_stdout is not None:
                return self.list_rc, self.list_stdout
            return self.list_rc, json.dumps(self.open_issues)
        if args[:2] == ["issue", "create"]:
            return 0, "https://github.com/o/r/issues/99\n"
        return 0, ""

    def verbs(self) -> list[str]:
        return [" ".join(c[:2]) for c in self.calls]


def _body(tmp_path: Path) -> str:
    path = tmp_path / "body.md"
    path.write_text("the sync broke", encoding="utf-8")
    return str(path)


def _failed_argv(body_file: str) -> list[str]:
    return [
        "failed", "--repo", REPO, "--title", TITLE, "--label", "bug",
        "--body-file", body_file, "--run-url", "https://run/1", "--commit", "abc1234",
    ]


def _recovered_argv() -> list[str]:
    return [
        "recovered", "--repo", REPO, "--title", TITLE,
        "--run-url", "https://run/2", "--commit", "def5678",
    ]


@pytest.mark.unit
class TestFailedMode:
    def test_files_an_issue_when_none_is_open(self, notify, tmp_path) -> None:
        gh = FakeGh(open_issues=[])
        assert notify.main(_failed_argv(_body(tmp_path)), runner=gh) == 0
        assert "issue create" in gh.verbs()

    def test_comments_instead_of_filing_a_duplicate(self, notify, tmp_path) -> None:
        """The whole point: a storm of failures becomes comments on ONE issue."""
        gh = FakeGh(open_issues=[{"number": 3448, "title": TITLE}])
        assert notify.main(_failed_argv(_body(tmp_path)), runner=gh) == 0
        assert "issue create" not in gh.verbs()
        assert "issue comment" in gh.verbs()
        comment = gh.calls[-1]
        assert "3448" in comment
        assert any("abc1234" in part for part in comment), "comment must name the run's commit"

    def test_a_similar_title_is_not_a_match(self, notify, tmp_path) -> None:
        """Exact match only — two guards must not collapse into each other."""
        gh = FakeGh(open_issues=[
            {"number": 1, "title": TITLE + " (run 123)"},
            {"number": 2, "title": "⚠️ main fails a ratchet lint"},
        ])
        assert notify.main(_failed_argv(_body(tmp_path)), runner=gh) == 0
        assert "issue create" in gh.verbs()


@pytest.mark.unit
class TestRecoveredMode:
    def test_closes_the_open_issue(self, notify) -> None:
        """The fix clears the issue — nobody should close these by hand."""
        gh = FakeGh(open_issues=[{"number": 3448, "title": TITLE}])
        assert notify.main(_recovered_argv(), runner=gh) == 0
        assert "issue close" in gh.verbs()
        assert "issue comment" in gh.verbs()

    def test_is_a_no_op_when_nothing_is_open(self, notify) -> None:
        gh = FakeGh(open_issues=[])
        assert notify.main(_recovered_argv(), runner=gh) == 0
        assert gh.verbs() == ["issue list"]

    def test_closes_every_duplicate_that_slipped_through(self, notify) -> None:
        """Self-heals a race in workflows with no concurrency group."""
        gh = FakeGh(open_issues=[
            {"number": 10, "title": TITLE},
            {"number": 11, "title": TITLE},
        ])
        assert notify.main(_recovered_argv(), runner=gh) == 0
        closed = {c[2] for c in gh.calls if c[:2] == ["issue", "close"]}
        assert closed == {"10", "11"}


@pytest.mark.unit
class TestGhTraps:
    def test_lookup_passes_an_explicit_limit(self, notify) -> None:
        """`gh issue list` silently truncates at 30 — a miss files a duplicate."""
        gh = FakeGh(open_issues=[])
        notify.find_open_issues(REPO, TITLE, runner=gh)
        listing = gh.calls[0]
        assert "--limit" in listing
        assert int(listing[listing.index("--limit") + 1]) >= 100

    def test_lookup_never_uses_the_search_index(self, notify) -> None:
        """The search index lags writes, so dedupe built on it misses its own
        just-filed issue — the exact bug this script fixes."""
        gh = FakeGh(open_issues=[])
        notify.find_open_issues(REPO, TITLE, runner=gh)
        assert "--search" not in gh.calls[0]


@pytest.mark.unit
class TestNeverBreaksTheBuild:
    """Best-effort: a flaky bookkeeping call must not redden a green sync."""

    def test_survives_a_failing_gh(self, notify) -> None:
        gh = FakeGh(list_rc=1, list_stdout="")
        assert notify.main(_recovered_argv(), runner=gh) == 0
        assert "issue close" not in gh.verbs()

    def test_survives_unparseable_gh_output(self, notify) -> None:
        gh = FakeGh(list_stdout="not json at all")
        assert notify.main(_recovered_argv(), runner=gh) == 0

    def test_survives_a_missing_body_file(self, notify, tmp_path) -> None:
        gh = FakeGh(open_issues=[])
        argv = _failed_argv(str(tmp_path / "nope.md"))
        assert notify.main(argv, runner=gh) == 0
        assert "issue create" not in gh.verbs()
