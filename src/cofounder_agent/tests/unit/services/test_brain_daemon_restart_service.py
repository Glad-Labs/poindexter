"""Unit tests for brain/brain_daemon.py :func:`restart_service`.

Pins the 2026-05-16 fix that stopped the brain from firing a
``No such container: poindexter-worker`` notification when the
container is briefly absent during a ``docker compose up
--force-recreate`` (stop → rm → run sequence leaves the container
name unbound for ~1-2 seconds).

The fix: ``docker inspect <name>`` is run as a cheap pre-check;
"no such container" → log + return without restarting or notifying. Real
"container is broken and needs a kick" calls still hit the
``docker restart`` path because ``inspect`` succeeds.

Both docker calls now happen in the brain's one restart implementation,
``docker_utils.restart_container`` (shared with the firefighter and
health_probes' self-heal), so ``subprocess.run`` is stubbed there. These
tests pin what ``restart_service`` does with each outcome: a notice, a
page, or a quiet skip.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# brain/ lives outside the poindexter distro; mirror the path-prelude
# the auto_remediate tests use so brain_daemon imports resolve.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "src" / "cofounder_agent" / "poindexter" / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from poindexter.brain import brain_daemon as bd  # noqa: E402
from poindexter.brain import docker_utils  # noqa: E402

pytestmark = pytest.mark.asyncio

# ``docker inspect``'s answer for a container that has been up for months, so
# the recently-started guard has nothing to say about it.
_RUNNING_FOR_MONTHS = "running 2026-01-01T00:00:00.000000000Z\n"


@pytest.fixture
def mock_notify():
    """Patch the async notify() (the page: Telegram + Discord) so we can
    assert it was/wasn't called."""
    with patch.object(bd, "notify", new=AsyncMock()) as m:
        yield m


@pytest.fixture
def mock_notice():
    """Patch notify_discord_ops() (a Discord #ops notice that never pages).

    A restart that worked lands here, not on ``notify``: "self-heal before
    paging" (docs/operations/self-healing.md). Every failure still pages.
    """
    with patch.object(bd, "notify_discord_ops", new=AsyncMock()) as m:
        yield m


def _inspect_result(returncode: int, stdout: str = "", stderr: str = ""):
    """Build a CompletedProcess-shaped mock for subprocess.run()."""
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


async def test_missing_container_skips_restart_and_notify(mock_notify, mock_notice):
    """Compose --force-recreate gap: container temporarily doesn't exist.
    Brain should log + return without notifying — the next cycle (≤5
    min) will see the recreated container and recover quietly.
    """
    inspect_miss = _inspect_result(
        returncode=1,
        stderr="Error: No such object: poindexter-worker\n",
    )

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(docker_utils.subprocess, "run", return_value=inspect_miss) as run_mock:
        await bd.restart_service("worker", pool=None)

    # Only the inspect call should have run — restart was skipped.
    assert run_mock.call_count == 1
    cmd_args = run_mock.call_args.args[0]
    assert cmd_args[:2] == ["docker", "inspect"]
    assert "poindexter-worker" in cmd_args

    # Critical: no notification fired. The transient absence shouldn't
    # page the operator, or post a notice either.
    mock_notify.assert_not_called()
    mock_notice.assert_not_called()


async def test_existing_container_proceeds_with_restart(mock_notify, mock_notice):
    """Real outage path: container exists but unhealthy → restart fires
    and a Discord #ops notice confirms the auto-recovery. A heal that
    worked does not page.
    """
    inspect_hit = _inspect_result(returncode=0, stdout=_RUNNING_FOR_MONTHS)
    restart_ok = _inspect_result(returncode=0)

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             docker_utils.subprocess, "run",
             side_effect=[inspect_hit, restart_ok],
         ) as run_mock:
        await bd.restart_service("worker", pool=None)

    # Both inspect AND restart should have fired.
    assert run_mock.call_count == 2
    inspect_args = run_mock.call_args_list[0].args[0]
    restart_args = run_mock.call_args_list[1].args[0]
    assert inspect_args[:2] == ["docker", "inspect"]
    assert restart_args[:2] == ["docker", "restart"]

    # Operator told of the recovery action on Discord only, not paged.
    mock_notify.assert_not_called()
    mock_notice.assert_called_once()
    msg = mock_notice.call_args.args[0]
    assert "Auto-restarted" in msg
    assert "poindexter-worker" in msg


async def test_unknown_service_name_notifies_no_mapping(mock_notify):
    """Names not in ``_container_map`` (no Docker container associated)
    should still send the legacy 'no container mapping' notice so the
    operator knows the brain noticed but can't auto-fix.
    """
    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(docker_utils.subprocess, "run") as run_mock:
        await bd.restart_service("redis", pool=None)

    # No docker calls at all when the name doesn't map.
    run_mock.assert_not_called()
    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    assert "no container mapping" in msg


async def test_restart_failure_notifies_operator(mock_notify, mock_notice):
    """If inspect succeeds (container exists) but restart fails (e.g.
    Docker socket lost permissions mid-operation), the operator should
    still be notified — this is the failure mode the inspect pre-check
    was NOT designed to catch.
    """
    inspect_hit = _inspect_result(returncode=0, stdout=_RUNNING_FOR_MONTHS)
    restart_fail = _inspect_result(
        returncode=1, stderr="permission denied\n",
    )

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             docker_utils.subprocess, "run",
             side_effect=[inspect_hit, restart_fail],
         ):
        await bd.restart_service("worker", pool=None)

    # A heal that failed pages (Telegram + Discord), never just a notice.
    mock_notify.assert_called_once()
    mock_notice.assert_not_called()
    msg = mock_notify.call_args.args[0]
    assert "Failed to restart" in msg
    assert "permission denied" in msg


async def test_docker_cli_missing_notifies_install_hint(mock_notify, mock_notice):
    """Brain container without docker-cli installed: ``subprocess.run``
    raises ``FileNotFoundError`` on the inspect call. The operator
    should get an actionable install-hint notification, not a confusing
    stack trace.
    """
    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(docker_utils.subprocess, "run", side_effect=FileNotFoundError):
        await bd.restart_service("worker", pool=None)

    mock_notify.assert_called_once()
    mock_notice.assert_not_called()
    msg = mock_notify.call_args.args[0]
    assert "Docker CLI not found" in msg
    # The service name (not the container name) appears in the message
    # so the operator can correlate with the upstream health probe.
    assert "worker" in msg


async def test_inspect_timeout_notifies_generic_failure(mock_notify, mock_notice):
    """``subprocess.TimeoutExpired`` on the inspect (Docker daemon hung)
    comes back from ``docker_utils.restart_container`` as an error, never
    a raise — operator gets notified so the brain doesn't silently swallow
    the hang.
    """
    timeout_exc = docker_utils.subprocess.TimeoutExpired(cmd="docker inspect", timeout=10)
    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(docker_utils.subprocess, "run", side_effect=timeout_exc):
        await bd.restart_service("worker", pool=None)

    mock_notify.assert_called_once()
    mock_notice.assert_not_called()
    msg = mock_notify.call_args.args[0]
    assert "Restart failed" in msg
    assert "worker" in msg


async def test_api_alias_maps_to_worker_container(mock_notify, mock_notice):
    """``api`` is an alias for the worker container — the FastAPI app
    lives in the same process as the worker, so restarting one
    restarts both. This pins the alias so a future
    container-decomposition split surfaces as a test failure.
    """
    inspect_hit = _inspect_result(returncode=0, stdout=_RUNNING_FOR_MONTHS)
    restart_ok = _inspect_result(returncode=0)

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             docker_utils.subprocess, "run",
             side_effect=[inspect_hit, restart_ok],
         ) as run_mock:
        await bd.restart_service("api", pool=None)

    restart_args = run_mock.call_args_list[1].args[0]
    assert restart_args == ["docker", "restart", "poindexter-worker"]
    mock_notify.assert_not_called()
    mock_notice.assert_called_once()
    assert "poindexter-worker" in mock_notice.call_args.args[0]


async def test_image_gen_server_alias_maps_to_image_gen_container(mock_notify, mock_notice):
    """``image-gen-server`` routes to the image-gen container, not the worker.
    Regression guard against a copy-paste mistake collapsing the alias to
    ``poindexter-worker``.
    """
    inspect_hit = _inspect_result(returncode=0, stdout=_RUNNING_FOR_MONTHS)
    restart_ok = _inspect_result(returncode=0)

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             docker_utils.subprocess, "run",
             side_effect=[inspect_hit, restart_ok],
         ) as run_mock:
        await bd.restart_service("image-gen-server", pool=None)

    inspect_args = run_mock.call_args_list[0].args[0]
    restart_args = run_mock.call_args_list[1].args[0]
    assert "poindexter-image-gen-server" in inspect_args
    assert restart_args == ["docker", "restart", "poindexter-image-gen-server"]
    # Recovery notice names the image-gen container, not the worker.
    mock_notify.assert_not_called()
    mock_notice.assert_called_once()
    assert "poindexter-image-gen-server" in mock_notice.call_args.args[0]


async def test_inspect_command_uses_state_status_format(mock_notify, mock_notice):
    """The inspect pre-check uses ``--format "{{.State.Status}}
    {{.State.StartedAt}}"`` so the output stays cheap (one short line, no
    JSON parse). If this drifts to a full ``docker inspect`` the pre-check
    still works but the output size balloons — pin the format so future
    edits stay tight. The second field is what the recently-started guard
    reads.
    """
    inspect_hit = _inspect_result(returncode=0, stdout=_RUNNING_FOR_MONTHS)
    restart_ok = _inspect_result(returncode=0)

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             docker_utils.subprocess, "run",
             side_effect=[inspect_hit, restart_ok],
         ) as run_mock:
        await bd.restart_service("worker", pool=None)

    inspect_args = run_mock.call_args_list[0].args[0]
    assert "--format" in inspect_args
    assert "{{.State.Status}} {{.State.StartedAt}}" in inspect_args
    # And the timeouts are asymmetric — inspect is cheap, restart slow.
    # ``docker restart`` waits out the container's stop grace period (the
    # worker's is 75 s) before it kills and starts it: the old hardcoded
    # 30 s paged a misleading "Restart failed" while dockerd completed the
    # restart fine (2026-08-15 api-down investigation).
    inspect_kwargs = run_mock.call_args_list[0].kwargs
    restart_kwargs = run_mock.call_args_list[1].kwargs
    assert inspect_kwargs.get("timeout") == 10
    assert (
        restart_kwargs.get("timeout")
        == docker_utils.DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS
        == 90
    )
    # Sanity: the format pre-check still drives a real notice on success.
    mock_notice.assert_called_once()


async def test_restart_timeout_is_db_tunable(mock_notify, mock_notice):
    """With a pool available, the docker-restart subprocess timeout comes
    from ``app_settings.brain_docker_restart_timeout_seconds``."""
    inspect_hit = _inspect_result(returncode=0, stdout=_RUNNING_FOR_MONTHS)
    restart_ok = _inspect_result(returncode=0)
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value="120")

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             docker_utils.subprocess, "run",
             side_effect=[inspect_hit, restart_ok],
         ) as run_mock:
        await bd.restart_service("worker", pool=pool)

    assert run_mock.call_args_list[1].kwargs.get("timeout") == 120


async def test_timed_out_restart_pages_and_says_dockerd_may_finish(mock_notify, mock_notice):
    """A restart that outlives ``brain_docker_restart_timeout_seconds`` still
    pages (the brain could not confirm the service came back), but the page
    says dockerd may still complete it rather than a bare ``Command ...
    timed out`` that reads as if the restart itself failed."""
    inspect_hit = _inspect_result(returncode=0, stdout=_RUNNING_FOR_MONTHS)
    timeout_exc = docker_utils.subprocess.TimeoutExpired(
        cmd=["docker", "restart", "poindexter-worker"], timeout=90,
    )

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             docker_utils.subprocess, "run",
             side_effect=[inspect_hit, timeout_exc],
         ):
        await bd.restart_service("worker", pool=None)

    mock_notify.assert_called_once()
    mock_notice.assert_not_called()
    msg = mock_notify.call_args.args[0]
    assert msg.startswith("Service worker is down. Restart failed: ")
    assert "did not return within 90s" in msg
    assert "may still complete it" in msg


async def test_unreachable_docker_daemon_pages_instead_of_skipping(mock_notify, mock_notice):
    """Only "no such container" is a recreate window. A dead docker socket
    also makes ``docker inspect`` exit 1, and the old pre-check read any
    non-zero exit as absence: the heal was skipped with an INFO log saying
    the container was mid-recreate."""
    inspect_fail = _inspect_result(
        returncode=1,
        stderr="failed to connect to the docker API at unix:///var/run/docker.sock\n",
    )

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(docker_utils.subprocess, "run", return_value=inspect_fail) as run_mock:
        await bd.restart_service("worker", pool=None)

    assert run_mock.call_count == 1  # no restart attempted
    mock_notify.assert_called_once()
    mock_notice.assert_not_called()
    msg = mock_notify.call_args.args[0]
    assert "Restart failed" in msg
    assert "failed to connect to the docker API" in msg


def _running_for(seconds: float) -> str:
    """``docker inspect``'s answer for a container that started ``seconds`` ago."""
    then = datetime.now(UTC) - timedelta(seconds=seconds)
    return "running " + then.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z\n"


async def test_recently_started_container_is_not_restarted_and_sends_nothing(
    mock_notify, mock_notice, caplog,
):
    """The worker takes 40-90 s to come back. A restart from another path,
    deploy-sync or compose moments ago means the failed checks that led here
    may be reading its start-up, and restarting it again would kill it
    mid-boot. Not a heal (no notice) and not a failure (no page): the next
    cycle is longer than the guard window, so it checks again."""
    inspect_recent = _inspect_result(returncode=0, stdout=_running_for(30))

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             docker_utils.subprocess, "run", return_value=inspect_recent,
         ) as run_mock, \
         caplog.at_level("INFO", logger=bd.logger.name):
        await bd.restart_service("worker", pool=None)

    assert run_mock.call_count == 1  # inspect only: no `docker restart`
    assert run_mock.call_args.args[0][:2] == ["docker", "inspect"]
    mock_notify.assert_not_called()
    mock_notice.assert_not_called()
    assert any(
        "poindexter-worker started" in r.getMessage()
        and "skipping auto-restart this cycle" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.parametrize("service", ["worker", "api", "site", "image_gen", "image-gen-server"])
async def test_every_mapped_service_meets_the_recently_started_guard(
    mock_notify, mock_notice, service,
):
    inspect_recent = _inspect_result(returncode=0, stdout=_running_for(10))

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(docker_utils.subprocess, "run", return_value=inspect_recent) as run_mock:
        await bd.restart_service(service, pool=None)

    assert run_mock.call_count == 1
    mock_notify.assert_not_called()
    mock_notice.assert_not_called()


async def test_recently_started_window_is_db_tunable_and_zero_turns_it_off(
    mock_notify, mock_notice,
):
    """``brain_docker_restart_min_uptime_seconds`` reaches ``restart_service``
    through the pool, and 0 restores the pre-guard behaviour."""
    inspect_recent = _inspect_result(returncode=0, stdout=_running_for(30))
    restart_ok = _inspect_result(returncode=0)
    pool = MagicMock()
    pool.fetchval = AsyncMock(
        side_effect=lambda _sql, key: "0" if key == docker_utils.DOCKER_RESTART_MIN_UPTIME_KEY else None,
    )

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             docker_utils.subprocess, "run",
             side_effect=[inspect_recent, restart_ok],
         ) as run_mock:
        await bd.restart_service("worker", pool=pool)

    assert run_mock.call_count == 2
    mock_notice.assert_called_once()
    assert "Auto-restarted" in mock_notice.call_args.args[0]
    mock_notify.assert_not_called()


async def test_a_container_up_past_the_window_is_restarted(mock_notify, mock_notice):
    """The guard only covers the first ``brain_docker_restart_min_uptime_seconds``."""
    inspect_old = _inspect_result(returncode=0, stdout=_running_for(600))
    restart_ok = _inspect_result(returncode=0)

    with patch.object(bd, "IS_DOCKER", True), \
         patch.object(
             docker_utils.subprocess, "run", side_effect=[inspect_old, restart_ok],
         ) as run_mock:
        await bd.restart_service("worker", pool=None)

    assert run_mock.call_count == 2
    mock_notice.assert_called_once()
    mock_notify.assert_not_called()


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
