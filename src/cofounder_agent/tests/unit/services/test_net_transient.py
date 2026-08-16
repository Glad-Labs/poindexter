"""Tests for services/net_transient.py (stack#3161).

The classifier decides whether an exhausted-retries request failure is a
NETWORK fault (defer, shared finding) or a job fault (ok=False, job_failure
page) — a wrong answer either suppresses a real fault or revives the
one-blip-two-pages noise this module exists to kill.
"""
from __future__ import annotations

import httpx
import pytest

from services.net_transient import (
    is_transient_network_error,
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
