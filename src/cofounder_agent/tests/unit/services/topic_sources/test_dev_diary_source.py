"""Unit tests for DevDiarySource.

Two layers:

1. **Pure-function helpers** (``_collect_merged_prs``, ``_collect_notable_commits``,
   ``DevDiaryContext``) — no DB, no network, no subprocess. Patched
   subprocess output covers the gh + git collectors.
2. **End-to-end ``gather_context``** — uses the ``db_pool`` fixture
   (a real Postgres database with all migrations applied) so we
   actually exercise the brain_decisions / audit_log / posts /
   cost_logs queries against real schema. Subprocess calls are
   patched out.

Per Matt's directive (PR #155): no row-faker MagicMocks for DB
behavior. The ``db_pool`` fixture provides a real pool.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio

from plugins.topic_source import TopicSource
from services.topic_sources.dev_diary_source import (
    DevDiaryContext,
    DevDiarySource,
    _collect_merged_prs,
    _collect_notable_commits,
    _CC_RE,
    _NOTABLE_COMMIT_PREFIXES,
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

    def test_headline_uses_top_pr(self):
        ctx = self._empty()
        ctx.merged_prs = [{"title": "feat: per-medium approval gate engine"}]
        h = ctx.headline()
        assert "2026-05-02" in h
        assert "per-medium approval gate engine" in h

    def test_headline_falls_back_to_commit(self):
        ctx = self._empty()
        ctx.notable_commits = [{"subject": "fix: stuck pipeline retry loop"}]
        h = ctx.headline()
        assert "stuck pipeline retry loop" in h

    def test_headline_generic_when_empty(self):
        h = self._empty().headline()
        assert h == "Daily dev diary — 2026-05-02"


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
# Subprocess collectors (mocked subprocess.run)
# ---------------------------------------------------------------------------


def _fake_subprocess_run(stdout: str, returncode: int = 0):
    """Build a callable that mimics subprocess.run with the given output."""
    class _Result:
        def __init__(self):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode
    return lambda *a, **kw: _Result()


class _CapturingSubprocess:
    """subprocess.run double that records the kwargs of the most recent call.

    Used to assert that ``env`` (and other args) propagate correctly
    from the collector callsites down into ``subprocess.run``. Returns
    a stdout-only fake result so existing parsing logic still works.
    """

    def __init__(self, stdout: str = "", returncode: int = 0):
        self._stdout = stdout
        self._returncode = returncode
        self.last_kwargs: dict = {}
        self.last_args: tuple = ()

    def __call__(self, *args, **kwargs):
        self.last_args = args
        self.last_kwargs = kwargs

        class _Result:
            def __init__(self_inner):
                self_inner.stdout = self._stdout
                self_inner.stderr = ""
                self_inner.returncode = self._returncode

        return _Result()


@pytest.mark.unit
class TestCollectMergedPRs:
    def test_returns_empty_when_gh_not_installed(self):
        with patch("services.topic_sources.dev_diary_source.subprocess.run",
                   side_effect=FileNotFoundError("gh")):
            result = _collect_merged_prs(hours=24, repo_root="/tmp")
        assert result == []

    def test_returns_empty_when_gh_returns_nothing(self):
        with patch("services.topic_sources.dev_diary_source.subprocess.run",
                   _fake_subprocess_run("", returncode=0)):
            result = _collect_merged_prs(hours=24, repo_root=None)
        assert result == []

    def test_parses_gh_json_output(self):
        gh_output = json.dumps([
            {
                "number": 156,
                "title": "feat(gates): per-medium approval gate engine",
                "url": "https://github.com/Glad-Labs/poindexter/pull/156",
                "mergedAt": "2026-05-01T12:00:00Z",
                "author": {"login": "matty"},
            },
            {
                "number": 155,
                "title": "test: tighten _make_row helpers",
                "url": "https://github.com/Glad-Labs/poindexter/pull/155",
                "mergedAt": "2026-05-01T11:30:00Z",
                "author": {"login": "matty"},
            },
        ])
        with patch("services.topic_sources.dev_diary_source.subprocess.run",
                   _fake_subprocess_run(gh_output)):
            result = _collect_merged_prs(hours=24, repo_root=None)
        assert len(result) == 2
        assert result[0]["number"] == 156
        assert result[0]["title"] == "feat(gates): per-medium approval gate engine"
        assert result[0]["author"] == "matty"
        assert "Glad-Labs/poindexter/pull/156" in result[0]["url"]

    def test_handles_malformed_json_gracefully(self):
        with patch("services.topic_sources.dev_diary_source.subprocess.run",
                   _fake_subprocess_run("not valid json {{{")):
            result = _collect_merged_prs(hours=24, repo_root=None)
        assert result == []

    def test_handles_missing_author_dict(self):
        gh_output = json.dumps([{
            "number": 1, "title": "x", "url": "y", "mergedAt": "z", "author": None,
        }])
        with patch("services.topic_sources.dev_diary_source.subprocess.run",
                   _fake_subprocess_run(gh_output)):
            result = _collect_merged_prs(hours=24, repo_root=None)
        assert result[0]["author"] == ""

    def test_gh_token_exported_into_subprocess_env(self):
        """A non-empty gh_token must land in the subprocess env as
        ``GH_TOKEN`` (and ``GITHUB_TOKEN``) so ``gh pr list`` authenticates.

        Closes Glad-Labs/poindexter#348 — the worker now ships with
        ``gh`` installed but the secret has to be plumbed into the
        subprocess env (NOT the worker process env, NOT the Docker
        layer) for each call. Verify the wiring with a capturing
        subprocess double.
        """
        capture = _CapturingSubprocess(stdout="[]", returncode=0)
        with patch("services.topic_sources.dev_diary_source.subprocess.run",
                   capture):
            _collect_merged_prs(
                hours=24, repo_root=None, gh_token="ghp_secret_123",
            )
        env = capture.last_kwargs.get("env")
        assert env is not None, "subprocess.run was called without env="
        assert env.get("GH_TOKEN") == "ghp_secret_123"
        # gh respects either GH_TOKEN or GITHUB_TOKEN — set both so
        # nested invocations / scripts reading either name authenticate.
        assert env.get("GITHUB_TOKEN") == "ghp_secret_123"
        # Inherited env keys (e.g. PATH) must still be present —
        # _run_subprocess merges on top of os.environ rather than
        # replacing it. PATH is the canary because gh + git both
        # need it to resolve their helper binaries.
        import os as _os
        if "PATH" in _os.environ:
            assert env.get("PATH") == _os.environ["PATH"]

    def test_no_gh_token_means_no_env_override(self):
        """When gh_token is empty/None, subprocess.run must be called with
        ``env=None`` so the child inherits the worker env unmodified
        (not a stripped-down dict missing PATH/HOME)."""
        capture = _CapturingSubprocess(stdout="[]", returncode=0)
        with patch("services.topic_sources.dev_diary_source.subprocess.run",
                   capture):
            _collect_merged_prs(hours=24, repo_root=None, gh_token=None)
        assert capture.last_kwargs.get("env") is None

        capture2 = _CapturingSubprocess(stdout="[]", returncode=0)
        with patch("services.topic_sources.dev_diary_source.subprocess.run",
                   capture2):
            _collect_merged_prs(hours=24, repo_root=None, gh_token="")
        assert capture2.last_kwargs.get("env") is None


@pytest.mark.unit
class TestCollectNotableCommits:
    def test_filters_to_notable_prefixes_only(self):
        # tab-separated %H \t %s \t %an \t %aI
        git_output = "\n".join([
            "abc12345aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tfeat: new feature\tMatt\t2026-05-01T12:00:00Z",
            "bcd23456bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\tfix: a bug\tMatt\t2026-05-01T11:00:00Z",
            "cde34567cccccccccccccccccccccccccccccccc\tchore: bump deps\tMatt\t2026-05-01T10:00:00Z",
            "def45678dddddddddddddddddddddddddddddddd\tdocs: add readme line\tMatt\t2026-05-01T09:00:00Z",
            "efa56789eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee\trefactor(svc): split file\tMatt\t2026-05-01T08:00:00Z",
            "fab67890ffffffffffffffffffffffffffffffff\tNot a CC commit at all\tMatt\t2026-05-01T07:00:00Z",
        ])
        with patch("services.topic_sources.dev_diary_source.subprocess.run",
                   _fake_subprocess_run(git_output)):
            result = _collect_notable_commits(hours=24, repo_root=None)
        # Should keep feat, fix, refactor; drop chore, docs, non-CC.
        prefixes = [c["prefix"] for c in result]
        assert prefixes == ["feat", "fix", "refactor"]
        assert result[0]["sha"] == "abc12345"
        assert result[0]["author"] == "Matt"

    def test_empty_when_git_returns_nothing(self):
        with patch("services.topic_sources.dev_diary_source.subprocess.run",
                   _fake_subprocess_run("", returncode=0)):
            result = _collect_notable_commits(hours=24, repo_root=None)
        assert result == []

    def test_handles_git_not_installed(self):
        with patch("services.topic_sources.dev_diary_source.subprocess.run",
                   side_effect=FileNotFoundError("git")):
            result = _collect_notable_commits(hours=24, repo_root=None)
        assert result == []


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

        with (
            patch("services.topic_sources.dev_diary_source._collect_merged_prs",
                  return_value=[]),
            patch("services.topic_sources.dev_diary_source._collect_notable_commits",
                  return_value=[]),
        ):
            ctx = await DevDiarySource().gather_context(
                pool, hours_lookback=24, confidence_floor=0.7,
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

        with (
            patch("services.topic_sources.dev_diary_source._collect_merged_prs",
                  return_value=[]),
            patch("services.topic_sources.dev_diary_source._collect_notable_commits",
                  return_value=[]),
        ):
            ctx = await DevDiarySource().gather_context(
                pool, hours_lookback=24, confidence_floor=0.7,
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

        with (
            patch("services.topic_sources.dev_diary_source._collect_merged_prs",
                  return_value=[]),
            patch("services.topic_sources.dev_diary_source._collect_notable_commits",
                  return_value=[]),
        ):
            ctx = await DevDiarySource().gather_context(
                pool, hours_lookback=24,
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

        with (
            patch("services.topic_sources.dev_diary_source._collect_merged_prs",
                  return_value=[]),
            patch("services.topic_sources.dev_diary_source._collect_notable_commits",
                  return_value=[]),
        ):
            ctx = await DevDiarySource().gather_context(pool, hours_lookback=24)

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
# gather_context — gh_token plumbing (no DB needed)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestGatherContextGhTokenWiring:
    """Verify gh_token flows from the explicit override through
    _collect_merged_prs without touching the DB.

    The end-to-end DB-backed wiring (app_settings → plugins.secrets →
    _fetch_gh_token) is exercised by TestCollectMergedPRs above for
    the subprocess-side, and by integration tests for the secret
    decryption side. This test class isolates the in-process plumbing
    so we don't need a Postgres fixture for it.
    """

    async def test_explicit_token_passed_through_to_collector(self):
        captured: dict = {}

        def fake_collect_prs(hours, repo_root, gh_token=None):
            captured["hours"] = hours
            captured["repo_root"] = repo_root
            captured["gh_token"] = gh_token
            return []

        with (
            patch(
                "services.topic_sources.dev_diary_source._collect_merged_prs",
                fake_collect_prs,
            ),
            patch(
                "services.topic_sources.dev_diary_source._collect_notable_commits",
                return_value=[],
            ),
        ):
            ctx = await DevDiarySource().gather_context(
                pool=None,
                hours_lookback=12,
                gh_token="explicit_test_token",
            )

        assert captured["gh_token"] == "explicit_test_token"
        assert captured["hours"] == 12
        assert ctx.merged_prs == []

    async def test_pool_none_means_no_token_fetch_attempt(self):
        """With pool=None and no explicit token, the source must not
        crash trying to fetch the secret — it falls back to empty."""
        captured: dict = {}

        def fake_collect_prs(hours, repo_root, gh_token=None):
            captured["gh_token"] = gh_token
            return []

        with (
            patch(
                "services.topic_sources.dev_diary_source._collect_merged_prs",
                fake_collect_prs,
            ),
            patch(
                "services.topic_sources.dev_diary_source._collect_notable_commits",
                return_value=[],
            ),
        ):
            await DevDiarySource().gather_context(pool=None)

        # Empty string from _fetch_gh_token's pool-None short-circuit.
        assert captured["gh_token"] == ""
