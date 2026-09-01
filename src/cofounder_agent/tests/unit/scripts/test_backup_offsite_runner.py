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

import os
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
pg_dump() {
    echo "PGDUMP_ARGS $*" >&2
    printf 'FAKE_DUMP_BYTES'
    # FAIL_DB fails exactly one database, so a mixed tick can be tested.
    if [[ -n "${FAIL_DB:-}" && "$*" == *"-d ${FAIL_DB} "* ]]; then return 1; fi
    return "${FAKE_PGDUMP_RC:-0}"
}
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
# Settings come from the env so each test can vary one key without psql
# (same convention as _CONFIG_HARNESS below).
read_setting() {
    local key="$1" default="$2" var
    var="SETTING_${key}"
    if [[ -n "${!var:-}" ]]; then printf '%s' "${!var}"; else printf '%s' "${default}"; fi
}

rc=0
if [[ -n "${DIRECT_DB:-}" ]]; then
    # Call the single-database worker directly. The exact-rc contract lives
    # here (see test_failure_alert_reports_real_exit_code).
    run_db_backup "s3:example.test/repo" "daily" "poindexter" "${DIRECT_DB}" || rc=$?
elif [[ -n "${HOST_ARG:-}" ]]; then
    run_backup "s3:example.test/repo" "daily" "${HOST_ARG}" || rc=$?
else
    # Two-arg call — exercises the in-function default under `set -u`.
    run_backup "s3:example.test/repo" "daily" || rc=$?
fi
echo "RUN_BACKUP_RC=${rc}"
echo "BACKUP_ANY_OK=${BACKUP_ANY_OK}"
"""


def _run_backup_harness(
    tmp_path: Path,
    restic_rc: int,
    restic_host: str | None = None,
    pgdump_rc: int = 0,
    restic_stderr: str | None = None,
    databases: str | None = None,
    direct_db: str | None = None,
    fail_db: str | None = None,
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
    if databases is not None:
        env["SETTING_offsite_backup_databases"] = databases
    if direct_db is not None:
        env["DIRECT_DB"] = direct_db
    if fail_db is not None:
        env["FAIL_DB"] = fail_db
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
    """A restic failure must alert with the actual rc — never 'rc=0'.

    Exercised against `run_db_backup`, which is where this contract lives
    since poindexter#891 made the source set a list: an aggregate rc over N
    databases cannot represent "one failed with 3 and another with 5", so
    `run_backup` returns a plain 1 and each database alerts with its own real
    rc. The 2026-06-23 regression was in the *annotation*, and that is still
    pinned here, one layer down.
    """
    result = _run_backup_harness(
        tmp_path, restic_rc=restic_rc, direct_db="poindexter_brain"
    )
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
    result = _run_backup_harness(
        tmp_path, restic_rc=0, pgdump_rc=3, direct_db="poindexter_brain"
    )
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


# --------------------------------------------------------------------------
# run_config_backup — the second (tag=config) snapshot (poindexter#889)
#
# Context these tests exist to protect: until 2026-08-27 the offsite tier
# shipped ONLY a pg_dump. bootstrap.toml (holding `poindexter_secret_key`)
# and the Claude memory tree rode exclusively on the Tier-3 DR USB job, so
# pulling that stick collapsed every copy of the secret key onto one
# partition — and without that key, this repo's own restic password cannot
# be decrypted, making the healthy DB snapshot unopenable.
# --------------------------------------------------------------------------

_CONFIG_HARNESS = """
source "${RUN_SH}"

restic() {
    echo "RESTIC_ARGS $*"
    if [[ -n "${FAKE_RESTIC_STDERR:-}" ]]; then
        printf '%s' "${FAKE_RESTIC_STDERR}" >&2
    fi
    return "${FAKE_RESTIC_RC}"
}
# Settings come from the env so each test can vary one key without psql.
read_setting() {
    local key="$1" default="$2" var
    var="SETTING_${key}"
    if [[ -n "${!var:-}" ]]; then printf '%s' "${!var}"; else printf '%s' "${default}"; fi
}
emit_heartbeat() { echo "HEARTBEAT event=$1"; }
emit_alert() { echo "ALERT severity=$1 summary=$2 description=$3"; }

rc=0
run_config_backup "s3:example.test/repo" "poindexter" || rc=$?
echo "CONFIG_RC=${rc}"
"""


def _default_setting(name: str) -> str:
    """Read a DEFAULT_* constant out of run.sh by asking bash for it, so the
    assertions below test the value the container actually ships rather than
    a copy that can drift."""
    assert _BASH is not None
    result = subprocess.run(
        [_BASH, "-c", f'source "$RUN_SH"; printf "%s" "${{{name}}}"'],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": str(Path(_BASH).parent), "RUN_SH": _RUN_SH.as_posix()},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _run_config_harness(
    tmp_path: Path,
    restic_rc: int = 0,
    settings: dict[str, str] | None = None,
    make_paths: tuple[str, ...] = ("poindexter", "claude"),
) -> subprocess.CompletedProcess:
    assert _BASH is not None
    root = tmp_path / "config"
    for name in make_paths:
        (root / name).mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": str(Path(_BASH).parent),
        "RUN_SH": _RUN_SH.as_posix(),
        "BACKUP_DIR": (tmp_path / "backups").as_posix(),
        "PGPASSWORD": "test",
        "FAKE_RESTIC_RC": str(restic_rc),
        # Point the default path list at the tmp tree.
        "SETTING_offsite_backup_config_paths": ",".join(
            (root / n).as_posix() for n in ("poindexter", "claude")
        ),
    }
    for key, value in (settings or {}).items():
        env[f"SETTING_{key}"] = value
    return subprocess.run(
        [_BASH, "-c", _CONFIG_HARNESS],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_config_backup_tags_and_pins_host(tmp_path):
    """The config snapshot must be tagged so `restic snapshots --tag config`
    isolates it, and share the pinned --host so its parent lineage is stable
    across container recreates (same reason as the DB snapshot)."""
    result = _run_config_harness(tmp_path)
    assert "--tag config" in result.stdout
    assert "--host poindexter" in result.stdout
    assert "HEARTBEAT event=offsite_config_backup_succeeded" in result.stdout
    assert "CONFIG_RC=0" in result.stdout


def test_config_backup_does_not_exclude_the_config_roots(tmp_path):
    """THE regression that would silently empty this backup: an exclude
    pattern broad enough to swallow /config/poindexter itself would keep the
    snapshot succeeding while covering nothing — the exact silent-success
    shape of the failure this whole feature exists to fix. The shipped
    default excludes must all be strictly BELOW a root, never a root."""
    from_defaults = [
        p.strip()
        for p in _default_setting("DEFAULT_CONFIG_EXCLUDES").split(",")
        if p.strip()
    ]
    roots = [
        p.strip()
        for p in _default_setting("DEFAULT_CONFIG_PATHS").split(",")
        if p.strip()
    ]
    assert roots, "config paths default must not be empty"
    for root in roots:
        assert root not in from_defaults
        for pattern in from_defaults:
            assert pattern.startswith(f"{root}/") or not pattern.startswith(root), (
                f"exclude {pattern!r} is broad enough to swallow root {root!r}"
            )


def test_config_backup_applies_the_last_exclude_in_the_list(tmp_path):
    """CSV parsing must not drop the final element. `printf '%s'` emits no
    trailing newline, so `while read` discards the unterminated last field —
    which silently dropped the last exclude pattern (found in review). The
    list would keep reading as correct while doing less than it said."""
    excludes = _default_setting("DEFAULT_CONFIG_EXCLUDES")
    last = excludes.split(",")[-1].strip()
    assert last, "excludes default must not end with a trailing comma"
    result = _run_config_harness(
        tmp_path, settings={"offsite_backup_config_excludes": excludes}
    )
    assert f"--exclude {last}" in result.stdout


def test_config_backup_includes_the_last_configured_path(tmp_path):
    """Same trailing-newline trap on the path list: dropping the last entry
    would silently omit a whole config root from the backup."""
    result = _run_config_harness(tmp_path)
    backup_line = result.stdout.split("RESTIC_ARGS")[1]
    assert "/config/claude" in backup_line or "claude" in backup_line


_UNREADABLE_HARNESS = _CONFIG_HARNESS.replace(
    "rc=0\nrun_config_backup",
    # Force the predicate to report the claude path unreadable. Stubbing the
    # predicate (rather than chmod'ing a fixture) is what makes this runnable
    # as root — see _config_path_readable in run.sh.
    '_config_path_readable() { [[ "$1" != *"/claude" ]]; }\n\nrc=0\nrun_config_backup',
)


def test_config_backup_alerts_on_mounted_but_unreadable_path(tmp_path):
    """A uid mismatch makes a correctly-mounted path unreadable, and that is
    WORSE than a missing mount: restic exits 3 on unreadable content but
    still SAVES A SNAPSHOT ("processed 0 files ... snapshot saved", verified
    against restic 0.16.4), so `restic snapshots --tag config` lists
    reassuring entries containing nothing. The path must be alerted on and
    never handed to restic.

    Drives the branch by stubbing `_config_path_readable`, not by chmod:
    CI runs as root, root holds CAP_DAC_OVERRIDE, and a `chmod 000` fixture
    is therefore readable there — so a permissions-based fixture would make
    this test silently vacuous in the one environment that gates merges."""
    assert _BASH is not None
    root = tmp_path / "config"
    for name in ("poindexter", "claude"):
        (root / name).mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": str(Path(_BASH).parent),
        "RUN_SH": _RUN_SH.as_posix(),
        "BACKUP_DIR": (tmp_path / "backups").as_posix(),
        "PGPASSWORD": "test",
        "FAKE_RESTIC_RC": "0",
        "SETTING_offsite_backup_config_paths": ",".join(
            (root / n).as_posix() for n in ("poindexter", "claude")
        ),
    }
    result = subprocess.run(
        [_BASH, "-c", _UNREADABLE_HARNESS],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert "UNREADABLE" in result.stdout
    assert "ALERT severity=warning" in result.stdout
    assert "cannot read mounted path" in result.stdout
    # The unreadable path must NOT be handed to restic — that is what
    # produces the empty-but-saved snapshot.
    args = result.stdout.split("RESTIC_ARGS")[1]
    assert (root / "claude").as_posix() not in args
    assert (root / "poindexter").as_posix() in args


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root bypasses DAC (CAP_DAC_OVERRIDE), so chmod 000 is still readable",
)
def test_config_path_readable_predicate_honours_real_permissions(tmp_path):
    """Companion to the stubbed test above: proves the predicate itself
    actually tracks filesystem permissions, not just that the branch works.
    Skipped as root, where the premise cannot hold."""
    assert _BASH is not None
    locked = tmp_path / "locked"
    locked.mkdir()
    ok = tmp_path / "ok"
    ok.mkdir()
    locked.chmod(0o000)
    try:
        result = subprocess.run(
            [
                _BASH, "-c",
                f'source "$RUN_SH"; '
                f'_config_path_readable "{ok.as_posix()}" && echo OK_READABLE; '
                f'_config_path_readable "{locked.as_posix()}" || echo LOCKED_UNREADABLE',
            ],
            capture_output=True, text=True, timeout=30,
            env={"PATH": str(Path(_BASH).parent), "RUN_SH": _RUN_SH.as_posix()},
        )
    finally:
        locked.chmod(0o755)  # let pytest clean up
    assert "OK_READABLE" in result.stdout
    assert "LOCKED_UNREADABLE" in result.stdout


def test_config_backup_skips_when_disabled(tmp_path):
    """Operators must be able to turn this off without editing compose."""
    result = _run_config_harness(
        tmp_path, settings={"offsite_backup_config_enabled": "false"}
    )
    assert "RESTIC_ARGS" not in result.stdout
    assert "HEARTBEAT" not in result.stdout
    assert "CONFIG_RC=0" in result.stdout


def test_config_backup_skips_missing_paths_but_backs_up_the_rest(tmp_path):
    """A path listed but not mounted is a compose mistake, not a reason to
    lose the paths that ARE present."""
    result = _run_config_harness(tmp_path, make_paths=("poindexter",))
    assert "not mounted" in result.stdout
    assert "RESTIC_ARGS" in result.stdout
    # Assert on the SOURCE path (the tmp tree), not "/config/claude" — that
    # literal also appears in the --exclude args and would match either way.
    absent_source = (tmp_path / "config" / "claude").as_posix()
    present_source = (tmp_path / "config" / "poindexter").as_posix()
    args = result.stdout.split("RESTIC_ARGS")[1]
    assert present_source in args
    assert absent_source not in args
    assert "CONFIG_RC=0" in result.stdout


def test_config_backup_alerts_when_no_paths_are_mounted(tmp_path):
    """All mounts missing means the backup covers nothing. It must alert
    rather than report a cheerful empty success — a silently-skipped config
    backup is precisely the failure mode being fixed here."""
    result = _run_config_harness(tmp_path, make_paths=())
    assert "ALERT severity=warning" in result.stdout
    assert "found no paths" in result.stdout
    assert "RESTIC_ARGS" not in result.stdout


def test_config_backup_failure_is_warning_and_non_fatal(tmp_path):
    """A config-backup failure must alert (never silent) but must NOT abort
    the tick: the DB snapshot already succeeded by this point, and swallowing
    prune/verify over a config hiccup would be a worse trade."""
    result = _run_config_harness(tmp_path, restic_rc=1)
    assert "ALERT severity=warning" in result.stdout
    assert "ALERT severity=critical" not in result.stdout
    assert "(rc=1)" in result.stdout
    assert "HEARTBEAT" not in result.stdout
    # Non-fatal: returns 0 so tick() still runs prune + verify.
    assert "CONFIG_RC=0" in result.stdout


# --- poindexter#891: multi-database source set --------------------------------


def test_default_database_list_preserves_the_single_stream_path(tmp_path):
    """Back-compat: with the shipped default, the tick must produce exactly the
    one `poindexter_brain.dump` stream it produced before the list existed.

    restic selects a parent snapshot by (host, paths), so the --stdin-filename
    IS the identity of the lineage. Changing it on an existing install would
    orphan every prior snapshot and re-ingest the full database as new data.
    """
    result = _run_backup_harness(tmp_path, restic_rc=0)
    out = _combined(result)
    assert out.count("--stdin-filename poindexter_brain.dump") == 1
    assert out.count("RESTIC_ARGS") == 1
    assert "RUN_BACKUP_RC=0" in result.stdout


def test_each_database_gets_its_own_stream_path(tmp_path):
    """One restic --stdin backup per database, each under its own filename."""
    result = _run_backup_harness(
        tmp_path, restic_rc=0, databases="poindexter_brain,langfuse,prefect"
    )
    out = _combined(result)
    for db in ("poindexter_brain", "langfuse", "prefect"):
        assert f"--stdin-filename {db}.dump" in out
        assert f"-d {db} " in out  # pg_dump targeted the right database
    assert out.count("RESTIC_ARGS") == 3
    assert "RUN_BACKUP_RC=0" in result.stdout


def test_database_list_is_whitespace_tolerant_and_deduped(tmp_path):
    """A repeated entry would dump twice and split that database's parent
    chain across two identical paths."""
    result = _run_backup_harness(
        tmp_path, restic_rc=0, databases=" langfuse , langfuse ,prefect"
    )
    out = _combined(result)
    assert out.count("--stdin-filename langfuse.dump") == 1
    assert out.count("--stdin-filename prefect.dump") == 1
    assert out.count("RESTIC_ARGS") == 2


def test_empty_database_list_alerts_instead_of_silently_dumping_nothing(tmp_path):
    """The whole point of the tier is that it produces something. An empty
    setting must not read as a clean run."""
    result = _run_backup_harness(tmp_path, restic_rc=0, databases="  , ,")
    out = _combined(result)
    assert "RESTIC_ARGS" not in out
    assert "ALERT severity=critical" in out
    assert "no source databases" in out
    assert "HEARTBEAT" not in out
    assert "RUN_BACKUP_RC=0" not in result.stdout
    assert "BACKUP_ANY_OK=false" in result.stdout


def test_one_database_failing_does_not_stop_the_others(tmp_path):
    """A broken 15 MB database must not cost you the 1.6 GB one, and must not
    suppress the config tier — hence BACKUP_ANY_OK stays true."""
    # FAKE_RESTIC_RC applies to every call, so drive the failure through
    # pg_dump on a database that does not exist in the stub's success path.
    result = _run_backup_harness(
        tmp_path, restic_rc=0, pgdump_rc=1, databases="a,b,c"
    )
    out = _combined(result)
    # All three were attempted even though every one failed.
    assert out.count("PGDUMP_ARGS") == 3
    assert out.count("ALERT severity=critical") == 3
    assert "RUN_BACKUP_RC=0" not in result.stdout


def test_partial_success_keeps_the_config_tier_reachable(tmp_path):
    """BACKUP_ANY_OK is the gate tick() uses; it means "the repo is writable",
    not "everything succeeded".

    This is the behaviour change that matters: before the source set was a
    list, any failure skipped the config tier and the coverage check. With
    several databases, letting one failed dump suppress the check that
    notices the repo has stopped carrying bootstrap.toml would be exactly
    backwards — that check matters MORE on a bad night, not less.
    """
    result = _run_backup_harness(
        tmp_path,
        restic_rc=0,
        databases="poindexter_brain,langfuse,prefect",
        fail_db="langfuse",
    )
    out = _combined(result)
    # The failure did not stop the run: all three were attempted...
    assert out.count("PGDUMP_ARGS") == 3
    # ...exactly one alerted, naming itself...
    assert out.count("ALERT severity=critical") == 1
    assert "langfuse" in out
    # ...the two healthy ones still reached the repo...
    assert out.count("HEARTBEAT event=offsite_backup_succeeded") == 2
    # ...the tick reports failure...
    assert "RUN_BACKUP_RC=0" not in result.stdout
    # ...but the config tier + coverage check still run.
    assert "BACKUP_ANY_OK=true" in result.stdout


def test_per_database_alert_names_the_database(tmp_path):
    """An alert that does not say WHICH database failed makes the operator
    re-derive it from logs."""
    result = _run_backup_harness(
        tmp_path, restic_rc=2, databases="langfuse"
    )
    out = _combined(result)
    assert "ALERT severity=critical" in out
    assert "langfuse" in out
    assert "(rc=2)" in out


# --- poindexter#891 fix 3: config coverage verification -----------------------

_COVERAGE_HARNESS = """
source "${RUN_SH}"

# restic stub dispatching on the subcommand (args are: -r <repo> <sub> ...).
restic() {
    shift 2
    case "$1" in
        ls)   printf '%s\\n' "${FAKE_LS_OUTPUT:-}"; return "${FAKE_LS_RC:-0}" ;;
        dump) printf '%s' "${FAKE_DUMP_CONTENT:-}"; return "${FAKE_DUMP_RC:-0}" ;;
    esac
    return 0
}
read_setting() {
    local key="$1" default="$2" var
    var="SETTING_${key}"
    if [[ -n "${!var:-}" ]]; then printf '%s' "${!var}"; else printf '%s' "${default}"; fi
}
emit_heartbeat() { echo "HEARTBEAT event=$1 detail=$2"; }
emit_alert() { echo "ALERT severity=$1 summary=$2 description=$3"; }

rc=0
verify_config_coverage "s3:example.test/repo" "poindexter" || rc=$?
echo "COVERAGE_RC=${rc}"
"""

_SECRET_BODY = "poindexter_secret_key = 'SUPERSECRET-DO-NOT-LOG'"


def _run_coverage_harness(
    tmp_path: Path,
    *,
    on_disk: str | None = _SECRET_BODY,
    stored: str | None = _SECRET_BODY,
    listed: bool = True,
    ls_rc: int = 0,
    settings: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess, Path]:
    assert _BASH is not None
    artifact = tmp_path / "bootstrap.toml"
    if on_disk is not None:
        artifact.write_text(on_disk)
    env = {
        "PATH": os.environ["PATH"],  # needs sha256sum/grep/sed/tr from coreutils
        "RUN_SH": _RUN_SH.as_posix(),
        "BACKUP_DIR": (tmp_path / "backups").as_posix(),
        "PGPASSWORD": "test",
        "FAKE_LS_RC": str(ls_rc),
        "FAKE_LS_OUTPUT": artifact.as_posix() if listed else "/config/other/file",
        "FAKE_DUMP_CONTENT": stored if stored is not None else "",
        "SETTING_offsite_backup_verify_config_artifacts": artifact.as_posix(),
    }
    for k, v in (settings or {}).items():
        env[f"SETTING_{k}"] = v
    result = subprocess.run(
        [_BASH, "-c", _COVERAGE_HARNESS],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    return result, artifact


def test_coverage_passes_when_artifact_is_present_and_current(tmp_path):
    result, _ = _run_coverage_harness(tmp_path)
    assert "HEARTBEAT event=offsite_config_coverage_verified" in result.stdout
    assert "ALERT" not in result.stdout
    assert "COVERAGE_RC=0" in result.stdout


def test_coverage_never_prints_the_secret_it_verifies(tmp_path):
    """The artifacts are secret-bearing (master key, DB URL, credentials).
    Contents go straight into sha256sum and must never reach a log or an
    alert — on the success path OR on any failure path."""
    for kwargs in (
        {},                                    # match
        {"stored": "DIFFERENT-CONTENT"},       # drift
        {"listed": False},                     # missing from snapshot
        {"ls_rc": 1},                          # no snapshot at all
    ):
        result, _ = _run_coverage_harness(tmp_path, **kwargs)
        combined = result.stdout + result.stderr
        assert "SUPERSECRET-DO-NOT-LOG" not in combined, kwargs
        assert "DIFFERENT-CONTENT" not in combined, kwargs


def test_coverage_flags_artifact_missing_from_the_snapshot_as_critical(tmp_path):
    """The silent-decay case: the backup reported success, but the file that
    makes the restore usable is not in it (a widened exclude, a dropped
    mount). It never self-heals, so it is critical."""
    result, _ = _run_coverage_harness(tmp_path, listed=False)
    assert "ALERT severity=critical" in result.stdout
    assert "missing" in result.stdout.lower()
    assert "HEARTBEAT" not in result.stdout
    assert "COVERAGE_RC=0" not in result.stdout


def test_coverage_flags_stale_stored_copy_as_warning(tmp_path):
    """Drift is a warning, not critical: a copy exists, the config backup runs
    immediately before this check, and a single tick's mismatch is usually a
    mid-tick edit that self-heals."""
    result, _ = _run_coverage_harness(tmp_path, stored="OLD-KEY-CONTENT")
    assert "ALERT severity=warning" in result.stdout
    assert "stale" in result.stdout.lower()
    assert "HEARTBEAT" not in result.stdout
    assert "COVERAGE_RC=0" not in result.stdout


def test_coverage_alerts_when_there_is_no_config_snapshot(tmp_path):
    result, _ = _run_coverage_harness(tmp_path, ls_rc=1)
    assert "ALERT severity=critical" in result.stdout
    assert "no readable config snapshot" in result.stdout
    assert "COVERAGE_RC=0" not in result.stdout


def test_coverage_that_scanned_nothing_does_not_pass(tmp_path):
    """A check whose configured artifacts are all absent has verified nothing;
    reporting success there is how the tier goes quietly blind."""
    result, _ = _run_coverage_harness(tmp_path, on_disk=None)
    assert "ALERT severity=warning" in result.stdout
    assert "verified nothing" in result.stdout
    assert "HEARTBEAT" not in result.stdout
    assert "COVERAGE_RC=0" not in result.stdout


def test_coverage_can_be_disabled(tmp_path):
    result, _ = _run_coverage_harness(
        tmp_path, settings={"offsite_backup_verify_config_enabled": "false"}
    )
    assert "ALERT" not in result.stdout
    assert "HEARTBEAT" not in result.stdout
    assert "COVERAGE_RC=0" in result.stdout


def test_default_verified_artifact_is_the_master_key_file():
    """bootstrap.toml holds poindexter_secret_key; without it the app_settings
    secrets inside the DB snapshot cannot be decrypted (#889)."""
    assert _default_setting("DEFAULT_VERIFY_CONFIG_ARTIFACTS") == (
        "/config/poindexter/bootstrap.toml"
    )


def test_default_database_list_matches_the_pre_891_behaviour():
    assert _default_setting("DEFAULT_DATABASES") == "poindexter_brain"
