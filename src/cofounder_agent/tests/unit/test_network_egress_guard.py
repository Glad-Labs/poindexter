"""The egress guard has to be able to fail, or it is decoration.

Glad-Labs/poindexter#1011. A guard nobody has watched fire is indistinguishable
from one that silently allows everything — which is the exact failure mode it
exists to prevent (the hero-VRAM tests patched a seam the code never reached and
looked green for weeks).
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from tests.unit._egress_guard import (
    BASELINE_PATH,
    UnitTestNetworkEgress,
    guard_is_enforcing,
    load_egress_baseline,
)

BASELINE = BASELINE_PATH

# The raise-behaviour tests below assert the guard FAILS a connect. Under
# EGRESS_GUARD_MODE=report it deliberately does not — it records and lets the
# connection through — so these would fail for the right reason in the wrong
# mode. Skip rather than weaken the assertions: a harvest run must stay green
# so every CI step completes, which is the entire purpose of report mode.
enforcing_only = pytest.mark.skipif(
    not guard_is_enforcing(),
    reason="guard is in report mode (EGRESS_GUARD_MODE=report); it records instead of raising",
)


@enforcing_only
class TestGuardFires:
    def test_connect_is_refused(self):
        """socket.socket.connect from a non-baselined test must raise."""
        with pytest.raises(UnitTestNetworkEgress) as ei:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(("127.0.0.1", 5433))
            finally:
                s.close()
        assert "poindexter#1011" in str(ei.value)

    def test_create_connection_is_refused(self):
        with pytest.raises(UnitTestNetworkEgress):
            socket.create_connection(("127.0.0.1", 5433), timeout=1)

    def test_message_names_the_target_and_the_test(self):
        """The operator has to know WHICH test reached WHERE, or the failure
        is a scavenger hunt."""
        with pytest.raises(UnitTestNetworkEgress) as ei:
            socket.create_connection(("192.0.2.9", 4242), timeout=1)
        msg = str(ei.value)
        assert "192.0.2.9:4242" in msg
        assert "test_message_names_the_target_and_the_test" in msg

    def test_loopback_is_not_exempt(self):
        """127.0.0.1 IS the problem on this box — the services under test run
        locally. An exemption for loopback would exempt the whole bug."""
        with pytest.raises(UnitTestNetworkEgress):
            socket.create_connection(("127.0.0.1", 9836), timeout=1)


@enforcing_only
class TestSurvivesBroadExcept:
    """The guard's base class is load-bearing, not stylistic.

    Most code this watches is best-effort network code inside a broad
    `except Exception` (this repo baselines 108 such handlers). If the guard
    raised an Exception subclass, the code UNDER TEST would swallow it and the
    test would pass green — the guard would be decoration on exactly the paths
    it exists to watch.

    Measured before the fix: un-baselining test_operator_notifier.py, which
    really does open TLS to api.telegram.org, still gave `26 passed`. With
    BaseException it correctly gives 5 failures.
    """

    def test_exception_is_not_swallowed_by_except_exception(self):
        swallowed = False
        try:
            try:
                socket.create_connection(("127.0.0.1", 5433), timeout=1)
            except Exception:            # noqa: BLE001 - the point of the test
                swallowed = True
        except UnitTestNetworkEgress:
            pass
        assert not swallowed, (
            "a broad `except Exception` in code under test absorbed the egress "
            "guard — it must derive from BaseException"
        )

    def test_base_is_baseexception_not_exception(self):
        assert issubclass(UnitTestNetworkEgress, BaseException)
        assert not issubclass(UnitTestNetworkEgress, Exception), (
            "regression: deriving from Exception lets `except Exception` in the "
            "code under test swallow the guard (see class docstring)"
        )


@pytest.mark.allow_network
class TestMarkerEscapeHatch:
    def test_marked_test_may_open_a_socket(self):
        """A deliberate socket user opts out explicitly and greppably."""
        try:
            socket.create_connection(("127.0.0.1", 1), timeout=0.2)
        except UnitTestNetworkEgress:  # pragma: no cover
            pytest.fail("allow_network marker did not bypass the guard")
        except OSError:
            pass  # connection refused is the expected real-world outcome


class TestBaselineRatchet:
    def test_baseline_parses(self):
        allowed = load_egress_baseline()
        assert allowed, "baseline file should not be empty while burn-down is open"
        assert all(isinstance(v, int) and v > 0 for v in allowed.values())

    def test_baseline_entries_still_exist(self):
        """A baselined path that no longer exists means the ratchet is carrying
        a dead entry — it should have been removed with the file."""
        root = Path(__file__).resolve().parents[2]
        missing = [p for p in load_egress_baseline() if not (root / p).exists()]
        assert not missing, f"baseline references files that no longer exist: {missing}"

    def test_baseline_is_sorted_and_unique(self):
        """Keeps the burn-down diff readable — one line changes when one file
        is fixed."""
        lines = [
            ln.strip().split(" ", 1)[1]
            for ln in BASELINE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        assert lines == sorted(lines), "baseline must stay sorted by path"
        assert len(lines) == len(set(lines)), "baseline has duplicate paths"
