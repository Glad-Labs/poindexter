"""Unit tests for the BACKUP_TIER=volumes mode of scripts/backup/run.sh
(Glad-Labs/poindexter#890).

Tiers 1/2 (hourly/daily pg_dump, offsite restic) never touched anything but
Postgres. Nothing backed up the Docker volumes behind Grafana, Loki, Tempo,
Pyroscope, Langfuse ClickHouse or pgAdmin, and stale one-off tarballs sitting
in the backup directory (all dated the same manual sweep, three of them
0 bytes even from that run) made the gap look like a working tier.

`run_volume_backup` tars whatever the compose service mounts read-only under
`${VOLUMES_MOUNT_ROOT}/<label>/` — which volumes are protected is entirely a
compose-file decision, this script just walks what it's handed. The load-
bearing behaviour is the file-count check: a size threshold cannot tell
"volume is genuinely empty" from "the mount failed and we archived nothing"
(both produce a valid, tiny gzip) — that ambiguity is the exact failure mode
that sat undetected for months.

Sources the real script under bash (functions are exposed before the service
loop — see the BASH_SOURCE guard at the bottom of run.sh) with `read_setting`
stubbed the same way test_backup_offsite_runner.py does it. `tar`/`find` are
left real for the happy-path tests; only the failure-injection tests stub
`tar` on PATH.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_RUN_SH = Path(__file__).resolve().parents[5] / "scripts" / "backup" / "run.sh"

_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    _BASH is None, reason="bash not available on this host"
)


def test_run_sh_exists():
    assert _RUN_SH.is_file()


_HARNESS = """
source "${RUN_SH}"
read_setting() {
    local key="$1" default="$2" var
    var="SETTING_${key}"
    if [[ -n "${!var:-}" ]]; then printf '%s' "${!var}"; else printf '%s' "${default}"; fi
}
emit_alert() { echo "ALERT severity=$1 summary=$2 description=$3"; }

rc=0
run_volume_backup || rc=$?
echo "RUN_VOLUME_BACKUP_RC=${rc}"
"""


def _make_volume(root: Path, label: str, files: dict[str, str]) -> None:
    vol = root / label
    vol.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = vol / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _run(tmp_path: Path, extra_path: Path | None = None, **settings: str):
    env = {
        "PATH": f"{extra_path}:{Path(_BASH).parent}" if extra_path else str(Path(_BASH).parent),
        "RUN_SH": _RUN_SH.as_posix(),
        "BACKUP_DIR": (tmp_path / "backups").as_posix(),
        "VOLUMES_MOUNT_ROOT": (tmp_path / "volumes").as_posix(),
        "PGPASSWORD": "test",
    }
    for k, v in settings.items():
        env[f"SETTING_{k}"] = v
    return subprocess.run(
        [_BASH, "-c", _HARNESS], capture_output=True, text=True, timeout=30, env=env,
    )


def test_a_populated_volume_is_archived_with_its_real_content(tmp_path):
    _make_volume(tmp_path / "volumes", "grafana", {"dashboards/a.json": "{}", "b.db": "x"})
    result = _run(tmp_path)
    assert "RUN_VOLUME_BACKUP_RC=0" in result.stdout, result.stdout
    archives = list((tmp_path / "backups" / "volumes").glob("grafana_*.tar.gz"))
    assert len(archives) == 1
    listing = subprocess.run(
        ["tar", "-tzf", str(archives[0])], capture_output=True, text=True
    ).stdout
    assert "dashboards/a.json" in listing
    assert "b.db" in listing


def test_a_genuinely_empty_volume_is_not_a_failure(tmp_path):
    """0 files at the source, 0 in the archive, is the CORRECT outcome — must
    not be confused with the failed-mount shape below."""
    (tmp_path / "volumes" / "empty-vol").mkdir(parents=True)
    result = _run(tmp_path)
    assert "RUN_VOLUME_BACKUP_RC=0" in result.stdout, result.stdout
    assert "FAIL" not in result.stdout
    archives = list((tmp_path / "backups" / "volumes").glob("empty-vol_*.tar.gz"))
    assert len(archives) == 1


def test_no_mounts_at_all_fails_loudly(tmp_path):
    """An empty VOLUMES_MOUNT_ROOT means the compose service isn't mounting
    anything — almost certainly a compose-file mistake, not a clean run."""
    (tmp_path / "volumes").mkdir(parents=True)
    result = _run(tmp_path)
    assert "RUN_VOLUME_BACKUP_RC=0" not in result.stdout
    assert "no mounts under" in result.stdout


def test_two_volumes_one_empty_one_populated_both_succeed(tmp_path):
    _make_volume(tmp_path / "volumes", "grafana", {"a": "x"})
    (tmp_path / "volumes" / "empty-vol").mkdir(parents=True)
    result = _run(tmp_path)
    assert "RUN_VOLUME_BACKUP_RC=0" in result.stdout, result.stdout
    vols = tmp_path / "backups" / "volumes"
    assert list(vols.glob("grafana_*.tar.gz"))
    assert list(vols.glob("empty-vol_*.tar.gz"))


# --- The #890 failure shape: a size threshold cannot see this ---------------

_FAKE_TAR_EMPTY_ARCHIVE = """#!/usr/bin/env bash
if [[ "$1" == "czf" ]]; then
    gzip -n < /dev/null > "$2"
    exit "${FAKE_TAR_RC:-0}"
fi
exec /usr/bin/tar "$@"
"""


def test_source_has_files_but_archive_captured_none_is_a_failure(tmp_path):
    """The exact bug this issue is about: tar exits 0, produces a valid small
    gzip, but the gzip has nothing in it. A size check would have passed this;
    the file-count check must not."""
    _make_volume(tmp_path / "volumes", "loki", {"chunk1": "real data"})
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    tar_stub = fakebin / "tar"
    tar_stub.write_text(_FAKE_TAR_EMPTY_ARCHIVE, encoding="utf-8")
    tar_stub.chmod(0o755)

    result = _run(tmp_path, extra_path=fakebin)
    assert "RUN_VOLUME_BACKUP_RC=0" not in result.stdout
    assert "source has 1 file(s) but the archive captured none" in result.stdout
    # No partial/misleading artifact left behind for this volume.
    assert not list((tmp_path / "backups" / "volumes").glob("loki_*"))


def test_tar_exit_1_from_a_file_changing_mid_read_is_not_a_failure(tmp_path):
    """GNU tar exits 1 for 'file changed as we read it', not just warnings —
    expected for live observability data that writes continuously. Only the
    file-count check (above) should be trusted to call this a real failure;
    exit 1 alone must not."""
    _make_volume(tmp_path / "volumes", "loki", {"chunk1": "real data"})
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    tar_stub = fakebin / "tar"
    # A stub, not real tar coerced into raising 1: exercises the documented
    # exit-status contract (0 success / 1 partial-but-readable / >=2 fatal)
    # directly, while still producing a real, correctly-populated archive.
    tar_stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "czf" ]]; then\n'
        '  /usr/bin/tar "$@" >/dev/null 2>&1\n'
        "  exit 1\n"
        "fi\n"
        'exec /usr/bin/tar "$@"\n',
        encoding="utf-8",
    )
    tar_stub.chmod(0o755)

    result = _run(tmp_path, extra_path=fakebin)
    assert "RUN_VOLUME_BACKUP_RC=0" in result.stdout, result.stdout
    assert "FAIL" not in result.stdout
    assert list((tmp_path / "backups" / "volumes").glob("loki_*.tar.gz"))


def test_fatal_tar_error_is_a_failure_and_leaves_no_partial_file(tmp_path):
    _make_volume(tmp_path / "volumes", "loki", {"chunk1": "real data"})
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    tar_stub = fakebin / "tar"
    tar_stub.write_text(
        '#!/usr/bin/env bash\nif [[ "$1" == "czf" ]]; then exit 2; fi\nexec /usr/bin/tar "$@"\n',
        encoding="utf-8",
    )
    tar_stub.chmod(0o755)

    result = _run(tmp_path, extra_path=fakebin)
    assert "RUN_VOLUME_BACKUP_RC=0" not in result.stdout
    assert "tar exited 2 (fatal)" in result.stdout
    assert not list((tmp_path / "backups" / "volumes").glob("loki_*"))


def test_one_volume_failing_does_not_stop_the_others(tmp_path):
    """A broken mount for one volume must not cost you the rest."""
    _make_volume(tmp_path / "volumes", "aaa-broken", {"f": "x"})
    _make_volume(tmp_path / "volumes", "zzz-fine", {"f": "x"})
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    tar_stub = fakebin / "tar"
    # Invocation under test: tar czf $tmp --warning=... -C $mount . — so the
    # mount path is $5, not $4.
    tar_stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "czf" ]]; then\n'
        '  if [[ "$5" == *"aaa-broken"* ]]; then gzip -n < /dev/null > "$2"; exit 0; fi\n'
        '  exec /usr/bin/tar "$@"\n'
        "fi\n"
        'exec /usr/bin/tar "$@"\n',
        encoding="utf-8",
    )
    tar_stub.chmod(0o755)

    result = _run(tmp_path, extra_path=fakebin)
    assert "RUN_VOLUME_BACKUP_RC=0" not in result.stdout
    vols = tmp_path / "backups" / "volumes"
    assert not list(vols.glob("aaa-broken_*"))
    assert list(vols.glob("zzz-fine_*.tar.gz"))


# --- prune_old_volumes: per-label retention ---------------------------------

_PRUNE_HARNESS = """
source "${RUN_SH}"
read_setting() {
    local key="$1" default="$2" var
    var="SETTING_${key}"
    if [[ -n "${!var:-}" ]]; then printf '%s' "${!var}"; else printf '%s' "${default}"; fi
}
prune_old_volumes
"""


def _run_prune(tmp_path: Path, retention: str = "2"):
    env = {
        "PATH": str(Path(_BASH).parent),
        "RUN_SH": _RUN_SH.as_posix(),
        "BACKUP_DIR": (tmp_path / "backups").as_posix(),
        "SETTING_backup_volumes_retention": retention,
    }
    return subprocess.run(
        [_BASH, "-c", _PRUNE_HARNESS], capture_output=True, text=True, timeout=30, env=env,
    )


def test_prune_keeps_the_configured_count_per_label_independently(tmp_path):
    vol_dir = tmp_path / "backups" / "volumes"
    vol_dir.mkdir(parents=True)
    for i in range(1, 6):
        (vol_dir / f"grafana_2026010{i}T000000Z.tar.gz").touch()
        (vol_dir / f"loki_2026010{i}T000000Z.tar.gz").touch()

    _run_prune(tmp_path, retention="2")

    remaining = sorted(p.name for p in vol_dir.glob("*.tar.gz"))
    assert remaining == [
        "grafana_20260104T000000Z.tar.gz",
        "grafana_20260105T000000Z.tar.gz",
        "loki_20260104T000000Z.tar.gz",
        "loki_20260105T000000Z.tar.gz",
    ]


def test_prune_with_no_archives_yet_does_not_error(tmp_path):
    (tmp_path / "backups" / "volumes").mkdir(parents=True)
    result = _run_prune(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


# --- tick()-level integration: the alert path is the generic one, unchanged --


_TICK_HARNESS = """
source "${RUN_SH}"
read_setting() {
    local key="$1" default="$2" var
    var="SETTING_${key}"
    if [[ -n "${!var:-}" ]]; then printf '%s' "${!var}"; else printf '%s' "${default}"; fi
}
emit_alert() { echo "ALERT severity=$1 summary=$2 description=$3"; }

rc=0
tick || rc=$?
echo "TICK_RC=${rc}"
"""


def _run_tick(tmp_path: Path, extra_path: Path | None = None):
    env = {
        "PATH": f"{extra_path}:{Path(_BASH).parent}" if extra_path else str(Path(_BASH).parent),
        "RUN_SH": _RUN_SH.as_posix(),
        "BACKUP_DIR": (tmp_path / "backups").as_posix(),
        "VOLUMES_MOUNT_ROOT": (tmp_path / "volumes").as_posix(),
        "BACKUP_TIER": "volumes",
        "PGPASSWORD": "test",
    }
    return subprocess.run(
        [_BASH, "-c", _TICK_HARNESS], capture_output=True, text=True, timeout=30, env=env,
    )


def test_tick_on_success_prunes_and_does_not_alert(tmp_path):
    _make_volume(tmp_path / "volumes", "grafana", {"a": "x"})
    result = _run_tick(tmp_path)
    assert "TICK_RC=0" in result.stdout, result.stdout
    assert "ALERT" not in result.stdout


def test_tick_on_failure_fires_the_generic_backup_alert(tmp_path):
    """No new alert wiring needed: run_dump's tier dispatch feeds the SAME
    tick()/emit_alert() path hourly/daily already use, keyed off
    BACKUP_TIER — a single alertname family (backup_<tier>_failed) covers
    all four tiers with zero changes to tick() or emit_alert()."""
    (tmp_path / "volumes").mkdir(parents=True)  # no mounts -> hard failure
    result = _run_tick(tmp_path)
    assert "TICK_RC=0" not in result.stdout
    assert "ALERT severity=critical" in result.stdout
    assert "tier=volumes" in result.stdout
