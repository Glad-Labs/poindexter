"""Unit tests for brain/glitchtip_triage_probe.py.

Covers the triage probe's main behaviors:

1. disabled via app_settings → no-op, returns status=disabled
2. token missing → status=unconfigured, no HTTP calls
3. real cycle: pulls issues, applies rules (resolve / ignore / pass-through),
   pages on novel high-count, dedupes alerts within process uptime
4. rule-validation: malformed / unparsable rules are skipped, valid ones still apply
5. pagination: follows the Sentry-compatible Link cursor across pages
6. resolve API failure → recorded in failed-resolve list, cycle still completes
7. http exception during fetch → status=error returned, brain cycle survives

All HTTP I/O is mocked via an injected http_client_factory — no real
network calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import glitchtip_triage_probe as gt


def _make_pool(settings: dict[str, str] | None = None, secrets: dict[str, str] | None = None):
    """Async pool mock that returns app_settings rows from dicts.

    settings -> regular plaintext rows (is_secret=false).
    secrets -> rows with is_secret=true; we just return the value
               verbatim (skipping the encryption path) since the
               probe's _read_secret tolerates plaintext rows.
    """
    pool = MagicMock()
    settings = settings or {}
    secrets = secrets or {}

    async def _fetchval(query, *args):
        # Cover both the plaintext fetchval path and the pgp_sym_decrypt
        # SELECT used during secret decryption (we never trigger the
        # latter in tests because we return is_secret=False rows).
        if "app_settings" in query and len(args) >= 1:
            key = args[0]
            return settings.get(key)
        return None

    async def _fetchrow(_query, key):
        if key in secrets:
            return {"value": secrets[key], "is_secret": False}
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


class _FakeResponse:
    """Minimal stand-in for httpx.Response used by the probe."""

    def __init__(self, status_code=200, json_data=None, link="", text=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = {"Link": link} if link else {}
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeClient:
    """In-memory httpx.AsyncClient stand-in.

    Tests configure ``get_responses`` (list of _FakeResponse) returned in
    order for ``client.get()``. ``put_results`` is a dict of
    ``issue_id -> _FakeResponse`` returned for the PUT-resolve calls.
    """

    def __init__(self, get_responses, put_results=None, raise_on_get=None):
        self._get_queue = list(get_responses)
        self._put_results = put_results or {}
        self._raise_on_get = raise_on_get
        self.get_calls: list[tuple[str, dict]] = []
        self.put_calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        self.get_calls.append((url, params or {}))
        if self._raise_on_get is not None:
            raise self._raise_on_get
        if not self._get_queue:
            return _FakeResponse(status_code=200, json_data=[])
        return self._get_queue.pop(0)

    async def put(self, url, json=None):
        # Extract issue id from the URL: .../issues/<id>/
        issue_id = url.rstrip("/").rsplit("/", 1)[-1]
        self.put_calls.append((url, json or {}))
        return self._put_results.get(
            issue_id, _FakeResponse(status_code=200, json_data={"status": "resolved"})
        )


def _factory(client):
    """Wrap a _FakeClient so http_client_factory() returns it."""
    return lambda: client


@pytest.fixture(autouse=True)
def _reset_module_state():
    gt._alerted_ids.clear()
    yield
    gt._alerted_ids.clear()


# ---------------------------------------------------------------------------
# Scenario 1 — disabled flag
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disabled_flag_short_circuits():
    pool = _make_pool({"glitchtip_triage_enabled": "false"})
    client = _FakeClient(get_responses=[])

    summary = await gt.run_glitchtip_triage_probe(
        pool, http_client_factory=_factory(client)
    )

    assert summary["ok"] is True
    assert summary["status"] == "disabled"
    assert client.get_calls == []
    assert client.put_calls == []


# ---------------------------------------------------------------------------
# Scenario 2 — missing API token
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_token_returns_unconfigured():
    # No secret -> token is empty string -> probe should bail.
    pool = _make_pool({"glitchtip_triage_enabled": "true"})
    client = _FakeClient(get_responses=[])

    summary = await gt.run_glitchtip_triage_probe(
        pool, http_client_factory=_factory(client)
    )

    assert summary["ok"] is True
    assert summary["status"] == "unconfigured"
    assert "not configured" in summary["detail"]
    assert client.get_calls == []
    # An audit row about the misconfiguration should be written.
    audit_event_args = [
        c.args for c in pool.execute.call_args_list if "audit_log" in c.args[0]
    ]
    assert any(args[1] == "probe.glitchtip_triage_unconfigured" for args in audit_event_args)


# ---------------------------------------------------------------------------
# Scenario 3 — full cycle: resolve, ignore, pass-through, alert, dedupe
# ---------------------------------------------------------------------------


def _issue(
    id_, title, count, level="error", last_seen=None,
    permalink="http://glitchtip/issues/x"
):
    """Build a fake GlitchTip issue.

    2026-05-16: ``last_seen`` defaults to "1 hour ago" (always fresh
    under the default 24h freshness gate) so existing tests that don't
    care about the gate keep passing. Tests that exercise the gate
    pass a specific timestamp.
    """
    if last_seen is None:
        from datetime import datetime, timezone, timedelta
        last_seen = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat().replace("+00:00", "Z")
    return {
        "id": id_,
        "title": title,
        "count": str(count),
        "level": level,
        "lastSeen": last_seen,
        "permalink": permalink,
        "metadata": {},
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_full_cycle_resolve_ignore_pass_through_and_alert():
    rules = [
        {
            "title_pattern": r"Failed to export span batch.*langfuse",
            "action": "resolve",
            "reason": "langfuse exporter noise",
            "max_count": None,
        },
        {
            "title_pattern": r"AllModelsFailedError",
            "action": "ignore",
            "reason": "tracked elsewhere",
        },
        # Bad regex — should be silently skipped, not crash the cycle.
        {
            "title_pattern": r"[unterminated",
            "action": "resolve",
            "reason": "broken",
        },
        # Wrong type for action — also skipped.
        {"title_pattern": "x", "action": "what"},
    ]
    pool = _make_pool(
        settings={
            "glitchtip_triage_enabled": "true",
            "glitchtip_base_url": "http://gt:8000",
            "glitchtip_triage_org_slug": "glad-labs",
            "glitchtip_triage_alert_threshold_count": "100",
            "glitchtip_triage_auto_resolve_patterns": json.dumps(rules),
        },
        secrets={"glitchtip_triage_api_token": "tok-abc"},
    )

    issues = [
        _issue("100", "Failed to export span batch code: None langfuse-web", 5000),
        _issue("101", "AllModelsFailedError: All AI models failed for topic X", 200, level="fatal"),
        _issue("102", "RuntimeError: novel thing exploded", 250, level="error"),
        _issue("103", "OneOff: this barely happened", 3),
    ]

    client = _FakeClient(
        get_responses=[_FakeResponse(status_code=200, json_data=issues)],
    )

    notifies: list[dict] = []

    summary = await gt.run_glitchtip_triage_probe(
        pool,
        notify_fn=lambda **kw: notifies.append(kw),
        http_client_factory=_factory(client),
    )

    assert summary["ok"] is True
    assert summary["status"] == "completed"
    assert summary["issues_seen"] == 4
    assert summary["auto_resolved_count"] == 1   # langfuse one
    assert summary["ignored_count"] == 1         # AllModelsFailedError
    assert summary["alerted_count"] == 1         # RuntimeError above threshold
    assert summary["auto_resolve_failed_count"] == 0

    # The langfuse issue should have hit the resolve API.
    assert len(client.put_calls) == 1
    assert client.put_calls[0][0].endswith("/api/0/issues/100/")
    assert client.put_calls[0][1] == {"status": "resolved"}

    # One alert went out for the novel high-count issue.
    assert len(notifies) == 1
    n = notifies[0]
    assert n["source"] == "brain.glitchtip_triage_probe"
    assert "RuntimeError" in n["title"]
    assert n["severity"] == "warning"  # level=error, not fatal


@pytest.mark.unit
@pytest.mark.asyncio
async def test_alert_dedupe_within_process_uptime():
    pool = _make_pool(
        settings={
            "glitchtip_triage_enabled": "true",
            "glitchtip_triage_alert_threshold_count": "10",
            "glitchtip_triage_auto_resolve_patterns": "[]",
        },
        secrets={"glitchtip_triage_api_token": "tok-abc"},
    )
    issues = [_issue("999", "Whoa: novel error", 50)]
    client1 = _FakeClient(get_responses=[_FakeResponse(json_data=issues)])
    notifies1: list[dict] = []
    s1 = await gt.run_glitchtip_triage_probe(
        pool, notify_fn=lambda **kw: notifies1.append(kw),
        http_client_factory=_factory(client1),
    )
    assert s1["alerted_count"] == 1
    assert len(notifies1) == 1

    # Second cycle in the same process — same issue should not re-page.
    client2 = _FakeClient(get_responses=[_FakeResponse(json_data=issues)])
    notifies2: list[dict] = []
    s2 = await gt.run_glitchtip_triage_probe(
        pool, notify_fn=lambda **kw: notifies2.append(kw),
        http_client_factory=_factory(client2),
    )
    assert s2["alerted_count"] == 0
    assert notifies2 == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_max_count_gates_auto_resolve():
    # Rule should NOT auto-resolve issues above max_count.
    rules = [
        {
            "title_pattern": r"^transient: ",
            "action": "resolve",
            "reason": "small ones only",
            "max_count": 10,
        },
    ]
    pool = _make_pool(
        settings={
            "glitchtip_triage_enabled": "true",
            "glitchtip_triage_alert_threshold_count": "1000000",  # never alert
            "glitchtip_triage_auto_resolve_patterns": json.dumps(rules),
        },
        secrets={"glitchtip_triage_api_token": "tok-abc"},
    )
    issues = [
        _issue("1", "transient: small one", 5),       # should resolve
        _issue("2", "transient: too loud", 50),       # NOT resolved
    ]
    client = _FakeClient(get_responses=[_FakeResponse(json_data=issues)])
    summary = await gt.run_glitchtip_triage_probe(
        pool, http_client_factory=_factory(client),
    )
    assert summary["auto_resolved_count"] == 1
    assert client.put_calls[0][0].endswith("/api/0/issues/1/")


# ---------------------------------------------------------------------------
# Scenario 4 — pagination via Link header cursor
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pagination_follows_link_cursor():
    pool = _make_pool(
        settings={
            "glitchtip_triage_enabled": "true",
            "glitchtip_triage_alert_threshold_count": "1000000",
            "glitchtip_triage_auto_resolve_patterns": "[]",
        },
        secrets={"glitchtip_triage_api_token": "tok-abc"},
    )
    page1 = [_issue("1", "a", 1), _issue("2", "b", 2)]
    page2 = [_issue("3", "c", 3)]
    next_link = (
        '<http://gt/api/0/organizations/glad-labs/issues/?cursor=NEXT&limit=100>; '
        'rel="next"; results="true"; cursor="NEXT"'
    )
    end_link = (
        '<http://gt/api/0/organizations/glad-labs/issues/?cursor=END>; '
        'rel="next"; results="false"; cursor="END"'
    )
    client = _FakeClient(get_responses=[
        _FakeResponse(json_data=page1, link=next_link),
        _FakeResponse(json_data=page2, link=end_link),
    ])

    summary = await gt.run_glitchtip_triage_probe(
        pool, http_client_factory=_factory(client),
    )

    assert summary["issues_seen"] == 3
    assert len(client.get_calls) == 2
    # Second call should carry the cursor parameter.
    assert client.get_calls[1][1].get("cursor") == "NEXT"


# ---------------------------------------------------------------------------
# Scenario 5 — resolve API failure recorded but cycle continues
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_api_failure_is_recorded():
    rules = [{"title_pattern": r"foo", "action": "resolve", "reason": "x"}]
    pool = _make_pool(
        settings={
            "glitchtip_triage_enabled": "true",
            "glitchtip_triage_auto_resolve_patterns": json.dumps(rules),
            "glitchtip_triage_alert_threshold_count": "1000000",
        },
        secrets={"glitchtip_triage_api_token": "tok-abc"},
    )
    issues = [_issue("77", "foo bar", 4)]
    client = _FakeClient(
        get_responses=[_FakeResponse(json_data=issues)],
        put_results={"77": _FakeResponse(status_code=500, text="server error")},
    )
    summary = await gt.run_glitchtip_triage_probe(
        pool, http_client_factory=_factory(client),
    )
    assert summary["ok"] is True
    assert summary["auto_resolved_count"] == 0
    assert summary["auto_resolve_failed_count"] == 1


# ---------------------------------------------------------------------------
# Scenario 6 — fetch HTTP exception → status=error, brain cycle survives
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_exception_returns_error_status():
    pool = _make_pool(
        settings={
            "glitchtip_triage_enabled": "true",
            "glitchtip_triage_auto_resolve_patterns": "[]",
        },
        secrets={"glitchtip_triage_api_token": "tok-abc"},
    )

    # Use a client whose async with raises during use — simulate a
    # broken response by forcing a TypeError inside the cycle.
    class _ExplodingClient:
        async def __aenter__(self):
            raise RuntimeError("connection refused")

        async def __aexit__(self, *exc):
            return False

    summary = await gt.run_glitchtip_triage_probe(
        pool, http_client_factory=lambda: _ExplodingClient(),
    )
    assert summary["ok"] is False
    assert summary["status"] == "error"
    assert "RuntimeError" in summary["detail"]


# ---------------------------------------------------------------------------
# Rule helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_match_rule_respects_level_in():
    rule = {
        "title_pattern": "x",
        "_compiled": __import__("re").compile("x"),
        "action": "resolve",
        "reason": "",
        "max_count": None,
        "level_in": ["fatal"],
    }
    assert gt._match_rule({"title": "xyz", "level": "fatal", "count": 1}, [rule]) is rule
    assert gt._match_rule({"title": "xyz", "level": "error", "count": 1}, [rule]) is None


# ---------------------------------------------------------------------------
# min_age_days gate (Part 2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_min_age_days_gate_blocks_fresh_issues():
    """Rule with min_age_days=7 should NOT match an issue from yesterday."""
    from datetime import datetime, timedelta, timezone
    rule = {
        "title_pattern": "GC me",
        "_compiled": __import__("re").compile("GC me"),
        "action": "resolve",
        "reason": "weekly GC",
        "max_count": 5,
        "min_age_days": 7,
        "level_in": None,
    }
    fresh_first_seen = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    issue = {
        "title": "GC me please",
        "count": 3,
        "level": "error",
        "firstSeen": fresh_first_seen,
    }
    assert gt._match_rule(issue, [rule]) is None


@pytest.mark.unit
def test_min_age_days_gate_allows_old_issues():
    """Rule with min_age_days=7 SHOULD match an issue from 10 days ago."""
    from datetime import datetime, timedelta, timezone
    rule = {
        "title_pattern": "GC me",
        "_compiled": __import__("re").compile("GC me"),
        "action": "resolve",
        "reason": "weekly GC",
        "max_count": 5,
        "min_age_days": 7,
        "level_in": None,
    }
    old_first_seen = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    issue = {
        "title": "GC me please",
        "count": 3,
        "level": "error",
        "firstSeen": old_first_seen,
    }
    assert gt._match_rule(issue, [rule]) is rule


@pytest.mark.unit
def test_min_age_days_combines_with_max_count():
    """The classic 'GC > N days old AND <= K occurrences' combo."""
    from datetime import datetime, timedelta, timezone
    rule = {
        "title_pattern": ".*",
        "_compiled": __import__("re").compile(".*"),
        "action": "resolve",
        "reason": "GC stale low-count noise",
        "max_count": 5,
        "min_age_days": 7,
        "level_in": None,
    }
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

    # Old AND low count -> resolve.
    matched = gt._match_rule(
        {"title": "x", "count": 4, "level": "error", "firstSeen": old_ts},
        [rule],
    )
    assert matched is rule

    # Old but high count -> max_count blocks.
    blocked_by_count = gt._match_rule(
        {"title": "x", "count": 50, "level": "error", "firstSeen": old_ts},
        [rule],
    )
    assert blocked_by_count is None

    # Low count but fresh -> min_age_days blocks.
    fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    blocked_by_age = gt._match_rule(
        {"title": "x", "count": 4, "level": "error", "firstSeen": fresh_ts},
        [rule],
    )
    assert blocked_by_age is None


@pytest.mark.unit
def test_min_age_days_handles_missing_first_seen():
    """No ``firstSeen`` field -> rule cannot satisfy the age gate, so no match."""
    rule = {
        "title_pattern": ".*",
        "_compiled": __import__("re").compile(".*"),
        "action": "resolve",
        "reason": "",
        "max_count": None,
        "min_age_days": 7,
        "level_in": None,
    }
    issue = {"title": "x", "count": 1, "level": "error"}  # firstSeen missing
    assert gt._match_rule(issue, [rule]) is None


@pytest.mark.unit
def test_min_age_days_handles_z_suffix_iso8601():
    """GlitchTip API returns timestamps with trailing 'Z' -- must parse cleanly."""
    from datetime import datetime, timedelta, timezone
    rule = {
        "title_pattern": ".*",
        "_compiled": __import__("re").compile(".*"),
        "action": "resolve",
        "reason": "",
        "max_count": None,
        "min_age_days": 1,
        "level_in": None,
    }
    old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    issue = {"title": "x", "count": 1, "level": "error", "firstSeen": old}
    assert gt._match_rule(issue, [rule]) is rule


@pytest.mark.unit
def test_min_age_days_zero_is_no_gate():
    """min_age_days=0 should behave as 'no age gate' (act regardless)."""
    rule = {
        "title_pattern": ".*",
        "_compiled": __import__("re").compile(".*"),
        "action": "resolve",
        "reason": "",
        "max_count": None,
        "min_age_days": 0,
        "level_in": None,
    }
    # Even an issue with no firstSeen at all should match (gate is no-op).
    issue = {"title": "x", "count": 1, "level": "error"}
    assert gt._match_rule(issue, [rule]) is rule


@pytest.mark.unit
def test_read_rules_preserves_min_age_days():
    """``_read_rules`` must surface min_age_days through the cleaned dict so
    ``_match_rule`` can consult it. Smoke-tests the JSON schema additions."""
    import asyncio
    rules_json = json.dumps([
        {
            "title_pattern": ".*",
            "action": "resolve",
            "reason": "test",
            "max_count": 5,
            "min_age_days": 7,
        },
    ])
    pool = _make_pool({"glitchtip_triage_auto_resolve_patterns": rules_json})
    cleaned = asyncio.run(gt._read_rules(pool))
    assert len(cleaned) == 1
    assert cleaned[0]["min_age_days"] == 7
    assert cleaned[0]["max_count"] == 5


@pytest.mark.unit
def test_parse_next_cursor_handles_terminal_page():
    link = (
        '<http://x?cursor=PREV>; rel="previous"; results="true"; cursor="PREV", '
        '<http://x?cursor=END>; rel="next"; results="false"; cursor="END"'
    )
    assert gt._parse_next_cursor(link) is None


@pytest.mark.unit
def test_parse_next_cursor_extracts_real_cursor():
    link = (
        '<http://x?cursor=PREV>; rel="previous"; results="false", '
        '<http://x?cursor=NEXT>; rel="next"; results="true"; cursor="NEXT"'
    )
    assert gt._parse_next_cursor(link) == "NEXT"


# ---------------------------------------------------------------------------
# Freshness gate — 2026-05-16
# ---------------------------------------------------------------------------
#
# Captured 2026-05-16: a brain restart re-paged a 2-day-stale unresolved
# issue (id=71, ``UndefinedColumnError: column "podcast_url" does not
# exist``) as "novel high-count" because the in-memory ``_alerted_ids``
# dedup set was empty and the threshold check ignored freshness. The
# bug had already been fixed in code but the GlitchTip issue wasn't
# marked resolved. The freshness gate prevents this class of false page
# going forward — stale unresolved issues are operator-housekeeping
# (close in the UI), not on-call signal.


@pytest.mark.unit
class TestIsFresh:
    def test_recent_iso_is_fresh(self):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        assert gt._is_fresh(recent, max_age_hours=24) is True

    def test_stale_iso_is_not_fresh(self):
        from datetime import datetime, timezone, timedelta
        stale = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        assert gt._is_fresh(stale, max_age_hours=24) is False

    def test_z_suffix_iso_parses(self):
        """GlitchTip emits ``lastSeen`` ending in ``Z`` — must parse."""
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        z_form = recent.replace("+00:00", "Z")
        assert gt._is_fresh(z_form, max_age_hours=24) is True

    def test_naive_iso_treated_as_utc(self):
        """A naive (no-tzinfo) ISO string is treated as UTC so we don't
        accidentally flag fresh issues as stale because of local time."""
        from datetime import datetime, timezone, timedelta
        recent_naive = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).replace(tzinfo=None).isoformat()
        assert gt._is_fresh(recent_naive, max_age_hours=24) is True

    def test_none_is_treated_as_fresh(self):
        """No timestamp = don't suppress a real signal on a parser quirk."""
        assert gt._is_fresh(None, max_age_hours=24) is True

    def test_garbage_iso_is_treated_as_fresh(self):
        """Unparseable input = also fresh (fail-open, don't lose signal)."""
        assert gt._is_fresh("not-an-iso-string", max_age_hours=24) is True

    def test_zero_max_age_disables_gate(self):
        """Operators who want every restart to re-page everything can
        set freshness=0 — gate degrades to always-fresh."""
        from datetime import datetime, timezone, timedelta
        ancient = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        assert gt._is_fresh(ancient, max_age_hours=0) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_unresolved_issue_is_not_alerted():
    """End-to-end pin: an unresolved issue above the threshold but
    stale (``lastSeen`` older than 24h) does NOT page the operator.
    Captured 2026-05-16: a brain restart turned the empty in-memory
    ``_alerted_ids`` set into a "re-page every stale issue" loop."""
    from datetime import datetime, timezone, timedelta
    stale_iso = (
        datetime.now(timezone.utc) - timedelta(hours=48)
    ).isoformat().replace("+00:00", "Z")
    stale_issue = _issue("999", "Stale unresolved boom", 250, last_seen=stale_iso)

    pool = _make_pool(
        settings={
            "glitchtip_triage_enabled": "true",
            "glitchtip_base_url": "http://gt:8000",
            "glitchtip_triage_org_slug": "glad-labs",
            "glitchtip_triage_alert_threshold_count": "100",
            "glitchtip_triage_alert_freshness_hours": "24",
            "glitchtip_triage_auto_resolve_patterns": "[]",
        },
        secrets={"glitchtip_triage_api_token": "tok-abc"},
    )
    client = _FakeClient(
        get_responses=[_FakeResponse(status_code=200, json_data=[stale_issue])],
    )

    notifies: list[dict] = []
    gt._alerted_ids.clear()
    try:
        summary = await gt.run_glitchtip_triage_probe(
            pool,
            notify_fn=lambda **kw: notifies.append(kw),
            http_client_factory=_factory(client),
        )
    finally:
        gt._alerted_ids.clear()

    assert summary["ok"] is True
    # Issue was seen, but freshness gate prevented the page.
    assert summary["issues_seen"] == 1
    assert summary["alerted_count"] == 0
    assert notifies == [], (
        f"Stale issue paged operator despite freshness gate: {notifies}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fresh_issue_still_alerted_when_threshold_crossed():
    """Mirror test — a fresh issue above the threshold DOES page.
    Confirms the gate isn't accidentally silencing real signal."""
    from datetime import datetime, timezone, timedelta
    fresh_iso = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat().replace("+00:00", "Z")
    fresh_issue = _issue("888", "Fresh unresolved boom", 250, last_seen=fresh_iso)

    pool = _make_pool(
        settings={
            "glitchtip_triage_enabled": "true",
            "glitchtip_base_url": "http://gt:8000",
            "glitchtip_triage_org_slug": "glad-labs",
            "glitchtip_triage_alert_threshold_count": "100",
            "glitchtip_triage_alert_freshness_hours": "24",
            "glitchtip_triage_auto_resolve_patterns": "[]",
        },
        secrets={"glitchtip_triage_api_token": "tok-abc"},
    )
    client = _FakeClient(
        get_responses=[_FakeResponse(status_code=200, json_data=[fresh_issue])],
    )

    notifies: list[dict] = []
    gt._alerted_ids.clear()
    try:
        summary = await gt.run_glitchtip_triage_probe(
            pool,
            notify_fn=lambda **kw: notifies.append(kw),
            http_client_factory=_factory(client),
        )
    finally:
        gt._alerted_ids.clear()

    assert summary["ok"] is True
    assert summary["alerted_count"] == 1
    assert len(notifies) == 1
    assert "Fresh unresolved boom" in notifies[0]["title"]
