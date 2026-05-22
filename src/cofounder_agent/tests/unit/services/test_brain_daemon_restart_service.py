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


async def test_docker_cli_missing_notifies_install_hint(mock_notify):
    """Brain container without docker-cli installed: ``subprocess.run``
    raises ``FileNotFoundError`` on the inspect call. The operator
    should get an actionable install-hint notification, not a confusing
    stack trace.
    """
    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(bd.subprocess, "run", side_effect=FileNotFoundError):
        await bd.restart_service("worker", pool=None)

    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    assert "Docker CLI not found" in msg
    # The service name (not the container name) appears in the message
    # so the operator can correlate with the upstream health probe.
    assert "worker" in msg


async def test_inspect_timeout_notifies_generic_failure(mock_notify):
    """``subprocess.TimeoutExpired`` (Docker daemon hung) falls through
    to the generic ``except Exception`` arm — operator gets notified so
    the brain doesn't silently swallow the hang.
    """
    timeout_exc = bd.subprocess.TimeoutExpired(cmd="docker inspect", timeout=10)
    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(bd.subprocess, "run", side_effect=timeout_exc):
        await bd.restart_service("worker", pool=None)

    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    assert "Restart failed" in msg
    assert "worker" in msg


async def test_api_alias_maps_to_worker_container(mock_notify):
    """``api`` is an alias for the worker container — the FastAPI app
    lives in the same process as the worker, so restarting one
    restarts both. This pins the alias so a future
    container-decomposition split surfaces as a test failure.
    """
    inspect_hit = _inspect_result(returncode=0, stdout="running\n")
    restart_ok = _inspect_result(returncode=0)

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             bd.subprocess, "run",
             side_effect=[inspect_hit, restart_ok],
         ) as run_mock:
        await bd.restart_service("api", pool=None)

    restart_args = run_mock.call_args_list[1].args[0]
    assert restart_args == ["docker", "restart", "poindexter-worker"]
    mock_notify.assert_called_once()
    assert "poindexter-worker" in mock_notify.call_args.args[0]


async def test_sdxl_server_alias_maps_to_sdxl_container(mock_notify):
    """``sdxl-server`` (and ``sdxl``) route to the SDXL image-gen
    container, not the worker. Regression guard against a copy-paste
    mistake collapsing both aliases to ``poindexter-worker``.
    """
    inspect_hit = _inspect_result(returncode=0, stdout="running\n")
    restart_ok = _inspect_result(returncode=0)

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             bd.subprocess, "run",
             side_effect=[inspect_hit, restart_ok],
         ) as run_mock:
        await bd.restart_service("sdxl-server", pool=None)

    inspect_args = run_mock.call_args_list[0].args[0]
    restart_args = run_mock.call_args_list[1].args[0]
    assert "poindexter-sdxl-server" in inspect_args
    assert restart_args == ["docker", "restart", "poindexter-sdxl-server"]
    # Recovery notification names the SDXL container, not the worker.
    mock_notify.assert_called_once()
    assert "poindexter-sdxl-server" in mock_notify.call_args.args[0]


async def test_inspect_command_uses_state_status_format(mock_notify):
    """The inspect pre-check uses ``--format {{.State.Status}}`` so the
    output stays cheap (one word, no JSON parse). If this drifts to a
    full ``docker inspect`` the pre-check still works but the output
    size balloons — pin the format so future edits stay tight.
    """
    inspect_hit = _inspect_result(returncode=0, stdout="running\n")
    restart_ok = _inspect_result(returncode=0)

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             bd.subprocess, "run",
             side_effect=[inspect_hit, restart_ok],
         ) as run_mock:
        await bd.restart_service("worker", pool=None)

    inspect_args = run_mock.call_args_list[0].args[0]
    assert "--format" in inspect_args
    assert "{{.State.Status}}" in inspect_args
    # And the timeouts are asymmetric — inspect is cheap, restart slow.
    inspect_kwargs = run_mock.call_args_list[0].kwargs
    restart_kwargs = run_mock.call_args_list[1].kwargs
    assert inspect_kwargs.get("timeout") == 10
    assert restart_kwargs.get("timeout") == 30
    # Sanity: the format pre-check still drives a real notify on success.
    mock_notify.assert_called_once()


async def test_host_worker_without_restart_script_notifies(mock_notify):
    """Host (non-Docker) path: brain on the host without
    ``app_settings.worker_restart_script`` set should refuse to
    auto-restart and tell the operator how to fix it. No Popen call
    should escape (would launch the wrong thing).
    """
    with patch.object(bd, "IS_DOCKER", False), \
         patch.object(bd, "_read_app_setting", new=AsyncMock(return_value="")), \
         patch.object(bd.subprocess, "Popen") as popen_mock:
        await bd.restart_service("worker", pool=None)

    popen_mock.assert_not_called()
    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    assert "worker_restart_script" in msg


async def test_host_worker_with_script_invokes_powershell(mock_notify):
    """Host path with a configured restart script: brain spawns the
    PowerShell file via ``subprocess.Popen``. We don't notify on the
    happy path here — the upstream health probe will confirm recovery
    on the next cycle.
    """
    script_path = r"C:\repo\scripts\start-worker.ps1"
    with patch.object(bd, "IS_DOCKER", False), \
         patch.object(
             bd, "_read_app_setting",
             new=AsyncMock(return_value=script_path),
         ), \
         patch.object(bd.subprocess, "Popen") as popen_mock:
        await bd.restart_service("worker", pool=None)

    popen_mock.assert_called_once()
    cmd = popen_mock.call_args.args[0]
    assert cmd[0] == "powershell"
    assert script_path in cmd
    # Happy-path host restart stays quiet — no operator notify on success.
    mock_notify.assert_not_called()


async def test_host_openclaw_restart_invokes_cli(mock_notify):
    """``openclaw`` is a host-only service (no Docker mapping). The
    non-Docker branch shells out to the openclaw CLI via PowerShell.
    """
    with patch.object(bd, "IS_DOCKER", False), \
         patch.object(bd.subprocess, "Popen") as popen_mock:
        await bd.restart_service("openclaw", pool=None)

    popen_mock.assert_called_once()
    cmd = popen_mock.call_args.args[0]
    assert cmd[0] == "powershell"
    # The openclaw restart command is passed inline via -Command.
    assert any("openclaw gateway restart" in part for part in cmd)
    # Host openclaw restart stays quiet — no notify on the happy path.
    mock_notify.assert_not_called()


async def test_host_unknown_service_is_silent_no_op(mock_notify):
    """Host path with a name the brain doesn't know how to restart
    (e.g., ``redis``, ``grafana``) is a silent no-op — no Popen, no
    notify, no exception. The brain just logs and moves on; the
    upstream health probe is the right place to escalate.
    """
    with patch.object(bd, "IS_DOCKER", False), \
         patch.object(bd.subprocess, "Popen") as popen_mock:
        await bd.restart_service("grafana", pool=None)

    popen_mock.assert_not_called()
    mock_notify.assert_not_called()
