"""Unit tests for scripts/backup-offsite/run.sh (poindexter#386).

`run_backup` streams a fresh UNCOMPRESSED pg_dump straight into
`restic backup --stdin` (2026-07 dedup fix): Tier 1's `--format=custom`
dumps are zlib-compressed, which defeats restic's content-defined
chunking — every daily dump looked like 100% new data (measured 1.01x
restic compression, repo at 8.3 GiB / 62 snapshots after 25 days, on
track to breach the B2 10 GB free cap). Feeding restic uncompressed
bytes lets it dedupe + compress, so the repo holds near the live DB
size regardless of snapshot count.

Two regressions are pinned here:

1. The 2026-06-23 `offsite_backup_failed` alert that reported "rc=0":
   `run_backup` captured `$?` *after* the `if restic ...; fi` statement,
   and a fall-through `if` with no `else` resets `$?` to 0 — so the alert
   annotation lied about the exit code and the function returned success
   to `tick()` on a real restic failure.
2. The streamed-pipe truncation trap: if pg_dump dies mid-stream, restic
   can still exit 0 on the truncated input and save a short snapshot.
   `set -o pipefail` (global) must propagate the pg_dump failure so the
   runner alerts + returns non-zero instead of silently "succeeding".

The script exposes its functions when sourced (it returns before the
service loop), so these tests source it under bash with `pg_dump`,
`restic`, and the DB-writing helpers stubbed out.
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

# Stub the external/DB-touching pieces.
#   pg_dump  — echoes its args to STDERR (its stdout is piped into restic),
#              emits a few fake bytes, and exits FAKE_PGDUMP_RC.
#   restic   — drains stdin, echoes its args to stdout for flag assertions,
#              and exits FAKE_RESTIC_RC.
#   emit_*   — print markers instead of hitting psql.
pg_dump() { echo "PGDUMP_ARGS $*" >&2; printf 'FAKE_DUMP_BYTES'; return "${FAKE_PGDUMP_RC:-0}"; }
restic() {
    cat >/dev/null 2>&1 || true
    echo "RESTIC_ARGS $*"
    if [[ -n "${FAKE_RESTIC_STDERR:-}" ]]; then
        printf '%s' "${FAKE_RESTIC_STDERR}" >&2
    fi
    return "${FAKE_RESTIC_RC}"
}
emit_heartbeat() { echo "HEARTBEAT event=$1"; }
emit_alert() { echo "ALERT severity=$1 summary=$2 description=$3"; }

rc=0
if [[ -n "${HOST_ARG:-}" ]]; then
    run_backup "s3:example.test/repo" "daily" "${HOST_ARG}" || rc=$?
else
    # Two-arg call — exercises the in-function default under `set -u`.
    run_backup "s3:example.test/repo" "daily" || rc=$?
fi
echo "RUN_BACKUP_RC=${rc}"
"""


def _run_backup_harness(
    tmp_path: Path,
    restic_rc: int,
    restic_host: str | None = None,
    pgdump_rc: int = 0,
    restic_stderr: str | None = None,
) -> subprocess.CompletedProcess:
    assert _BASH is not None  # guarded by the module-level skipif
    env = {
        "PATH": str(Path(_BASH).parent),
        "RUN_SH": _RUN_SH.as_posix(),
        "BACKUP_DIR": (tmp_path / "backups").as_posix(),
        "PGPASSWORD": "test",  # run_backup now references it (pg_dump prefix)
        "FAKE_RESTIC_RC": str(restic_rc),
        "FAKE_PGDUMP_RC": str(pgdump_rc),
    }
    if restic_host is not None:
        env["HOST_ARG"] = restic_host
    if restic_stderr is not None:
        env["FAKE_RESTIC_STDERR"] = restic_stderr
    return subprocess.run(
        [_BASH, "-c", _HARNESS],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _combined(result: subprocess.CompletedProcess) -> str:
    # pg_dump echoes its args to stderr (stdout is piped into restic); every
    # other marker lands on stdout. Combine so assertions don't care which.
    return result.stdout + result.stderr


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


def test_backup_streams_uncompressed_dump_into_restic_stdin(tmp_path):
    """The dedup fix: pg_dump must run with -Z0 (no compression) and restic
    must read it via --stdin. If either regresses, restic's dedup/compression
    silently breaks again and the B2 repo resumes unbounded growth."""
    out = _combined(_run_backup_harness(tmp_path, restic_rc=0))
    assert "PGDUMP_ARGS" in out
    assert "-Z0" in out  # no pg_dump-side compression
    assert "--format=custom" in out
    assert "--stdin" in out
    assert "--stdin-filename poindexter_brain.dump" in out


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


def test_pgdump_failure_is_not_silent_success(tmp_path):
    """Truncation trap: pg_dump fails mid-stream but restic exits 0 on the
    short input. `set -o pipefail` must surface the pg_dump failure so the
    runner alerts + returns non-zero instead of saving a truncated snapshot
    and stamping a success heartbeat."""
    result = _run_backup_harness(tmp_path, restic_rc=0, pgdump_rc=3)
    assert "HEARTBEAT" not in result.stdout
    assert "ALERT severity=critical" in result.stdout
    assert "RUN_BACKUP_RC=0" not in result.stdout
    assert "RUN_BACKUP_RC=3" in result.stdout


def test_failure_propagates_nonzero_to_caller(tmp_path):
    """tick() must see failure so it skips prune/verify on a failed backup."""
    result = _run_backup_harness(tmp_path, restic_rc=1)
    assert "RUN_BACKUP_RC=0" not in result.stdout


def test_backup_pins_default_restic_host(tmp_path):
    """--host must default to a stable value: the container hostname changes
    on every recreate, which would start a new snapshot lineage ("no parent
    snapshot found") and force a full re-ingest instead of a delta."""
    result = _run_backup_harness(tmp_path, restic_rc=0)
    assert "--host poindexter" in result.stdout


def test_backup_pins_configured_restic_host(tmp_path):
    """offsite_backup_restic_host (passed through by tick()) wins over the
    default."""
    result = _run_backup_harness(tmp_path, restic_rc=0, restic_host="my-install")
    assert "--host my-install" in result.stdout
    assert "--host poindexter" not in result.stdout


def test_failure_alert_keeps_generic_guidance_text(tmp_path):
    """The original creds/network/postgres-reachability hint must survive —
    it's the only lead left when a pg_dump-side failure (e.g. postgres
    unreachable) never reaches restic, so err_tail captures nothing."""
    result = _run_backup_harness(tmp_path, restic_rc=1)
    assert "Check creds, network, postgres reachability" in result.stdout


def test_failure_alert_includes_restic_stderr(tmp_path):
    """The alert must surface restic's actual error text, not just an rc
    number — see the 2026-07-16 B2 storage-cap incident, where diagnosing
    the real cause required grepping docker logs because the alert only
    said "rc=1"."""
    result = _run_backup_harness(
        tmp_path,
        restic_rc=1,
        restic_stderr="client.PutObject: storage cap exceeded",
    )
    assert "ALERT severity=critical" in result.stdout
    assert "storage cap exceeded" in result.stdout


def test_failure_alert_truncates_long_restic_stderr(tmp_path):
    """A retry-spamming restic must not blow up the alert row — the tail is
    capped, keeping the most recent (most decisive) error text rather than
    the first line of a long retry sequence."""
    long_stderr = ("X" * 900) + "TAILMARKER_end"
    result = _run_backup_harness(tmp_path, restic_rc=1, restic_stderr=long_stderr)
    assert "TAILMARKER_end" in result.stdout
    assert result.stdout.count("X") < 700


def test_restic_stderr_still_visible_on_success(tmp_path):
    """Capturing stderr for the alert must not swallow it from docker logs —
    restic can warn on stderr even on a 0 exit (e.g. a retried-then-succeeded
    upload)."""
    result = _run_backup_harness(
        tmp_path, restic_rc=0, restic_stderr="warning: retried once",
    )
    assert "warning: retried once" in _combined(result)
    assert "ALERT" not in result.stdout
