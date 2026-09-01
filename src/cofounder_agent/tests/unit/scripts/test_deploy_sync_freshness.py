"""Contract tests for the deploy-path dead-man's switch (poindexter#977).

Three separate things have to stay true for a frozen deploy timer to page,
and each can rot independently:

1. the timer must schedule off the CLOCK, not off the last run's outcome;
2. every ``write_status`` must also emit the DB heartbeat the probe reads;
3. the probe's known-result set must keep up with the result strings the
   script actually writes.

(3) is the sneaky one. Add a new ``write_status some-new-outcome`` and the
probe still runs, still reports fresh, and quietly classifies a healthy
outcome as unknown — the drift is invisible until you need the alarm.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "deploy-checkout-sync.sh").exists()
    )


@pytest.fixture(scope="module")
def sync_sh() -> str:
    return (_repo_root() / "scripts/linux/deploy-checkout-sync.sh").read_text()


@pytest.fixture(scope="module")
def timer_unit() -> str:
    path = _repo_root() / "infrastructure/systemd/poindexter-deploy-sync.timer"
    assert path.is_file(), f"missing timer unit: {path}"
    return path.read_text()


def test_timer_schedules_on_the_clock_not_the_last_run(timer_unit):
    """The 2026-08-02 outage: `OnUnitActiveSec` chains the next fire off the
    last activation, and after a failed run the timer stopped scheduling
    entirely (`NEXT: -`). Merged main sat undeployed for ~45 minutes and
    needed a manual `systemctl start`. An absolute schedule cannot enter that
    state — the next fire is a property of the clock."""
    assert re.search(r"^OnCalendar=", timer_unit, re.M), "timer needs OnCalendar"
    assert not re.search(r"^OnUnitActiveSec=", timer_unit, re.M), (
        "OnUnitActiveSec makes scheduling depend on the previous run's outcome "
        "— the exact failure #977 is about"
    )


def test_timer_catches_up_after_downtime(timer_unit):
    assert re.search(r"^Persistent=true", timer_unit, re.M)


def test_timer_still_fires_after_boot(timer_unit):
    """OnCalendar alone waits for the next aligned slot; the boot trigger is
    what deploys a commit merged while the host was down."""
    assert re.search(r"^OnBootSec=", timer_unit, re.M)


def test_every_status_write_also_emits_the_heartbeat(sync_sh):
    """The probe reads the DB heartbeat, not the status file — so a
    `write_status` path that skipped the heartbeat would be a silent hole in
    liveness exactly when something is going wrong."""
    body = sync_sh.split("write_status() {", 1)[1].split("\n}", 1)[0]
    assert "emit_run_heartbeat" in body, (
        "write_status must emit the DB heartbeat, or the probe goes blind"
    )


def test_heartbeat_defuses_dollar_and_newlines(sync_sh):
    """`detail` carries arbitrary git/docker output into a `$$`-quoted SQL
    literal in a single-statement `psql -c`. A `$` would terminate the quote
    and a newline would break the statement."""
    body = sync_sh.split("emit_run_heartbeat() {", 1)[1].split("\n}", 1)[0]
    assert "tr '\\n$'" in body, "detail must have newlines and $ stripped"


def test_heartbeat_can_never_block_a_deploy(sync_sh):
    """Postgres being unreachable is often WHY a deploy is failing. Reporting
    on a deploy must not be able to hang or fail one."""
    body = sync_sh.split("emit_run_heartbeat() {", 1)[1].split("\n}", 1)[0]
    assert "timeout " in body, "docker exec inherits no timeout"
    assert "|| true" in body, "heartbeat must be fail-open"


def test_probe_knows_every_result_the_script_writes(sync_sh):
    """Drift guard. `_OK_RESULTS` is an explicit allowlist so a NEW result
    string surfaces as unknown rather than being folded into 'healthy' — but
    that only helps if someone notices. This fails the moment the two lists
    disagree."""
    from brain.deploy_sync_probe import _OK_RESULTS

    written = set(re.findall(r"^\s*write_status\s+([a-z][a-z0-9-]*)", sync_sh, re.M))
    assert written, "found no write_status call sites — did the script move?"

    unknown = written - set(_OK_RESULTS) - {"error"}
    assert not unknown, (
        f"deploy-checkout-sync.sh writes result(s) {sorted(unknown)} that "
        f"brain/deploy_sync_probe.py does not classify. Add them to "
        f"_OK_RESULTS if they mean the sync did its job, or leave them out "
        f"deliberately — but decide, don't drift."
    )

    stale = set(_OK_RESULTS) - written
    assert not stale, (
        f"_OK_RESULTS lists {sorted(stale)}, which the script no longer "
        f"writes — a dead entry hides a renamed outcome."
    )
