"""Unit tests for DevDiarySource.

Two layers:

1. **Pure-function helpers** (``_collect_merged_prs``, ``_collect_notable_commits``,
   ``DevDiaryContext``) — no DB. The GitHub REST API is mocked via
   ``httpx.MockTransport``; no live network access.
2. **End-to-end ``gather_context``** — uses the ``db_pool`` fixture
   (a real Postgres database with all migrations applied) so we
   actually exercise the brain_decisions / audit_log / posts /
   cost_logs queries against real schema. GitHub calls are patched
   out at the collector level.

Per Matt's directive (PR #155): no row-faker MagicMocks for DB
behavior. The ``db_pool`` fixture provides a real pool.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from plugins.topic_source import TopicSource
from services.topic_sources.dev_diary_source import (
    _CC_RE,
    _NOTABLE_COMMIT_PREFIXES,
    DevDiaryContext,
    DevDiarySource,
    _collect_merged_prs,
    _collect_notable_commits,
)

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dev_diary_source_implements_topic_source_protocol():
    assert isinstance(DevDiarySource(), TopicSource)


@pytest.mark.unit
def test_dev_diary_source_name():
    assert DevDiarySource.name == "dev_diary"


# ---------------------------------------------------------------------------
# DevDiaryContext dataclass
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDevDiaryContext:
    def _empty(self) -> DevDiaryContext:
        return DevDiaryContext(
            date="2026-05-02",
            merged_prs=[],
            notable_commits=[],
            brain_decisions=[],
            audit_resolved=[],
            recent_posts=[],
            cost_summary={"total_usd": 0.0, "total_inferences": 0, "by_model": []},
        )

    def test_to_dict_roundtrip(self):
        ctx = self._empty()
        d = ctx.to_dict()
        assert d["date"] == "2026-05-02"
        assert d["merged_prs"] == []
        assert d["cost_summary"]["total_inferences"] == 0

    def test_is_empty_true_for_quiet_day(self):
        assert self._empty().is_empty() is True

    def test_is_empty_false_when_pr_present(self):
        ctx = self._empty()
        ctx.merged_prs = [{"number": 1, "title": "feat: new thing"}]
        assert ctx.is_empty() is False

    def test_is_empty_false_when_commit_present(self):
        ctx = self._empty()
        ctx.notable_commits = [{"sha": "abc12345", "subject": "fix: bug"}]
        assert ctx.is_empty() is False

    def test_is_empty_true_when_only_brain_decisions_present(self):
        # Brain decisions are NOT signal on their own — the brain emits
        # high-confidence "Cycle complete" heartbeats every 5 minutes,
        # so their presence doesn't justify a post. Required real signal
        # is git activity, audit events, or published posts.
        ctx = self._empty()
        ctx.brain_decisions = [{"id": 1, "decision": "swap models", "confidence": 0.9}]
        assert ctx.is_empty() is True

    def test_is_empty_false_when_audit_resolved_present(self):
        ctx = self._empty()
        ctx.audit_resolved = [{"id": 1, "event_type": "stuck_task_resolved"}]
        assert ctx.is_empty() is False

    def test_is_empty_false_when_recent_posts_present(self):
        ctx = self._empty()
        ctx.recent_posts = [{"id": "p1", "title": "Today's drop"}]
        assert ctx.is_empty() is False

    def test_is_empty_ignores_cost_summary(self):
        # Cost is metadata — it tells the writer what the day cost,
        # but doesn't justify a post on its own.
        ctx = self._empty()
        ctx.cost_summary = {"total_usd": 5.0, "total_inferences": 1000, "by_model": []}
        assert ctx.is_empty() is True

    def test_headline_summarizes_pr_count(self):
        ctx = self._empty()
        ctx.merged_prs = [
            {"title": "feat: per-medium approval gate engine"},
            {"title": "fix: stuck retry"},
        ]
        h = ctx.headline()
        assert h == "Daily dev diary — 2026-05-02 (2 PRs)"

    def test_headline_summarizes_pr_and_commit_counts(self):
        ctx = self._empty()
        ctx.merged_prs = [{"title": "feat: thing"}]
        ctx.notable_commits = [
            {"subject": "fix: a"},
            {"subject": "fix: b"},
            {"subject": "fix: c"},
        ]
        h = ctx.headline()
        assert h == "Daily dev diary — 2026-05-02 (1 PR, 3 commits)"

    def test_headline_falls_back_to_commit_count(self):
        ctx = self._empty()
        ctx.notable_commits = [{"subject": "fix: stuck pipeline retry loop"}]
        h = ctx.headline()
        assert h == "Daily dev diary — 2026-05-02 (1 commit)"

    def test_headline_falls_back_to_post_count_when_no_git_signal(self):
        ctx = self._empty()
        ctx.recent_posts = [{"id": "p1", "title": "Today"}, {"id": "p2", "title": "Yesterday"}]
        h = ctx.headline()
        assert h == "Daily dev diary — 2026-05-02 (2 posts)"

    def test_headline_generic_when_empty(self):
        h = self._empty().headline()
        assert h == "Daily dev diary — 2026-05-02"

    def test_headline_does_not_embed_pr_titles(self):
        # Regression: Glad-Labs/poindexter#353 — PR titles in the topic
        # were truncated mid-identifier (POINDEXTER_SECRET_KEY →
        # POINDEXTER_SE), and the writer hallucinated an explanation of
        # the partial identifier. The fix is to keep PR/commit titles
        # OUT of the topic entirely; the writer reads them from
        # task_metadata.context_bundle instead.
        long_title = (
            "fix(cli): rank-batch sys#N markers + auto-load "
            "POINDEXTER_SECRET_KEY"
        )
        ctx = self._empty()
        ctx.merged_prs = [{"title": long_title}]
        h = ctx.headline()
        assert "POINDEXTER" not in h
        assert "rank-batch" not in h
        assert "auto-load" not in h
        # And the bundle still carries the full title for the writer:
        assert ctx.to_dict()["merged_prs"][0]["title"] == long_title

    def test_headline_handles_arbitrary_long_titles_without_truncation(self):
        # Any title — short, long, or pathologically all-one-word —
        # produces the same well-formed topic, because the topic is
        # built from counts only.
        ctx = self._empty()
        ctx.merged_prs = [
            {"title": "X" * 500},
            {"title": "feat: " + "AAAAAAAAAAAAAAAAAAAA" * 20},
        ]
        h = ctx.headline()
        # No ellipsis; no mid-string truncation; deterministic output.
        assert "…" not in h
        assert "..." not in h
        assert h == "Daily dev diary — 2026-05-02 (2 PRs)"


# ---------------------------------------------------------------------------
# Conventional-commit regex
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConventionalCommitRegex:
    @pytest.mark.parametrize(
        "subject,prefix",
        [
            ("feat: add daily dev diary", "feat"),
            ("fix: stuck task retry", "fix"),
            ("refactor(scope): split god file", "refactor"),
            ("perf!: drop redundant index", "perf"),
            ("security: rotate jwt secret", "security"),
        ],
    )
    def test_matches_notable_prefix(self, subject, prefix):
        m = _CC_RE.match(subject)
        assert m is not None
        assert m.group(1).lower() == prefix
        assert prefix in _NOTABLE_COMMIT_PREFIXES or prefix == "refactor"

    def test_chore_matches_but_filtered_by_caller(self):
        m = _CC_RE.match("chore: bump deps")
        assert m is not None
        # 'chore' parses cleanly but is filtered out at the collector level
        # because chore: commits aren't notable enough for build-in-public.
        assert m.group(1) == "chore"
        assert "chore" not in _NOTABLE_COMMIT_PREFIXES

    def test_unconventional_subject_no_match(self):
        assert _CC_RE.match("Just a regular sentence as a commit subject") is None


# ---------------------------------------------------------------------------
# httpx MockTransport helpers — wire fake GitHub REST API responses
# ---------------------------------------------------------------------------


def _mock_transport(
    responses: dict[str, httpx.Response],
    captured_requests: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """Build an ``httpx.MockTransport`` that dispatches by URL substring.

    ``responses`` keys are case-sensitive substrings looked up against
    ``str(request.url)`` — pick something distinctive like ``"/pulls"``
    or ``"/commits"``. Returns a 599 placeholder if no key matches so
    surprises surface as a clear failure rather than passing silently.

    ``captured_requests`` (optional): when provided, every dispatched
    request is appended so the test can inspect headers / URL params.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if captured_requests is not None:
            captured_requests.append(request)
        url = str(request.url)
        for needle, resp in responses.items():
            if needle in url:
                return resp
        return httpx.Response(599, text=f"no mock for {url}")

    return httpx.MockTransport(handler)


def _gh_pull_payload(
    *,
    number: int,
    title: str,
    merged_at: str | None,
    author_login: str = "matty",
    body: str = "",
    repo: str = "Glad-Labs/poindexter",
) -> dict:
    """Shape of an item in the GitHub ``GET /repos/.../pulls`` response."""
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "merged_at": merged_at,
        "user": {"login": author_login} if author_login else None,
        "body": body,
        "state": "closed",
    }


def _gh_commit_payload(
    *,
    sha: str,
    subject: str,
    author_name: str = "Matt",
    date: str = "2026-05-06T12:00:00Z",
    parents: int = 1,
) -> dict:
    """Shape of an item in the GitHub ``GET /repos/.../commits`` response."""
    return {
        "sha": sha,
        "commit": {
            "message": subject,
            "author": {"name": author_name, "date": date},
        },
        "parents": [{"sha": "p" * 40} for _ in range(parents)],
    }


# ---------------------------------------------------------------------------
# _collect_merged_prs — async, talks to GitHub REST via httpx
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestCollectMergedPRs:
    async def test_returns_empty_on_network_error(self):
        def boom(request):
            raise httpx.ConnectError("dns failed")

        transport = httpx.MockTransport(boom)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _collect_merged_prs(
                hours=24, repo="Glad-Labs/poindexter", client=client,
            )
        assert result == []

    async def test_returns_empty_when_api_returns_empty_list(self):
        transport = _mock_transport({
            "/pulls": httpx.Response(200, json=[]),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _collect_merged_prs(
                hours=24, repo="Glad-Labs/poindexter", client=client,
            )
        assert result == []

    async def test_parses_api_response(self):
        recent = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = [
            _gh_pull_payload(
                number=156,
                title="feat(gates): per-medium approval gate engine",
                merged_at=recent,
                body="A long PR description that explains the change.",
            ),
            _gh_pull_payload(
                number=155,
                title="test: tighten _make_row helpers",
                merged_at=recent,
            ),
        ]
        transport = _mock_transport({
            "/pulls": httpx.Response(200, json=payload),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _collect_merged_prs(
                hours=24, repo="Glad-Labs/poindexter", client=client,
            )
        assert len(result) == 2
        assert result[0]["number"] == 156
        assert result[0]["title"] == "feat(gates): per-medium approval gate engine"
        assert result[0]["author"] == "matty"
        assert result[0]["body"] == "A long PR description that explains the change."
        assert "Glad-Labs/poindexter/pull/156" in result[0]["url"]

    async def test_filters_closed_unmerged_prs(self):
        recent = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = [
            _gh_pull_payload(number=1, title="feat: kept", merged_at=recent),
            # Closed-but-not-merged — merged_at is null on the wire.
            _gh_pull_payload(number=2, title="abandoned", merged_at=None),
        ]
        transport = _mock_transport({
            "/pulls": httpx.Response(200, json=payload),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _collect_merged_prs(
                hours=24, repo="Glad-Labs/poindexter", client=client,
            )
        assert [pr["number"] for pr in result] == [1]

    async def test_filters_prs_outside_lookback_window(self):
        # GitHub returns the most-recently-updated 30 closed PRs; we
        # have to filter by merged_at >= since on the client side.
        recent = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = (
            datetime.now(timezone.utc) - timedelta(hours=72)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = [
            _gh_pull_payload(number=1, title="feat: in window", merged_at=recent),
            _gh_pull_payload(number=2, title="feat: too old", merged_at=old),
        ]
        transport = _mock_transport({
            "/pulls": httpx.Response(200, json=payload),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _collect_merged_prs(
                hours=24, repo="Glad-Labs/poindexter", client=client,
            )
        assert [pr["number"] for pr in result] == [1]

    async def test_handles_malformed_json_gracefully(self):
        transport = _mock_transport({
            "/pulls": httpx.Response(
                200, text="not valid json {{{",
                headers={"content-type": "application/json"},
            ),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _collect_merged_prs(
                hours=24, repo="Glad-Labs/poindexter", client=client,
            )
        assert result == []

    async def test_handles_missing_user_dict(self):
        recent = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = [{
            "number": 1, "title": "x", "html_url": "y",
            "merged_at": recent, "user": None,
        }]
        transport = _mock_transport({
            "/pulls": httpx.Response(200, json=payload),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _collect_merged_prs(
                hours=24, repo="Glad-Labs/poindexter", client=client,
            )
        assert result[0]["author"] == ""

    async def test_5xx_returns_empty_with_warning(self, caplog):
        transport = _mock_transport({
            "/pulls": httpx.Response(503, text="service unavailable"),
        })
        with caplog.at_level(logging.WARNING, logger="services.topic_sources.dev_diary_source"):
            async with httpx.AsyncClient(transport=transport) as client:
                result = await _collect_merged_prs(
                    hours=24, repo="Glad-Labs/poindexter", client=client,
                )
        assert result == []
        assert any(
            "503" in rec.getMessage() and "GitHub API" in rec.getMessage()
            for rec in caplog.records
        ), f"expected a 503 warning, got: {[r.getMessage() for r in caplog.records]}"

    async def test_401_bad_token_returns_empty_with_warning(self, caplog):
        transport = _mock_transport({
            "/pulls": httpx.Response(401, json={"message": "Bad credentials"}),
        })
        with caplog.at_level(logging.WARNING, logger="services.topic_sources.dev_diary_source"):
            async with httpx.AsyncClient(transport=transport) as client:
                result = await _collect_merged_prs(
                    hours=24, repo="Glad-Labs/poindexter",
                    gh_token="bad_token", client=client,
                )
        assert result == []
        assert any(
            "401" in rec.getMessage() for rec in caplog.records
        ), f"expected a 401 warning, got: {[r.getMessage() for r in caplog.records]}"

    async def test_gh_token_set_as_authorization_bearer_header(self):
        captured: list[httpx.Request] = []
        transport = _mock_transport(
            {"/pulls": httpx.Response(200, json=[])},
            captured_requests=captured,
        )
        async with httpx.AsyncClient(transport=transport) as client:
            await _collect_merged_prs(
                hours=24, repo="Glad-Labs/poindexter",
                gh_token="ghp_secret_123", client=client,
            )
        assert len(captured) == 1
        assert captured[0].headers["Authorization"] == "Bearer ghp_secret_123"

    async def test_missing_token_logs_debug_and_omits_auth_header(self, caplog):
        captured: list[httpx.Request] = []
        transport = _mock_transport(
            {"/pulls": httpx.Response(200, json=[])},
            captured_requests=captured,
        )
        with caplog.at_level(logging.DEBUG, logger="services.topic_sources.dev_diary_source"):
            async with httpx.AsyncClient(transport=transport) as client:
                await _collect_merged_prs(
                    hours=24, repo="Glad-Labs/poindexter",
                    gh_token=None, client=client,
                )
        assert "Authorization" not in captured[0].headers
        assert any(
            "unauthenticated" in rec.getMessage().lower()
            for rec in caplog.records
        ), f"expected unauth debug log, got: {[r.getMessage() for r in caplog.records]}"


# ---------------------------------------------------------------------------
# _collect_notable_commits — async, talks to GitHub REST via httpx
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestCollectNotableCommits:
    async def test_filters_to_notable_prefixes_only(self):
        payload = [
            _gh_commit_payload(sha="abc12345" + "a" * 32, subject="feat: new feature"),
            _gh_commit_payload(sha="bcd23456" + "b" * 32, subject="fix: a bug"),
            _gh_commit_payload(sha="cde34567" + "c" * 32, subject="chore: bump deps"),
            _gh_commit_payload(sha="def45678" + "d" * 32, subject="docs: add readme line"),
            _gh_commit_payload(
                sha="efa56789" + "e" * 32, subject="refactor(svc): split file",
            ),
            _gh_commit_payload(
                sha="fab67890" + "f" * 32, subject="Not a CC commit at all",
            ),
        ]
        transport = _mock_transport({
            "/commits": httpx.Response(200, json=payload),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _collect_notable_commits(
                hours=24, repo="Glad-Labs/poindexter", client=client,
            )
        prefixes = [c["prefix"] for c in result]
        assert prefixes == ["feat", "fix", "refactor"]
        assert result[0]["sha"] == "abc12345"
        assert result[0]["author"] == "Matt"

    async def test_skips_merge_commits(self):
        payload = [
            _gh_commit_payload(
                sha="m" * 40, subject="feat: real feature", parents=2,
            ),
            _gh_commit_payload(
                sha="kept0000" + "0" * 32, subject="feat: kept", parents=1,
            ),
        ]
        transport = _mock_transport({
            "/commits": httpx.Response(200, json=payload),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _collect_notable_commits(
                hours=24, repo="Glad-Labs/poindexter", client=client,
            )
        assert [c["sha"] for c in result] == ["kept0000"]

    async def test_empty_when_api_returns_empty(self):
        transport = _mock_transport({
            "/commits": httpx.Response(200, json=[]),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _collect_notable_commits(
                hours=24, repo="Glad-Labs/poindexter", client=client,
            )
        assert result == []

    async def test_handles_network_error(self):
        def boom(request):
            raise httpx.ConnectError("dns failed")

        transport = httpx.MockTransport(boom)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _collect_notable_commits(
                hours=24, repo="Glad-Labs/poindexter", client=client,
            )
        assert result == []

    async def test_5xx_returns_empty_with_warning(self, caplog):
        transport = _mock_transport({
            "/commits": httpx.Response(500, text="boom"),
        })
        with caplog.at_level(logging.WARNING, logger="services.topic_sources.dev_diary_source"):
            async with httpx.AsyncClient(transport=transport) as client:
                result = await _collect_notable_commits(
                    hours=24, repo="Glad-Labs/poindexter", client=client,
                )
        assert result == []
        assert any(
            "500" in rec.getMessage() and "GitHub API" in rec.getMessage()
            for rec in caplog.records
        )


# ---------------------------------------------------------------------------
# gather_context — end-to-end against real Postgres (db_pool fixture)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def _seed_brain_decisions_table(db_pool):
    """Ensure the brain_decisions table exists in the test DB.

    The brain schema isn't part of the cofounder migrations — it's a
    separate daemon DB. For this test we create the table inline with
    the same shape ``test_taps_db.py`` uses (single source of truth).
    """
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS brain_decisions (
                id SERIAL PRIMARY KEY,
                decision TEXT,
                reasoning TEXT,
                context JSONB,
                confidence REAL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Clean slate per-test.
        await conn.execute("TRUNCATE brain_decisions RESTART IDENTITY")
    yield db_pool
    async with db_pool.acquire() as conn:
        await conn.execute("TRUNCATE brain_decisions RESTART IDENTITY")


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestGatherContextDB:
    async def test_high_confidence_brain_decisions_picked_up(
        self, _seed_brain_decisions_table,
    ):
        pool = _seed_brain_decisions_table
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO brain_decisions (decision, reasoning, confidence) "
                "VALUES ($1, $2, $3)",
                "Swap writer model from gemma to glm-4.7",
                "Approval rate dropped 30% on gemma run", 0.9,
            )
            await conn.execute(
                "INSERT INTO brain_decisions (decision, reasoning, confidence) "
                "VALUES ($1, $2, $3)",
                "Lower-confidence guess",
                "Not sure about this one", 0.5,
            )

        async def _empty_collector(*args, **kwargs):
            return []

        with (
            patch("services.topic_sources.dev_diary_source._collect_merged_prs",
                  _empty_collector),
            patch("services.topic_sources.dev_diary_source._collect_notable_commits",
                  _empty_collector),
        ):
            ctx = await DevDiarySource().gather_context(
                pool, hours_lookback=24, confidence_floor=0.7,
                gh_token="",
            )

        assert len(ctx.brain_decisions) == 1
        assert ctx.brain_decisions[0]["confidence"] == pytest.approx(0.9)
        assert "Swap writer model" in ctx.brain_decisions[0]["decision"]

    async def test_skips_brain_decisions_outside_window(
        self, _seed_brain_decisions_table,
    ):
        pool = _seed_brain_decisions_table
        old_ts = datetime.now(timezone.utc) - timedelta(hours=48)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO brain_decisions (decision, reasoning, confidence, created_at) "
                "VALUES ($1, $2, $3, $4)",
                "Stale decision", "Reason", 0.95, old_ts,
            )

        async def _empty_collector(*args, **kwargs):
            return []

        with (
            patch("services.topic_sources.dev_diary_source._collect_merged_prs",
                  _empty_collector),
            patch("services.topic_sources.dev_diary_source._collect_notable_commits",
                  _empty_collector),
        ):
            ctx = await DevDiarySource().gather_context(
                pool, hours_lookback=24, confidence_floor=0.7,
                gh_token="",
            )
        assert ctx.brain_decisions == []

    async def test_audit_resolved_picks_up_warning_with_followup(
        self, _seed_brain_decisions_table,
    ):
        pool = _seed_brain_decisions_table
        # The cofounder migrations DON'T create audit_log — that's the
        # init.sql layer. Skip cleanly if it's not there.
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'audit_log')"
            )
            if not exists:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id BIGSERIAL PRIMARY KEY,
                        timestamp TIMESTAMPTZ DEFAULT NOW(),
                        event_type VARCHAR(50) NOT NULL,
                        source VARCHAR(50) NOT NULL,
                        task_id VARCHAR(255),
                        details JSONB DEFAULT '{}'::jsonb,
                        severity VARCHAR(10) DEFAULT 'info'
                    )
                """)
            await conn.execute("TRUNCATE audit_log RESTART IDENTITY")
            now = datetime.now(timezone.utc)
            tid = str(uuid4())
            await conn.execute(
                "INSERT INTO audit_log (event_type, source, task_id, severity, timestamp) "
                "VALUES ($1, $2, $3, $4, $5)",
                "ollama_unhealthy", "ollama_resilience", tid, "warning",
                now - timedelta(hours=2),
            )
            await conn.execute(
                "INSERT INTO audit_log (event_type, source, task_id, severity, timestamp) "
                "VALUES ($1, $2, $3, $4, $5)",
                "ollama_unhealthy_resolved", "ollama_resilience", tid, "info",
                now - timedelta(hours=1),
            )
            # Unrelated warning, no follow-up — should NOT be returned.
            await conn.execute(
                "INSERT INTO audit_log (event_type, source, task_id, severity, timestamp) "
                "VALUES ($1, $2, $3, $4, $5)",
                "stuck_task", "task_executor", str(uuid4()), "warning",
                now - timedelta(hours=3),
            )

        async def _empty_collector(*args, **kwargs):
            return []

        with (
            patch("services.topic_sources.dev_diary_source._collect_merged_prs",
                  _empty_collector),
            patch("services.topic_sources.dev_diary_source._collect_notable_commits",
                  _empty_collector),
        ):
            ctx = await DevDiarySource().gather_context(
                pool, hours_lookback=24, gh_token="",
            )

        assert len(ctx.audit_resolved) == 1
        assert ctx.audit_resolved[0]["event_type"] == "ollama_unhealthy"
        assert ctx.audit_resolved[0]["severity"] == "warning"

    async def test_cost_summary_aggregates_per_model(
        self, _seed_brain_decisions_table,
    ):
        pool = _seed_brain_decisions_table
        # cost_logs.task_id column type varies across schema versions
        # (UUID in 0000_base_schema, VARCHAR in earlier ones). The
        # production schema is UUID, so build the row with a real
        # asyncpg-friendly UUID object.
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE cost_logs")
            tid = uuid4()
            for model, tokens, cost in [
                ("glm-4.7-5090", 5000, 0.0),
                ("claude-haiku-4-5", 1500, 0.0024),
                ("glm-4.7-5090", 7500, 0.0),
            ]:
                await conn.execute(
                    "INSERT INTO cost_logs (task_id, phase, model, provider, "
                    "total_tokens, cost_usd) "
                    "VALUES ($1::uuid, $2, $3, $4, $5, $6)",
                    str(tid), "draft", model, "ollama", tokens, cost,
                )

        async def _empty_collector(*args, **kwargs):
            return []

        with (
            patch("services.topic_sources.dev_diary_source._collect_merged_prs",
                  _empty_collector),
            patch("services.topic_sources.dev_diary_source._collect_notable_commits",
                  _empty_collector),
        ):
            ctx = await DevDiarySource().gather_context(
                pool, hours_lookback=24, gh_token="",
            )

        cost = ctx.cost_summary
        assert cost["total_inferences"] == 3
        assert cost["total_usd"] == pytest.approx(0.0024, abs=1e-6)
        models = {m["model"]: m for m in cost["by_model"]}
        assert models["glm-4.7-5090"]["inferences"] == 2
        assert models["glm-4.7-5090"]["tokens"] == 12500
        assert models["claude-haiku-4-5"]["inferences"] == 1


# ---------------------------------------------------------------------------
# extract() — TopicSource Protocol entry point
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestExtract:
    async def test_returns_single_topic_when_activity_present(self):
        source = DevDiarySource()

        async def fake_gather(self_, pool, **kwargs):
            return DevDiaryContext(
                date="2026-05-02",
                merged_prs=[{"number": 156, "title": "feat: gates engine"}],
                notable_commits=[],
                brain_decisions=[],
                audit_resolved=[],
                recent_posts=[],
                cost_summary={"total_usd": 0.0, "total_inferences": 0, "by_model": []},
            )

        with patch.object(DevDiarySource, "gather_context", fake_gather):
            topics = await source.extract(pool=None, config={})

        assert len(topics) == 1
        assert topics[0].source == "dev_diary"
        assert topics[0].category == "dev_diary"
        assert "2026-05-02" in topics[0].title
        assert topics[0].relevance_score > 0.5

    async def test_returns_empty_on_quiet_day(self):
        source = DevDiarySource()

        async def fake_gather(self_, pool, **kwargs):
            return DevDiaryContext(
                date="2026-05-02",
                merged_prs=[], notable_commits=[], brain_decisions=[],
                audit_resolved=[], recent_posts=[],
                cost_summary={"total_usd": 0.0, "total_inferences": 0, "by_model": []},
            )

        with patch.object(DevDiarySource, "gather_context", fake_gather):
            topics = await source.extract(pool=None, config={})

        assert topics == []


# ---------------------------------------------------------------------------
# gather_context — gh_token + gh_repo plumbing (no DB needed)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestGatherContextWiring:
    """Verify gh_token + gh_repo flow from the explicit overrides
    through the API collectors without touching the DB.

    The end-to-end DB-backed wiring (app_settings → plugins.secrets →
    _fetch_gh_token / _fetch_gh_repo) is exercised by integration
    tests. This class isolates the in-process plumbing so we don't
    need a Postgres fixture for it.
    """

    async def test_explicit_token_passed_through_to_collector(self):
        captured: dict = {}

        async def fake_collect_prs(hours, repo, gh_token=None, client=None):
            captured["hours"] = hours
            captured["repo"] = repo
            captured["gh_token"] = gh_token
            return []

        async def fake_collect_commits(hours, repo, gh_token=None, client=None):
            return []

        with (
            patch(
                "services.topic_sources.dev_diary_source._collect_merged_prs",
                fake_collect_prs,
            ),
            patch(
                "services.topic_sources.dev_diary_source._collect_notable_commits",
                fake_collect_commits,
            ),
        ):
            ctx = await DevDiarySource().gather_context(
                pool=None,
                hours_lookback=12,
                gh_token="explicit_test_token",
                gh_repo="Glad-Labs/poindexter",
            )

        assert captured["gh_token"] == "explicit_test_token"
        assert captured["hours"] == 12
        assert captured["repo"] == "Glad-Labs/poindexter"
        assert ctx.merged_prs == []

    async def test_pool_none_means_no_token_fetch_attempt(self):
        """With pool=None and no explicit token, the source must not
        crash trying to fetch the secret — it falls back to empty."""
        captured: dict = {}

        async def fake_collect_prs(hours, repo, gh_token=None, client=None):
            captured["gh_token"] = gh_token
            captured["repo"] = repo
            return []

        async def fake_collect_commits(hours, repo, gh_token=None, client=None):
            return []

        with (
            patch(
                "services.topic_sources.dev_diary_source._collect_merged_prs",
                fake_collect_prs,
            ),
            patch(
                "services.topic_sources.dev_diary_source._collect_notable_commits",
                fake_collect_commits,
            ),
        ):
            await DevDiarySource().gather_context(pool=None)

        # Empty string from _fetch_gh_token's pool-None short-circuit.
        assert captured["gh_token"] == ""
        # Default repo when no overrides + no pool. Pinned to the public
        # OSS repo so the dev_diary source is honest about its origin on
        # fresh installs (was glad-labs-stack pre-2026-05-14).
        assert captured["repo"] == "Glad-Labs/poindexter"

    async def test_site_config_supplies_gh_repo(self):
        captured: dict = {}

        async def fake_collect_prs(hours, repo, gh_token=None, client=None):
            captured["repo"] = repo
            return []

        async def fake_collect_commits(hours, repo, gh_token=None, client=None):
            return []

        class _FakeSiteConfig:
            def get(self, key, default=None):
                if key == "gh_repo":
                    return "operator-fork/example"
                return default

        with (
            patch(
                "services.topic_sources.dev_diary_source._collect_merged_prs",
                fake_collect_prs,
            ),
            patch(
                "services.topic_sources.dev_diary_source._collect_notable_commits",
                fake_collect_commits,
            ),
        ):
            await DevDiarySource().gather_context(
                pool=None, site_config=_FakeSiteConfig(),
            )

        assert captured["repo"] == "operator-fork/example"

    async def test_constructor_gh_repo_used_when_no_other_source(self):
        captured: dict = {}

        async def fake_collect_prs(hours, repo, gh_token=None, client=None):
            captured["repo"] = repo
            return []

        async def fake_collect_commits(hours, repo, gh_token=None, client=None):
            return []

        with (
            patch(
                "services.topic_sources.dev_diary_source._collect_merged_prs",
                fake_collect_prs,
            ),
            patch(
                "services.topic_sources.dev_diary_source._collect_notable_commits",
                fake_collect_commits,
            ),
        ):
            await DevDiarySource(gh_repo="ctor/repo").gather_context(pool=None)

        assert captured["repo"] == "ctor/repo"


# ---------------------------------------------------------------------------
# Smoke serialization — the writer expects dict shapes to be JSON-safe
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dict_output_is_json_serializable():
    ctx = DevDiaryContext(
        date="2026-05-06",
        merged_prs=[{"number": 1, "title": "feat: x", "url": "u",
                     "merged_at": "2026-05-06T12:00:00Z",
                     "author": "matty", "body": "b"}],
        notable_commits=[{"sha": "abc12345", "subject": "feat: y",
                          "prefix": "feat", "author": "Matt",
                          "date": "2026-05-06T12:00:00Z"}],
        brain_decisions=[],
        audit_resolved=[],
        recent_posts=[],
        cost_summary={"total_usd": 0.0, "total_inferences": 0, "by_model": []},
    )
    blob = json.dumps(ctx.to_dict())
    assert "feat: x" in blob
    assert "abc12345" in blob
