"""Unit tests for scripts/backup-offsite/run.sh (poindexter#386).

Regression coverage for the 2026-06-23 `offsite_backup_failed` alert that
reported "rc=0": `run_backup` captured `$?` *after* the `if restic ...; fi`
statement completed, and a fall-through `if` with no `else` resets `$?` to 0 —
so the alert annotation lied about the exit code and the function returned
success to `tick()` on a real restic failure.

The script exposes its functions when sourced (it returns before the service
loop), so these tests source it under bash with `restic` and the DB-writing
helpers stubbed out.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# From this test file: parents[0]=scripts, [1]=unit, [2]=tests,
# [3]=cofounder_agent, [4]=src, [5]=repo root (same convention as
# test_backup_offsite_secrets.py).
_RUN_SH = (
    Path(__file__).resolve().parents[5] / "scripts" / "backup-offsite" / "run.sh"
)

_BASH = shutil.which("bash")

_HARNESS = """
source "${RUN_SH}"

# Stub the external/DB-touching pieces. restic's exit code is driven by
# FAKE_RESTIC_RC; the emit_* helpers print markers instead of hitting psql.
restic() { return "${FAKE_RESTIC_RC}"; }
emit_heartbeat() { echo "HEARTBEAT event=$1"; }
emit_alert() { echo "ALERT severity=$1 summary=$2 description=$3"; }

rc=0
run_backup "s3:example.test/repo" "daily" || rc=$?
echo "RUN_BACKUP_RC=${rc}"
"""


def _run_backup_harness(tmp_path: Path, restic_rc: int) -> subprocess.CompletedProcess:
    assert _BASH is not None  # guarded by the module-level skipif
    backup_dir = tmp_path / "backups"
    (backup_dir / "daily").mkdir(parents=True)
    return subprocess.run(
        [_BASH, "-c", _HARNESS],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "PATH": str(Path(_BASH).parent),
            "RUN_SH": _RUN_SH.as_posix(),
            "BACKUP_DIR": backup_dir.as_posix(),
            "FAKE_RESTIC_RC": str(restic_rc),
        },
    )


pytestmark = pytest.mark.skipif(
    _BASH is None, reason="bash not available on this host"
)


def test_run_sh_exists():
    assert _RUN_SH.is_file()


def test_success_emits_heartbeat_and_no_alert(tmp_path):
    result = _run_backup_harness(tmp_path, restic_rc=0)
    assert "HEARTBEAT event=offsite_backup_succeeded" in result.stdout
    assert "ALERT" not in result.stdout
    assert "RUN_BACKUP_RC=0" in result.stdout


@pytest.mark.parametrize("restic_rc", [1, 3])
def test_failure_alert_reports_real_exit_code(tmp_path, restic_rc):
    """A restic failure must alert with the actual rc — never 'rc=0'."""
    result = _run_backup_harness(tmp_path, restic_rc=restic_rc)
    assert "HEARTBEAT" not in result.stdout
    assert "ALERT severity=critical" in result.stdout
    assert f"(rc={restic_rc})" in result.stdout
    # The 2026-06-23 bug: annotation claimed the backup "returned 0".
    assert "(rc=0)" not in result.stdout
    assert f"RUN_BACKUP_RC={restic_rc}" in result.stdout


def test_failure_propagates_nonzero_to_caller(tmp_path):
    """tick() must see failure so it skips prune/verify on a failed backup."""
    result = _run_backup_harness(tmp_path, restic_rc=1)
    assert "RUN_BACKUP_RC=0" not in result.stdout
