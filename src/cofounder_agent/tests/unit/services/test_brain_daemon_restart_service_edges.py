"""Edge-case unit tests for brain/brain_daemon.py :func:`restart_service`.

Complements the baseline tests in
``test_brain_daemon_restart_service.py`` (which pin the Docker
inspect/restart happy + sad paths). Here we cover the gaps:

* Container-name aliases (``api`` / ``site`` → poindexter-worker,
  ``sdxl`` and ``sdxl-server`` → poindexter-sdxl-server) — proves the
  ``_container_map`` is wired the way the brain's health probes expect.
* ``FileNotFoundError`` when the Docker CLI isn't in the brain
  container — should notify with a CLI-specific message, NOT swallow.
* Generic exception while running ``docker inspect`` / ``docker
  restart`` — should notify with the restart-failed message instead
  of bubbling to the cycle loop.
* Host-side (non-Docker) restart: ``worker`` with an unset
  ``app_settings.worker_restart_script`` should notify + return
  without spawning a subprocess (loud-fail per
  ``feedback_no_silent_defaults``).
* Host-side ``worker`` with a configured script → spawns
  ``powershell ... -File <script>`` via ``subprocess.Popen``.
* Host-side ``openclaw`` → spawns the ``openclaw gateway restart``
  command via ``subprocess.Popen``.

The brain runs on a 5-minute cycle, so any path that bubbles an
exception kills the whole monitor loop — that's why we assert these
paths *always* reach ``notify`` or return cleanly rather than raising.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not (_BRAIN_DIR / "brain_daemon.py").is_file(),
        reason="brain/ not present in this checkout (docker worker only "
        "mounts src/cofounder_agent as /app).",
    ),
]

from brain import brain_daemon as bd  # noqa: E402


@pytest.fixture
def mock_notify():
    with patch.object(bd, "notify", new=AsyncMock()) as m:
        yield m


def _inspect_result(returncode: int, stdout: str = "", stderr: str = ""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ---------------------------------------------------------------------------
# Docker path — container-name aliases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "service_name,expected_container",
    [
        ("api", "poindexter-worker"),
        ("site", "poindexter-worker"),
        ("sdxl", "poindexter-sdxl-server"),
        ("sdxl-server", "poindexter-sdxl-server"),
    ],
)
async def test_container_aliases_resolve_to_correct_container(
    mock_notify, service_name, expected_container,
):
    """``api`` and ``site`` share the worker container; ``sdxl`` and
    ``sdxl-server`` share the SDXL container. The health probes use
    these short names, so the map must keep them aligned with the
    actual Docker container names.
    """
    inspect_hit = _inspect_result(returncode=0, stdout="running\n")
    restart_ok = _inspect_result(returncode=0)

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             bd.subprocess, "run",
             side_effect=[inspect_hit, restart_ok],
         ) as run_mock:
        await bd.restart_service(service_name, pool=None)

    assert run_mock.call_count == 2
    inspect_args = run_mock.call_args_list[0].args[0]
    restart_args = run_mock.call_args_list[1].args[0]
    assert expected_container in inspect_args
    assert expected_container in restart_args
    mock_notify.assert_called_once()
    assert expected_container in mock_notify.call_args.args[0]


# ---------------------------------------------------------------------------
# Docker path — Docker CLI missing / generic exception
# ---------------------------------------------------------------------------


async def test_docker_cli_missing_in_container_notifies_with_specific_message(
    mock_notify,
):
    """If the brain container is missing the docker CLI binary, the
    inspect call raises ``FileNotFoundError`` BEFORE returning a
    CompletedProcess. The handler should catch that and notify with
    a CLI-specific message so the operator knows to install
    ``docker-cli`` or bind-mount the binary — not a generic
    "restart failed" string.
    """
    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             bd.subprocess, "run",
             side_effect=FileNotFoundError("docker not on PATH"),
         ):
        await bd.restart_service("worker", pool=None)

    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    assert "Docker CLI not found" in msg
    assert "worker" in msg


async def test_generic_exception_during_docker_call_notifies_and_does_not_raise(
    mock_notify,
):
    """If subprocess.run blows up with something other than
    FileNotFoundError (e.g. ``TimeoutExpired``, a permission error
    triggering ``OSError``), the handler should fall through to the
    catch-all and notify — NOT propagate. The brain runs on a 5-min
    cycle and a raise would tank the whole monitor pass.
    """
    boom = RuntimeError("docker socket unavailable")
    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(bd.subprocess, "run", side_effect=boom):
        await bd.restart_service("worker", pool=None)

    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    assert "Restart failed" in msg
    assert "docker socket unavailable" in msg


# ---------------------------------------------------------------------------
# Host-side path — worker restart script handling
# ---------------------------------------------------------------------------


async def test_host_worker_restart_without_configured_script_notifies_only(
    mock_notify,
):
    """Per ``feedback_no_silent_defaults``: when the operator has not
    seeded ``app_settings.worker_restart_script`` and the brain runs
    on the host, the function MUST notify the operator with the
    remediation hint AND skip spawning anything — there's no
    sensible default path to guess at.
    """
    with patch.object(bd, "IS_DOCKER", False), \
         patch.object(bd, "_read_app_setting", AsyncMock(return_value="")), \
         patch.object(bd.subprocess, "Popen") as popen_mock:
        await bd.restart_service("worker", pool=None)

    popen_mock.assert_not_called()
    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    assert "worker_restart_script" in msg


async def test_host_worker_restart_with_script_spawns_powershell(mock_notify):
    """When ``app_settings.worker_restart_script`` is set, the host
    path spawns powershell with ``-File <script>`` (NoProfile,
    Bypass execution policy). The brain doesn't wait for the result
    — Popen, not run — because the worker restart can take 30+ s
    and the cycle must not block on it.
    """
    script_path = r"C:\Users\matt\poindexter\scripts\start-worker.ps1"
    with patch.object(bd, "IS_DOCKER", False), \
         patch.object(bd, "sys") as sys_mock, \
         patch.object(
             bd, "_read_app_setting",
             AsyncMock(return_value=script_path),
         ), \
         patch.object(bd.subprocess, "Popen") as popen_mock:
        sys_mock.platform = "win32"
        await bd.restart_service("worker", pool=None)

    popen_mock.assert_called_once()
    cmd = popen_mock.call_args.args[0]
    assert cmd[0] == "powershell"
    assert "-NoProfile" in cmd
    assert "-ExecutionPolicy" in cmd
    assert "Bypass" in cmd
    assert "-File" in cmd
    assert script_path in cmd
    # Don't notify on the happy host path — Popen succeeded, logs
    # capture the rest. Operator only hears about it if it fails.
    mock_notify.assert_not_called()


async def test_host_openclaw_restart_spawns_gateway_restart_command(
    mock_notify,
):
    """``openclaw`` is a host-side CLI; the brain triggers its
    gateway restart via ``powershell -Command``. No DB setting
    needed — operator either has the CLI on PATH or doesn't (in
    which case Popen raises FileNotFoundError and falls into the
    generic Exception handler).
    """
    with patch.object(bd, "IS_DOCKER", False), \
         patch.object(bd, "sys") as sys_mock, \
         patch.object(bd.subprocess, "Popen") as popen_mock:
        sys_mock.platform = "win32"
        await bd.restart_service("openclaw", pool=None)

    popen_mock.assert_called_once()
    cmd = popen_mock.call_args.args[0]
    assert cmd[0] == "powershell"
    assert "-Command" in cmd
    assert any("openclaw gateway restart" in part for part in cmd)
    mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# Inspect command shape regression
# ---------------------------------------------------------------------------


async def test_inspect_uses_state_status_format(mock_notify):
    """The inspect pre-check uses ``--format {{.State.Status}}``.
    That's load-bearing: without the format flag, ``docker inspect``
    dumps the full container JSON to stdout (megabytes for a healthy
    container), which would bloat brain logs and slow the cycle. Pin
    the exact command shape so a future "simplification" doesn't
    silently drop the format flag.
    """
    inspect_hit = _inspect_result(returncode=0, stdout="running\n")
    restart_ok = _inspect_result(returncode=0)

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             bd.subprocess, "run",
             side_effect=[inspect_hit, restart_ok],
         ) as run_mock:
        await bd.restart_service("worker", pool=None)

    inspect_kwargs = run_mock.call_args_list[0]
    cmd = inspect_kwargs.args[0]
    assert cmd[:4] == ["docker", "inspect", "--format", "{{.State.Status}}"]
    # capture_output + text → we read stdout/stderr as strings.
    assert inspect_kwargs.kwargs.get("capture_output") is True
    assert inspect_kwargs.kwargs.get("text") is True
    # 10s inspect timeout — short enough that a hung docker daemon
    # doesn't stall the whole monitor cycle.
    assert inspect_kwargs.kwargs.get("timeout") == 10
