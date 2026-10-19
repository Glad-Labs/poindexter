"""Contract tests for ``scripts/linux/cli-venv-sync.sh`` (Glad-Labs/poindexter#4156).

The host ``poindexter`` CLI used to run out of a poetry venv editable-installed
against the operator's working checkout, which sat 148 commits behind main with
nothing reporting it — so in-process commands (``media approve/reject``, …) ran
week-old service code. It now runs from ``~/.poindexter/cli-venv``, editable-
installed from the DEPLOY CLONE. This script keeps that venv's dependency set on
the clone's lockfile, keyed by a fingerprint of (recipe, project dir, extras,
pyproject.toml, poetry.lock).

Driven against the real script with recorder fakes on PATH: ``python3.13``
(builds a fake venv on ``-m venv``) and ``poetry`` (records its argv + the
``VIRTUAL_ENV`` it was aimed at, then writes the venv's ``bin/poindexter`` and a
marker naming where ``import poindexter`` would resolve — the editable
install). All fakes append to one events file so call order is assertable.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("flock") is None,
    reason="needs bash + flock (util-linux)",
)


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "cli-venv-sync.sh").exists()
    )


SCRIPT = _repo_root() / "scripts" / "linux" / "cli-venv-sync.sh"

# The venv's interpreter: `-c <probe>` prints wherever the fake editable
# install says `poindexter` resolves (or fails when nothing is installed yet).
_FAKE_VENV_PYTHON = """#!/usr/bin/env bash
venv="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "${1:-}" == -c ]]; then
  [ -f "$venv/.fake-import-root" ] || { echo "ModuleNotFoundError: poindexter" >&2; exit 1; }
  cat "$venv/.fake-import-root"; exit 0
fi
exit 0
"""

_FAKE_PYTHON313 = """#!/usr/bin/env bash
echo "python3.13 $*" >> "$EVENTS_FILE"
if [[ "${1:-} ${2:-}" == "-m venv" ]]; then
  mkdir -p "$3/bin"
  echo "home = /usr/bin" > "$3/pyvenv.cfg"
  cp "$FAKE_VENV_PYTHON_SRC" "$3/bin/python"
  chmod +x "$3/bin/python"
  exit 0
fi
exit 1
"""

_FAKE_POETRY = """#!/usr/bin/env bash
echo "poetry $* | VIRTUAL_ENV=${VIRTUAL_ENV:-}" >> "$EVENTS_FILE"
sleep "${FAKE_POETRY_SLEEP:-0}"
proj=""
while [ $# -gt 0 ]; do
  case "$1" in -C) proj="$2"; shift 2 ;; *) shift ;; esac
done
if [ "${FAKE_POETRY_EXIT:-0}" != 0 ]; then
  echo "Extra [bogus] is not specified."
  exit "$FAKE_POETRY_EXIT"
fi
cat > "$VIRTUAL_ENV/bin/poindexter" <<'EOF'
#!/usr/bin/env bash
echo "poindexter $*" >> "$EVENTS_FILE"
exit 0
EOF
chmod +x "$VIRTUAL_ENV/bin/poindexter"
if [ -n "${FAKE_IMPORT_ROOT:-}" ]; then
  echo "$FAKE_IMPORT_ROOT" > "$VIRTUAL_ENV/.fake-import-root"
else
  (cd "$proj/poindexter" && pwd -P) > "$VIRTUAL_ENV/.fake-import-root"
fi
echo "Installing the current project: poindexter (0.0.1)"
exit 0
"""


def _write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _make_deploy_root(root: Path) -> Path:
    proj = root / "src" / "cofounder_agent"
    (proj / "poindexter").mkdir(parents=True)
    (proj / "poindexter" / "__init__.py").write_text("", encoding="utf-8")
    (proj / "pyproject.toml").write_text('[project]\nname = "poindexter"\n', encoding="utf-8")
    (proj / "poetry.lock").write_text("lock-v1\n", encoding="utf-8")
    return root


@pytest.fixture
def rig(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    (home / ".poindexter").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    venv_python_src = tmp_path / "fake-venv-python"
    _write_exe(venv_python_src, _FAKE_VENV_PYTHON)
    _write_exe(bin_dir / "python3.13", _FAKE_PYTHON313)
    _write_exe(bin_dir / "poetry", _FAKE_POETRY)
    deploy = _make_deploy_root(tmp_path / "deploy")
    return {
        "tmp": tmp_path,
        "home": home,
        "bin": bin_dir,
        "deploy": deploy,
        "project": deploy / "src" / "cofounder_agent",
        "venv": home / ".poindexter" / "cli-venv",
        "events": tmp_path / "events",
        "env": {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(home),
            "POINDEXTER_DEPLOY_ROOT": str(deploy),
            "EVENTS_FILE": str(tmp_path / "events"),
            "FAKE_VENV_PYTHON_SRC": str(venv_python_src),
        },
    }


def _run(rig: dict, *args: str, **env_extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={**rig["env"], **env_extra},
        capture_output=True, text=True, timeout=60,
    )


def _events(rig: dict) -> list[str]:
    f = rig["events"]
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def _poetry_calls(rig: dict) -> list[str]:
    return [e for e in _events(rig) if e.startswith("poetry ")]


def _stamp(rig: dict) -> str | None:
    p = rig["venv"] / ".poindexter-cli-fingerprint"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def _build(rig: dict) -> None:
    proc = _run(rig, "--ensure")
    assert proc.returncode == 0, proc.stderr
    rig["events"].unlink()


# ---------------------------------------------------------------------------
# Not installed: deploy-checkout-sync runs the default mode on EVERY host.
# ---------------------------------------------------------------------------


def test_check_reports_not_installed_and_touches_nothing(rig):
    proc = _run(rig, "--check")
    assert proc.returncode == 3
    assert not rig["venv"].exists()
    assert _events(rig) == []


def test_default_mode_is_silent_on_a_host_without_the_host_cli(rig):
    """deploy-checkout-sync calls this every 10 minutes whether or not the
    operator installed the host CLI; a consumer box must see no venv built,
    no poetry run and no log noise."""
    proc = _run(rig)
    assert proc.returncode == 3
    assert proc.stderr == "" and proc.stdout == ""
    assert not rig["venv"].exists()
    assert _poetry_calls(rig) == []


# ---------------------------------------------------------------------------
# Building and keeping current
# ---------------------------------------------------------------------------


def test_ensure_builds_the_venv_from_the_deploy_clone(rig):
    proc = _run(rig, "--ensure")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", "stdout belongs to the CLI the launcher runs next"
    ev = _events(rig)
    assert f"python3.13 -m venv {rig['venv']}" in ev
    (call,) = _poetry_calls(rig)
    assert f"-C {rig['project']} sync --only main" in call, call
    for extra in ("pipeline", "qa", "rag", "youtube"):
        assert f"--extras {extra}" in call, call
    assert "rerank" not in call and "profiling" not in call
    assert call.endswith(f"VIRTUAL_ENV={rig['venv']}"), (
        "poetry must install into THIS venv, not mint one keyed to the project path"
    )
    assert ev.index(f"python3.13 -m venv {rig['venv']}") < ev.index(call)
    assert "poindexter --help" in ev, "the synced env is smoke-tested before it is stamped"
    assert _stamp(rig)
    assert (rig["venv"] / "bin" / "poindexter").exists()


def test_current_env_is_a_silent_noop(rig):
    _build(rig)
    for mode in ("--ensure", ""):
        proc = _run(rig, *([mode] if mode else []))
        assert proc.returncode == 0
        assert proc.stderr == "", "the launcher runs this before EVERY command"
    assert _events(rig) == []
    assert _run(rig, "--check").returncode == 0


def test_lockfile_change_resyncs_in_place(rig):
    _build(rig)
    before = _stamp(rig)
    (rig["project"] / "poetry.lock").write_text("lock-v2\n", encoding="utf-8")
    assert _run(rig, "--check").returncode == 1
    proc = _run(rig)
    assert proc.returncode == 0, proc.stderr
    assert len(_poetry_calls(rig)) == 1
    assert not [e for e in _events(rig) if "-m venv" in e], "an intact venv is synced, not rebuilt"
    assert _stamp(rig) not in (None, before)
    assert _run(rig, "--check").returncode == 0


def test_pyproject_change_is_stale_too(rig):
    _build(rig)
    (rig["project"] / "pyproject.toml").write_text('[project]\nname = "poindexter"\n# x\n', encoding="utf-8")
    assert _run(rig, "--check").returncode == 1


def test_extras_set_is_part_of_the_fingerprint_but_order_is_not(rig):
    _build(rig)
    same = _run(rig, "--check", POINDEXTER_CLI_EXTRAS="youtube rag  qa pipeline")
    assert same.returncode == 0, "reordering the same extras must not force a sync"
    wider = _run(rig, "--check", POINDEXTER_CLI_EXTRAS="pipeline qa rag youtube rerank")
    assert wider.returncode == 1


def test_a_different_deploy_root_repoints_the_editable_install(rig):
    _build(rig)
    other = _make_deploy_root(rig["tmp"] / "other-deploy")
    proc = _run(rig, POINDEXTER_DEPLOY_ROOT=str(other))
    assert proc.returncode == 0, proc.stderr
    (call,) = _poetry_calls(rig)
    assert f"-C {other / 'src' / 'cofounder_agent'} sync" in call


# ---------------------------------------------------------------------------
# Verification: a stamp means "imports poindexter from the deploy clone"
# ---------------------------------------------------------------------------


def test_env_importing_another_tree_is_never_stamped(rig):
    elsewhere = rig["tmp"] / "operator-checkout" / "poindexter"
    elsewhere.mkdir(parents=True)
    proc = _run(rig, "--ensure", FAKE_IMPORT_ROOT=str(elsewhere))
    assert proc.returncode == 1
    assert "not the deploy clone" in proc.stderr
    assert _stamp(rig) is None
    assert _run(rig, "--check").returncode == 1


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_failed_sync_keeps_the_old_stamp_and_says_the_cli_still_runs(rig):
    _build(rig)
    before = _stamp(rig)
    (rig["project"] / "poetry.lock").write_text("lock-v2\n", encoding="utf-8")
    proc = _run(rig, "--ensure", FAKE_POETRY_EXIT="1")
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "Extra [bogus] is not specified." in proc.stderr, "poetry's own reason is surfaced"
    assert "previous dependency set" in proc.stderr
    assert _stamp(rig) == before, "a failed sync must never stamp"
    assert (rig["venv"].parent / "cli-venv.sync-failed").exists()


def test_launcher_backs_off_after_a_failure_but_deploy_sync_and_force_retry(rig):
    _build(rig)
    (rig["project"] / "poetry.lock").write_text("lock-v2\n", encoding="utf-8")
    assert _run(rig, "--ensure", FAKE_POETRY_EXIT="1").returncode == 1
    assert len(_poetry_calls(rig)) == 1

    again = _run(rig, "--ensure", FAKE_POETRY_EXIT="1")
    assert again.returncode == 1
    assert "failed less than" in again.stderr
    assert len(_poetry_calls(rig)) == 1, "the launcher must not pay a failing sync on every command"

    assert _run(rig, FAKE_POETRY_EXIT="1").returncode == 1
    assert len(_poetry_calls(rig)) == 2, "the background (deploy-sync) mode retries regardless"

    assert _run(rig, "--force").returncode == 0
    assert len(_poetry_calls(rig)) == 3
    assert not (rig["venv"].parent / "cli-venv.sync-failed").exists()
    assert _run(rig, "--check").returncode == 0


def test_backoff_expires(rig):
    _build(rig)
    (rig["project"] / "poetry.lock").write_text("lock-v2\n", encoding="utf-8")
    assert _run(rig, "--ensure", FAKE_POETRY_EXIT="1").returncode == 1
    fail = rig["venv"].parent / "cli-venv.sync-failed"
    fp, _ts = fail.read_text(encoding="utf-8").split()
    fail.write_text(f"{fp} {int(time.time()) - 16 * 60}\n", encoding="utf-8")
    assert _run(rig, "--ensure").returncode == 0
    assert len(_poetry_calls(rig)) == 2


def test_backoff_is_per_fingerprint(rig):
    """A failure recorded for an older lockfile must not block syncing a newer one."""
    _build(rig)
    (rig["project"] / "poetry.lock").write_text("lock-v2\n", encoding="utf-8")
    assert _run(rig, "--ensure", FAKE_POETRY_EXIT="1").returncode == 1
    (rig["project"] / "poetry.lock").write_text("lock-v3\n", encoding="utf-8")
    assert _run(rig, "--ensure").returncode == 0
    assert len(_poetry_calls(rig)) == 2


# ---------------------------------------------------------------------------
# Broken / unsafe venv paths
# ---------------------------------------------------------------------------


def test_venv_with_an_unusable_interpreter_is_rebuilt(rig):
    _build(rig)
    (rig["venv"] / "bin" / "python").unlink()
    assert _run(rig, "--check").returncode == 1
    proc = _run(rig)
    assert proc.returncode == 0, proc.stderr
    assert f"python3.13 -m venv {rig['venv']}" in _events(rig)
    assert _run(rig, "--check").returncode == 0


def test_refuses_to_delete_a_directory_that_is_not_a_venv(rig):
    precious = rig["tmp"] / "not-a-venv"
    precious.mkdir()
    (precious / "notes.txt").write_text("keep me\n", encoding="utf-8")
    proc = _run(rig, "--ensure", POINDEXTER_CLI_VENV=str(precious))
    assert proc.returncode == 2
    assert "not a venv" in proc.stderr
    assert (precious / "notes.txt").read_text(encoding="utf-8") == "keep me\n"


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------


def test_missing_deploy_clone_is_a_configuration_error(rig):
    proc = _run(rig, "--ensure", POINDEXTER_DEPLOY_ROOT=str(rig["tmp"] / "nope"))
    assert proc.returncode == 2
    assert "setup-deploy-checkout.sh" in proc.stderr
    assert not rig["venv"].exists()


def test_poetry_is_found_in_local_bin_when_not_on_path(rig):
    """systemd's PATH has no ~/.local/bin — where pipx installs poetry."""
    if any(Path(d, "poetry").exists() for d in ("/usr/bin", "/bin")):
        pytest.skip("a system-wide poetry would be found on PATH first")
    local_bin = rig["home"] / ".local" / "bin"
    local_bin.mkdir(parents=True)
    shutil.move(str(rig["bin"] / "poetry"), str(local_bin / "poetry"))
    proc = _run(rig, "--ensure")
    assert proc.returncode == 0, proc.stderr
    assert len(_poetry_calls(rig)) == 1


def test_unusable_poetry_override_is_a_configuration_error(rig):
    proc = _run(rig, "--ensure", POINDEXTER_POETRY_BIN=str(rig["tmp"] / "no-poetry"))
    assert proc.returncode == 2
    assert "POINDEXTER_POETRY_BIN" in proc.stderr


# ---------------------------------------------------------------------------
# Concurrency: the launcher and deploy-checkout-sync can both get here at once
# ---------------------------------------------------------------------------


def test_concurrent_callers_sync_exactly_once(rig):
    env = {**rig["env"], "FAKE_POETRY_SLEEP": "1"}
    procs = [
        subprocess.Popen(
            ["bash", str(SCRIPT), "--ensure"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(2)
    ]
    for p in procs:
        _out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
    assert len(_poetry_calls(rig)) == 1, _events(rig)
    assert len([e for e in _events(rig) if "-m venv" in e]) == 1


# ---------------------------------------------------------------------------
# --status
# ---------------------------------------------------------------------------


def test_status_names_the_venv_and_where_it_imports_from(rig):
    _build(rig)
    proc = _run(rig, "--status")
    assert proc.returncode == 0
    assert "host CLI environment: current" in proc.stdout
    assert str(rig["venv"]) in proc.stdout
    assert f"imports from: {(rig['project'] / 'poindexter').resolve()}" in proc.stdout
    assert _stamp(rig) in proc.stdout


def test_status_on_a_host_without_the_host_cli(rig):
    proc = _run(rig, "--status")
    assert proc.returncode == 3
    assert "NOT INSTALLED" in proc.stdout
    assert "install-host-cli.sh" in proc.stdout


@pytest.mark.parametrize(
    "name", ["cli-venv-sync.sh", "poindexter-cli.sh", "install-host-cli.sh"]
)
def test_scripts_are_committed_executable(name):
    """The launcher is exec'd through a symlink; a 100644 blob would make every
    `poindexter` call fail with 'Permission denied' after the next deploy."""
    if shutil.which("git") is None:
        pytest.skip("needs git")
    rel = f"scripts/linux/{name}"
    out = subprocess.run(
        ["git", "ls-files", "-s", rel], cwd=_repo_root(),
        capture_output=True, text=True, timeout=30,
    ).stdout
    if not out.strip():
        pytest.skip(f"{rel} not tracked in this checkout")
    assert out.split()[0] == "100755", out
    assert os.access(_repo_root() / rel, os.X_OK)
