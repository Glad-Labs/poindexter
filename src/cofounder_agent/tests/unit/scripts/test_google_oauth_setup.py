"""The OAuth helper must not hardcode the loopback port.

``scripts/google-oauth-setup.py`` mints the ``google_oauth_refresh_token``
that BOTH Singer taps (``gsc_main`` + ``ga4_main``) share, so it only ever
runs when analytics ingestion is already broken and someone is trying to
un-break it. It used to bind a hardcoded ``8765``.

2026-09-15: that default lost the port to an unrelated ``systemd --user``
service which had been listening for six hours, and the helper died with
``OSError: [Errno 98] Address already in use`` *before opening the browser* --
during a live outage, with both taps at 155+ consecutive failures.

Nothing required 8765. ``docs/integrations/setup-gsc-and-ga4.md`` creates a
**Desktop app** client, and Google matches loopback redirects on the address
alone, ignoring the port, for that client type. ``google_auth_oauthlib``'s
``run_local_server`` rewrites ``flow.redirect_uri`` from the *bound* socket,
so ``port=0`` produces a correct auth URL. A fixed port bought nothing and
could only collide.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _find_script() -> Path | None:
    """Walk up for the repo root rather than counting ``parents[N]``.

    A fixed parent index silently mis-resolves wherever the tree is mounted
    at a different depth -- which is exactly why a handful of tests in this
    suite skip in-container. Searching cannot drift.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "scripts" / "google-oauth-setup.py"
        if candidate.is_file():
            return candidate
    return None


_SCRIPT = _find_script()

pytestmark = pytest.mark.skipif(
    _SCRIPT is None,
    reason="scripts/ is stripped from the public mirror tree",
)


def _load():
    """Import the hyphenated script file under a module name."""
    spec = importlib.util.spec_from_file_location("google_oauth_setup", _SCRIPT)
    assert spec and spec.loader, f"cannot load {_SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeCreds:
    refresh_token = "fake-refresh-token"


class _FakeFlow:
    """Records the port it was asked to bind; never touches a socket."""

    last_kwargs: dict = {}

    @classmethod
    def from_client_config(cls, client_config, scopes):
        inst = cls()
        inst.client_config = client_config
        inst.scopes = scopes
        return inst

    def run_local_server(self, **kwargs):
        type(self).last_kwargs = dict(kwargs)
        return _FakeCreds()


@pytest.fixture
def stub_oauthlib(monkeypatch):
    """Stand in for google-auth-oauthlib, which the script imports lazily."""
    _FakeFlow.last_kwargs = {}
    pkg = types.ModuleType("google_auth_oauthlib")
    flow_mod = types.ModuleType("google_auth_oauthlib.flow")
    flow_mod.InstalledAppFlow = _FakeFlow
    pkg.flow = flow_mod
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", pkg)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_mod)
    return _FakeFlow


def _argv(*extra):
    return [
        "google-oauth-setup.py",
        "--client-id", "cid.apps.googleusercontent.com",
        "--client-secret", "csecret",
        *extra,
    ]


def test_the_default_port_is_os_assigned(monkeypatch, stub_oauthlib):
    """Port 0 = the OS picks a free one, so nothing can already hold it."""
    mod = _load()
    monkeypatch.setattr(sys, "argv", _argv())
    assert mod.main() == 0
    assert stub_oauthlib.last_kwargs["port"] == 0, (
        "a hardcoded port can be taken by any unrelated local service; "
        "0 means the OS hands us a free one"
    )


def test_an_explicit_port_still_wins(monkeypatch, stub_oauthlib):
    """The escape hatch for Web-application clients, whose URI IS matched."""
    mod = _load()
    monkeypatch.setattr(sys, "argv", _argv("--port", "9099"))
    assert mod.main() == 0
    assert stub_oauthlib.last_kwargs["port"] == 9099


def test_offline_consent_is_requested(monkeypatch, stub_oauthlib):
    """Without both of these Google returns no refresh_token at all."""
    mod = _load()
    monkeypatch.setattr(sys, "argv", _argv())
    assert mod.main() == 0
    assert stub_oauthlib.last_kwargs["access_type"] == "offline"
    assert stub_oauthlib.last_kwargs["prompt"] == "consent"


def test_the_banner_does_not_announce_port_zero(monkeypatch, stub_oauthlib, capsys):
    """'port 0' would read as a real port to whoever is debugging at 2am."""
    mod = _load()
    monkeypatch.setattr(sys, "argv", _argv())
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "port 0" not in out
    assert "OS-assigned free port" in out


def test_an_explicit_port_is_named_in_the_banner(monkeypatch, stub_oauthlib, capsys):
    mod = _load()
    monkeypatch.setattr(sys, "argv", _argv("--port", "9099"))
    assert mod.main() == 0
    assert "port 9099" in capsys.readouterr().out


def test_a_missing_refresh_token_fails_loudly(monkeypatch, stub_oauthlib):
    """Google only issues one on FIRST consent; silence here would strand the taps."""
    class _NoToken(_FakeCreds):
        refresh_token = None

    monkeypatch.setattr(stub_oauthlib, "run_local_server", lambda self, **kw: _NoToken(), raising=False)
    mod = _load()
    monkeypatch.setattr(sys, "argv", _argv())
    assert mod.main() == 2
