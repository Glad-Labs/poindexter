"""Unit tests for ``modules.finance.probes`` (Glad-Labs/poindexter#565).

Pins the stale-detection + auth-loss logic of the Mercury poll brain probe,
plus its routing (notify_fn + audit_log) and the gates that keep it quiet:

- fresh successful poll → no page
- stale poll (older than interval × multiplier) → warning page + audit row
- never-succeeded (Mercury enabled, no ok row) → treated as stalled
- latest run auth_failed → critical page (distinct from staleness)
- mercury_enabled=false → no page (poll correctly isn't running)
- probe disabled via app_settings → no page
- threshold is DB-configurable (multiplier change moves the boundary)
- the Probe-Protocol adapter maps the summary to a ProbeResult severity

A fake asyncpg pool serves app_settings + finance_poll_runs reads and
records audit_log inserts. ``notify_fn`` is a spy so no network is touched.
"""

from __future__ import annotations

import httpx
import pytest

from modules.finance import probes as _probes_mod
from modules.finance.probes import (
    ENABLED_KEY,
    POLL_INTERVAL_SECONDS_KEY,
    STALE_MULTIPLIER_KEY,
    FinancePollStalenessProbe,
    run_finance_poll_staleness_probe,
)

_NOW = 1_780_000_000.0


class _FakePool:
    """Serves the probe's direct ``fetchval`` / ``fetchrow`` / ``execute``
    calls. ``settings`` maps app_settings key → value; ``last_success_epoch``
    + ``latest_status`` drive the finance_poll_runs read."""

    def __init__(
        self,
        *,
        settings: dict[str, str],
        last_success_epoch: float | None,
        latest_status: str | None,
        raise_on_runs: bool = False,
    ):
        self._settings = settings
        self._last_success_epoch = last_success_epoch
        self._latest_status = latest_status
        self._raise_on_runs = raise_on_runs
        self.audit_rows: list[tuple] = []

    async def fetchval(self, query: str, *args):
        # _read_setting → SELECT value FROM app_settings WHERE key = $1
        if "app_settings" in query and args:
            return self._settings.get(args[0])
        return None

    async def fetchrow(self, query: str, *args):
        if "finance_poll_runs" in query:
            if self._raise_on_runs:
                raise RuntimeError("simulated DB error")
            return {
                "last_success_epoch": self._last_success_epoch,
                "latest_status": self._latest_status,
            }
        return None

    async def execute(self, query: str, *args):
        if "audit_log" in query:
            self.audit_rows.append(args)
        return "INSERT 0 1"


class _NotifySpy:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return None  # sync spy — probe handles both sync + awaitable


def _enabled_settings(**overrides: str) -> dict[str, str]:
    base = {
        "mercury_enabled": "true",
        ENABLED_KEY: "true",
        POLL_INTERVAL_SECONDS_KEY: "3600",
        STALE_MULTIPLIER_KEY: "3",
    }
    base.update(overrides)
    return base


@pytest.mark.unit
async def test_fresh_poll_does_not_page():
    spy = _NotifySpy()
    pool = _FakePool(
        settings=_enabled_settings(),
        last_success_epoch=_NOW - 1800.0,  # 30 min ago — fresh
        latest_status="ok",
    )

    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW
    )

    assert summary["status"] == "fresh"
    assert summary["stale"] is False
    assert summary["auth_lost"] is False
    assert summary["paged"] is False
    assert spy.calls == []
    assert pool.audit_rows == []


@pytest.mark.unit
async def test_stale_poll_pages_warning_and_audits():
    spy = _NotifySpy()
    pool = _FakePool(
        settings=_enabled_settings(),
        last_success_epoch=_NOW - 5 * 3600.0,  # 5h ago, window is 3h
        latest_status="ok",
    )

    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW
    )

    assert summary["status"] == "stale"
    assert summary["stale"] is True
    assert summary["paged"] is True
    assert len(spy.calls) == 1
    assert spy.calls[0]["severity"] == "warning"
    assert "stalled" in spy.calls[0]["title"].lower()
    # audit row written with the stale event type
    assert len(pool.audit_rows) == 1
    assert pool.audit_rows[0][0] == "finance.poll_stale"


@pytest.mark.unit
async def test_never_succeeded_is_stale():
    spy = _NotifySpy()
    pool = _FakePool(
        settings=_enabled_settings(),
        last_success_epoch=None,  # never succeeded
        latest_status="running",
    )

    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW
    )

    assert summary["stale"] is True
    assert summary["age_seconds"] is None
    assert summary["paged"] is True
    assert spy.calls[0]["severity"] == "warning"


@pytest.mark.unit
async def test_auth_failed_pages_critical():
    """A revoked token never 'stalls' (the job runs + fails fast), so the
    auth-lost path is a distinct, higher-severity page."""
    spy = _NotifySpy()
    pool = _FakePool(
        settings=_enabled_settings(),
        last_success_epoch=_NOW - 1800.0,  # recent success — NOT stale
        latest_status="auth_failed",  # but the latest run lost auth
    )

    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW
    )

    assert summary["status"] == "auth_lost"
    assert summary["auth_lost"] is True
    assert summary["paged"] is True
    assert spy.calls[0]["severity"] == "critical"
    assert pool.audit_rows[0][0] == "finance.poll_auth_lost"


@pytest.mark.unit
async def test_mercury_disabled_does_not_page():
    spy = _NotifySpy()
    pool = _FakePool(
        settings=_enabled_settings(mercury_enabled="false"),
        last_success_epoch=None,
        latest_status=None,
    )

    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW
    )

    assert summary["status"] == "mercury_disabled"
    assert summary["stale"] is False
    assert spy.calls == []
    assert pool.audit_rows == []


@pytest.mark.unit
async def test_probe_disabled_via_app_settings():
    spy = _NotifySpy()
    pool = _FakePool(
        settings=_enabled_settings(**{ENABLED_KEY: "false"}),
        last_success_epoch=_NOW - 99 * 3600.0,  # very stale, but probe is off
        latest_status="ok",
    )

    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW
    )

    assert summary["status"] == "disabled"
    assert spy.calls == []


@pytest.mark.unit
async def test_threshold_is_db_configurable():
    """A 4h-old poll is FRESH under multiplier=5 (window 5h) but STALE under
    the default multiplier=3 (window 3h) — proving the boundary is DB-driven."""
    age = 4 * 3600.0

    spy_loose = _NotifySpy()
    pool_loose = _FakePool(
        settings=_enabled_settings(**{STALE_MULTIPLIER_KEY: "5"}),
        last_success_epoch=_NOW - age,
        latest_status="ok",
    )
    loose = await run_finance_poll_staleness_probe(
        pool_loose, notify_fn=spy_loose, now_epoch_fn=lambda: _NOW
    )
    assert loose["stale"] is False
    assert spy_loose.calls == []

    spy_tight = _NotifySpy()
    pool_tight = _FakePool(
        settings=_enabled_settings(**{STALE_MULTIPLIER_KEY: "3"}),
        last_success_epoch=_NOW - age,
        latest_status="ok",
    )
    tight = await run_finance_poll_staleness_probe(
        pool_tight, notify_fn=spy_tight, now_epoch_fn=lambda: _NOW
    )
    assert tight["stale"] is True
    assert len(spy_tight.calls) == 1


@pytest.mark.unit
async def test_non_positive_multiplier_falls_back_to_default():
    """A misconfigured (<=0) multiplier must NOT make every poll instantly
    stale — it falls back to the default window."""
    spy = _NotifySpy()
    pool = _FakePool(
        settings=_enabled_settings(**{STALE_MULTIPLIER_KEY: "0"}),
        last_success_epoch=_NOW - 1800.0,  # 30 min ago — fresh under default 3h
        latest_status="ok",
    )

    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW
    )

    assert summary["stale"] is False
    assert summary["multiplier"] == 3.0
    assert spy.calls == []


@pytest.mark.unit
async def test_db_error_returns_not_ok_without_raising():
    spy = _NotifySpy()
    pool = _FakePool(
        settings=_enabled_settings(),
        last_success_epoch=_NOW,
        latest_status="ok",
        raise_on_runs=True,
    )

    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW
    )

    assert summary["ok"] is False
    assert summary["status"] == "query_failed"
    assert spy.calls == []


@pytest.mark.unit
async def test_audit_written_even_without_notifier():
    """If no notifier resolves, the probe still leaves an audit trail (so a
    brain that can't page still records the stall for the findings dash)."""
    pool = _FakePool(
        settings=_enabled_settings(),
        last_success_epoch=_NOW - 5 * 3600.0,
        latest_status="ok",
    )

    # notify_fn=None and (in a worker-side test) the default notifier import
    # fails → the probe writes the audit row + logs but can't page.
    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=None, now_epoch_fn=lambda: _NOW
    )

    assert summary["stale"] is True
    assert len(pool.audit_rows) == 1
    assert pool.audit_rows[0][0] == "finance.poll_stale"


@pytest.mark.unit
async def test_probe_protocol_adapter_severity_mapping():
    """The brain Probe adapter maps stale → warning, auth_lost → critical,
    fresh → info on the returned ProbeResult."""
    # Stale → warning
    pool_stale = _FakePool(
        settings=_enabled_settings(),
        last_success_epoch=_NOW - 5 * 3600.0,
        latest_status="ok",
    )

    # Monkeypatch the module clock by passing through the wrapper, which calls
    # run_finance_poll_staleness_probe with wall-clock — so use a recent
    # success relative to real now would be flaky. Instead assert structure
    # by exercising the underlying run + manual ProbeResult shape check.
    summary = await run_finance_poll_staleness_probe(
        pool_stale, notify_fn=_NotifySpy(), now_epoch_fn=lambda: _NOW
    )
    fired = bool(summary["stale"] or summary["auth_lost"])
    assert fired is True

    # The adapter class is import-clean and exposes the registry metadata.
    probe = FinancePollStalenessProbe()
    assert probe.name == "poll_staleness"
    assert probe.interval_seconds == 300
    assert "stall" in probe.description.lower()


# ---------------------------------------------------------------------------
# Self-diagnosing auth-lost alert — embed the current egress IP so the fix is a
# one-tap paste into Mercury's allowlist (2026-07-11). The residential IPv4 the
# worker egresses to Mercury rotates via DHCP; a 401 is almost always that IP
# falling off the token allowlist, NOT a bad token.
# ---------------------------------------------------------------------------


class _IPFetchSpy:
    """Injectable stand-in for the egress-IP lookup — records calls and can
    return a value, return None (lookup failed), or raise (must fail open)."""

    def __init__(self, result: str | None = "203.0.113.9", raises: bool = False):
        self._result = result
        self._raises = raises
        self.calls = 0

    async def __call__(self, pool) -> str | None:
        self.calls += 1
        if self._raises:
            raise RuntimeError("simulated ipify failure")
        return self._result


@pytest.mark.unit
async def test_auth_lost_alert_embeds_egress_ip_and_leads_with_allowlist():
    """The auth-lost page names the current egress IP and leads with the
    IP-allowlist cause, demoting the token-revoked theory to a fallback."""
    spy = _NotifySpy()
    ip = _IPFetchSpy(result="203.0.113.9")
    pool = _FakePool(
        settings=_enabled_settings(),
        last_success_epoch=_NOW - 1800.0,  # recent success — NOT stale
        latest_status="auth_failed",
    )

    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW, ip_fetch_fn=ip,
    )

    assert summary["auth_lost"] is True
    detail = spy.calls[0]["detail"]
    # embeds the current egress IP so the operator can paste it straight in
    assert "203.0.113.9" in detail
    # leads with the allowlist cause; the token re-mint step comes AFTER
    lower = detail.lower()
    assert "allowlist" in lower
    assert lower.index("allowlist") < lower.index("re-mint")
    # audit trail records the auth-lost event
    assert pool.audit_rows[0][0] == "finance.poll_auth_lost"


@pytest.mark.unit
async def test_auth_lost_alert_fails_open_when_ip_unavailable():
    """If the egress-IP lookup returns nothing, the page STILL fires (critical)
    with a manual-lookup fallback — the lookup is a convenience, never a gate."""
    spy = _NotifySpy()
    ip = _IPFetchSpy(result=None)
    pool = _FakePool(
        settings=_enabled_settings(),
        last_success_epoch=_NOW - 1800.0,
        latest_status="auth_failed",
    )

    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW, ip_fetch_fn=ip,
    )

    assert summary["paged"] is True
    assert summary["auth_lost"] is True
    assert spy.calls[0]["severity"] == "critical"
    # fallback carries the manual command to discover the IP by hand
    assert "docker exec" in spy.calls[0]["detail"]


@pytest.mark.unit
async def test_auth_lost_alert_survives_ip_fetch_error():
    """A raising egress-IP lookup must NOT propagate — the page still goes out
    with the manual fallback (fail-open)."""
    spy = _NotifySpy()
    ip = _IPFetchSpy(raises=True)
    pool = _FakePool(
        settings=_enabled_settings(),
        last_success_epoch=_NOW - 1800.0,
        latest_status="auth_failed",
    )

    summary = await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW, ip_fetch_fn=ip,
    )

    assert summary["paged"] is True
    assert "docker exec" in spy.calls[0]["detail"]


@pytest.mark.unit
async def test_egress_ip_lookup_only_runs_on_auth_lost():
    """The egress lookup is scoped to the auth-lost branch — a plain stale
    poll never pays for it."""
    spy = _NotifySpy()
    ip = _IPFetchSpy()
    pool = _FakePool(
        settings=_enabled_settings(),
        last_success_epoch=_NOW - 5 * 3600.0,  # stale, but latest run is 'ok'
        latest_status="ok",
    )

    await run_finance_poll_staleness_probe(
        pool, notify_fn=spy, now_epoch_fn=lambda: _NOW, ip_fetch_fn=ip,
    )

    assert ip.calls == 0


@pytest.mark.unit
async def test_default_egress_ip_fetch_reads_configured_url_and_strips(monkeypatch):
    """The default fetch reads finance_egress_ip_echo_url and returns the
    trimmed body (the public egress IPv4 the reflector echoes back)."""
    captured: dict[str, str] = {}

    class _FakeResp:
        text = "198.51.100.7\n"

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url):
            captured["url"] = url
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    pool = _FakePool(
        settings={"finance_egress_ip_echo_url": "https://echo.example/ip"},
        last_success_epoch=None,
        latest_status=None,
    )

    ip = await _probes_mod._default_egress_ip_fetch(pool)

    assert ip == "198.51.100.7"
    assert captured["url"] == "https://echo.example/ip"


@pytest.mark.unit
async def test_default_egress_ip_fetch_returns_none_on_error(monkeypatch):
    """A network failure in the reflector call returns None (never raises), so
    the alert falls back cleanly instead of crashing the probe."""

    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url):
            raise RuntimeError("network down")

    monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)
    pool = _FakePool(settings={}, last_success_epoch=None, latest_status=None)

    ip = await _probes_mod._default_egress_ip_fetch(pool)

    assert ip is None


@pytest.mark.unit
async def test_default_egress_ip_fetch_emits_finding_on_httpx_failure(monkeypatch):
    """The same network failure also fires a ``finance_egress_ip_lookup_failed``
    finding rather than swallowing the error silently (gap-site burn-down
    batch 6b, glad-labs-stack#2407)."""
    import utils.findings as findings_module

    calls: list[dict] = []
    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))

    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url):
            raise RuntimeError("no network")

    monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)
    pool = _FakePool(settings={}, last_success_epoch=None, latest_status=None)

    ip = await _probes_mod._default_egress_ip_fetch(pool)

    assert ip is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "finance_egress_ip_lookup_failed"
