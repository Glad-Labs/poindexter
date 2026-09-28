"""``restart_host_service``: the firefighter restarts a host unit (Ollama).

The executor restarts an allowlisted host service through the host Recovery
Agent, the way the probe path does (``health_probes._call_agent_recovery``),
but only after the brain has asked the service itself and got no answer. A
service that answers is up, a restart would only kill its in-flight work, and
the action refuses so the alert pages. See
``poindexter/brain/remediation/host_services.py`` for why that check, and not
the GPU advisory lock, gates the restart.

Only the two network edges are faked here: the brain's own ``/api/tags``
request (``host_services._http_status``) and the agent POST
(``health_probes._call_agent_recovery``). Everything between them is the
production code.
"""
from __future__ import annotations

import http.client
import importlib.util
import logging
import re
import urllib.error
from pathlib import Path

import pytest

from poindexter.brain import health_probes as hp
from poindexter.brain.remediation import engine as E
from poindexter.brain.remediation import host_services as hs
from poindexter.brain.remediation import registry as reg
from poindexter.brain.remediation import rules as R
from poindexter.brain.remediation.registry import RemediationContext
from tests.unit.brain._remediation_fakes import FakePool

OLLAMA_URL = "http://host.docker.internal:11434"
TAGS = f"{OLLAMA_URL}/api/tags"
AGENT_OK = "HTTP 200 — restarted ollama-primary.service (system)"


@pytest.fixture(autouse=True)
def _brain_env(monkeypatch):
    """The brain container sets OLLAMA_URL; pin it so no test reads the host's.
    And no real second between attempts."""
    monkeypatch.setenv("OLLAMA_URL", OLLAMA_URL)
    monkeypatch.setattr(hs, "OLLAMA_CONFIRM_GAP_SECONDS", 0)


class _Ollama:
    """``_http_status`` from a script: each attempt takes the next answer (an
    HTTP status, or None for silence). Silent once the script runs out."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        return self.answers.pop(0) if self.answers else None


class _Agent:
    """``health_probes._call_agent_recovery``, recording what it was asked."""

    def __init__(self, ok=True, detail=AGENT_OK):
        self.ok = ok
        self.detail = detail
        self.calls: list[tuple[str, float]] = []

    async def __call__(self, pool, service, *, timeout=15.0):
        self.calls.append((service, timeout))
        return self.ok, self.detail


def _install(monkeypatch, *, ollama=None, agent=None):
    ollama = ollama if ollama is not None else _Ollama()
    agent = agent if agent is not None else _Agent()
    monkeypatch.setattr(hs, "_http_status", ollama)
    monkeypatch.setattr(hp, "_call_agent_recovery", agent)
    return ollama, agent


def _ctx(pool=None):
    return RemediationContext(pool=pool or FakePool(), alert={}, logger=logging.getLogger("t"))


OLLAMA = {"service": "ollama"}


# --- the restart ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_confirmed_outage_restarts_ollama_through_the_agent(monkeypatch):
    ollama, agent = _install(monkeypatch)
    result = await reg.execute("restart_host_service", OLLAMA, _ctx())
    assert result.status == "ok"
    assert agent.calls == [("ollama", hs.AGENT_TIMEOUT_SECONDS)]
    assert [url for url, _ in ollama.calls] == [TAGS] * hs.OLLAMA_CONFIRM_ATTEMPTS
    # The record names what the agent did and why the brain let it.
    assert result.detail.startswith(f"recovery agent: {AGENT_OK}; confirmed down first: ")
    assert f"no answer from {TAGS} on 2 tries (5 s each)" in result.detail


def _worker_gauge_timeout() -> float:
    """The timeout the worker's ``poindexter_ollama_reachable`` check gives
    /api/tags, read from ``metrics_exporter.refresh_metrics`` itself."""
    for parent in Path(hp.__file__).resolve().parents:
        source = parent / "services" / "metrics_exporter.py"
        if source.is_file():
            break
    else:  # pragma: no cover
        raise AssertionError("services/metrics_exporter.py not found above health_probes")
    found = re.search(r'/api/tags",\s*timeout=([0-9.]+)', source.read_text(encoding="utf-8"))
    assert found, "metrics_exporter no longer states its /api/tags timeout inline"
    return float(found.group(1))


@pytest.mark.asyncio
async def test_each_attempt_waits_longer_than_the_workers_check(monkeypatch):
    """The alert's gauge gives /api/tags its own timeout. The brain's check
    must be more patient, or it could read as silence a slow answer the alert
    itself would have counted."""
    ollama, _ = _install(monkeypatch)
    await reg.execute("restart_host_service", OLLAMA, _ctx())
    assert {timeout for _, timeout in ollama.calls} == {hs.OLLAMA_CONFIRM_TIMEOUT_SECONDS}
    assert hs.OLLAMA_CONFIRM_TIMEOUT_SECONDS > _worker_gauge_timeout()


@pytest.mark.parametrize("status", [200, 404, 500, 503])
@pytest.mark.asyncio
async def test_an_ollama_that_answers_is_never_restarted(monkeypatch, status):
    """Any HTTP answer means the server is up: the alert's vantage point failed,
    or Ollama is broken in a way a restart doesn't fix. Either way a restart
    would kill live work, so it refuses (and the alert pages)."""
    ollama, agent = _install(monkeypatch, ollama=_Ollama(status))
    result = await reg.execute("restart_host_service", OLLAMA, _ctx())
    assert result.status == "skipped"
    assert agent.calls == []
    assert len(ollama.calls) == 1  # the first answer settles it
    assert f"Ollama answers {TAGS} from the brain (HTTP {status})" in result.detail


@pytest.mark.asyncio
async def test_an_answer_on_the_second_try_still_refuses(monkeypatch):
    """Silence has to hold on every try; one blip of the brain's own is not an
    outage."""
    ollama, agent = _install(monkeypatch, ollama=_Ollama(None, 200))
    result = await reg.execute("restart_host_service", OLLAMA, _ctx())
    assert result.status == "skipped"
    assert agent.calls == []
    assert len(ollama.calls) == 2


@pytest.mark.parametrize(
    "agent_detail",
    [
        "HTTP 500 — Job for ollama-primary.service failed because the control process exited",
        "recovery agent URL/token not configured in app_settings",
        "HTTP 401 — invalid token",
        "ConnectError: All connection attempts failed",
    ],
    ids=["systemd-failed", "unconfigured", "bad-token", "agent-down"],
)
@pytest.mark.asyncio
async def test_an_agent_that_does_not_restart_it_is_a_failed_action(monkeypatch, agent_detail):
    """Failed pages now (the engine holds only an ok). The agent's reason
    leads the detail, because the page carries only its first 200 characters."""
    _install(monkeypatch, agent=_Agent(ok=False, detail=agent_detail))
    result = await reg.execute("restart_host_service", OLLAMA, _ctx())
    assert result.status == "failed"
    assert result.detail.startswith(f"recovery agent: {agent_detail}")


# --- what it refuses ------------------------------------------------------


@pytest.mark.asyncio
async def test_no_service_param_is_refused_before_anything_is_asked(monkeypatch):
    ollama, agent = _install(monkeypatch)
    result = await reg.execute("restart_host_service", {}, _ctx())
    assert (result.status, result.detail) == ("skipped", "restart_host_service: no 'service' param")
    assert ollama.calls == [] and agent.calls == []


@pytest.mark.parametrize(
    "service", ["compose-reapply", "mcp-http", "ollama-vision", "docker", "OLLAMA"],
)
@pytest.mark.asyncio
async def test_only_allowlisted_services_are_restarted(monkeypatch, service):
    """The agent can do more than restart Ollama: ``compose-reapply`` recreates
    every drifted container. Only HOST_SERVICES, each with its own is-it-down
    check, is reachable through this action."""
    ollama, agent = _install(monkeypatch)
    result = await reg.execute("restart_host_service", {"service": service}, _ctx())
    assert result.status == "skipped"
    assert f"{service!r} is not a host service the firefighter may restart (allowed: ollama)" in result.detail
    assert ollama.calls == [] and agent.calls == []


@pytest.mark.asyncio
async def test_a_url_that_is_not_http_refuses_without_asking(monkeypatch):
    """A misconfigured URL must not read as silence: silence restarts."""
    monkeypatch.setenv("OLLAMA_URL", "file:///etc/hostname")
    ollama, agent = _install(monkeypatch)
    result = await reg.execute("restart_host_service", OLLAMA, _ctx())
    assert result.status == "skipped"
    assert "is not an http(s) URL" in result.detail
    assert ollama.calls == [] and agent.calls == []


@pytest.mark.asyncio
async def test_a_check_that_raises_fails_closed(monkeypatch):
    """Anything the check can't classify stops the restart: the executor
    fails (and pages), and a dry run's refusal says so."""
    def _garbled(url, timeout):
        raise http.client.BadStatusLine("garbage")

    _ollama, agent = _install(monkeypatch)
    monkeypatch.setattr(hs, "_http_status", _garbled)
    result = await reg.execute("restart_host_service", OLLAMA, _ctx())
    assert result.status == "failed"
    assert agent.calls == []
    assert (await reg.refusal("restart_host_service", OLLAMA, _ctx())).startswith("refusal check raised")


@pytest.mark.asyncio
async def test_the_check_asks_the_url_the_brain_resolves(monkeypatch):
    """The container's OLLAMA_URL wins (it is set in compose); without it the
    brain reads app_settings.ollama_base_url, the row the worker's gauge reads.
    A host name that localize_url leaves alone keeps this independent of
    whether the test itself runs in a container."""
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    pool = FakePool()
    pool.set_fetchval(lambda sql, args: "http://ollama.test:11434/" if args == ("ollama_base_url",) else None)
    ollama, _ = _install(monkeypatch)
    await reg.execute("restart_host_service", OLLAMA, _ctx(pool))
    assert {url for url, _ in ollama.calls} == {"http://ollama.test:11434/api/tags"}


@pytest.mark.parametrize(
    "params,answers",
    [({}, ()), ({"service": "compose-reapply"}, ()), (OLLAMA, (200,))],
    ids=["no-param", "off-the-allowlist", "ollama-answers"],
)
@pytest.mark.asyncio
async def test_the_refusal_check_is_the_executors_own(monkeypatch, params, answers):
    """A dry run asks ``refusal`` instead of running the executor. Both call
    the same check, so they cannot disagree."""
    _install(monkeypatch, ollama=_Ollama(*answers))
    refused = await reg.refusal("restart_host_service", params, _ctx())
    _, agent = _install(monkeypatch, ollama=_Ollama(*answers))
    executed = await reg.execute("restart_host_service", params, _ctx())
    assert refused and refused == executed.detail
    assert agent.calls == []


@pytest.mark.asyncio
async def test_asking_whether_it_would_run_restarts_nothing(monkeypatch):
    _, agent = _install(monkeypatch)
    assert await reg.refusal("restart_host_service", OLLAMA, _ctx()) is None
    assert agent.calls == []


# --- how the page reads -----------------------------------------------------


@pytest.mark.asyncio
async def test_the_refusal_survives_the_pages_200_character_cut(monkeypatch):
    """The dispatcher appends ``Not auto-remediated (<action>: <reason>)`` to
    the page, and the engine cuts that reason at 200 characters. The reason
    the operator most needs, that Ollama answered, must fit whole."""
    _install(monkeypatch, ollama=_Ollama(200))
    result = await reg.execute("restart_host_service", OLLAMA, _ctx())
    reason = f"action {result.status}: {result.detail}"
    assert len(reason) <= 200, (len(reason), reason)


# --- the rules-only guarantee ------------------------------------------------


def test_restart_host_service_is_never_offered_to_the_llm():
    """Its only target is Ollama, which the selector itself runs on. Not in the
    catalog, even when the allowlist names it."""
    assert reg.is_rules_only("restart_host_service")
    assert "restart_host_service" not in {a["name"] for a in reg.describe_catalog()}
    assert reg.describe_catalog(allowlist=["restart_host_service"]) == []


@pytest.mark.asyncio
async def test_an_llm_that_names_it_anyway_is_refused_as_off_catalog(monkeypatch):
    """The engine validates a pick against the catalog it offered, so a model
    that answers "restart Ollama" for a pipeline finding runs nothing."""
    ollama, agent = _install(monkeypatch)
    offered: list[list[str]] = []

    async def _select(*, alert, catalog):
        offered.append([a["name"] for a in catalog])
        return {"action_name": "restart_host_service", "params": OLLAMA, "confidence": 0.99}

    async def _no_rule(*a, **k):
        return None

    monkeypatch.setattr(R, "match_rule", _no_rule)
    config = {
        "enabled": True, "max_attempts_per_window": 3, "window_minutes": 60,
        "verify_after_seconds": 120, "alertmanager_verify_after_seconds": 600,
        "max_actions_per_hour": 10, "action_allowlist": [], "llm_longtail_enabled": True,
        "min_repeats": 2, "min_age_minutes": 10, "min_confidence": 0.6,
        "llm_exclude_regex": r"(?i)(ollama|gpu|vram|cuda|inference)", "llm_dry_run": False,
    }
    alert = {"labels": {"alertname": "qa_rail_degraded", "severity": "warning"}, "annotations": {}}
    decision = await E.evaluate_for_dispatch(
        FakePool(), alert=alert, fingerprint="fp", config=config,
        logger=logging.getLogger("t"), select_fn=_select, repeat_count=5,
    )
    assert decision.acted is False
    assert decision.reason == "llm picked off-catalog action"
    assert offered == [["restart_container", "run_auto_remediate"]]
    assert ollama.calls == [] and agent.calls == []


# --- the agent call ---------------------------------------------------------


def _load_recovery_agent():
    here = Path(__file__).resolve()
    root = next(p for p in here.parents if (p / "scripts" / "recovery-agent.py").is_file())
    spec = importlib.util.spec_from_file_location(
        "recovery_agent_for_host_services", root / "scripts" / "recovery-agent.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_the_agent_call_is_the_probe_paths_with_a_longer_wait(monkeypatch):
    agent = _Agent()
    monkeypatch.setattr(hp, "_call_agent_recovery", agent)
    assert await hs.restart_via_agent(FakePool(), "ollama") == (True, AGENT_OK)
    assert agent.calls == [("ollama", hs.AGENT_TIMEOUT_SECONDS)]


def test_the_brain_waits_longer_than_the_agent_waits_on_systemd():
    """The agent gives ``systemctl restart`` TASK_TIMEOUT_SECONDS and then
    answers with systemd's error. A shorter wait here would record a restart
    that is still running, and may yet work, as a failure."""
    agent = _load_recovery_agent()
    assert hs.AGENT_TIMEOUT_SECONDS > agent.TASK_TIMEOUT_SECONDS


def test_every_allowlisted_service_is_a_unit_restart_the_agent_knows():
    """Each HOST_SERVICES key must be a systemd restart in the agent's Linux
    registry. ``compose-reapply`` is in that registry too, and must never be
    reachable through this action: it recreates every drifted container."""
    agent = _load_recovery_agent()
    for name, spec in hs.HOST_SERVICES.items():
        entry = agent._LINUX_SERVICES.get(name)
        assert entry is not None, f"the recovery agent has no {name!r}"
        assert entry["kind"] == "systemd", entry
        assert entry["unit"] in spec.restarts, (entry["unit"], spec.restarts)
    assert "compose-reapply" not in hs.HOST_SERVICES
    assert agent._LINUX_SERVICES["ollama"]["unit"] == "ollama-primary.service"


# --- the HTTP check itself ----------------------------------------------------


class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (_Response(200), 200),
        (urllib.error.HTTPError(TAGS, 503, "busy", {}, None), 503),
        (urllib.error.URLError(ConnectionRefusedError(111, "refused")), None),
        (urllib.error.URLError(TimeoutError("timed out")), None),
        (TimeoutError("timed out"), None),
        (ConnectionResetError(104, "reset"), None),
        (http.client.RemoteDisconnected("closed"), None),
    ],
    ids=["200", "http-error", "refused", "url-timeout", "read-timeout", "reset", "remote-disconnected"],
)
def test_http_status_tells_an_answer_from_silence(monkeypatch, outcome, expected):
    def _urlopen(request, timeout):
        assert request.full_url == TAGS and timeout == 5.0
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(hs.urllib.request, "urlopen", _urlopen)
    assert hs._http_status(TAGS, 5.0) == expected


def test_http_status_raises_what_it_cannot_classify(monkeypatch):
    def _urlopen(request, timeout):
        raise http.client.BadStatusLine("garbage")

    monkeypatch.setattr(hs.urllib.request, "urlopen", _urlopen)
    with pytest.raises(http.client.BadStatusLine):
        hs._http_status(TAGS, 5.0)
