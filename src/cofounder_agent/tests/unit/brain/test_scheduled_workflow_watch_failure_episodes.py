"""Unit tests — the scheduled-CI watchdog pages its own blindness once per episode.

The incident (2026-09-23 → 25): ``gh_token`` was replaced at 23:13 UTC with a
token that cannot see the private repo. From then on every hourly pass got
``404 Not Found`` for all nine watched workflows. ``_fetch_runs`` raised a
plain RuntimeError, ``_assess`` marked each workflow ``not_assessed`` with a
WARNING, and the pass returned ok=True with "no scheduled workflow(s)
assessed". The brain heartbeat read ``ok`` on every cycle from then on. The
last cycle to report a problem was 22:51 UTC, while ``playwright-e2e`` was
still visibly stale.

These tests pin the replacement, built on ``brain/failure_episode.py`` (shared
with the branch-drift canary and the PR staleness probe):

* LOUD failures (401, a 403 that is not a rate limit, a 404 the other watched
  workflows explain, a missing token or httpx) page once per episode, kept in
  ``brain_knowledge``. They page again only when the failure changes, when a
  replaced token fails too, when the last page reached no channel, and on the
  reminder.
* QUIET failures (5xx, timeouts, DNS, rate limits) page only if they last
  ``scheduled_workflow_watch_transient_failure_page_hours`` without a break.
* A pass that assesses nothing, or cannot check every workflow, is ok=False,
  and so is every throttled cycle after it.
* One recovery note on the first clean pass after a page.

The pool is a small STATEFUL fake, as in
``test_branch_drift_probe_failure_episodes.py``: the episode, the hourly
throttle and the last verdict only exist across passes, so what one pass
writes the next must read back.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from poindexter.brain import failure_episode as fe
from poindexter.brain import operator_notifier as on
from poindexter.brain import scheduled_workflow_watch as swf

REPO = "Test-Org/test-repo"
OTHER_REPO = "Test-Org/other-repo"
_TOKEN_SET_AT = datetime(2026, 9, 23, 23, 13, 20, tzinfo=UTC)
_T0 = datetime(2026, 9, 23, 23, 48, 35, tzinfo=UTC)
# The operator's list (docs/operations/ci-deploy-chain.md), in config order.
_WORKFLOWS = [
    ("benchmarks.yml", 30),
    ("console-contract-drift.yml", 30),
    ("regen-app-settings-doc.yml", 30),
    ("sync-claude-md.yml", 30),
    ("release-please.yml", 30),
    ("unit-tests.yml", 30),
    ("runner-healthcheck.yml", 12),
    ("playwright-e2e.yml", 192),
    ("security.yml", 192),
]
_WATCHES = [{"repo": REPO, "workflow": wf, "max_age_hours": age} for wf, age in _WORKFLOWS]
# Verbatim from the prod brain log, 2026-09-25 20:48 UTC.
_NOT_FOUND = (
    '{"message":"Not Found","documentation_url":"https://docs.github.com/rest/'
    'actions/workflow-runs#list-workflow-runs-for-a-workflow","status":"404"}'
)
_BAD_CREDENTIALS = json.dumps({"message": "Bad credentials", "status": "401"})
_NO_ACTIONS = json.dumps(
    {"message": "Resource not accessible by personal access token", "status": "403"},
)
_RATE_LIMITED = json.dumps({"message": "API rate limit exceeded for user ID 1."})
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
    """asyncpg-pool stand-in holding the rows this watchdog reads and writes."""

    def __init__(
        self,
        *,
        settings: dict[str, str] | None = None,
        watches: list[dict[str, Any]] | None = None,
    ) -> None:
        self.settings: dict[str, str] = {
            swf.ENABLED_SETTING_KEY: "true",
            swf.WATCHES_SETTING_KEY: json.dumps(_WATCHES if watches is None else watches),
            swf.INTERVAL_SETTING_KEY: "60",
            swf.FAILURE_REPAGE_HOURS_KEY: "24",
            swf.TRANSIENT_FAILURE_PAGE_HOURS_KEY: "6",
            "gh_token": "test-token",
            **(settings or {}),
        }
        # app_settings.updated_at of the gh_token row: moves when it is replaced.
        self.token_changed_at: datetime | None = _TOKEN_SET_AT
        self.knowledge: dict[tuple[str, str], str] = {}
        self.knowledge_deletes = 0
        self.audit: list[tuple[str, dict[str, Any]]] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "FROM brain_knowledge" in query:  # failure_episode.read_episode
            return self.knowledge.get((args[0], args[1]))
        if "SELECT updated_at FROM app_settings" in query:
            return self.token_changed_at
        return None

    async def fetchrow(self, query: str, *args: Any) -> Any:
        if "FROM app_settings" in query:  # settings, and the token via secret_reader
            key = args[0]
            if key in self.settings:
                return {"value": self.settings[key], "is_secret": False}
            return None
        if "FROM brain_knowledge" in query:  # last_state rows: targets, throttle, verdict
            value = self.knowledge.get((args[0], "last_state"))
            return {"value": value} if value is not None else None
        return None

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO brain_knowledge" in query:
            if "'last_state'" in query:
                self.knowledge[(args[0], "last_state")] = args[1]
            else:
                self.knowledge[(args[0], args[1])] = args[2]
        elif "DELETE FROM brain_knowledge" in query:
            self.knowledge_deletes += 1
            self.knowledge.pop((args[0], args[1]), None)
        elif "INSERT INTO audit_log" in query:
            if "'finding'" in query:
                self.audit.append(("finding", json.loads(args[0])))
            else:
                self.audit.append((args[0], json.loads(args[2])))
        return "OK"

    def audit_of(self, event: str) -> list[dict[str, Any]]:
        return [details for name, details in self.audit if name == event]

    def findings(self) -> list[dict[str, Any]]:
        return self.audit_of("finding")

    def episode(self, repo: str = REPO) -> dict[str, Any] | None:
        raw = self.knowledge.get((swf.FAILURE_STATE_ENTITY, f"failure_episode:{repo}"))
        return json.loads(raw) if raw else None

    def target_state(self, workflow: str, repo: str = REPO) -> str | None:
        return self.knowledge.get(
            (f"scheduled_workflow_watchdog:{repo}:{workflow}", "last_state"),
        )


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


def _runs(last_success: datetime, total: int = 40) -> _Resp:
    return _Resp(200, {"total_count": total, "workflow_runs": [{"created_at": last_success.isoformat()}]})


class _FakeGitHub:
    """Scriptable ``GET /repos/{repo}/actions/workflows/{workflow}/runs``.

    ``answer`` applies to every workflow unless ``per_workflow`` or
    ``per_repo`` says otherwise; each can be a response or an exception.
    None means healthy: the last scheduled run succeeded an hour before the
    clock. ``requests`` lists every workflow asked about, in order.
    """

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.answer: Any = None
        self.per_workflow: dict[str, Any] = {}
        self.per_repo: dict[str, Any] = {}
        self.requests: list[str] = []

    def factory(self) -> _FakeGitHub:
        return self

    async def __aenter__(self) -> _FakeGitHub:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def get(self, url: str, params: dict[str, Any] | None = None) -> _Resp:
        repo = url.split("/repos/", 1)[1].split("/actions/", 1)[0]
        workflow = url.rsplit("/", 2)[-2]
        self.requests.append(workflow)
        answer = self.per_workflow.get(workflow, self.per_repo.get(repo, self.answer))
        if answer is None:
            answer = _runs(self.clock.now - timedelta(hours=1))
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
    return await swf.run_scheduled_workflow_watch(
        db, now_fn=clock, notify_fn=notify, http_client_factory=gh.factory,
    )


async def _cycles(
    n: int, db: _FakeDB, gh: _FakeGitHub, clock: _Clock, notify: Any, *,
    every: timedelta = timedelta(minutes=5),
) -> list[dict[str, Any]]:
    """Run ``n`` brain cycles ``every`` apart; the clock ends one step past the last."""
    out = []
    for _ in range(n):
        out.append(await _run(db, gh, clock, notify))
        clock.now += every
    return out


async def _passes(
    n: int, db: _FakeDB, gh: _FakeGitHub, clock: _Clock, notify: Any,
) -> list[dict[str, Any]]:
    """``n`` real passes, an hour apart: the watchdog's own cadence."""
    return await _cycles(n, db, gh, clock, notify, every=timedelta(hours=1))


def _setup(**db_kwargs: Any) -> tuple[_FakeDB, _FakeGitHub, _Clock, _Notifier]:
    clock = _Clock()
    return _FakeDB(**db_kwargs), _FakeGitHub(clock), clock, _Notifier()


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No test here may reach the real notifier by default: it writes to
    ``~/.poindexter/alerts.log`` and to any Discord webhook in the env."""

    def _unexpected(**kwargs: Any) -> None:
        pytest.fail(f"the default notify_operator was reached: {kwargs.get('title')!r}")

    monkeypatch.setattr(swf, "notify_operator", _unexpected)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


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
class TestTheBlindWatchdogIsReported:
    @pytest.mark.asyncio
    async def test_a_day_of_404s_pages_once_and_reads_github_hourly(self):
        """288 five-minute brain cycles against a token that cannot see the
        repo: one page (the old path sent none), 24 hourly passes of nine
        requests each, and ok=False on every cycle."""
        db, gh, clock, notify = _setup()
        gh.answer = _Resp(404, text=_NOT_FOUND)

        results = await _cycles(288, db, gh, clock, notify)

        assert len(notify.calls) == 1, notify.titles()
        page = notify.calls[0]
        assert page["title"] == f"Scheduled-CI watchdog cannot check {REPO}"
        assert page["severity"] == "warning"
        assert page["source"] == "brain.scheduled_workflow_watch"
        assert (
            f"GitHub answered 404 for all 9 watched workflows in {REPO}, so the "
            f"gh_token cannot see the repo, check its scopes." in page["detail"]
        )
        assert "404, not 403" in page["detail"]
        assert f"needs Actions (read) on {REPO}" in page["detail"]
        assert "poindexter settings set gh_token <token> --secret" in page["detail"]
        assert f"misspelled in app_settings.{swf.WATCHES_SETTING_KEY}" in page["detail"]
        assert (
            f"nothing notices a scheduled workflow in {REPO} that stops firing"
            in page["detail"]
        )
        assert page["dedup_key"] == f"scheduled_workflow_watch_failed:{REPO}:workflow-runs:404:0"

        # A 404 stops each workflow at its first request.
        assert len(gh.requests) == 24 * 9
        failed = db.audit_of("probe.scheduled_workflow_watch_failed")
        assert len(failed) == 24
        assert [f["page_reason"] for f in failed] == [fe.PAGE_NEW] + [None] * 23
        assert failed[0]["paged"] is True
        assert failed[0]["transient"] is False
        assert failed[0]["repo"] == REPO
        # A blind watchdog must never read as healthy, throttled cycles included.
        assert [r["ok"] for r in results] == [False] * 288
        throttled = [r for r in results if r["detail"].startswith("throttled")]
        assert len(throttled) == 264
        assert throttled[0]["detail"] == (
            f"throttled (60m); last pass: no scheduled workflow(s) assessed; "
            f"watchdog failing for {REPO} (workflow-runs:404)"
        )
        # Nothing was read, so nothing is judged stale or never-green.
        assert db.findings() == []

        episode = db.episode()
        assert episode is not None
        assert episode["attempts"] == 24
        assert episode["pages"] == 1
        assert episode["signature"] == "workflow-runs:404"
        assert episode["since"] == _T0.isoformat()

    @pytest.mark.asyncio
    async def test_the_real_notifier_sends_one_discord_message(self, monkeypatch):
        """End to end through ``notify_operator`` with the prod 30-min cooldown."""
        db, gh, clock, _ = _setup()
        sent = _real_notifier(monkeypatch, clock)
        gh.answer = _Resp(404, text=_NOT_FOUND)

        await _cycles(288, db, gh, clock, on.notify_operator)

        assert len(sent) == 1
        assert f"404 for all 9 watched workflows in {REPO}, so the gh_token cannot see" in sent[0]
        assert "***" not in sent[0]  # survived the notifier's credential redaction

    @pytest.mark.asyncio
    async def test_the_verdict_survives_a_brain_restart(self):
        """The episode, the throttle and the last verdict all live in
        brain_knowledge. A brain restarted a few minutes after a failing pass
        is still throttled, still reports the failure, and does not page."""
        db, gh, clock, notify = _setup()
        gh.answer = _Resp(404, text=_NOT_FOUND)
        await _run(db, gh, clock, notify)

        last_pass = json.loads(db.knowledge[("scheduled_workflow_watchdog:_last_pass", "last_state")])
        assert last_pass["ok"] is False
        clock.advance(minutes=4)  # deploy-sync rebuilt the brain
        after = await _run(db, gh, clock, notify)

        assert after["ok"] is False
        assert "last pass: no scheduled workflow(s) assessed" in after["detail"]
        assert len(gh.requests) == 9
        assert len(notify.calls) == 1


# ---------------------------------------------------------------------------
# A 404: the token, or the workflow name?
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWhichWorkflowIsWrong:
    @pytest.mark.asyncio
    async def test_one_404_among_answering_workflows_names_the_workflow(self):
        db, gh, clock, notify = _setup()
        gh.per_workflow["benchmarks.yml"] = _Resp(404, text=_NOT_FOUND)

        summary = await _run(db, gh, clock, notify)

        assert len(notify.calls) == 1
        page = notify.calls[0]
        assert page["title"] == f"Scheduled-CI watchdog cannot check benchmarks.yml in {REPO}"
        assert (
            f"GitHub answered 404 for benchmarks.yml in {REPO}, while 8 other "
            f"watched workflows there answered. So the gh_token can see {REPO}, "
            f"and there is no workflow file by that name" in page["detail"]
        )
        assert f"misspelled in app_settings.{swf.WATCHES_SETTING_KEY}" in page["detail"]
        assert "Fix or remove the entry;" in page["detail"]
        assert "cannot see" not in page["detail"]
        assert "Until then, benchmarks.yml is unwatched." in page["detail"]

        failure = summary["failures"][REPO]
        assert failure["signature"] == "workflow-runs:404:benchmarks.yml"
        assert failure["workflows"] == ["benchmarks.yml"]
        assert summary["ok"] is False
        assert summary["detail"] == (
            f"all 8 assessed scheduled workflow(s) healthy (1 not assessed); "
            f"watchdog failing for {REPO} (workflow-runs:404:benchmarks.yml)"
        )
        # The rest of the repo was still judged.
        assert db.target_state("security.yml") == "ok"
        assert db.target_state("benchmarks.yml") is None

    @pytest.mark.asyncio
    async def test_two_404s_among_answering_workflows(self):
        db, gh, clock, notify = _setup()
        gh.per_workflow["security.yml"] = _Resp(404, text=_NOT_FOUND)
        gh.per_workflow["benchmarks.yml"] = _Resp(404, text=_NOT_FOUND)

        summary = await _run(db, gh, clock, notify)

        page = notify.calls[0]
        assert page["title"] == f"Scheduled-CI watchdog cannot check 2 workflows in {REPO}"
        assert "404 for benchmarks.yml, security.yml in" in page["detail"]
        assert "no workflow file by those names" in page["detail"]
        assert "Fix or remove the entries;" in page["detail"]
        assert "Until then, benchmarks.yml, security.yml are unwatched." in page["detail"]
        assert summary["failures"][REPO]["signature"] == (
            "workflow-runs:404:benchmarks.yml,security.yml"
        )

    @pytest.mark.asyncio
    async def test_a_second_missing_workflow_is_news(self):
        db, gh, clock, notify = _setup()
        gh.per_workflow["benchmarks.yml"] = _Resp(404, text=_NOT_FOUND)
        await _passes(2, db, gh, clock, notify)
        gh.per_workflow["security.yml"] = _Resp(404, text=_NOT_FOUND)
        changed = await _run(db, gh, clock, notify)

        assert changed["failures"][REPO]["page_reason"] == fe.PAGE_CHANGED
        assert len(notify.calls) == 2
        assert (
            "The failure changed (was workflow-runs:404:benchmarks.yml, now "
            "workflow-runs:404:benchmarks.yml,security.yml)." in notify.calls[1]["detail"]
        )

    @pytest.mark.asyncio
    async def test_the_only_watched_workflow_404ing_names_both_causes(self):
        """With nothing else in the repo to compare against, a 404 could be
        either, and the page says so."""
        db, gh, clock, notify = _setup(watches=_WATCHES[:1])
        gh.answer = _Resp(404, text=_NOT_FOUND)

        summary = await _run(db, gh, clock, notify)

        detail = notify.calls[0]["detail"]
        assert notify.calls[0]["title"] == f"Scheduled-CI watchdog cannot check {REPO}"
        assert f"Either the gh_token cannot see {REPO}" in detail
        assert f"or {REPO} has no workflow file named benchmarks.yml" in detail
        assert "so the watchdog cannot tell which" in detail
        assert f"needs Actions (read) on {REPO}" in detail
        assert summary["failures"][REPO]["signature"] == "workflow-runs:404"

    @pytest.mark.asyncio
    async def test_404s_with_nothing_answering_wait_for_github(self):
        """Some 404, the rest 503: that proves neither cause, so it is not
        paged as either until GitHub answers the rest."""
        db, gh, clock, notify = _setup()
        gh.answer = _Resp(503, text=_UNICORN)
        gh.per_workflow["benchmarks.yml"] = _Resp(404, text=_NOT_FOUND)

        summary = await _run(db, gh, clock, notify)

        assert notify.calls == []
        failure = summary["failures"][REPO]
        assert failure["signature"] == "workflow-runs:5xx"
        assert failure["transient"] is True
        assert (
            "1 of them answered 404, which is diagnosed once GitHub answers the rest."
            in failure["detail"]
        )
        assert "9 of 9 watched workflows in" in failure["detail"]

    @pytest.mark.asyncio
    async def test_an_outage_does_not_turn_a_missing_workflow_into_a_token_page(self):
        """Once the operator has heard "benchmarks.yml does not exist", a pass
        where everything else 503s must not page "the token cannot see the
        repo". The quiet pass leaves what they were told standing."""
        db, gh, clock, notify = _setup()
        gh.per_workflow["benchmarks.yml"] = _Resp(404, text=_NOT_FOUND)
        await _passes(1, db, gh, clock, notify)
        gh.answer = _Resp(503, text=_UNICORN)
        await _passes(2, db, gh, clock, notify)
        gh.answer = None
        await _passes(1, db, gh, clock, notify)

        assert len(notify.calls) == 1
        assert db.episode()["paged_signature"] == "workflow-runs:404:benchmarks.yml"

    @pytest.mark.asyncio
    async def test_fixing_the_entry_sends_the_recovery_note(self):
        db, gh, clock, notify = _setup()
        gh.per_workflow["benchmarks.yml"] = _Resp(404, text=_NOT_FOUND)
        await _passes(3, db, gh, clock, notify)

        # The operator drops the stale entry from the watch list.
        db.settings[swf.WATCHES_SETTING_KEY] = json.dumps(_WATCHES[1:])
        healed = await _run(db, gh, clock, notify)

        assert healed["ok"] is True
        assert notify.titles() == [
            f"Scheduled-CI watchdog cannot check benchmarks.yml in {REPO}",
            f"Scheduled-CI watchdog checking {REPO} again",
        ]
        assert "(last failure: workflow-runs:404:benchmarks.yml)" in notify.calls[1]["detail"]
        assert db.episode() is None


# ---------------------------------------------------------------------------
# Transient failures stay quiet unless the watchdog stays blind
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransientFailures:
    @pytest.mark.asyncio
    async def test_a_5xx_spell_stays_quiet(self):
        db, gh, clock, notify = _setup()
        gh.answer = _Resp(503, text=_UNICORN)

        results = await _passes(5, db, gh, clock, notify)

        assert notify.calls == []
        assert all(r["ok"] is False for r in results)
        failed = db.audit_of("probe.scheduled_workflow_watch_failed")
        assert len(failed) == 5
        assert failed[0]["signature"] == "workflow-runs:5xx"
        assert failed[0]["transient"] is True
        assert "GitHub-side error" in failed[0]["detail"]
        assert "DOCTYPE" not in failed[0]["detail"]
        assert db.episode()["paged_at"] is None

    @pytest.mark.asyncio
    async def test_a_transient_failure_that_lasts_pages_once(self):
        db, gh, clock, notify = _setup()
        gh.answer = _Resp(502, text=_UNICORN)

        await _passes(6, db, gh, clock, notify)  # t0 .. t0 + 5h
        assert notify.calls == []
        results = await _passes(1, db, gh, clock, notify)  # t0 + 6h
        assert results[0]["failures"][REPO]["page_reason"] == fe.PAGE_PERSISTING
        assert len(notify.calls) == 1
        detail = notify.calls[0]["detail"]
        assert notify.calls[0]["title"] == f"Scheduled-CI watchdog cannot check {REPO}"
        assert "only once it has lasted 6h. This one has." in detail
        assert "Failing since 2026-09-23 23:48 UTC (7 attempts)" in detail
        assert "the probe retries every 60 min" in detail

        await _passes(10, db, gh, clock, notify)
        assert len(notify.calls) == 1

    @pytest.mark.asyncio
    async def test_rotating_transient_kinds_are_one_blind_spell(self):
        """A 502, a timeout and a DNS failure in rotation never page on their
        own, but the watchdog has been blind all along, so 6h in it says so."""
        db, gh, clock, notify = _setup()
        kinds = [
            _Resp(502, text=_UNICORN),
            httpx.ConnectTimeout(""),
            httpx.ConnectError("[Errno -3] Temporary failure in name resolution"),
        ]
        for i in range(7):
            gh.answer = kinds[i % 3]
            await _passes(1, db, gh, clock, notify)

        assert len(notify.calls) == 1
        assert "only once it has lasted 6h" in notify.calls[0]["detail"]

    @pytest.mark.asyncio
    async def test_one_clean_pass_resets_the_transient_clock(self):
        db, gh, clock, notify = _setup()
        gh.answer = _Resp(503, text=_UNICORN)
        await _passes(5, db, gh, clock, notify)
        gh.answer = None
        await _passes(1, db, gh, clock, notify)
        gh.answer = _Resp(503, text=_UNICORN)
        await _passes(5, db, gh, clock, notify)

        assert notify.calls == []  # never 6h unbroken, and nobody to tell of recovery

    @pytest.mark.asyncio
    async def test_zero_hours_means_a_transient_failure_never_pages(self):
        db, gh, clock, notify = _setup(settings={swf.TRANSIENT_FAILURE_PAGE_HOURS_KEY: "0"})
        gh.answer = _Resp(503, text=_UNICORN)

        await _passes(30, db, gh, clock, notify)

        assert notify.calls == []

    @pytest.mark.parametrize(
        "resp",
        [
            _Resp(403, text=_RATE_LIMITED, headers={"x-ratelimit-remaining": "0"}),
            _Resp(429, text=_RATE_LIMITED),
        ],
        ids=["403-primary", "429"],
    )
    @pytest.mark.asyncio
    async def test_a_rate_limit_is_transient(self, resp):
        db, gh, clock, notify = _setup()
        gh.answer = resp

        summary = await _run(db, gh, clock, notify)

        failure = summary["failures"][REPO]
        assert failure["signature"] == "workflow-runs:rate-limited"
        assert failure["transient"] is True
        assert "another gh_token consumer is spending the budget" in failure["detail"]
        assert notify.calls == []

    @pytest.mark.asyncio
    async def test_a_silent_timeout_is_named_by_its_class(self):
        """str(httpx.ReadTimeout) is empty; the detail names the class."""
        db, gh, clock, notify = _setup()
        gh.answer = httpx.ReadTimeout("")

        summary = await _run(db, gh, clock, notify)

        failure = summary["failures"][REPO]
        assert failure["signature"] == "ReadTimeout"
        assert failure["detail"].startswith(
            f"ReadTimeout. The watchdog's GitHub round-trip for {REPO} did not complete",
        )
        assert summary["workflows"][f"{REPO}:benchmarks.yml"]["reason"] == "ReadTimeout"
        assert notify.calls == []

    @pytest.mark.asyncio
    async def test_a_blip_on_one_workflow_clears_on_the_next_pass(self):
        db, gh, clock, notify = _setup()
        gh.per_workflow["security.yml"] = httpx.ReadTimeout("")
        blip = await _run(db, gh, clock, notify)
        del gh.per_workflow["security.yml"]
        clock.advance(hours=1)
        clean = await _run(db, gh, clock, notify)

        assert blip["ok"] is False
        assert "1 of 9 watched workflows in" in blip["failures"][REPO]["detail"]
        assert clean["ok"] is True
        assert notify.calls == []
        assert db.episode() is None
        assert db.audit_of("probe.scheduled_workflow_watch_recovered")[0]["was_paged"] is False


# ---------------------------------------------------------------------------
# When a failing watchdog pages again
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRepageTriggers:
    @pytest.mark.asyncio
    async def test_a_503_blip_inside_a_404_outage_does_not_repage(self):
        db, gh, clock, notify = _setup()
        for answer in (_Resp(404, text=_NOT_FOUND), _Resp(503, text=_UNICORN),
                       _Resp(404, text=_NOT_FOUND), httpx.ConnectTimeout(""),
                       _Resp(404, text=_NOT_FOUND)):
            gh.answer = answer
            await _passes(1, db, gh, clock, notify)

        assert len(notify.calls) == 1

    @pytest.mark.asyncio
    async def test_a_changed_failure_is_news(self):
        db, gh, clock, notify = _setup()
        gh.answer = _Resp(404, text=_NOT_FOUND)
        await _passes(1, db, gh, clock, notify)
        gh.answer = _Resp(401, text=_BAD_CREDENTIALS)
        changed = await _run(db, gh, clock, notify)

        assert changed["failures"][REPO]["page_reason"] == fe.PAGE_CHANGED
        assert len(notify.calls) == 2
        detail = notify.calls[1]["detail"]
        assert "The failure changed (was workflow-runs:404, now workflow-runs:401)." in detail
        assert "HTTP 401: Bad credentials" in detail
        assert "invalid, expired or revoked" in detail
        assert db.episode()["since"] == _T0.isoformat()

    @pytest.mark.asyncio
    async def test_a_replaced_token_that_still_fails_is_reported(self, monkeypatch):
        """Through the real notifier: the operator rotates the token to one
        that still cannot see the repo, and the next pass says so."""
        db, gh, clock, _ = _setup()
        sent = _real_notifier(monkeypatch, clock)
        gh.answer = _Resp(404, text=_NOT_FOUND)
        await _run(db, gh, clock, on.notify_operator)

        clock.advance(minutes=20)
        db.token_changed_at = clock.now  # the operator rotates the token…
        clock.advance(minutes=40)
        still = await _run(db, gh, clock, on.notify_operator)  # …to another blind one

        assert still["failures"][REPO]["page_reason"] == fe.PAGE_TOKEN_REPLACED
        assert still["failures"][REPO]["paged"] is True
        assert len(sent) == 2
        assert (
            "The gh_token was replaced at 2026-09-24 00:08 UTC, and the new token "
            "fails the same way." in sent[1]
        )

        clock.advance(hours=1)
        await _run(db, gh, clock, on.notify_operator)
        assert len(sent) == 2  # same token, same failure: quiet again

    @pytest.mark.asyncio
    async def test_an_undelivered_page_is_retried_next_pass(self):
        db, gh, clock, _ = _setup()
        gh.answer = _Resp(404, text=_NOT_FOUND)
        notify = _Notifier({
            "telegram": "skipped (severity below error)",
            "discord": "discord send failed: URLError('name resolution')",
        })

        first = await _run(db, gh, clock, notify)
        assert first["failures"][REPO]["paged"] is False
        assert db.episode()["owed"] == fe.PAGE_NEW

        notify.result = {"telegram": "skipped (severity below error)", "discord": "discord"}
        clock.advance(hours=1)
        second = await _run(db, gh, clock, notify)
        assert second["failures"][REPO]["page_reason"] == fe.PAGE_UNDELIVERED
        assert second["failures"][REPO]["paged"] is True
        assert "The previous page about this failure reached no channel." in notify.calls[1]["detail"]

        clock.advance(hours=1)
        await _run(db, gh, clock, notify)
        assert len(notify.calls) == 2

    @pytest.mark.asyncio
    async def test_reminder_after_the_repage_window(self):
        db, gh, clock, notify = _setup()
        gh.answer = _Resp(404, text=_NOT_FOUND)

        await _passes(24, db, gh, clock, notify)  # t0 .. t0 + 23h
        assert len(notify.calls) == 1
        reminder = await _run(db, gh, clock, notify)  # t0 + 24h

        assert reminder["failures"][REPO]["page_reason"] == fe.PAGE_REMINDER
        assert notify.titles() == [
            f"Scheduled-CI watchdog cannot check {REPO}",
            f"Scheduled-CI watchdog still cannot check {REPO}",
        ]
        assert "Failing since 2026-09-23 23:48 UTC (25 attempts)" in notify.calls[1]["detail"]

    @pytest.mark.asyncio
    async def test_zero_repage_hours_never_reminds(self):
        db, gh, clock, notify = _setup(settings={swf.FAILURE_REPAGE_HOURS_KEY: "0"})
        gh.answer = _Resp(404, text=_NOT_FOUND)

        await _cycles(4, db, gh, clock, notify, every=timedelta(hours=24))

        assert len(notify.calls) == 1
        assert (
            f"Reminders are off (app_settings.{swf.FAILURE_REPAGE_HOURS_KEY}=0)"
            in notify.calls[0]["detail"]
        )


# ---------------------------------------------------------------------------
# A watchdog that cannot start rides the same episode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigurationFailures:
    @pytest.mark.asyncio
    async def test_a_missing_token_pages_once_and_never_calls_github(self):
        """Until 2026-09-25 this reported ok, with an INFO line."""
        db, gh, clock, notify = _setup(settings={"gh_token": ""})

        results = await _passes(8, db, gh, clock, notify)

        assert len(notify.calls) == 1
        assert results[0]["failures"][REPO]["signature"] == "no-token"
        assert all(r["ok"] is False for r in results)
        detail = notify.calls[0]["detail"]
        assert "gh_token is not set" in detail
        assert "9 workflows watched in app_settings.scheduled_workflows" in detail
        assert f"needs Actions (read) on {REPO}" in detail
        assert gh.requests == []

    @pytest.mark.asyncio
    async def test_setting_a_token_that_cannot_see_the_repo_is_news(self):
        db, gh, clock, notify = _setup(settings={"gh_token": ""})
        await _passes(1, db, gh, clock, notify)

        db.settings["gh_token"] = "blind-token"
        db.token_changed_at = clock.now - timedelta(minutes=10)
        gh.answer = _Resp(404, text=_NOT_FOUND)
        summary = await _run(db, gh, clock, notify)

        assert summary["failures"][REPO]["page_reason"] == fe.PAGE_CHANGED
        assert "was no-token, now workflow-runs:404" in notify.calls[1]["detail"]

    @pytest.mark.asyncio
    async def test_missing_httpx_pages_once(self, monkeypatch):
        monkeypatch.setattr(swf, "httpx", None)
        db, _, clock, notify = _setup()

        for _ in range(3):
            summary = await swf.run_scheduled_workflow_watch(db, now_fn=clock, notify_fn=notify)
            clock.advance(hours=1)

        assert len(notify.calls) == 1
        assert summary["failures"][REPO]["signature"] == "no-httpx"
        assert "Rebuild the brain image." in notify.calls[0]["detail"]

    @pytest.mark.asyncio
    async def test_nothing_configured_never_pages_even_without_a_token(self):
        """The OSS default: scheduled_workflows ships as []. No watches, no
        token, no page and no GitHub call."""
        db, gh, clock, notify = _setup(watches=[], settings={"gh_token": ""})

        summary = await _run(db, gh, clock, notify)

        assert summary == {"ok": True, "detail": "no workflows configured", "workflows": {}}
        assert notify.calls == []
        assert gh.requests == []

    @pytest.mark.asyncio
    async def test_disabled_never_pages(self):
        db, gh, clock, notify = _setup(
            settings={swf.ENABLED_SETTING_KEY: "false", "gh_token": ""},
        )

        summary = await _run(db, gh, clock, notify)

        assert summary["detail"] == "disabled"
        assert summary["ok"] is True
        assert notify.calls == []


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecovery:
    @pytest.mark.asyncio
    async def test_recovery_note_once_then_quiet(self):
        db, gh, clock, notify = _setup()
        gh.answer = _Resp(404, text=_NOT_FOUND)
        await _passes(3, db, gh, clock, notify)

        gh.answer = None
        healed = await _run(db, gh, clock, notify)
        clock.advance(minutes=5)
        throttled = await _run(db, gh, clock, notify)
        clock.advance(minutes=55)
        await _run(db, gh, clock, notify)

        assert healed["ok"] is True
        assert healed["detail"] == "all 9 assessed scheduled workflow(s) healthy"
        assert throttled == {"ok": True, "detail": "throttled (60m)", "workflows": {}}
        assert len(notify.calls) == 2
        note = notify.calls[1]
        assert note["title"] == f"Scheduled-CI watchdog checking {REPO} again"
        assert note["severity"] == "info"
        assert note["dedup_key"] == f"scheduled_workflow_watch_recovered:{REPO}"
        assert (
            f"The scheduled-CI watchdog is checking {REPO} again after 3 failed "
            f"attempts since 2026-09-23 23:48 UTC (last failure: workflow-runs:404)."
            in note["detail"]
        )
        assert db.episode() is None
        recovered = db.audit_of("probe.scheduled_workflow_watch_recovered")
        assert len(recovered) == 1
        assert recovered[0]["was_paged"] is True

    @pytest.mark.asyncio
    async def test_recovery_reports_what_went_stale_while_blind(self):
        """benchmarks.yml was healthy before the token broke and stopped
        passing while the watchdog could not see it: the first clean pass
        sends the recovery note and the stale finding."""
        db, gh, clock, notify = _setup()
        await _passes(1, db, gh, clock, notify)  # healthy at t0
        assert db.target_state("benchmarks.yml") == "ok"

        gh.answer = _Resp(404, text=_NOT_FOUND)
        await _passes(30, db, gh, clock, notify)
        gh.answer = None
        gh.per_workflow["benchmarks.yml"] = _runs(_T0 - timedelta(hours=1))
        summary = await _run(db, gh, clock, notify)

        assert notify.titles()[-1] == f"Scheduled-CI watchdog checking {REPO} again"
        found = db.findings()
        assert len(found) == 1
        assert found[0]["title"] == f"Scheduled workflow stale: {REPO}:benchmarks.yml"
        assert summary["ok"] is False
        assert summary["detail"].startswith("1 of 9 assessed scheduled workflow(s) unhealthy")

    @pytest.mark.asyncio
    async def test_a_transient_episode_nobody_heard_about_ends_silently(self):
        db, gh, clock, notify = _setup()
        gh.answer = _Resp(503, text=_UNICORN)
        await _passes(3, db, gh, clock, notify)

        gh.answer = None
        await _run(db, gh, clock, notify)

        assert notify.calls == []
        assert db.episode() is None
        assert db.audit_of("probe.scheduled_workflow_watch_recovered")[0]["was_paged"] is False

    @pytest.mark.asyncio
    async def test_a_healthy_watchdog_never_touches_episode_state(self):
        db, gh, clock, notify = _setup()

        results = await _cycles(36, db, gh, clock, notify)

        assert all(r["ok"] is True for r in results)
        assert not [k for k in db.knowledge if k[1].startswith("failure_episode:")]
        assert db.knowledge_deletes == 0
        assert notify.calls == []
        assert db.audit_of("probe.scheduled_workflow_watch_failed") == []


# ---------------------------------------------------------------------------
# One episode per repo
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSeveralRepos:
    @pytest.mark.asyncio
    async def test_each_repo_keeps_its_own_episode(self):
        watches = _WATCHES[:3] + [
            {"repo": OTHER_REPO, "workflow": "nightly.yml", "max_age_hours": 30},
            {"repo": OTHER_REPO, "workflow": "weekly.yml", "max_age_hours": 192},
        ]
        db, gh, clock, notify = _setup(watches=watches)
        gh.per_repo[REPO] = _Resp(404, text=_NOT_FOUND)

        blind = await _passes(3, db, gh, clock, notify)

        assert notify.titles() == [f"Scheduled-CI watchdog cannot check {REPO}"]
        assert "GitHub answered 404 for all 3 watched workflows" in notify.calls[0]["detail"]
        assert list(blind[0]["failures"]) == [REPO]
        assert blind[0]["workflows"][f"{OTHER_REPO}:nightly.yml"]["state"] == "ok"
        assert blind[0]["ok"] is False
        assert db.episode(OTHER_REPO) is None

        del gh.per_repo[REPO]
        await _passes(1, db, gh, clock, notify)
        assert notify.titles()[-1] == f"Scheduled-CI watchdog checking {REPO} again"
        assert len(notify.calls) == 2


# ---------------------------------------------------------------------------
# What the page says
# ---------------------------------------------------------------------------


def _api_error(status: int, body: str = "", *, workflow: str = "benchmarks.yml",
               **kwargs: Any) -> swf.GitHubAPIError:
    return swf.GitHubAPIError("workflow-runs", status, body, ref=workflow, **kwargs)


def _classify(failed: list[tuple[str, BaseException]], *, n_targets: int = 9) -> Any:
    return swf._classify_repo_failure(REPO, failed, n_targets=n_targets, retry_minutes=60)


@pytest.mark.unit
class TestFailureWording:
    def test_403_names_the_actions_permission(self):
        failure = _classify([("benchmarks.yml", _api_error(403, _NO_ACTIONS))])
        assert (failure.signature, failure.loud) == ("workflow-runs:403", True)
        assert f"may not list the workflow runs of {REPO}" in failure.detail
        assert "Resource not accessible by personal access token" in failure.detail
        assert "Contents (read) does not include" in failure.detail
        assert "SAML single sign-on" in failure.detail

    def test_401_says_rejected(self):
        failed = [(wf, _api_error(401, _BAD_CREDENTIALS, workflow=wf)) for wf, _ in _WORKFLOWS]
        failure = _classify(failed)
        assert (failure.signature, failure.loud) == ("workflow-runs:401", True)
        assert "Bad credentials" in failure.detail
        assert "Replace it with" in failure.detail
        assert len(failure.affected) == 9

    def test_a_redirect_means_the_repo_moved(self):
        failure = _classify([("benchmarks.yml", _api_error(301))])
        assert (failure.signature, failure.loud) == ("workflow-runs:3xx", True)
        assert "renamed or transferred" in failure.detail

    def test_any_other_4xx_is_loud_and_quotes_github(self):
        failure = _classify([(
            "benchmarks.yml",
            _api_error(422, json.dumps({"message": "Validation Failed"})),
        )])
        assert (failure.signature, failure.loud) == ("workflow-runs:422", True)
        assert "HTTP 422 for benchmarks.yml: Validation Failed" in failure.detail
        assert failure.affected == ("benchmarks.yml",)

    def test_5xx_groups_and_stays_quiet(self):
        for status in (500, 502, 503, 504):
            failure = _classify([("benchmarks.yml", _api_error(status, _UNICORN))])
            assert (failure.signature, failure.loud) == ("workflow-runs:5xx", False)

    def test_a_credential_failure_outranks_a_blip(self):
        failure = _classify([
            ("benchmarks.yml", httpx.ReadTimeout("")),
            ("security.yml", _api_error(401, _BAD_CREDENTIALS, workflow="security.yml")),
        ])
        assert failure.signature == "workflow-runs:401"

    def test_network_errors_are_quiet_and_quote_the_cause(self):
        failure = _classify([
            ("benchmarks.yml", httpx.ConnectError("[Errno -3] Temporary failure in name resolution")),
        ])
        assert (failure.signature, failure.loud) == ("ConnectError", False)
        assert "Temporary failure in name resolution" in failure.detail

    def test_a_malformed_answer_is_quiet(self):
        failure = _classify([("benchmarks.yml", RuntimeError("non-object payload: list"))])
        assert (failure.signature, failure.loud) == ("RuntimeError", False)
        assert failure.detail.startswith("RuntimeError: non-object payload: list.")

    def test_the_per_workflow_log_line_quotes_github_not_its_error_page(self):
        assert swf._describe_error(_api_error(404, _NOT_FOUND)) == (
            "GitHub /workflow-runs returned 404: Not Found"
        )
        assert swf._describe_error(_api_error(503, _UNICORN)) == "GitHub /workflow-runs returned 503"

    @pytest.mark.parametrize(
        "failed, n_targets",
        [
            ([(wf, _api_error(404, _NOT_FOUND, workflow=wf)) for wf, _ in _WORKFLOWS], 9),
            ([("benchmarks.yml", _api_error(404, _NOT_FOUND))], 9),
            ([("benchmarks.yml", _api_error(404, _NOT_FOUND))], 1),
            ([("benchmarks.yml", _api_error(401, _BAD_CREDENTIALS))], 1),
            ([("benchmarks.yml", _api_error(403, _NO_ACTIONS))], 1),
            ([("benchmarks.yml", _api_error(403, _RATE_LIMITED, rate_limited=True))], 1),
            ([("benchmarks.yml", _api_error(503, _UNICORN))], 9),
            ([("benchmarks.yml", httpx.ConnectTimeout(""))], 9),
        ],
        ids=["404-all", "404-one", "404-only", "401", "403", "rate-limited", "503", "timeout"],
    )
    @pytest.mark.parametrize(
        "reason",
        [fe.PAGE_NEW, fe.PAGE_PERSISTING, fe.PAGE_CHANGED, fe.PAGE_TOKEN_REPLACED,
         fe.PAGE_UNDELIVERED, fe.PAGE_REMINDER],
    )
    def test_the_page_survives_the_notifier_redaction(self, reason, failed, n_targets):
        """``_fmt_message`` masks ``token: <x>`` shapes. A page that phrased
        its fix as "token: ..." would reach Discord as ``token:***``."""
        failure = _classify(failed, n_targets=n_targets)
        episode = {
            "signature": "workflow-runs:404", "previous_signature": "workflow-runs:401",
            "token_changed_at": _TOKEN_SET_AT.isoformat(), "since": _T0.isoformat(),
            "attempts": 5, "owed": fe.PAGE_NEW,
        }
        config = {
            "interval_minutes": 60.0, "failure_repage_hours": 24,
            "transient_failure_page_hours": 6,
        }
        title, body = swf._build_failure_page(
            repo=REPO, reason=reason, failure=failure, n_targets=n_targets,
            episode=episode, config=config,
        )
        rendered = on._fmt_message(title, body, "brain.scheduled_workflow_watch", "warning")
        assert "***" not in rendered
        assert body in rendered

    def test_the_setup_pages_survive_the_redaction_too(self):
        for detail in (swf._no_token_detail(REPO, 9), swf._no_httpx_detail(REPO, 9)):
            rendered = on._fmt_message("t", detail, "brain.scheduled_workflow_watch", "warning")
            assert "***" not in rendered


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailureSettings:
    def test_seeded_defaults_match_the_in_code_fallbacks(self):
        from poindexter.services.settings_defaults import DEFAULTS, METADATA

        assert DEFAULTS[swf.FAILURE_REPAGE_HOURS_KEY] == str(swf.DEFAULT_FAILURE_REPAGE_HOURS)
        assert DEFAULTS[swf.TRANSIENT_FAILURE_PAGE_HOURS_KEY] == str(
            swf.DEFAULT_TRANSIENT_FAILURE_PAGE_HOURS
        )
        for key in (swf.FAILURE_REPAGE_HOURS_KEY, swf.TRANSIENT_FAILURE_PAGE_HOURS_KEY):
            assert METADATA[key] == {"owner": "scheduled_workflow_watch", "value_type": "integer"}

    @pytest.mark.asyncio
    async def test_negative_hours_mean_never(self):
        db = _FakeDB(settings={
            swf.FAILURE_REPAGE_HOURS_KEY: "-3",
            swf.TRANSIENT_FAILURE_PAGE_HOURS_KEY: "-1",
        })
        assert await swf._read_hours(db, swf.FAILURE_REPAGE_HOURS_KEY, 24) == 0
        assert await swf._read_hours(db, swf.TRANSIENT_FAILURE_PAGE_HOURS_KEY, 6) == 0
        assert swf._quiet_page_after({"transient_failure_page_hours": 0}) is None

    @pytest.mark.asyncio
    async def test_a_value_that_is_not_hours_falls_back_loudly(self, caplog):
        db = _FakeDB(settings={swf.FAILURE_REPAGE_HOURS_KEY: "daily"})
        with caplog.at_level("WARNING", logger=swf.logger.name):
            hours = await swf._read_hours(db, swf.FAILURE_REPAGE_HOURS_KEY, 24)
        assert hours == 24
        assert any("is not a whole number of hours" in r.getMessage() for r in caplog.records)
