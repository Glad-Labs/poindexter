"""Unit tests for brain/brain_daemon.py :func:`restart_service`.

Pins the 2026-05-16 fix that stopped the brain from firing a
``No such container: poindexter-worker`` notification when the
container is briefly absent during a ``docker compose up
--force-recreate`` (stop → rm → run sequence leaves the container
name unbound for ~1-2 seconds).

The fix: ``docker inspect <name>`` is run as a cheap pre-check;
exit != 0 → log + return without restarting or notifying. Real
"container is broken and needs a kick" calls still hit the
``docker restart`` path because ``inspect`` succeeds.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# brain/ lives outside the poindexter distro; mirror the path-prelude
# the auto_remediate tests use so brain_daemon imports resolve.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import brain_daemon as bd  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_notify():
    """Patch the async notify() so we can assert it was/wasn't called."""
    with patch.object(bd, "notify", new=AsyncMock()) as m:
        yield m


def _inspect_result(returncode: int, stdout: str = "", stderr: str = ""):
    """Build a CompletedProcess-shaped mock for subprocess.run()."""
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


async def test_missing_container_skips_restart_and_notify(mock_notify):
    """Compose --force-recreate gap: container temporarily doesn't exist.
    Brain should log + return without notifying — the next cycle (≤5
    min) will see the recreated container and recover quietly.
    """
    inspect_miss = _inspect_result(
        returncode=1,
        stderr="Error: No such object: poindexter-worker\n",
    )

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(bd.subprocess, "run", return_value=inspect_miss) as run_mock:
        await bd.restart_service("worker", pool=None)

    # Only the inspect call should have run — restart was skipped.
    assert run_mock.call_count == 1
    cmd_args = run_mock.call_args.args[0]
    assert cmd_args[:2] == ["docker", "inspect"]
    assert "poindexter-worker" in cmd_args

    # Critical: no notification fired. The transient absence shouldn't
    # page the operator.
    mock_notify.assert_not_called()


async def test_existing_container_proceeds_with_restart(mock_notify):
    """Real outage path: container exists but unhealthy → restart fires
    and the notify message confirms the auto-recovery.
    """
    inspect_hit = _inspect_result(returncode=0, stdout="running\n")
    restart_ok = _inspect_result(returncode=0)

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             bd.subprocess, "run",
             side_effect=[inspect_hit, restart_ok],
         ) as run_mock:
        await bd.restart_service("worker", pool=None)

    # Both inspect AND restart should have fired.
    assert run_mock.call_count == 2
    inspect_args = run_mock.call_args_list[0].args[0]
    restart_args = run_mock.call_args_list[1].args[0]
    assert inspect_args[:2] == ["docker", "inspect"]
    assert restart_args[:2] == ["docker", "restart"]

    # Operator notified of the recovery action.
    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    assert "Auto-restarted" in msg
    assert "poindexter-worker" in msg


async def test_unknown_service_name_notifies_no_mapping(mock_notify):
    """Names not in ``_container_map`` (no Docker container associated)
    should still send the legacy 'no container mapping' notice so the
    operator knows the brain noticed but can't auto-fix.
    """
    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(bd.subprocess, "run") as run_mock:
        await bd.restart_service("redis", pool=None)

    # No docker calls at all when the name doesn't map.
    run_mock.assert_not_called()
    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    assert "no container mapping" in msg


async def test_restart_failure_notifies_operator(mock_notify):
    """If inspect succeeds (container exists) but restart fails (e.g.
    Docker socket lost permissions mid-operation), the operator should
    still be notified — this is the failure mode the inspect pre-check
    was NOT designed to catch.
    """
    inspect_hit = _inspect_result(returncode=0, stdout="running\n")
    restart_fail = _inspect_result(
        returncode=1, stderr="permission denied\n",
    )

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             bd.subprocess, "run",
             side_effect=[inspect_hit, restart_fail],
         ):
        await bd.restart_service("worker", pool=None)

    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    assert "Failed to restart" in msg
    assert "permission denied" in msg
