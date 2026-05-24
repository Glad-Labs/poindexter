"""Regression: openclaw must not be auto-restarted from inside Docker.

When the brain daemon runs in its own container (the prod posture as of
2026-05-24), the openclaw service is on the host. The brain has neither
a sibling container to restart nor the openclaw CLI on its PATH, so a
prior version of the code would call ``restart_service('openclaw')``,
fall through to the "no container mapping for auto-restart" branch, and
spam Telegram with a misleading "Service openclaw is down" alert even on
a single transient probe blip — openclaw is marked ``critical=False``
specifically to keep its noise off operator chat.

These tests pin the decision via the small ``_should_auto_restart``
helper introduced alongside the fix.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import brain_daemon as bd  # noqa: E402


def test_worker_is_always_a_restart_target():
    """Worker is a sibling container in Docker and has a host PowerShell
    restart script otherwise — both paths qualify."""
    with patch.object(bd, "IS_DOCKER", True):
        assert bd._should_auto_restart("worker") is True
    with patch.object(bd, "IS_DOCKER", False):
        assert bd._should_auto_restart("worker") is True


def test_openclaw_in_docker_is_not_a_restart_target():
    """The regression this fix codifies. From inside the brain container
    there is no path to restart the host-side openclaw service, so the
    monitor loop must not even try — calling restart_service would emit
    the misleading 'no container mapping' Telegram alert."""
    with patch.object(bd, "IS_DOCKER", True):
        assert bd._should_auto_restart("openclaw") is False


def test_openclaw_on_host_is_a_restart_target():
    """Bare-metal posture: the openclaw CLI is on PATH and the
    PowerShell-based restart path in restart_service() works."""
    with patch.object(bd, "IS_DOCKER", False):
        assert bd._should_auto_restart("openclaw") is True


@pytest.mark.parametrize("name", ["api", "site", "sdxl", "grafana", "loki", "redis"])
def test_other_services_are_not_restart_targets(name):
    """Only worker (always) and openclaw (host only) qualify. Everything
    else falls through to the alert-actions auto-triage path — restart
    is not a generic recovery mechanism."""
    for is_docker in (True, False):
        with patch.object(bd, "IS_DOCKER", is_docker):
            assert bd._should_auto_restart(name) is False
