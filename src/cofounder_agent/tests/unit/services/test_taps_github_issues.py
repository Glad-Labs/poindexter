"""Unit tests for services.taps.github_issues.GitHubIssuesTap.

Pins the behaviours that make this Tap safe to run against two repos:

- ``source_id`` is repo-qualified, so poindexter#185 and
  glad-labs-stack#185 cannot collide (the exact defect in the legacy
  Gitea-era rows this Tap replaces).
- Pull requests are excluded — GitHub's /issues endpoint returns them.
- One unreachable repo does not lose the other repo's issues.
- Auth/permission failures degrade to a logged skip, never an exception
  that would take the whole embedding run down.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from plugins import Tap
from services.taps.github_issues import (
    GitHubIssuesTap,
    _build_source_id,
    _parse_repos,
    _render_issue,
)


def _issue(number: int, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "number": number,
        "title": f"Issue {number}",
        "body": f"body of {number}",
        "state": "open",
        "labels": [{"name": "bug"}],
        "html_url": f"https://github.com/o/r/issues/{number}",
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-02T00:00:00Z",
        "closed_at": "",
        "comments": 0,
    }
    base.update(over)
    return base


def _transport(routes: dict[str, list[Any]]) -> httpx.MockTransport:
    """Serve per-repo pages; each list entry is one page's JSON payload.

    A payload may be an int status code to simulate an error response.
    """
    calls: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        repo = "/".join(request.url.path.split("/")[2:4])
        pages = routes.get(repo)
        if pages is None:
            return httpx.Response(404, json={"message": "Not Found"})
        idx = calls.get(repo, 0)
        calls[repo] = idx + 1
        if idx >= len(pages):
            return httpx.Response(200, json=[])
        page = pages[idx]
        if isinstance(page, int):
            return httpx.Response(page, json={"message": "err"}, headers={"x-ratelimit-remaining": "0"})
        return httpx.Response(200, json=page)

    return httpx.MockTransport(handler)


async def _collect(tap: GitHubIssuesTap, config: dict[str, Any]) -> list[Any]:
    return [d async for d in tap.extract(pool=None, config=config)]


@pytest.fixture
def patched_client(monkeypatch):
    """Route the Tap's httpx.AsyncClient through a MockTransport."""

    def _install(routes: dict[str, list[Any]]):
        transport = _transport(routes)
        real_init = httpx.AsyncClient.__init__

        def init(self, *a, **kw):
            kw["transport"] = transport
            real_init(self, *a, **kw)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", init)

    return _install


class TestParseRepos:
    def test_parses_csv(self):
        assert _parse_repos("a/b, c/d") == ["a/b", "c/d"]

    def test_accepts_list(self):
        assert _parse_repos(["a/b"]) == ["a/b"]

    @pytest.mark.parametrize("bad", ["noslash", "a/b/c", "/b", "a/"])
    def test_rejects_malformed(self, bad, caplog):
        with caplog.at_level(logging.WARNING, logger="services.taps.github_issues"):
            assert _parse_repos(bad) == []
        assert "malformed repo" in caplog.text

    def test_empty_is_empty(self):
        assert _parse_repos("") == []


class TestBuildSourceId:
    def test_is_repo_qualified(self):
        assert (
            _build_source_id("Glad-Labs/poindexter", 185)
            == "github/Glad-Labs/poindexter/issues/185"
        )

    def test_same_number_across_repos_does_not_collide(self):
        """The exact defect in the legacy rows: bare issue number as key."""
        a = _build_source_id("Glad-Labs/poindexter", 185)
        b = _build_source_id("Glad-Labs/poindexter", 185)
        assert a != b


class TestRenderIssue:
    def test_leads_with_identifying_header(self):
        text = _render_issue(_issue(7, title="Fix the thing"), "o/r", 1000)
        assert text.startswith("Issue o/r#7 (open): Fix the thing")

    def test_includes_labels_and_body(self):
        text = _render_issue(_issue(7), "o/r", 1000)
        assert "Labels: bug" in text
        assert "body of 7" in text

    def test_truncates_oversized_body(self):
        text = _render_issue(_issue(7, body="x" * 5000), "o/r", 100)
        assert "[... truncated]" in text
        assert len(text) < 500

    def test_survives_null_body(self):
        """GitHub returns body: null for issues opened with a title only."""
        text = _render_issue(_issue(7, body=None), "o/r", 1000)
        assert "Issue o/r#7" in text


class TestExtract:
    @pytest.mark.asyncio
    async def test_yields_issues_from_every_repo(self, patched_client):
        patched_client({"o/a": [[_issue(1)]], "o/b": [[_issue(2)]]})
        docs = await _collect(GitHubIssuesTap(), {"repos": "o/a,o/b", "gh_token": "t"})

        ids = {d.source_id for d in docs}
        assert ids == {"github/o/a/issues/1", "github/o/b/issues/2"}
        assert all(d.source_table == "issues" for d in docs)
        assert all(d.writer == "github" for d in docs)

    @pytest.mark.asyncio
    async def test_excludes_pull_requests(self, patched_client):
        """GitHub's /issues endpoint returns PRs — they carry `pull_request`."""
        patched_client(
            {"o/a": [[_issue(1), _issue(2, pull_request={"url": "..."}), _issue(3)]]}
        )
        docs = await _collect(GitHubIssuesTap(), {"repos": "o/a", "gh_token": "t"})

        nums = sorted(d.metadata["issue_number"] for d in docs)
        assert nums == [1, 3]

    @pytest.mark.asyncio
    async def test_paginates_until_short_page(self, patched_client):
        full = [_issue(n) for n in range(1, 101)]  # exactly _PER_PAGE
        patched_client({"o/a": [full, [_issue(101)]]})
        docs = await _collect(GitHubIssuesTap(), {"repos": "o/a", "gh_token": "t"})
        assert len(docs) == 101

    @pytest.mark.asyncio
    async def test_respects_max_issues_per_repo(self, patched_client):
        patched_client({"o/a": [[_issue(n) for n in range(1, 51)]]})
        docs = await _collect(
            GitHubIssuesTap(), {"repos": "o/a", "gh_token": "t", "max_issues_per_repo": 5}
        )
        assert len(docs) == 5

    @pytest.mark.asyncio
    async def test_cap_counts_issues_not_api_items(self, patched_client):
        """A PR-heavy page must not exhaust the cap and emit nothing.

        Caught live: with max_issues_per_repo=10 against the real repos, one
        returned ZERO issues because its oldest 10 API items were all pull
        requests. Counting raw items rather than emitted issues turned a
        config knob into what looked like an outage — and a tap yielding
        zero also trips the zero-yield finding.
        """
        page = [_issue(n, pull_request={"url": "..."}) for n in range(1, 21)]
        page += [_issue(n) for n in range(21, 26)]
        patched_client({"o/a": [page]})

        docs = await _collect(
            GitHubIssuesTap(), {"repos": "o/a", "gh_token": "t", "max_issues_per_repo": 3}
        )

        assert len(docs) == 3
        assert sorted(d.metadata["issue_number"] for d in docs) == [21, 22, 23]

    @pytest.mark.asyncio
    async def test_missing_repo_does_not_lose_the_other(self, patched_client, caplog):
        """A 404 on one repo must not abort the whole run."""
        patched_client({"o/good": [[_issue(1)]]})  # o/missing is absent -> 404
        with caplog.at_level(logging.WARNING, logger="services.taps.github_issues"):
            docs = await _collect(
                GitHubIssuesTap(), {"repos": "o/missing,o/good", "gh_token": "t"}
            )
        assert [d.metadata["issue_number"] for d in docs] == [1]
        assert "404" in caplog.text

    @pytest.mark.asyncio
    async def test_rate_limit_is_reported_distinctly(self, patched_client, caplog):
        """403 + remaining=0 is a rate limit, not a permissions problem —
        they need different operator responses."""
        patched_client({"o/a": [403]})
        with caplog.at_level(logging.WARNING, logger="services.taps.github_issues"):
            docs = await _collect(GitHubIssuesTap(), {"repos": "o/a", "gh_token": "t"})
        assert docs == []
        assert "rate limit" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_no_repos_configured_warns(self, caplog):
        with caplog.at_level(logging.WARNING, logger="services.taps.github_issues"):
            docs = await _collect(GitHubIssuesTap(), {"repos": ""})
        assert docs == []
        assert "no valid repos" in caplog.text

    @pytest.mark.asyncio
    async def test_missing_token_warns_but_still_tries(self, patched_client, caplog):
        """Public repos work unauthenticated (slowly); say so rather than
        silently producing an empty result."""
        patched_client({"o/a": [[_issue(1)]]})
        with caplog.at_level(logging.WARNING, logger="services.taps.github_issues"):
            docs = await _collect(GitHubIssuesTap(), {"repos": "o/a"})
        assert len(docs) == 1
        assert "gh_token" in caplog.text

    @pytest.mark.asyncio
    async def test_metadata_carries_triage_fields(self, patched_client):
        patched_client({"o/a": [[_issue(9, state="closed", closed_at="2026-08-03T00:00:00Z")]]})
        docs = await _collect(GitHubIssuesTap(), {"repos": "o/a", "gh_token": "t"})

        md = docs[0].metadata
        assert md["repo"] == "o/a"
        assert md["issue_number"] == 9
        assert md["state"] == "closed"
        assert md["labels"] == ["bug"]
        assert md["url"].endswith("/issues/9")
        assert md["closed_at"] == "2026-08-03T00:00:00Z"


class TestOssGenericDefaults:
    """No repo may be baked in as a default — OSS installs are not ours.

    A code or seed default naming particular repos would make a fresh
    Poindexter install scrape a stranger's issue tracker on first boot. The
    operator's own repos live in ``services/operator_overrides.py``, which
    is stripped from the public mirror.
    """

    def test_code_default_is_empty(self):
        from services.taps.github_issues import _DEFAULT_REPOS

        assert _DEFAULT_REPOS == ""

    def test_seeded_default_names_no_repo(self):
        import json

        from services.settings_defaults import DEFAULTS

        cfg = json.loads(DEFAULTS["plugin.tap.github_issues"])
        assert cfg["config"]["repos"] == ""

    @pytest.mark.asyncio
    async def test_unconfigured_yields_nothing_and_says_so(self, caplog):
        """Silence here would be indistinguishable from a broken tap."""
        with caplog.at_level(logging.WARNING, logger="services.taps.github_issues"):
            docs = await _collect(GitHubIssuesTap(), {})
        assert docs == []
        assert "no valid repos" in caplog.text


class TestConformance:
    def test_satisfies_tap_protocol(self):
        assert isinstance(GitHubIssuesTap(), Tap)

    def test_declares_name_and_interval(self):
        tap = GitHubIssuesTap()
        assert tap.name == "github_issues"
        assert tap.interval_seconds == 21600

    def test_is_registered_in_the_plugin_registry(self):
        """A Tap on disk but absent from _SAMPLES never runs — that is
        exactly how OpenClawSQLiteTap sat dormant (finding #189)."""
        from plugins.registry import get_core_samples

        names = {t.name for t in get_core_samples().get("taps", [])}
        assert "github_issues" in names
