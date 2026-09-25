"""Unit tests — pr_staleness_probe pages a broken probe once per episode.

The incident (2026-09-23 → 25): ``gh_token`` was replaced at 23:13 UTC with a
token that cannot see the private repo, and from 23:37 every pass failed with
``GitHub /pulls returned 404``. The failure path did not advance the hourly
cadence gate and kept no memory of having paged, so the probe retried every
~5-min brain cycle and called ``notify_operator`` each time — 286
``operator_paged`` rows on 2026-09-24, 52 of them delivered to Discord (one
per 30-min ``operator_page_cooldown_minutes`` window, plus one after every
brain restart, which reset that in-memory cooldown).

These tests pin the replacement: a failure opens an episode persisted in
``brain_knowledge``; the operator is paged when it opens, when the failure
changes, when a replaced token fails too, when the last page reached no
channel, and on the ``pr_staleness_failure_repage_hours`` reminder — and gets
one recovery note when it clears. They also pin the 404 wording: GitHub
answers 404, not 403, for a private repo the token cannot see.

The pool here is a small STATEFUL fake (unlike the MagicMock pool in
``test_pr_staleness_probe.py``) because an episode only exists across passes:
what one pass writes to ``brain_knowledge`` the next must read back.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from poindexter.brain import operator_notifier as on
from poindexter.brain import pr_staleness_probe as psp

REPO = "Test-Org/test-repo"
_T0 = datetime(2026, 9, 23, 23, 37, 0, tzinfo=UTC)
_TOKEN_SET_AT = datetime(2026, 9, 23, 23, 13, 20, tzinfo=UTC)
_NOT_FOUND = json.dumps({
    "message": "Not Found",
    "documentation_url": "https://docs.github.com/rest/pulls/pulls#list-pull-requests",
    "status": "404",
})


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
            psp.ENABLED_KEY: "true",
            psp.POLL_INTERVAL_MINUTES_KEY: "60",
            psp.MIN_HOURS_KEY: "24",
            psp.DEDUP_HOURS_KEY: "12",
            psp.REPO_KEY: REPO,
            psp.MAX_PRS_PER_ALERT_KEY: "5",
            psp.FAILURE_REPAGE_HOURS_KEY: "24",
            "gh_token": "test-token",
            **(settings or {}),
        }
        # app_settings.updated_at of the gh_token row — moves when it is replaced.
        self.token_changed_at: datetime | None = _TOKEN_SET_AT
        self.knowledge: dict[tuple[str, str], str] = {}
        self.knowledge_deletes = 0
        self.audit: list[tuple[str, dict[str, Any]]] = []
        self.alert_events: list[tuple[Any, ...]] = []

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
        return None  # alert_dedup_state: no PR has been paged yet

    async def fetch(self, *_args: Any) -> list[Any]:
        return []

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
        return "OK"

    def audit_of(self, event: str) -> list[dict[str, Any]]:
        return [details for name, details in self.audit if name == event]

    def episode(self) -> dict[str, Any] | None:
        raw = self.knowledge.get((psp.FAILURE_STATE_ENTITY, f"failure_episode:{REPO}"))
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
    """Scriptable GitHub: set ``pulls`` / ``check_runs`` to a response or an
    exception between passes; ``requests`` counts every round-trip."""

    def __init__(self) -> None:
        self.pulls: Any = _Resp(200, [])
        self.check_runs: Any = _Resp(200, {"check_runs": []})
        self.requests = 0

    def factory(self) -> _FakeGitHub:
        return self

    async def __aenter__(self) -> _FakeGitHub:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def get(self, url: str, params: dict[str, Any] | None = None) -> _Resp:
        self.requests += 1
        answer = self.check_runs if "/check-runs" in url else self.pulls
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


async def _run(db: _FakeDB, gh: _FakeGitHub, clock: _Clock, notify: Any) -> dict[str, Any]:
    return await psp.run_pr_staleness_probe(
        db, now_fn=clock, notify_fn=notify, http_client_factory=gh.factory,
    )


def _old_green_pr() -> dict[str, Any]:
    return {
        "number": 7,
        "title": "an old PR",
        "created_at": (_T0 - timedelta(hours=30)).isoformat().replace("+00:00", "Z"),
        "head": {"sha": "abc1234def"},
        "user": {"login": "claude"},
    }


@pytest.fixture(autouse=True)
def _clear_module_state(monkeypatch):
    psp._reset_state()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    yield
    psp._reset_state()


# ---------------------------------------------------------------------------
# The incident, replayed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPersistentFailurePagesOnce:
    @pytest.mark.asyncio
    async def test_a_day_of_404s_pages_once_and_retries_hourly(self):
        """288 five-minute brain cycles against a token that can't see the repo:
        one page, 24 GitHub round-trips, and every cycle reports ok=False."""
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.pulls = _Resp(404, text=_NOT_FOUND)

        results = []
        for _ in range(288):
            results.append(await _run(db, gh, clock, notify))
            clock.advance(minutes=5)

        assert len(notify.calls) == 1, notify.titles()
        page = notify.calls[0]
        assert page["title"] == f"PR staleness probe failed against {REPO}"
        assert page["severity"] == "warning"
        assert f"The gh_token cannot see {REPO}, check its scopes." in page["detail"]
        assert "404, not 403" in page["detail"]
        # <prefix>:<signature>:<pages delivered so far>. The count makes every
        # page the episode decides on new to the notifier's own cooldown.
        assert page["dedup_key"] == f"pr_staleness_failed:{REPO}:pulls:404:0"

        # The cadence gate now advances on failure too: hourly, not per cycle.
        assert gh.requests == 24
        assert len(db.audit_of("probe.pr_staleness_failed")) == 24
        # A broken probe must never read as healthy — skipped cycles included.
        assert [r["ok"] for r in results] == [False] * 288
        skipped = [r for r in results if r["status"] == "skipped_interval"]
        assert len(skipped) == 264
        assert "Last attempt failed" in skipped[0]["detail"]

        episode = db.episode()
        assert episode is not None
        assert episode["attempts"] == 24
        assert episode["pages"] == 1
        assert episode["signature"] == "pulls:404"

    @pytest.mark.asyncio
    async def test_the_real_notifier_sends_one_discord_message_not_48(self, monkeypatch):
        """End to end through ``notify_operator`` with the prod 30-min cooldown,
        its clock tied to the test clock. The old path produced one Discord
        send per cooldown window — 48 a day — which is what prod saw."""
        clock = _Clock()
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

        db, gh = _FakeDB(), _FakeGitHub()
        gh.pulls = _Resp(404, text=_NOT_FOUND)
        for _ in range(288):
            await psp.run_pr_staleness_probe(
                db, now_fn=clock, notify_fn=on.notify_operator,
                http_client_factory=gh.factory,
            )
            clock.advance(minutes=5)

        assert len(sent) == 1
        assert f"The gh_token cannot see {REPO}, check its scopes." in sent[0]

    @pytest.mark.asyncio
    async def test_a_brain_restart_does_not_repage(self):
        """The episode lives in brain_knowledge, so a restart (in-memory state
        wiped, cadence gate open) finds it and stays quiet."""
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.pulls = _Resp(404, text=_NOT_FOUND)

        await _run(db, gh, clock, notify)
        for _ in range(3):  # deploy-sync rebuilt the brain three times
            psp._reset_state()
            clock.advance(minutes=7)
            summary = await _run(db, gh, clock, notify)
            assert summary["status"] == "github_error"
            assert summary["page_reason"] is None

        assert len(notify.calls) == 1
        assert gh.requests == 4


# ---------------------------------------------------------------------------
# When a failing probe pages again
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRepageTriggers:
    @pytest.mark.asyncio
    async def test_reminder_after_the_repage_window(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.pulls = _Resp(404, text=_NOT_FOUND)

        await _run(db, gh, clock, notify)
        clock.advance(hours=23)
        quiet = await _run(db, gh, clock, notify)
        clock.advance(hours=1)
        reminder = await _run(db, gh, clock, notify)

        assert quiet["page_reason"] is None
        assert reminder["page_reason"] == psp.PAGE_REMINDER
        assert notify.titles() == [
            f"PR staleness probe failed against {REPO}",
            f"PR staleness probe still failing against {REPO}",
        ]
        assert "Failing since 2026-09-23 23:37 UTC (3 attempts)" in notify.calls[1]["detail"]

    @pytest.mark.asyncio
    async def test_zero_repage_hours_never_reminds(self):
        db = _FakeDB(settings={psp.FAILURE_REPAGE_HOURS_KEY: "0"})
        gh, clock, notify = _FakeGitHub(), _Clock(), _Notifier()
        gh.pulls = _Resp(404, text=_NOT_FOUND)

        for _ in range(4):
            await _run(db, gh, clock, notify)
            clock.advance(hours=24)

        assert len(notify.calls) == 1
        assert "Reminders are off" in notify.calls[0]["detail"]

    @pytest.mark.asyncio
    async def test_a_changed_failure_is_news(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.pulls = _Resp(404, text=_NOT_FOUND)
        await _run(db, gh, clock, notify)

        clock.advance(hours=1)
        gh.pulls = _Resp(401, text=json.dumps({"message": "Bad credentials"}))
        changed = await _run(db, gh, clock, notify)

        assert changed["page_reason"] == psp.PAGE_CHANGED
        assert len(notify.calls) == 2
        detail = notify.calls[1]["detail"]
        assert "The failure changed (was pulls:404, now pulls:401)." in detail
        assert "invalid, expired or revoked" in detail
        # Still one episode, counted from the first failure.
        assert db.episode()["since"] == _T0.isoformat()

    @pytest.mark.asyncio
    async def test_5xx_flapping_is_one_failure(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        for status in (502, 503, 500, 503):
            gh.pulls = _Resp(status, text="<!DOCTYPE html><title>Unicorn!</title>")
            await _run(db, gh, clock, notify)
            clock.advance(hours=1)

        assert len(notify.calls) == 1
        assert "GitHub-side error" in notify.calls[0]["detail"]
        assert "DOCTYPE" not in notify.calls[0]["detail"]

    @pytest.mark.asyncio
    async def test_a_replaced_token_that_still_fails_pages_within_the_hour(self):
        """The operator's fix didn't take — say so on the next pass rather
        than letting a day of silence read as success."""
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.pulls = _Resp(404, text=_NOT_FOUND)
        await _run(db, gh, clock, notify)

        clock.advance(minutes=20)
        db.token_changed_at = clock.now  # operator rotates the token…
        clock.advance(minutes=40)
        still = await _run(db, gh, clock, notify)  # …to another blind one

        assert still["page_reason"] == psp.PAGE_TOKEN_REPLACED
        assert len(notify.calls) == 2
        assert (
            "The gh_token was replaced at 2026-09-23 23:57 UTC, and the new "
            "token fails the same way." in notify.calls[1]["detail"]
        )

        clock.advance(hours=1)
        await _run(db, gh, clock, notify)
        assert len(notify.calls) == 2  # same token, same failure: quiet again

    @pytest.mark.asyncio
    async def test_an_undelivered_page_is_retried_next_pass(self):
        db, gh, clock = _FakeDB(), _FakeGitHub(), _Clock()
        gh.pulls = _Resp(404, text=_NOT_FOUND)
        notify = _Notifier({
            "telegram": "skipped (severity below error)",
            "discord": "discord send failed: URLError('name resolution')",
        })

        first = await _run(db, gh, clock, notify)
        assert first["paged"] is False
        assert db.episode()["paged_at"] is None

        notify.result = {"telegram": "skipped (severity below error)", "discord": "discord"}
        clock.advance(hours=1)
        second = await _run(db, gh, clock, notify)
        assert second["page_reason"] == psp.PAGE_UNDELIVERED
        assert second["paged"] is True
        assert "The previous page about this failure reached no channel." in notify.calls[1]["detail"]

        clock.advance(hours=1)
        await _run(db, gh, clock, notify)
        assert len(notify.calls) == 2

    @pytest.mark.asyncio
    async def test_a_raising_notifier_is_retried_next_pass(self):
        db, gh, clock = _FakeDB(), _FakeGitHub(), _Clock()
        gh.pulls = _Resp(404, text=_NOT_FOUND)
        calls: list[dict[str, Any]] = []

        def _down(**kwargs: Any) -> None:
            calls.append(kwargs)
            raise RuntimeError("notifier down")

        await _run(db, gh, clock, _down)
        clock.advance(hours=1)
        await _run(db, gh, clock, _down)
        assert len(calls) == 2
        assert db.episode()["pages"] == 0

    @pytest.mark.asyncio
    async def test_no_channel_configured_is_not_retried_every_pass(self):
        """Nothing a retry can fix — alerts.log already holds the page."""
        db, gh, clock = _FakeDB(), _FakeGitHub(), _Clock()
        gh.pulls = _Resp(404, text=_NOT_FOUND)
        notify = _Notifier({
            "telegram": "skipped (severity below error)",
            "discord": "no DISCORD_*_WEBHOOK_URL set",
        })
        for _ in range(3):
            await _run(db, gh, clock, notify)
            clock.advance(hours=1)
        assert len(notify.calls) == 1


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecovery:
    @pytest.mark.asyncio
    async def test_recovery_note_once_then_quiet(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.pulls = _Resp(404, text=_NOT_FOUND)
        for _ in range(3):
            await _run(db, gh, clock, notify)
            clock.advance(hours=1)

        gh.pulls = _Resp(200, [])
        healed = await _run(db, gh, clock, notify)
        clock.advance(minutes=5)
        skipped = await _run(db, gh, clock, notify)
        clock.advance(hours=1)
        await _run(db, gh, clock, notify)

        assert healed["ok"] is True
        assert skipped["ok"] is True  # the skip no longer inherits a failure
        assert len(notify.calls) == 2
        note = notify.calls[1]
        assert note["title"] == f"PR staleness probe recovered against {REPO}"
        assert note["severity"] == "info"
        assert "after 3 failed attempts since 2026-09-23 23:37 UTC" in note["detail"]
        assert "last failure: pulls:404" in note["detail"]
        assert db.episode() is None
        assert len(db.audit_of("probe.pr_staleness_recovered")) == 1

    @pytest.mark.asyncio
    async def test_an_episode_nobody_was_told_about_ends_silently(self):
        db, gh, clock = _FakeDB(), _FakeGitHub(), _Clock()
        gh.pulls = _Resp(404, text=_NOT_FOUND)
        notify = _Notifier({"discord": "discord send failed: timeout"})
        await _run(db, gh, clock, notify)

        gh.pulls = _Resp(200, [])
        clock.advance(hours=1)
        await _run(db, gh, clock, notify)

        assert len(notify.calls) == 1  # the failed page only; no recovery note
        assert db.episode() is None
        assert db.audit_of("probe.pr_staleness_recovered")[0]["was_paged"] is False

    @pytest.mark.asyncio
    async def test_a_healthy_probe_never_touches_episode_state(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        await _run(db, gh, clock, notify)
        assert db.knowledge == {}
        assert db.knowledge_deletes == 0
        assert notify.calls == []


# ---------------------------------------------------------------------------
# What the page says
# ---------------------------------------------------------------------------


def _api_error(endpoint: str, status: int, body: str = "", **kwargs: Any) -> psp.GitHubAPIError:
    return psp.GitHubAPIError(endpoint, status, body, **kwargs)


@pytest.mark.unit
class TestFailureWording:
    def test_404_with_a_token_names_the_token_and_its_scopes(self):
        sig, detail = psp._describe_failure(
            _api_error("pulls", 404, _NOT_FOUND), repo=REPO, has_token=True,
        )
        assert sig == "pulls:404"
        assert detail.startswith(f"The gh_token cannot see {REPO}, check its scopes.")
        assert "Pull requests (read)" in detail
        assert "Checks (read)" in detail
        assert "poindexter settings set gh_token <token> --secret" in detail

    def test_404_without_a_token_says_it_is_unset(self):
        sig, detail = psp._describe_failure(
            _api_error("pulls", 404, _NOT_FOUND), repo=REPO, has_token=False,
        )
        assert sig == "pulls:404-no-token"
        assert detail.startswith("gh_token is not set")

    def test_401_says_rejected(self):
        sig, detail = psp._describe_failure(
            _api_error("pulls", 401, json.dumps({"message": "Bad credentials"})),
            repo=REPO, has_token=True,
        )
        assert sig == "pulls:401"
        assert "Bad credentials" in detail

    def test_403_scope_vs_rate_limit(self):
        scope_sig, scope = psp._describe_failure(
            _api_error("pulls", 403, json.dumps({"message": "Resource not accessible by personal access token"})),
            repo=REPO, has_token=True,
        )
        limit_sig, limit = psp._describe_failure(
            _api_error("pulls", 403, "{}", rate_limited=True), repo=REPO, has_token=True,
        )
        assert scope_sig == "pulls:403"
        assert "check its scopes" in scope
        assert limit_sig == "pulls:rate-limited"
        assert "rate-limited" in limit

    def test_check_runs_403_asks_for_checks_read(self):
        sig, detail = psp._describe_failure(
            _api_error("check-runs", 403, "{}", ref="abc1234def"), repo=REPO, has_token=True,
        )
        assert sig == "check-runs:403"
        assert "Checks (read)" in detail

    def test_a_silent_timeout_is_named_by_its_class(self):
        """str(httpx.ConnectTimeout) is empty; the page must not read 'ConnectTimeout: '."""
        sig, detail = psp._describe_failure(
            httpx.ConnectTimeout(""), repo=REPO, has_token=True,
        )
        assert sig == "ConnectTimeout"
        assert detail.startswith("ConnectTimeout — ")

    @pytest.mark.asyncio
    async def test_check_runs_403_reaches_the_page(self):
        db, gh, clock, notify = _FakeDB(), _FakeGitHub(), _Clock(), _Notifier()
        gh.pulls = _Resp(200, [_old_green_pr()])
        gh.check_runs = _Resp(403, text=json.dumps({"message": "Resource not accessible"}))

        summary = await _run(db, gh, clock, notify)

        assert summary["failure_signature"] == "check-runs:403"
        assert "cannot read their check runs" in notify.calls[0]["detail"]

    @pytest.mark.parametrize("has_token", [True, False])
    @pytest.mark.parametrize(
        "reason",
        [psp.PAGE_NEW, psp.PAGE_CHANGED, psp.PAGE_TOKEN_REPLACED,
         psp.PAGE_UNDELIVERED, psp.PAGE_REMINDER],
    )
    def test_the_page_survives_the_notifier_redaction(self, reason, has_token):
        """``_fmt_message`` masks ``token: <x>`` shapes — a page that phrased
        its fix as "token: ..." would reach Discord as ``token:***``."""
        _sig, detail = psp._describe_failure(
            _api_error("pulls", 404, _NOT_FOUND), repo=REPO, has_token=has_token,
        )
        episode = {
            "signature": "pulls:404", "previous_signature": "pulls:401",
            "token_changed_at": _TOKEN_SET_AT.isoformat(),
            "since": _T0.isoformat(), "attempts": 5,
        }
        title, body = psp._build_failure_page(
            repo=REPO, reason=reason, detail=detail, episode=episode,
            poll_interval_minutes=60, repage_hours=24,
        )
        rendered = on._fmt_message(title, body, "brain.pr_staleness_probe", "warning")
        assert "***" not in rendered
        assert body in rendered


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRepageSetting:
    def test_seeded_default_matches_in_code_fallback(self):
        from poindexter.services.settings_defaults import DEFAULTS, METADATA

        assert DEFAULTS[psp.FAILURE_REPAGE_HOURS_KEY] == str(psp.DEFAULT_FAILURE_REPAGE_HOURS)
        assert METADATA[psp.FAILURE_REPAGE_HOURS_KEY]["value_type"] == "integer"

    @pytest.mark.asyncio
    async def test_a_negative_value_means_never_remind(self):
        config = await psp._read_config(_FakeDB(settings={psp.FAILURE_REPAGE_HOURS_KEY: "-3"}))
        assert config["failure_repage_hours"] == 0
