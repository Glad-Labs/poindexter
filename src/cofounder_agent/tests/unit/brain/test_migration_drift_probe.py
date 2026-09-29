"""Unit tests for brain/migration_drift_probe.py.

Two contracts are covered here (the services-tree twin,
tests/unit/services/test_brain_migration_drift_probe.py, covers the recovery
state machine):

1. Async safety. The brain awaits every probe sequentially on a single event
   loop (brain_daemon.py), so ``run_migration_drift_probe``'s blocking seams —
   ``_fetch_health`` (urllib GET), ``_sync_deploy_checkout`` (git) and the
   ``_wait_for_worker_healthy`` poll loop (urllib + ``time.sleep``) — MUST be
   offloaded via ``asyncio.to_thread``. A synchronous call on this loop freezes
   the whole watchdog for the duration. The worker restart is
   ``docker_utils.restart_container``, which offloads its own docker calls
   (tests/unit/brain/test_docker_utils_restart.py).
2. The deploy-checkout resync runs real git against a checkout another user
   owns. Every resync from 2026-08-15 to 2026-09-28 failed with "detected
   dubious ownership", because the brain runs as root and /host-deploy belongs
   to the host user. Nothing caught it before prod because every test stubbed
   ``sync_fn``. These tests run real git. They use git's own
   ``GIT_TEST_ASSUME_DIFFERENT_OWNER`` knob, which makes git refuse a
   repository the way it refuses a foreign-owned one, so no root is needed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import types
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.brain import docker_utils as du
from poindexter.brain import migration_drift_probe as md
from tests.unit._nonempty import nonempty
from tests.unit.brain._restart_fakes import restart_stub


def _make_pool():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=None)  # auto_recover -> False
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    return pool


def test_unknown_health_short_circuits():
    """A health response without a readable migrations block returns
    ``unknown`` and never touches the restart/sync/wait seams."""
    pool = _make_pool()
    summary = asyncio.run(
        md.run_migration_drift_probe(
            pool, health_fetcher=lambda: {"_error": "worker down"},
        )
    )
    assert summary["status"] == "unknown"


def test_blocking_health_fetch_does_not_block_event_loop():
    """``_fetch_health`` does a blocking urllib GET; the probe must offload it
    via ``asyncio.to_thread`` so the brain loop stays free while it runs."""
    pool = _make_pool()

    def blocking_health():
        time.sleep(0.4)  # simulate a slow /api/health GET
        return {"_error": "worker down"}

    async def scenario():
        loop = asyncio.get_running_loop()
        stamps: list[float] = []
        running = True

        async def ticker():
            while running:
                stamps.append(loop.time())
                await asyncio.sleep(0.01)

        t = asyncio.create_task(ticker())
        summary = await md.run_migration_drift_probe(
            pool, health_fetcher=blocking_health,
        )
        running = False
        await t
        return summary, stamps

    summary, stamps = asyncio.run(scenario())
    assert summary["status"] == "unknown"
    # A blocking health fetch on the brain loop starves the ticker; an
    # offloaded one lets it keep ticking during the 0.4 s wait.
    assert len(stamps) > 5, "event loop was starved during the blocking health fetch"
    gaps = [b - a for a, b in pairwise(stamps)]
    assert max(gaps) < 0.3, f"event loop blocked ~{max(gaps):.2f}s during health fetch"


def _migrations_health(pending: int) -> dict:
    return {
        "status": "healthy",
        "components": {
            "migrations": {"pending": pending, "applied": 5, "latest_applied": "x.py"},
        },
    }


def test_default_restart_is_the_shared_helper_with_the_pool(monkeypatch):
    """With no ``restart_fn`` injected the worker restart goes through
    ``docker_utils.restart_container`` and is handed the pool, so it waits
    ``app_settings.brain_docker_restart_timeout_seconds``. The probe used to
    hardcode 30 s, under the worker's 75 s stop grace, and page a restart that
    dockerd went on to finish."""
    helper = AsyncMock(return_value=du.ContainerRestart(
        md.WORKER_CONTAINER, du.RESTART_OK, f"restarted {md.WORKER_CONTAINER}", 90,
    ))
    monkeypatch.setattr(du, "restart_container", helper)
    settings = {
        md.AUTO_RECOVER_SETTING_KEY: "true",
        md.DEFER_WHILE_INFLIGHT_SETTING_KEY: "false",
    }

    async def fetchval(_query, *args):
        return settings.get(args[0]) if args else 0

    pool = _make_pool()
    pool.fetchval = AsyncMock(side_effect=fetchval)

    summary = asyncio.run(
        md.run_migration_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            wait_fn=lambda: (True, _migrations_health(0)),
            health_fetcher=lambda: _migrations_health(1),
        )
    )

    helper.assert_awaited_once_with(md.WORKER_CONTAINER, pool=pool)
    assert summary["status"] == "recovered"


def test_a_worker_that_started_moments_ago_is_not_restarted_and_nothing_pages(monkeypatch):
    """Through the real ``docker_utils`` helper, with only ``subprocess.run``
    faked underneath. The worker's ``State.StartedAt`` is 30 s ago (deploy-sync,
    another brain path's heal or compose restarted it), so ``docker restart``
    never runs: a second restart would kill it mid-boot, and applying
    migrations is what that boot does. No critical page and no health wait."""
    started = datetime.now(UTC) - timedelta(seconds=30)
    stamp = started.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=f"running {stamp}\n", stderr=""))
    monkeypatch.setattr(du.subprocess, "run", run)
    settings = {
        md.AUTO_RECOVER_SETTING_KEY: "true",
        md.DEFER_WHILE_INFLIGHT_SETTING_KEY: "false",
    }

    async def fetchval(_query, *args):
        return settings.get(args[0]) if args else 0

    pool = _make_pool()
    pool.fetchval = AsyncMock(side_effect=fetchval)
    notify = MagicMock()
    wait = MagicMock(return_value=(True, _migrations_health(0)))

    summary = asyncio.run(
        md.run_migration_drift_probe(
            pool,
            notify_fn=notify,
            wait_fn=wait,
            health_fetcher=lambda: _migrations_health(1),
        )
    )

    assert summary["status"] == "recover_worker_recently_started"
    assert [c.args[0][1] for c in run.call_args_list] == ["inspect"], "no `docker restart`"
    notify.assert_not_called()
    wait.assert_not_called()


# ---------------------------------------------------------------------------
# Deploy-checkout resync against real git
# ---------------------------------------------------------------------------


@pytest.fixture
def git_env(monkeypatch):
    """Keep the runner's own git config out of these tests.

    safe.directory is read from system, global and command-line config. A
    runner that carries a global ``safe.directory=*`` (a common workaround for
    exactly this error) would make the dubious-ownership control pass for the
    wrong reason, so both config files are switched off.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "drift-test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "drift-test@example.invalid")
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", raising=False)
    return monkeypatch


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _deploy_checkout_behind_origin(tmp_path: Path) -> tuple[Path, str]:
    """A deploy clone whose fetched origin/main is one commit ahead of HEAD.

    That commit adds a directory, and the clone holds a stray untracked file:
    the two things the resync exists to fix. Returns (clone, origin/main sha).
    """
    origin = tmp_path / "origin.git"
    author = tmp_path / "author"
    deploy = tmp_path / "deploy"
    _git("init", "-q", "--bare", "-b", "main", str(origin))
    _git("clone", "-q", str(origin), str(author))
    (author / "top.txt").write_text("c1\n")
    _git("-C", str(author), "add", "-A")
    _git("-C", str(author), "commit", "-qm", "c1")
    _git("-C", str(author), "push", "-q", "origin", "main")
    _git("clone", "-q", str(origin), str(deploy))
    (author / "newdir").mkdir()
    (author / "newdir" / "0001_migration.py").write_text("# c2\n")
    _git("-C", str(author), "add", "-A")
    _git("-C", str(author), "commit", "-qm", "c2 adds a directory")
    _git("-C", str(author), "push", "-q", "origin", "main")
    _git("-C", str(deploy), "fetch", "-q", "origin")
    (deploy / "stray_scaffold.py").write_text("# untracked\n")
    return deploy, _git("-C", str(author), "rev-parse", "HEAD")


def _refuse_as_foreign_owned(monkeypatch, deploy: Path) -> None:
    """Make git treat ``deploy`` as owned by another user, and prove it does.

    The control is the prod failure itself: plain ``git -C`` must refuse the
    checkout. If it does not, the tests below would pass without testing
    anything.
    """
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    control = subprocess.run(
        ["git", "-C", str(deploy), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    assert control.returncode != 0 and "dubious ownership" in control.stderr, (
        "git did not refuse the checkout, so this test cannot show the fix: "
        f"rc={control.returncode} stderr={control.stderr!r}"
    )


@pytest.mark.unit
def test_resync_works_on_a_checkout_another_user_owns(git_env, tmp_path):
    """The prod failure: root git against the host user's clone. The resync
    must reset to origin/main (new directory included) and clean the stray
    file rather than report 'not a git work tree'."""
    deploy, origin_sha = _deploy_checkout_behind_origin(tmp_path)
    _refuse_as_foreign_owned(git_env, deploy)

    ok, msg = md._sync_deploy_checkout(str(deploy))

    assert ok, msg
    assert _git("-C", str(deploy), "-c", f"safe.directory={deploy}",
                "rev-parse", "HEAD") == origin_sha
    assert (deploy / "newdir" / "0001_migration.py").exists()
    assert not (deploy / "stray_scaffold.py").exists()
    assert origin_sha[:7] in msg


@pytest.mark.unit
@pytest.mark.parametrize("spelling", ["trailing_slash", "symlink"])
def test_resync_trusts_the_resolved_path(git_env, tmp_path, spelling):
    """git 2.43 compares safe.directory to the resolved path as written, so
    a trailing slash or a symlink in migration_drift_deploy_checkout_path
    would not match unless the probe resolves it first."""
    deploy, origin_sha = _deploy_checkout_behind_origin(tmp_path)
    if spelling == "trailing_slash":
        configured = f"{deploy}/"
    else:
        link = tmp_path / "deploy-link"
        link.symlink_to(deploy, target_is_directory=True)
        configured = str(link)
    _refuse_as_foreign_owned(git_env, deploy)

    ok, msg = md._sync_deploy_checkout(configured)

    assert ok, msg
    assert origin_sha[:7] in msg


@pytest.mark.unit
def test_run_git_trusts_exactly_the_configured_checkout(monkeypatch, tmp_path):
    """The trust is scoped: one safe.directory entry, the resolved deploy
    path, never ``*``. The owner kwargs reach subprocess.run."""
    owner = {"user": 1000, "group": 1000, "extra_groups": [], "env": {"PATH": "/bin"}}
    monkeypatch.setattr(md, "_git_owner_kwargs", lambda _path: owner)
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(md.subprocess, "run", fake_run)
    md._run_git(f"{tmp_path}/", "status", "--porcelain")

    assert seen["argv"] == [
        "git", "-c", f"safe.directory={tmp_path.resolve()}",
        "-C", f"{tmp_path}/", "status", "--porcelain",
    ]
    assert {k: seen["kwargs"][k] for k in owner} == owner


def _stat_as(monkeypatch, path: str, *, uid: int, gid: int) -> None:
    """Fake ``os.stat`` for one path only; everything else sees the real one."""
    real_stat = os.stat
    fake = types.SimpleNamespace(st_uid=uid, st_gid=gid)
    monkeypatch.setattr(
        md.os, "stat",
        lambda p, *a, **k: fake if p == path else real_stat(p, *a, **k),
    )


@pytest.mark.unit
def test_root_brain_runs_git_as_the_checkout_owner(monkeypatch):
    """Prod: the brain is root and /host-deploy belongs to uid 1000. Run as
    root, a reset that creates a directory leaves it root:root 755 and locks
    the host's own deploy-sync out of it (reproduced 2026-09-28: 'fatal:
    Could not reset index file'). git must run as the owner instead, with no
    HOME, because the owner cannot read root's."""
    monkeypatch.setattr(md.os, "geteuid", lambda: 0, raising=False)
    _stat_as(monkeypatch, "/host-deploy", uid=1000, gid=1001)
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/root/.config")

    kwargs = md._git_owner_kwargs("/host-deploy")

    assert kwargs["user"] == 1000
    assert kwargs["group"] == 1001
    assert kwargs["extra_groups"] == []
    assert "HOME" not in kwargs["env"]
    assert "XDG_CONFIG_HOME" not in kwargs["env"]
    assert kwargs["env"]["PATH"] == os.environ["PATH"]


@pytest.mark.unit
def test_no_identity_switch_when_not_root(monkeypatch):
    """A brain that is not root cannot switch users, so it runs git as
    itself and relies on safe.directory."""
    monkeypatch.setattr(md.os, "geteuid", lambda: 1000, raising=False)
    _stat_as(monkeypatch, "/host-deploy", uid=1001, gid=1001)
    assert md._git_owner_kwargs("/host-deploy") == {}


@pytest.mark.unit
def test_no_identity_switch_for_a_root_owned_checkout(monkeypatch):
    """Docker Desktop presents bind mounts as root-owned. Root git on a
    root-owned tree needs no switch; that is why the resync worked on the
    Windows host through 2026-07-12."""
    monkeypatch.setattr(md.os, "geteuid", lambda: 0, raising=False)
    _stat_as(monkeypatch, "/host-deploy", uid=0, gid=0)
    assert md._git_owner_kwargs("/host-deploy") == {}


@pytest.mark.unit
def test_no_identity_switch_when_the_path_is_missing(monkeypatch, tmp_path):
    """A path that cannot be stat'ed gets no switch, so git reports the
    missing path itself instead of the probe guessing."""
    monkeypatch.setattr(md.os, "geteuid", lambda: 0, raising=False)
    assert md._git_owner_kwargs(str(tmp_path / "absent")) == {}


# ---------------------------------------------------------------------------
# A failed resync is loud
# ---------------------------------------------------------------------------


_EPISODE_STATE = {
    "_last_notify_drift_count": None,
    "_last_relkind_notify_key": None,
    "_last_recover_attempt_pending": None,
    "_recover_attempts": 0,
    "_recover_cycles_waited": 0,
    "_inflight_defers": 0,
}


@pytest.fixture(autouse=True)
def _fresh_episode(monkeypatch):
    """The probe keeps its recovery episode in module globals; start each
    test from a clean one and put it back afterwards."""
    for name, value in _EPISODE_STATE.items():
        monkeypatch.setattr(md, name, value)


def _auto_sync_pool():
    """A pool whose settings turn auto-recover and auto-sync on."""
    settings = {
        md.AUTO_RECOVER_SETTING_KEY: "true",
        md.AUTO_SYNC_SETTING_KEY: "true",
        md.RECOVER_MAX_ATTEMPTS_SETTING_KEY: "3",
        md.DEFER_WHILE_INFLIGHT_SETTING_KEY: "false",
    }

    async def fetchval(_query, *args):
        return settings.get(args[0]) if args else 0

    pool = _make_pool()
    pool.fetchval = AsyncMock(side_effect=fetchval)
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _run_with_sync_result(pool, ok: bool, msg: str) -> dict:
    return asyncio.run(
        md.run_migration_drift_probe(
            pool,
            notify_fn=lambda **_kw: None,
            restart_fn=restart_stub(),
            wait_fn=lambda: (True, _migrations_health(0)),
            health_fetcher=lambda: _migrations_health(1),
            sync_fn=lambda _path: (ok, msg),
        )
    )


def _findings(pool) -> list[dict]:
    return [
        json.loads(call.args[2]) for call in pool.execute.call_args_list
        if "'finding'" in call.args[0]
    ]


def _audit_severity(pool, event: str) -> str:
    for call in pool.execute.call_args_list:
        if "'finding'" not in call.args[0] and call.args[1] == event:
            return call.args[4]
    pytest.fail(f"no {event} audit row")


@pytest.mark.unit
def test_failed_resync_warns_and_emits_a_finding(caplog):
    """Six weeks of failed resyncs reached no one: an INFO log line and an
    info audit row. A failure must log at WARNING and raise a finding, and
    the restart must still run."""
    pool = _auto_sync_pool()
    error = "/host-deploy is not a git work tree (fatal: detected dubious ownership)"

    with caplog.at_level(logging.INFO, logger="brain.migration_drift_probe"):
        summary = _run_with_sync_result(pool, False, error)

    assert summary["status"] == "recovered"  # the restart still ran
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "deploy resync ok=False" in r.getMessage()
    ]
    assert warnings, "a failed resync must log at WARNING"
    assert _audit_severity(pool, "probe.migration_drift_sync_failed") == "warning"
    (finding,) = _findings(pool)
    assert finding["kind"] == md.FINDING_KIND_RESYNC_FAILED
    assert finding["extra"]["error"] == error
    assert finding["extra"]["deploy_path"] == md._DEFAULT_DEPLOY_PATH
    assert error in finding["body"]


@pytest.mark.unit
def test_successful_resync_stays_quiet(caplog):
    """The log line the post-deploy check looks for is ``deploy resync
    ok=True``, at INFO, with no finding."""
    pool = _auto_sync_pool()

    with caplog.at_level(logging.INFO, logger="brain.migration_drift_probe"):
        _run_with_sync_result(pool, True, "reset --hard origin/main + clean -fd → HEAD abc1234")

    infos = [
        r for r in caplog.records
        if r.levelno == logging.INFO and "deploy resync ok=True" in r.getMessage()
    ]
    assert infos
    assert _audit_severity(pool, "probe.migration_drift_synced") == "info"
    assert _findings(pool) == []


def _finding_kinds() -> list[str]:
    """Every FINDING_KIND_* the probe declares, derived rather than listed."""
    return [v for k, v in vars(md).items() if k.startswith("FINDING_KIND_")]


@pytest.mark.unit
def test_every_finding_kind_has_a_declared_delivery_policy():
    """findings.default is inert, so an undeclared kind would route by the
    dispatcher's default severity matrix rather than by a decision. The
    consumer-contract lint cannot see this probe's kinds: it only reads
    literal kinds passed to a function named ``emit_finding``."""
    from poindexter.services.settings_defaults import DEFAULTS

    for kind in nonempty(_finding_kinds(), "FINDING_KIND_*"):
        assert "." not in kind
        for field in ("delivery", "fallback", "cooldown_minutes", "min_severity"):
            assert f"findings.{kind}.{field}" in DEFAULTS, f"findings.{kind}.{field}"
