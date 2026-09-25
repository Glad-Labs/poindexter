"""Unit tests — the branch-drift canary pages its own failure once per episode.

The incident (2026-09-23 → 25): ``gh_token`` was replaced at 23:13 UTC with a
token that cannot see the private repo, and from 23:37 every pass failed with
``GitHub /commits/main returned 404``. The canary routed every GitHub error
through ``_fail(page=False)``, so each one landed in audit_log only: 99
``probe.branch_drift_failed`` rows on 2026-09-24, 0 ``operator_paged`` rows.
The deploy canary was blind and nobody was told. The same 404 had blinded
it for about three hours on 2026-08-17, just as silently.

These tests pin the replacement. Credential and configuration failures are
LOUD and page once per episode, persisted in ``brain_knowledge``. The page
repeats only when the failure changes, when a replaced token fails too, when
the last page reached no channel, and on the reminder. Transient failures
(5xx, timeouts, DNS, rate limits) are QUIET: they stay audit-only unless they
last ``branch_drift_transient_failure_page_hours``. One recovery note follows
when the canary runs again.

The pool is a small STATEFUL fake (unlike the MagicMock pool in
``test_branch_drift_probe.py``) because an episode only exists across passes:
what one pass writes to ``brain_knowledge`` the next must read back. The same
shape as ``test_pr_staleness_probe_failure_episodes.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from poindexter.brain import branch_drift_probe as bdp
from poindexter.brain import failure_episode as fe
from poindexter.brain import operator_notifier as on

REPO = "Test-Org/test-repo"
_T0 = datetime(2026, 9, 23, 23, 37, 0, tzinfo=UTC)
_TOKEN_SET_AT = datetime(2026, 9, 23, 23, 13, 20, tzinfo=UTC)
_LOCAL_HEAD = "abbad234cfa31863c8c43b4587784771d9a76612"
_MAIN_SHA = "80d9f033ca20fd1987f3f2821488f0115562ed83"
# Verbatim from prod's audit_log, 2026-09-25 17:27 UTC.
_NOT_FOUND = json.dumps({
    "message": "Not Found",
    "documentation_url": "https://docs.github.com/rest/commits/commits#get-a-commit",
    "status": "404",
})
_BAD_CREDENTIALS = json.dumps({"message": "Bad credentials"})
_UNICORN = "<!DOCTYPE html>\n<!--\n\nHello future GitHubber! I bet you're here to remove"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self, start: datetime = _T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)


class _FakeDB:
    """asyncpg-pool stand-in holding the rows this probe reads and writes."""

    def __init__(self, *, settings: dict[str, str] | None = None) -> None:
        self.settings: dict[str, str] = {
            bdp.ENABLED_KEY: "true",
            bdp.POLL_INTERVAL_MINUTES_KEY: "15",
            bdp.REPO_KEY: REPO,
            bdp.DEDUP_HOURS_KEY: "6",
            bdp.GIT_DIR_KEY: "/host-git",
            bdp.MIN_COMMITS_BEHIND_KEY: "3",
            bdp.FAILURE_REPAGE_HOURS_KEY: "24",
            bdp.TRANSIENT_FAILURE_PAGE_HOURS_KEY: "6",
            "gh_token": "test-token",
            **(settings or {}),
        }
        # app_settings.updated_at of the gh_token row — moves when it is replaced.
        self.token_changed_at: datetime | None = _TOKEN_SET_AT
        self.knowledge: dict[tuple[str, str], str] = {}
        self.knowledge_deletes = 0
        self.audit: list[tuple[str, dict[str, Any]]] = []
        self.alert_events: list[tuple[Any, ...]] = []
        self.dedup: dict[str, datetime] = {}

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "FROM brain_knowledge" in query:
            return self.knowledge.get((args[0], args[1]))
        if "SELECT updated_at FROM app_settings" in query:
            return self.token_changed_at
        if "FROM app_settings" in query:
            return self.settings.get(args[0])
        return None

    async def fetchrow(self, query: str, *args: Any) -> Any:
        # brain.secret_reader reads the token as (value, is_secret).
        if "FROM app_settings" in query:
            key = args[0]
            if key in self.settings:
                return {"value": self.settings[key], "is_secret": False}
            return None
        if "FROM alert_dedup_state" in query:
            seen = self.dedup.get(args[0])
            return {"last_seen_at": seen} if seen else None
        return None

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO brain_knowledge" in query:
            self.knowledge[(args[0], args[1])] = args[2]
        elif "DELETE FROM brain_knowledge" in query:
            self.knowledge_deletes += 1
            self.knowledge.pop((args[0], args[1]), None)
        elif "INSERT INTO audit_log" in query:
            self.audit.append((args[0], json.loads(args[2])))
        elif "INSERT INTO alert_events" in query:
            self.alert_events.append(args)
        elif "INSERT INTO alert_dedup_state" in query:
            self.dedup[args[0]] = args[1]
        return "OK"

    def audit_of(self, event: str) -> list[dict[str, Any]]:
        return [details for name, details in self.audit if name == event]

    def episode(self) -> dict[str, Any] | None:
        raw = self.knowledge.get((bdp.FAILURE_STATE_ENTITY, f"failure_episode:{REPO}"))
        return json.loads(raw) if raw else None


class _Resp:
    def __init__(
        self,
        status_code: int,
        payload: Any = None,
        *,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self) -> Any:
        return self._payload


class _FakeGitHub:
    """Scriptable GitHub: set ``main`` / ``compare`` to a response or an
    exception between passes; ``requests`` counts every round-trip."""

    def __init__(self) -> None:
        self.main: Any = _Resp(200, {"sha": _MAIN_SHA})
        self.compare: Any = _Resp(200, {"status": "behind", "ahead_by": 0})
        self.requests = 0

    def factory(self) -> _FakeGitHub:
        return self

    async def __aenter__(self) -> _FakeGitHub:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def get(self, url: str, params: dict[str, Any] | None = None) -> _Resp:
        self.requests += 1
        answer = self.compare if "/compare/" in url else self.main
        if isinstance(answer, BaseException):
            raise answer
        return answer


class _Notifier:
    """Records pages; answers the way ``notify_operator`` does."""

    def __init__(self, result: dict[str, str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result or {
            "telegram": "skipped (severity below error)",
            "discord": "discord",
            "alerts_log": "alerts.log (test)",
        }

    def __call__(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return dict(self.result)

    def titles(self) -> list[str]:
        return [c["title"] for c in self.calls]


class _Git:
    """git_runner stand-in: the prod checkout sits on main unless told otherwise."""

    def __init__(self) -> None:
        self.head = _MAIN_SHA
        self.error: BaseException | None = None

    def __call__(self, _git_dir: str) -> tuple[str, str]:
        if self.error is not None:
            raise self.error
        return self.head, "main"


async def _run(
    db: _FakeDB, gh: _FakeGitHub, clock: _Clock, notify: Any, git: _Git | None = None,
) -> dict[str, Any]:
    return await bdp.run_branch_drift_probe(
        db, now_fn=clock, notify_fn=notify, http_client_factory=gh.factory,
        git_runner=git or _Git(),
    )


async def _passes(
    n: int, db: _FakeDB, gh: _FakeGitHub, clock: _Clock, notify: Any, *,
    every: timedelta = timedelta(minutes=15), git: _Git | None = None,
) -> list[dict[str, Any]]:
    """Run ``n`` passes ``every`` apart; the clock ends one step past the last."""
    out = []
    for _ in range(n):
        out.append(await _run(db, gh, clock, notify, git))
        clock.now += every
    return out


@pytest.fixture(autouse=True)
def _clear_module_state(monkeypatch):
    bdp._reset_state()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    yield
    bdp._reset_state()


def _real_notifier(monkeypatch, clock: _Clock) -> list[str]:
    """Route ``notify_operator`` to a Discord stub with the prod 30-min
    cooldown, its clock tied to the test clock. Returns the sent messages."""
    sent: list[str] = []

    def _discord(text: str) -> tuple[bool, str]:
        sent.append(text)
        return True, "discord"

    monkeypatch.setattr(on, "_try_discord", _discord)
    monkeypatch.setattr(on, "_try_telegram", lambda text: (False, "not configured"))
    monkeypatch.setattr(on, "_append_alerts_log", lambda text: (True, "alerts.log (test)"))
    monkeypatch.setattr(on, "_NOTIFY_AUDIT_SINK", None)
    monkeypatch.setattr(on, "_LAST_PAGED_AT", {})
    monkeypatch.setattr(on, "_PAGE_COOLDOWN_SECONDS", 30 * 60)
    # Only the notifier's own view of time — asyncio keeps the real clock.
    monkeypatch.setattr(on, "time", SimpleNamespace(monotonic=lambda: clock.now.timestamp()))
    return sent


# ---------------------------------------------------------------------------
# The incident, replayed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTheBlindCanaryIsReported:
    @pytest.mark.asyncio
    async def test_a_day_of_404s_pages_once_and_retries_every_15_min(self):
        """288 five-minute brain cycles against a token that can't see the
        repo: one page (the old path sent none), 96 GitHub round-trips, and
        every cycle reports ok=False."""
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(404, text=_NOT_FOUND)

        results = await _passes(288, db, gh, clock, notify, every=timedelta(minutes=5))

        assert len(notify.calls) == 1, notify.titles()
        page = notify.calls[0]
        assert page["title"] == f"Branch-drift canary cannot run against {REPO}"
        assert page["severity"] == "warning"
        assert page["source"] == "brain.branch_drift_probe"
        assert f"The gh_token cannot see {REPO}, check its scopes." in page["detail"]
        assert "404, not 403" in page["detail"]
        assert f"needs Contents (read) on {REPO}" in page["detail"]
        assert "poindexter settings set gh_token <token> --secret" in page["detail"]
        assert "prod falling behind origin/main goes unnoticed" in page["detail"]
        assert page["dedup_key"] == f"branch_drift_failed:{REPO}:commits/main:404:0"

        assert gh.requests == 96
        failed = db.audit_of("probe.branch_drift_failed")
        assert len(failed) == 96
        assert [f["page_reason"] for f in failed] == [fe.PAGE_NEW] + [None] * 95
        assert failed[0]["paged"] is True
        assert failed[0]["transient"] is False
        # A broken canary must never read as healthy — skipped cycles included.
        assert [r["ok"] for r in results] == [False] * 288
        skipped = [r for r in results if r["status"] == "skipped"]
        assert len(skipped) == 192
        assert "last attempt failed: The gh_token cannot see" in skipped[0]["detail"]
        assert db.alert_events == []

        episode = db.episode()
        assert episode is not None
        assert episode["attempts"] == 96
        assert episode["pages"] == 1
        assert episode["signature"] == "commits/main:404"
        assert episode["since"] == _T0.isoformat()

    @pytest.mark.asyncio
    async def test_the_real_notifier_sends_one_discord_message(self, monkeypatch):
        """End to end through ``notify_operator`` with the prod 30-min cooldown.
        Paging every pass would still have reached Discord 48 times a day (one
        per cooldown window, more after each brain restart); the episode sends
        one message."""
        clock = _Clock()
        sent = _real_notifier(monkeypatch, clock)
        db, gh = _FakeDB(), _FakeGitHub()
        gh.main = _Resp(404, text=_NOT_FOUND)

        await _passes(288, db, gh, clock, on.notify_operator, every=timedelta(minutes=5))

        assert len(sent) == 1
        assert f"The gh_token cannot see {REPO}, check its scopes." in sent[0]
        assert "***" not in sent[0]  # survived the notifier's credential redaction

    @pytest.mark.asyncio
    async def test_a_brain_restart_does_not_repage(self):
        """The episode lives in brain_knowledge, so a restart (in-memory state
        wiped, cadence gate open) finds it and stays quiet."""
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(404, text=_NOT_FOUND)

        await _run(db, gh, clock, notify)
        for _ in range(3):  # deploy-sync rebuilt the brain three times
            bdp._reset_state()
            clock.advance(minutes=4)
            summary = await _run(db, gh, clock, notify)
            assert summary["status"] == "failed"
            assert summary["page_reason"] is None

        assert len(notify.calls) == 1
        assert gh.requests == 4


# ---------------------------------------------------------------------------
# Transient failures stay quiet unless the canary stays blind
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransientFailures:
    @pytest.mark.asyncio
    async def test_a_5xx_spell_stays_audit_only(self):
        """2026-07-16 in prod: five passes of GitHub 503 ("Unicorn!")."""
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(503, text=_UNICORN)

        results = await _passes(20, db, gh, clock, notify)  # 5 hours

        assert notify.calls == []
        assert all(r["ok"] is False and r["transient"] is True for r in results)
        failed = db.audit_of("probe.branch_drift_failed")
        assert len(failed) == 20
        assert failed[0]["signature"] == "commits/main:5xx"
        assert "GitHub-side error" in failed[0]["detail"]
        assert "DOCTYPE" not in failed[0]["detail"]
        assert db.episode()["paged_at"] is None

    @pytest.mark.asyncio
    async def test_a_transient_failure_that_lasts_pages_once(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(502, text=_UNICORN)

        results = await _passes(24, db, gh, clock, notify)  # up to t0 + 5h45m
        assert notify.calls == []
        results += await _passes(1, db, gh, clock, notify)  # t0 + 6h
        assert results[-1]["page_reason"] == fe.PAGE_PERSISTING
        assert len(notify.calls) == 1
        page = notify.calls[0]
        assert page["title"] == f"Branch-drift canary cannot run against {REPO}"
        assert "only once it has lasted 6h. This one has." in page["detail"]
        assert "Failing since 2026-09-23 23:37 UTC (25 attempts)" in page["detail"]

        await _passes(40, db, gh, clock, notify)  # 10 more hours: quiet
        assert len(notify.calls) == 1

    @pytest.mark.asyncio
    async def test_flapping_between_transient_kinds_is_one_blind_spell(self):
        """A 502, a timeout and a DNS failure in rotation never page on their
        own, but the canary has been blind all along, so 6h in it says so."""
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        kinds = [
            _Resp(502, text=_UNICORN),
            httpx.ConnectTimeout(""),
            httpx.ConnectError("[Errno -3] Temporary failure in name resolution"),
        ]
        for i in range(25):
            gh.main = kinds[i % 3]
            await _run(db, gh, clock, notify)
            clock.advance(minutes=15)

        assert len(notify.calls) == 1
        assert "only once it has lasted 6h" in notify.calls[0]["detail"]

    @pytest.mark.asyncio
    async def test_one_clean_pass_resets_the_transient_clock(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(503, text=_UNICORN)
        await _passes(20, db, gh, clock, notify)
        gh.main = _Resp(200, {"sha": _MAIN_SHA})
        await _passes(1, db, gh, clock, notify)
        gh.main = _Resp(503, text=_UNICORN)
        await _passes(20, db, gh, clock, notify)

        assert notify.calls == []  # never 6h unbroken, and nobody to tell of recovery

    @pytest.mark.asyncio
    async def test_zero_hours_means_a_transient_failure_never_pages(self):
        db = _FakeDB(settings={bdp.TRANSIENT_FAILURE_PAGE_HOURS_KEY: "0"})
        gh, clock, notify = _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(503, text=_UNICORN)

        await _passes(120, db, gh, clock, notify)  # 30 hours

        assert notify.calls == []

    @pytest.mark.asyncio
    async def test_a_rate_limit_is_transient(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(
            403, text=json.dumps({"message": "API rate limit exceeded for user ID 1."}),
            headers={"x-ratelimit-remaining": "0"},
        )

        summary = await _run(db, gh, clock, notify)

        assert summary["failure_signature"] == "commits/main:rate-limited"
        assert summary["transient"] is True
        assert notify.calls == []

    @pytest.mark.asyncio
    async def test_a_silent_timeout_is_named_by_its_class(self):
        """str(httpx.ReadTimeout) is empty; prod logged 'GitHub API error for
        …: ' with nothing after it on five days."""
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = httpx.ReadTimeout("")

        summary = await _run(db, gh, clock, notify)

        assert summary["failure_signature"] == "ReadTimeout"
        assert summary["detail"].startswith("ReadTimeout. The canary's GitHub round-trip")
        assert notify.calls == []


# ---------------------------------------------------------------------------
# When a failing canary pages again
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRepageTriggers:
    @pytest.mark.asyncio
    async def test_a_503_blip_inside_a_404_outage_does_not_repage(self):
        """The comparison is with what the operator was told, not with the
        previous pass: 404, 503, 404 is still the one 404 they heard about."""
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        for answer in (_Resp(404, text=_NOT_FOUND), _Resp(503, text=_UNICORN),
                       _Resp(404, text=_NOT_FOUND), httpx.ConnectTimeout(""),
                       _Resp(404, text=_NOT_FOUND)):
            gh.main = answer
            await _run(db, gh, clock, notify)
            clock.advance(minutes=15)

        assert len(notify.calls) == 1

    @pytest.mark.asyncio
    async def test_a_credential_failure_after_a_transient_spell_pages_at_once(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(503, text=_UNICORN)
        await _passes(8, db, gh, clock, notify)  # 2h: quiet
        gh.main = _Resp(404, text=_NOT_FOUND)
        summary = await _run(db, gh, clock, notify)

        assert summary["page_reason"] == fe.PAGE_NEW
        assert len(notify.calls) == 1
        # Counted from the first failure: the canary has been blind since then.
        assert "Failing since 2026-09-23 23:37 UTC (9 attempts)" in notify.calls[0]["detail"]

    @pytest.mark.asyncio
    async def test_a_changed_failure_is_news(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(404, text=_NOT_FOUND)
        await _run(db, gh, clock, notify)

        clock.advance(minutes=15)
        gh.main = _Resp(401, text=_BAD_CREDENTIALS)
        changed = await _run(db, gh, clock, notify)

        assert changed["page_reason"] == fe.PAGE_CHANGED
        assert len(notify.calls) == 2
        detail = notify.calls[1]["detail"]
        assert "The failure changed (was commits/main:404, now commits/main:401)." in detail
        assert "HTTP 401: Bad credentials" in detail
        assert "invalid, expired or revoked" in detail
        assert db.episode()["since"] == _T0.isoformat()

    @pytest.mark.asyncio
    async def test_a_replaced_token_that_still_fails_is_reported_on_the_next_pass(
        self, monkeypatch,
    ):
        """Through the real notifier: the canary retries every 15 min, inside
        the notifier's 30-min cooldown, so a per-failure dedup key would have
        swallowed this page and counted it as delivered."""
        clock = _Clock()
        sent = _real_notifier(monkeypatch, clock)
        db, gh = _FakeDB(), _FakeGitHub()
        gh.main = _Resp(404, text=_NOT_FOUND)
        await _run(db, gh, clock, on.notify_operator)

        clock.advance(minutes=5)
        db.token_changed_at = clock.now  # the operator rotates the token…
        clock.advance(minutes=10)
        still = await _run(db, gh, clock, on.notify_operator)  # …to another blind one

        assert still["page_reason"] == fe.PAGE_TOKEN_REPLACED
        assert still["paged"] is True
        assert len(sent) == 2
        assert (
            "The gh_token was replaced at 2026-09-23 23:42 UTC, and the new token "
            "fails the same way." in sent[1]
        )

        clock.advance(minutes=15)
        await _run(db, gh, clock, on.notify_operator)
        assert len(sent) == 2  # same token, same failure: quiet again

    @pytest.mark.asyncio
    async def test_an_undelivered_page_is_retried_next_pass(self):
        db, gh, clock = _FakeDB(), _FakeGitHub(), _Clock()
        gh.main = _Resp(404, text=_NOT_FOUND)
        notify = _Notifier({
            "telegram": "skipped (severity below error)",
            "discord": "discord send failed: URLError('name resolution')",
        })

        first = await _run(db, gh, clock, notify)
        assert first["paged"] is False
        assert db.episode()["paged_at"] is None
        assert db.episode()["owed"] == fe.PAGE_NEW

        notify.result = {"telegram": "skipped (severity below error)", "discord": "discord"}
        clock.advance(minutes=15)
        second = await _run(db, gh, clock, notify)
        assert second["page_reason"] == fe.PAGE_UNDELIVERED
        assert second["paged"] is True
        assert "The previous page about this failure reached no channel." in notify.calls[1]["detail"]
        assert db.episode()["owed"] is None

        clock.advance(minutes=15)
        await _run(db, gh, clock, notify)
        assert len(notify.calls) == 2

    @pytest.mark.asyncio
    async def test_an_undelivered_changed_page_is_retried_too(self):
        """Not just the first page: news that failed to send stays news."""
        db, gh, clock = _FakeDB(), _FakeGitHub(), _Clock()
        notify = _Notifier()
        gh.main = _Resp(404, text=_NOT_FOUND)
        await _run(db, gh, clock, notify)

        notify.result = {"discord": "discord send failed: timeout"}
        gh.main = _Resp(401, text=_BAD_CREDENTIALS)
        clock.advance(minutes=15)
        lost = await _run(db, gh, clock, notify)
        assert lost["page_reason"] == fe.PAGE_CHANGED
        assert lost["paged"] is False

        notify.result = {"discord": "discord"}
        clock.advance(minutes=15)
        retried = await _run(db, gh, clock, notify)
        assert retried["page_reason"] == fe.PAGE_CHANGED
        assert retried["paged"] is True
        detail = notify.calls[2]["detail"]
        assert "The failure changed (was commits/main:404, now commits/main:401)." in detail
        assert "The previous page about this failure reached no channel." in detail

    @pytest.mark.asyncio
    async def test_reminder_after_the_repage_window(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(404, text=_NOT_FOUND)

        await _passes(96, db, gh, clock, notify)  # t0 .. t0 + 23h45m
        assert len(notify.calls) == 1
        reminder = await _run(db, gh, clock, notify)  # t0 + 24h

        assert reminder["page_reason"] == fe.PAGE_REMINDER
        assert notify.titles() == [
            f"Branch-drift canary cannot run against {REPO}",
            f"Branch-drift canary still cannot run against {REPO}",
        ]
        assert "Failing since 2026-09-23 23:37 UTC (97 attempts)" in notify.calls[1]["detail"]

    @pytest.mark.asyncio
    async def test_zero_repage_hours_never_reminds(self):
        db = _FakeDB(settings={bdp.FAILURE_REPAGE_HOURS_KEY: "0"})
        gh, clock, notify = _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(404, text=_NOT_FOUND)

        await _passes(4, db, gh, clock, notify, every=timedelta(hours=24))

        assert len(notify.calls) == 1
        assert (
            f"Reminders are off (app_settings.{bdp.FAILURE_REPAGE_HOURS_KEY}=0)"
            in notify.calls[0]["detail"]
        )


# ---------------------------------------------------------------------------
# Configuration failures ride the same episode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigurationFailures:
    @pytest.mark.asyncio
    async def test_a_missing_token_pages_once_not_every_pass(self):
        """Before, this paged on every pass (every 15 min), held back only by
        the notifier's in-memory cooldown."""
        db = _FakeDB(settings={"gh_token": ""})
        gh, clock, notify = _FakeGitHub(), _Clock(), _Notifier()

        results = await _passes(8, db, gh, clock, notify)

        assert len(notify.calls) == 1
        assert results[0]["failure_signature"] == "no-token"
        assert "gh_token is not set" in notify.calls[0]["detail"]
        assert gh.requests == 0

    @pytest.mark.asyncio
    async def test_setting_a_token_that_cannot_see_the_repo_is_news(self):
        db = _FakeDB(settings={"gh_token": ""})
        gh, clock, notify = _FakeGitHub(), _Clock(), _Notifier()
        await _run(db, gh, clock, notify)

        db.settings["gh_token"] = "blind-token"
        db.token_changed_at = clock.now + timedelta(minutes=1)
        gh.main = _Resp(404, text=_NOT_FOUND)
        clock.advance(minutes=15)
        summary = await _run(db, gh, clock, notify)

        assert summary["page_reason"] == fe.PAGE_CHANGED
        assert "was no-token, now commits/main:404" in notify.calls[1]["detail"]

    @pytest.mark.asyncio
    async def test_a_broken_git_mount_pages_once(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        git = _Git()
        git.error = RuntimeError(
            "git rev-parse HEAD exit 128: fatal: not a git repository: '/host-git'"
        )

        results = await _passes(8, db, gh, clock, notify, git=git)

        assert len(notify.calls) == 1
        assert results[0]["failure_signature"] == "git-head"
        detail = notify.calls[0]["detail"]
        assert "could not read the running checkout's HEAD from /host-git" in detail
        assert "not a git repository" in detail
        assert f"app_settings.{bdp.GIT_DIR_KEY}" in detail
        assert "${POINDEXTER_DEPLOY_ROOT:-.}/.git:/host-git:ro" in detail
        assert gh.requests == 0


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecovery:
    @pytest.mark.asyncio
    async def test_recovery_note_once_then_quiet(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(404, text=_NOT_FOUND)
        await _passes(3, db, gh, clock, notify)

        gh.main = _Resp(200, {"sha": _MAIN_SHA})
        healed = await _run(db, gh, clock, notify)
        clock.advance(minutes=5)
        skipped = await _run(db, gh, clock, notify)
        clock.advance(minutes=15)
        await _run(db, gh, clock, notify)

        assert healed["ok"] is True
        assert healed["status"] == "no_drift"
        assert skipped["ok"] is True  # the skip no longer inherits a failure
        assert len(notify.calls) == 2
        note = notify.calls[1]
        assert note["title"] == f"Branch-drift canary running again against {REPO}"
        assert note["severity"] == "info"
        assert (
            f"The branch-drift canary is checking {REPO} again after 3 failed "
            f"attempts since 2026-09-23 23:37 UTC (last failure: commits/main:404)."
            in note["detail"]
        )
        assert db.episode() is None
        assert len(db.audit_of("probe.branch_drift_recovered")) == 1

    @pytest.mark.asyncio
    async def test_recovery_into_a_drift_sends_both_the_note_and_the_alert(self):
        """While the canary was blind prod fell behind: the first clean pass
        reports both."""
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(404, text=_NOT_FOUND)
        await _passes(2, db, gh, clock, notify)

        gh.main = _Resp(200, {"sha": _MAIN_SHA})
        gh.compare = _Resp(200, {"status": "behind", "ahead_by": 7})
        git = _Git()
        git.head = _LOCAL_HEAD
        summary = await _run(db, gh, clock, notify, git)

        assert summary["status"] == "drift_detected"
        assert summary["behind"] == 7
        assert len(db.alert_events) == 1
        assert notify.titles()[-1] == f"Branch-drift canary running again against {REPO}"

    @pytest.mark.asyncio
    async def test_a_transient_episode_nobody_heard_about_ends_silently(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.main = _Resp(503, text=_UNICORN)
        await _passes(3, db, gh, clock, notify)

        gh.main = _Resp(200, {"sha": _MAIN_SHA})
        await _run(db, gh, clock, notify)

        assert notify.calls == []
        assert db.episode() is None
        recovered = db.audit_of("probe.branch_drift_recovered")
        assert recovered[0]["was_paged"] is False

    @pytest.mark.asyncio
    async def test_a_healthy_canary_never_touches_episode_state(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        await _passes(3, db, gh, clock, notify)
        assert db.knowledge == {}
        assert db.knowledge_deletes == 0
        assert notify.calls == []


# ---------------------------------------------------------------------------
# What the page says
# ---------------------------------------------------------------------------


def _api_error(endpoint: str, status: int, body: str = "", **kwargs: Any) -> bdp.GitHubAPIError:
    return bdp.GitHubAPIError(endpoint, status, body, **kwargs)


def _describe(exc: BaseException) -> tuple[str, str, bool]:
    return bdp._describe_github_failure(exc, repo=REPO, poll_interval_minutes=15)


@pytest.mark.unit
class TestFailureWording:
    def test_404_names_the_token_its_scopes_and_what_the_canary_needs(self):
        sig, detail, loud = _describe(_api_error("commits/main", 404, _NOT_FOUND))
        assert (sig, loud) == ("commits/main:404", True)
        assert detail.startswith(f"The gh_token cannot see {REPO}, check its scopes.")
        assert f"app_settings.{bdp.REPO_KEY} is misspelled" in detail
        assert f"needs Contents (read) on {REPO}" in detail

    def test_401_says_rejected(self):
        sig, detail, loud = _describe(_api_error("commits/main", 401, _BAD_CREDENTIALS))
        assert (sig, loud) == ("commits/main:401", True)
        assert "Bad credentials" in detail
        assert "Contents (read)" in detail

    def test_403_scope_vs_rate_limit(self):
        scope = _describe(_api_error(
            "commits/main", 403,
            json.dumps({"message": "Resource not accessible by personal access token"}),
        ))
        limit = _describe(_api_error("commits/main", 403, "{}", rate_limited=True))
        assert scope[0] == "commits/main:403" and scope[2] is True
        assert "check its scopes" in scope[1]
        assert "SAML single sign-on" in scope[1]
        assert limit[0] == "commits/main:rate-limited" and limit[2] is False

    def test_5xx_groups_and_stays_quiet(self):
        assert _describe(_api_error("commits/main", 502, _UNICORN))[0::2] == ("commits/main:5xx", False)
        assert _describe(_api_error("compare", 503))[0::2] == ("compare:5xx", False)

    def test_a_redirect_means_the_repo_moved(self):
        sig, detail, loud = _describe(_api_error("commits/main", 301, ""))
        assert (sig, loud) == ("commits/main:3xx", True)
        assert "renamed or transferred" in detail

    def test_any_other_4xx_is_loud_and_quotes_github(self):
        sig, detail, loud = _describe(_api_error(
            "commits/main", 422, json.dumps({"message": "No commit found for SHA: main"}),
        ))
        assert (sig, loud) == ("commits/main:422", True)
        assert "No commit found for SHA: main" in detail

    def test_compare_403_is_a_credential_failure(self):
        sig, _detail, loud = _describe(_api_error("compare", 403, "{}"))
        assert (sig, loud) == ("compare:403", True)

    def test_network_errors_are_quiet(self):
        sig, detail, loud = _describe(
            httpx.ConnectError("[Errno -3] Temporary failure in name resolution"),
        )
        assert (sig, loud) == ("ConnectError", False)
        assert "Temporary failure in name resolution" in detail

    @pytest.mark.asyncio
    async def test_compare_404_is_still_an_unpushed_head_not_a_credential_failure(self):
        """/commits/main just answered with the same token, so a compare 404
        means GitHub can't resolve the pair (#942), as before."""
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.compare = _Resp(404, text=_NOT_FOUND)
        git = _Git()
        git.head = _LOCAL_HEAD

        summary = await _run(db, gh, clock, notify, git)

        assert summary["status"] == "drift_detected"
        assert summary["behind"] is None
        assert notify.calls == []
        assert db.episode() is None

    @pytest.mark.parametrize(
        "exc",
        [
            _api_error("commits/main", 404, _NOT_FOUND),
            _api_error("commits/main", 401, _BAD_CREDENTIALS),
            _api_error("commits/main", 403, json.dumps({"message": "Resource not accessible by personal access token"})),
            _api_error("commits/main", 503, _UNICORN),
            httpx.ConnectTimeout(""),
        ],
        ids=["404", "401", "403", "503", "timeout"],
    )
    @pytest.mark.parametrize(
        "reason",
        [fe.PAGE_NEW, fe.PAGE_PERSISTING, fe.PAGE_CHANGED, fe.PAGE_TOKEN_REPLACED,
         fe.PAGE_UNDELIVERED, fe.PAGE_REMINDER],
    )
    def test_the_page_survives_the_notifier_redaction(self, reason, exc):
        """``_fmt_message`` masks ``token: <x>`` shapes. A page that phrased
        its fix as "token: ..." would reach Discord as ``token:***``."""
        _sig, detail, _loud = _describe(exc)
        episode = {
            "signature": "commits/main:404", "previous_signature": "commits/main:401",
            "token_changed_at": _TOKEN_SET_AT.isoformat(), "since": _T0.isoformat(),
            "attempts": 5, "owed": fe.PAGE_NEW,
        }
        config = {
            "poll_interval_minutes": 15, "failure_repage_hours": 24,
            "transient_failure_page_hours": 6,
        }
        title, body = bdp._build_failure_page(
            repo=REPO, reason=reason, detail=detail, episode=episode, config=config,
        )
        rendered = on._fmt_message(title, body, "brain.branch_drift_probe", "warning")
        assert "***" not in rendered
        assert body in rendered


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailureSettings:
    def test_seeded_defaults_match_the_in_code_fallbacks(self):
        from poindexter.services.settings_defaults import DEFAULTS, METADATA

        assert DEFAULTS[bdp.FAILURE_REPAGE_HOURS_KEY] == str(bdp.DEFAULT_FAILURE_REPAGE_HOURS)
        assert DEFAULTS[bdp.TRANSIENT_FAILURE_PAGE_HOURS_KEY] == str(
            bdp.DEFAULT_TRANSIENT_FAILURE_PAGE_HOURS
        )
        for key in (bdp.FAILURE_REPAGE_HOURS_KEY, bdp.TRANSIENT_FAILURE_PAGE_HOURS_KEY):
            assert METADATA[key] == {"owner": "branch_drift_probe", "value_type": "integer"}

    @pytest.mark.asyncio
    async def test_negative_values_mean_never(self):
        config = await bdp._read_config(_FakeDB(settings={
            bdp.FAILURE_REPAGE_HOURS_KEY: "-3",
            bdp.TRANSIENT_FAILURE_PAGE_HOURS_KEY: "-1",
        }))
        assert config["failure_repage_hours"] == 0
        assert config["transient_failure_page_hours"] == 0
        assert bdp._quiet_page_after(config) is None
