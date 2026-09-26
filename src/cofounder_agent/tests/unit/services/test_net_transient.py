"""Tests for services/net_transient.py (stack#3161).

The classifier decides whether an exhausted-retries request failure is a
NETWORK fault (defer, shared finding) or a job fault (ok=False, job_failure
page) — a wrong answer either suppresses a real fault or revives the
one-blip-two-pages noise this module exists to kill.
"""
from __future__ import annotations

import httpx
import pytest

from poindexter.services.net_transient import (
    DEFAULT_SIDECAR_CONNECT_RETRIES,
    SIDECAR_CONNECT_RETRIES_KEY,
    is_transient_network_error,
    sidecar_connect_retries,
    transient_retry_transport,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("All connection attempts failed"),
    httpx.ConnectTimeout("timed out"),
    ConnectionError("[Errno -3] Temporary failure in name resolution"),
    OSError("EAI_AGAIN"),
    Exception("socket.gaierror: [Errno -2] Name or service not known"),
    RuntimeError("getaddrinfo failed"),
])
def test_transient_shapes_classify_true(exc):
    assert is_transient_network_error(exc) is True


@pytest.mark.parametrize("exc", [
    httpx.ReadTimeout("server slow"),        # connected fine — peer problem
    httpx.HTTPStatusError("500", request=None, response=None),
    ConnectionError("DNS fail"),             # generic text, no resolver marker
    ValueError("bad payload"),
])
def test_non_transient_shapes_classify_false(exc):
    assert is_transient_network_error(exc) is False


def test_transport_builder_returns_retrying_transport():
    t = transient_retry_transport(3)
    assert isinstance(t, httpx.AsyncHTTPTransport)


def test_transport_builder_clamps_negative_to_zero():
    t = transient_retry_transport(-5)
    assert isinstance(t, httpx.AsyncHTTPTransport)


# ---------------------------------------------------------------------------
# sidecar_connect_retries (2026-09-25) — RIFE / chatterbox / stable-audio /
# wan exit to give their CUDA context back, and a request landing in that
# ~2 s restart window sees exactly the connect-phase failure this transport
# retries.
# ---------------------------------------------------------------------------


class _SC:
    def __init__(self, value=None):
        self._value = value

    def get(self, key, default=None):
        if key == SIDECAR_CONNECT_RETRIES_KEY and self._value is not None:
            return self._value
        return default


def test_sidecar_retries_none_site_config_uses_the_default():
    assert sidecar_connect_retries(None) == DEFAULT_SIDECAR_CONNECT_RETRIES


def test_sidecar_retries_reads_the_setting():
    assert sidecar_connect_retries(_SC("2")) == 2


def test_sidecar_retries_unset_key_uses_the_default():
    assert sidecar_connect_retries(_SC()) == DEFAULT_SIDECAR_CONNECT_RETRIES


def test_sidecar_retries_negative_clamps_to_zero():
    assert sidecar_connect_retries(_SC("-3")) == 0


def test_sidecar_retries_unparseable_value_warns_and_uses_the_default(caplog):
    with caplog.at_level("WARNING"):
        result = sidecar_connect_retries(_SC("lots"))

    assert result == DEFAULT_SIDECAR_CONNECT_RETRIES
    assert any("not an integer" in r.getMessage() for r in caplog.records)
