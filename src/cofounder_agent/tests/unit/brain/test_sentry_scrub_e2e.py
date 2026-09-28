"""End to end: what the worker and the brain send to GlitchTip has no credentials.

Each test starts a fresh interpreter, runs a process's real Sentry init (the
brain's ``_init_sentry``, the worker's ``SentryIntegration.initialize``)
against a real ``sentry_sdk`` client, makes the requests that leaked, captures
an event, and hands back every envelope item the client produced. Only the
network is replaced: ``sentry_sdk.init`` gets a transport that collects
envelopes instead of posting them, and HTTPS goes to an in-memory socket, so
the SDK's own stdlib (``http.client``) integration records each request exactly
as it does for the brain's ``urllib`` pages. No socket is opened.

A fresh interpreter because ``sentry_sdk.init`` patches ``http.client``,
Starlette, threading and asyncio for the life of the process, and because the
child imports exactly this tree (``PYTHONPATH``), never an editable install
that points somewhere else.

Every token is fake. The assertions check both halves: the redacted request is
really there (so a breadcrumb that was never recorded cannot pass as a clean
one), and no fake secret appears anywhere in what would have been sent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import poindexter

pytestmark = pytest.mark.unit

_SRC_ROOT = str(Path(poindexter.__file__).resolve().parent.parent)
_RESULT = "RESULT="

TG_URL = "https://api.telegram.org/bot123456789:FAKEtelegramTOKENe2e/sendMessage"
DC_URL = "https://discord.com/api/webhooks/123456789/FAKE-discord-TOKEN_e2e"
DSN_ARG = "postgresql://poindexter:FAKEPWe2e@postgres-local:5432/poindexter_brain"
SECRETS = ("FAKEtelegramTOKENe2e", "FAKE-discord-TOKEN_e2e", "FAKEPWe2e", "FAKEquerye2e")

# Shared by both children: the collecting transport, the in-memory HTTPS
# connection, and the traffic that leaked in production.
_PREAMBLE = f"TG_URL = {TG_URL!r}\nDC_URL = {DC_URL!r}\nDSN_ARG = {DSN_ARG!r}\n" + r'''
import http.client, io, json, subprocess, sys, urllib.request

import sentry_sdk
from sentry_sdk.transport import Transport

ITEMS = []

class Collect(Transport):
    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type in ("event", "transaction"):
                ITEMS.append(item.payload.json)

INIT_KWARGS = {}
_real_init = sentry_sdk.init

def _init(*args, **kwargs):
    INIT_KWARGS.update({k: repr(v) for k, v in kwargs.items()})
    kwargs["transport"] = Collect
    return _real_init(*args, **kwargs)

sentry_sdk.init = _init

class _Socket:
    def sendall(self, data):
        pass
    def makefile(self, mode, *args, **kwargs):
        return io.BytesIO(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
    def close(self):
        pass

class _Conn(http.client.HTTPSConnection):
    def connect(self):
        self.sock = _Socket()

class _Handler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_Conn, req)

OPENER = urllib.request.build_opener(_Handler())

def post(url):
    """The brain's page, shape for shape: urllib -> http.client."""
    req = urllib.request.Request(url, data=b"{}", method="POST")
    OPENER.open(req, timeout=5).read()

def send_discord(message):
    webhook_url = DC_URL  # the local that GlitchTip stored from brain_daemon.send_discord
    post_url = webhook_url + "?wait=true"
    raise ConnectionError("discord unreachable")

def traffic():
    post(TG_URL)
    post(DC_URL)
    subprocess.run([sys.executable, "-c", "pass", DSN_ARG], check=True)
    try:
        send_discord("page")
    except ConnectionError as exc:
        sentry_sdk.capture_exception(exc)
    sentry_sdk.capture_message("e2e probe")

def result(**extra):
    sentry_sdk.flush(2)
    print("RESULT=" + json.dumps({"items": ITEMS, "init_kwargs": INIT_KWARGS, **extra}))
'''


def _run(body: str, home: Path) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),  # no ~/.poindexter/bootstrap.toml, so no real DSN
        "PYTHONPATH": _SRC_ROOT,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    code = _PREAMBLE + textwrap.dedent(body)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=home,
        env=env,
        timeout=180,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}"
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith(_RESULT)), None)
    assert line is not None, f"no result line:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    return json.loads(line[len(_RESULT):])


def _events(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("type") != "transaction"]


def _probe(items: list[dict]) -> dict:
    return next(e for e in _events(items) if e.get("message") == "e2e probe")


def _crumbs(event: dict) -> list[dict]:
    return (event.get("breadcrumbs") or {}).get("values") or []


def _assert_no_secret(payload: dict) -> None:
    dumped = json.dumps(payload)
    for secret in SECRETS:
        assert secret not in dumped, f"{secret} reached the transport"


def _assert_pages_redacted(event: dict) -> None:
    urls = [(c.get("data") or {}).get("url") for c in _crumbs(event) if c.get("type") == "http"]
    assert "https://api.telegram.org/bot[Filtered]/sendMessage" in urls, urls
    assert "https://discord.com/api/webhooks/123456789/[Filtered]" in urls, urls
    commands = [c.get("message") or "" for c in _crumbs(event) if c.get("category") == "subprocess"]
    assert any("postgresql://poindexter:[Filtered]@postgres-local" in m for m in commands), commands


def _assert_no_locals(items: list[dict]) -> None:
    exc_events = [e for e in _events(items) if e.get("exception")]
    assert exc_events, "the send_discord exception was not captured"
    frames = [
        frame
        for event in exc_events
        for value in event["exception"]["values"]
        for frame in (value.get("stacktrace") or {}).get("frames") or []
    ]
    assert any(f.get("function") == "send_discord" for f in frames), "positive control: frame missing"
    assert not [f for f in frames if "vars" in f], "stack-frame locals were sent"


def test_brain_sends_no_credentials(tmp_path):
    """The brain's pages go through urllib, which the SDK's default stdlib
    integration records URL and all: GlitchTip held the brain's Telegram and
    Discord tokens that way, and send_discord's locals besides (2026-09-28)."""
    result = _run(
        """
        import asyncio
        from poindexter.brain import brain_daemon as bd

        SETTINGS = {"sentry_dsn": "http://public@127.0.0.1:9/1", "sentry_environment": "e2e"}

        async def fake_read(pool, key, default=""):
            return SETTINGS.get(key, default)

        bd._read_app_setting = fake_read
        assert asyncio.run(bd._init_sentry(object())) is True
        traffic()
        result()
        """,
        tmp_path,
    )
    items = result["items"]
    _assert_pages_redacted(_probe(items))
    _assert_no_locals(items)
    _assert_no_secret(result)
    assert result["init_kwargs"]["include_local_variables"] == "False"


def test_worker_sends_no_credentials(tmp_path):
    """The worker and the Prefect flow runs: stdlib and httpx breadcrumbs (httpx
    re-enabled through sentry_extra_integrations, as an operator could), a
    transaction whose span names carry the URL, and the fingerprint the worker
    builds from exception text."""
    result = _run(
        """
        import httpx
        from poindexter.services.sentry_integration import SentryIntegration
        from poindexter.services.site_config import SiteConfig

        site_config = SiteConfig(initial_config={
            "sentry_dsn": "http://public@127.0.0.1:9/1",
            "sentry_enabled": "true",
            "sentry_traces_sample_rate": "1.0",
            "sentry_profiles_sample_rate": "0",
            "sentry_extra_integrations": "httpx",
        })
        assert SentryIntegration.initialize(None, site_config, service_name="e2e") is True

        def respond(request):
            return httpx.Response(204)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            client.post(DC_URL + "?token=FAKEquerye2e")
        with sentry_sdk.start_transaction(name="e2e-tx", op="task"):
            post(TG_URL)
        traffic()
        # The fingerprint is built from the exception value, and it ships too.
        try:
            raise RuntimeError("POST " + TG_URL + " failed after 12.5s")
        except RuntimeError as exc:
            sentry_sdk.capture_exception(exc)
        result()
        """,
        tmp_path,
    )
    items = result["items"]
    probe = _probe(items)
    _assert_pages_redacted(probe)

    # Only the httpx request had a query string, so this is its breadcrumb.
    queries = [(c.get("data") or {}).get("http.query") for c in _crumbs(probe)]
    assert "token=[Filtered]" in queries, "positive control: the httpx breadcrumb is missing"

    transaction = next(i for i in items if i.get("type") == "transaction")
    descriptions = [s.get("description") or "" for s in transaction.get("spans") or []]
    assert any("api.telegram.org/bot[Filtered]/sendMessage" in d for d in descriptions), descriptions

    fingerprinted = next(
        e for e in _events(items)
        if "failed after" in str((e.get("exception") or {}).get("values"))
    )
    assert fingerprinted.get("fingerprint"), "positive control: the fingerprint was not rewritten"
    assert "bot[Filtered]" in json.dumps(fingerprinted["fingerprint"])

    _assert_no_locals(items)
    _assert_no_secret(result)
    assert result["init_kwargs"]["include_local_variables"] == "False"
