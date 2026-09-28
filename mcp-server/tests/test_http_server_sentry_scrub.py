"""The connector's Sentry client keeps credentials out of GlitchTip.

``_init_sentry`` wires the same scrubber as the worker and the brain
(``poindexter/brain/sentry_scrub.py``): every breadcrumb and event is scrubbed
of credential-shaped text, and stack-frame locals stay home unless
``sentry_include_local_variables`` says otherwise. GlitchTip held a Discord
webhook token, a Telegram bot token, the Postgres password and API keys from
the other processes (2026-09-28); this pins that the connector cannot add to it.

Runs in a fresh interpreter against a real ``sentry_sdk`` client. Only the
edges are replaced: the settings read (no database), the transport (envelopes
are collected, not posted) and HTTPS (an in-memory socket, so the SDK's stdlib
integration records the request exactly as it would a real one). Every token
is fake.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

TG_URL = "https://api.telegram.org/bot123456789:FAKEmcpTOKEN/sendMessage"
DC_URL = "https://discord.com/api/webhooks/123456789/FAKE-mcp-discord-TOKEN"
SECRETS = ("FAKEmcpTOKEN", "FAKE-mcp-discord-TOKEN")

SCRIPT = r'''
import http.client, io, json, sys, urllib.request
sys.path.insert(0, %(here)r)
import http_server
http_server._ensure_poindexter_on_path()

import asyncpg
import sentry_sdk
from sentry_sdk.transport import Transport

ITEMS, KWARGS = [], {}

class Collect(Transport):
    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type in ("event", "transaction"):
                ITEMS.append(item.payload.json)

_real_init = sentry_sdk.init
def _init(*args, **kwargs):
    KWARGS.update({k: repr(v) for k, v in kwargs.items()})
    kwargs["transport"] = Collect
    return _real_init(*args, **kwargs)
sentry_sdk.init = _init

SETTINGS = {"sentry_dsn": "http://public@127.0.0.1:9/1", "sentry_enabled": "true"}
SETTINGS.update(%(settings)r)

class _Pool:
    async def close(self):
        pass

async def _create_pool(*args, **kwargs):
    return _Pool()

asyncpg.create_pool = _create_pool

import poindexter.brain.secret_reader as secret_reader

async def _read(pool, key, default=""):
    return SETTINGS.get(key, default)

secret_reader.read_app_setting = _read

assert http_server._init_sentry() is True

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

opener = urllib.request.build_opener(_Handler())
opener.open(urllib.request.Request(%(tg_url)r, data=b"{}", method="POST"), timeout=5).read()

def notify():
    webhook_url = %(dc_url)r
    target = webhook_url
    raise ConnectionError("unreachable")

try:
    notify()
except ConnectionError as exc:
    sentry_sdk.capture_exception(exc)
sentry_sdk.capture_message("mcp probe")
sentry_sdk.flush(2)
print("RESULT=" + json.dumps({"items": ITEMS, "kwargs": KWARGS}))
'''


def _run(tmp_path: Path, settings: dict[str, str]) -> dict:
    env = dict(os.environ)
    env.update({
        "HOME": str(tmp_path),  # no ~/.poindexter/bootstrap.toml
        "DATABASE_URL": "postgresql://nobody:nothing@127.0.0.1:1/nowhere",
        "POINDEXTER_SECRET_KEY": "unit-test-key",
    })
    code = SCRIPT % {"here": str(HERE), "settings": settings, "tg_url": TG_URL, "dc_url": DC_URL}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, timeout=120, cwd=str(HERE),
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT="))
    return json.loads(line[len("RESULT="):])


def _frames(items: list[dict]) -> list[dict]:
    return [
        frame
        for item in items
        for value in (item.get("exception") or {}).get("values") or []
        for frame in (value.get("stacktrace") or {}).get("frames") or []
    ]


def test_connector_sends_no_credentials(tmp_path: Path) -> None:
    result = _run(tmp_path, {})
    items = result["items"]

    probe = next(i for i in items if i.get("message") == "mcp probe")
    urls = [
        (crumb.get("data") or {}).get("url")
        for crumb in (probe.get("breadcrumbs") or {}).get("values") or []
    ]
    # Positive control: the request was recorded, redacted.
    assert "https://api.telegram.org/bot[Filtered]/sendMessage" in urls, urls

    frames = _frames(items)
    assert any(f.get("function") == "notify" for f in frames), "the exception was not captured"
    assert not [f for f in frames if "vars" in f], "stack-frame locals were sent"

    dumped = json.dumps(result)
    for secret in SECRETS:
        assert secret not in dumped
    assert result["kwargs"]["include_local_variables"] == "False"
    for hook in ("before_breadcrumb", "before_send", "before_send_transaction"):
        assert hook in result["kwargs"]


def test_local_variables_follow_the_setting(tmp_path: Path) -> None:
    """Turned on, locals ship, and the scrubber still runs over them: a local
    named as a secret is filtered whole, any other is pattern-scrubbed."""
    result = _run(tmp_path, {"sentry_include_local_variables": "true"})
    assert result["kwargs"]["include_local_variables"] == "True"
    frame = next(f for f in _frames(result["items"]) if f.get("function") == "notify")
    assert frame["vars"]["webhook_url"] == "[Filtered]"
    assert "discord.com/api/webhooks/123456789/[Filtered]" in frame["vars"]["target"]
    for secret in SECRETS:
        assert secret not in json.dumps(result)
