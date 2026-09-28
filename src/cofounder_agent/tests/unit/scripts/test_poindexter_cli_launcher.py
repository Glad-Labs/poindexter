"""Contract tests for the host CLI launcher and its installer (Glad-Labs/glad-labs-stack#4156).

``scripts/linux/poindexter-cli.sh`` is what ``~/.local/bin/poindexter`` links
to. Its predecessor exec'd the newest poetry venv under
``~/.cache/pypoetry/virtualenvs``, which was editable-installed against the
operator's working checkout, 148 commits behind main. The contract pinned here:

- it runs the CLI venv built from the deploy clone, passing argv and the exit
  code through untouched;
- it brings the venv's dependencies current first (``cli-venv-sync.sh
  --ensure``), and a failing sync never blocks the command;
- nothing but the CLI writes to stdout (``--json`` stays parseable);
- it never quietly runs another tree: a missing clone or venv is a loud 127,
  and a PYTHONPATH entry that shadows the deployed package is announced.

``install-host-cli.sh`` is tested for the symlink it leaves behind, for keeping
the launcher it replaces, and for refusing a clone that predates the launcher.
The sync script's own contract lives in ``test_cli_venv_sync.py``; here it is a
recorder fake.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "poindexter-cli.sh").exists()
    )


LAUNCHER = _repo_root() / "scripts" / "linux" / "poindexter-cli.sh"
INSTALLER = _repo_root() / "scripts" / "linux" / "install-host-cli.sh"

# Stands in for cli-venv-sync.sh: records the call, writes to STDOUT on purpose
# (the launcher must keep that off the CLI's stdout), exits as told.
_FAKE_SYNC = """#!/usr/bin/env bash
echo "sync $*" >> "$EVENTS_FILE"
echo "SYNC-STDOUT-NOISE"
echo "sync progress on stderr" >&2
exit "${FAKE_SYNC_EXIT:-0}"
"""

# The venv's CLI: echoes argv one per line plus the API URL it was given.
_FAKE_CLI = """#!/usr/bin/env bash
echo "cli $*" >> "$EVENTS_FILE"
for a in "$@"; do echo "arg:$a"; done
echo "api:${POINDEXTER_API_URL:-unset}"
exit "${FAKE_CLI_EXIT:-0}"
"""


def _write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def rig(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    deploy = tmp_path / "deploy"
    (deploy / ".git").mkdir(parents=True)
    _write_exe(deploy / "scripts" / "linux" / "cli-venv-sync.sh", _FAKE_SYNC)
    venv = home / ".poindexter" / "cli-venv"
    _write_exe(venv / "bin" / "poindexter", _FAKE_CLI)
    # The installed shape: a symlink on PATH pointing at the launcher.
    bin_dir = tmp_path / "localbin"
    bin_dir.mkdir()
    (bin_dir / "poindexter").symlink_to(LAUNCHER)
    return {
        "tmp": tmp_path, "home": home, "deploy": deploy, "venv": venv,
        "link": bin_dir / "poindexter", "events": tmp_path / "events",
        "env": {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(home),
            "POINDEXTER_DEPLOY_ROOT": str(deploy),
            "EVENTS_FILE": str(tmp_path / "events"),
        },
    }


def _run(rig: dict, *args: str, **env_extra: str) -> subprocess.CompletedProcess:
    env = {**rig["env"], **env_extra}
    return subprocess.run(
        [str(rig["link"]), *args], env=env, capture_output=True, text=True, timeout=30,
    )


def _events(rig: dict) -> list[str]:
    f = rig["events"]
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def test_runs_the_venv_cli_with_argv_and_exit_code_untouched(rig):
    proc = _run(rig, "tasks", "list", "--json", "two words", FAKE_CLI_EXIT="3")
    assert proc.returncode == 3
    assert proc.stdout.splitlines()[:4] == [
        "arg:tasks", "arg:list", "arg:--json", "arg:two words",
    ]


def test_syncs_before_running_and_keeps_stdout_for_the_cli(rig):
    proc = _run(rig, "settings", "list")
    assert proc.returncode == 0
    ev = _events(rig)
    assert ev[0] == "sync --ensure"
    assert ev[1] == "cli settings list"
    assert "SYNC-STDOUT-NOISE" not in proc.stdout, "stdout belongs to the CLI (--json)"
    assert "SYNC-STDOUT-NOISE" in proc.stderr


def test_a_failing_sync_never_blocks_the_command(rig):
    proc = _run(rig, "media", "approve", "abc", FAKE_SYNC_EXIT="1")
    assert proc.returncode == 0
    assert "cli media approve abc" in _events(rig)


def test_no_sync_escape_hatch(rig):
    proc = _run(rig, "--help", POINDEXTER_CLI_NO_SYNC="1")
    assert proc.returncode == 0
    assert _events(rig) == ["cli --help"]


def test_api_url_defaults_to_the_local_worker_and_honours_an_override(rig):
    assert "api:http://localhost:8002" in _run(rig).stdout
    assert "api:http://box:8002" in _run(rig, POINDEXTER_API_URL="http://box:8002").stdout


def test_missing_deploy_clone_is_a_loud_127(rig):
    proc = _run(rig, "tasks", "list", POINDEXTER_DEPLOY_ROOT=str(rig["tmp"] / "nope"))
    assert proc.returncode == 127
    assert "no deploy clone" in proc.stderr
    assert _events(rig) == [], "must not fall back to any other tree"


def test_clone_that_predates_the_launcher_says_so(rig):
    (rig["deploy"] / "scripts" / "linux" / "cli-venv-sync.sh").unlink()
    proc = _run(rig, "tasks", "list")
    assert proc.returncode == 127
    assert "predates the host-CLI launcher" in proc.stderr
    assert "poindexter-deploy-sync" in proc.stderr


def test_missing_venv_is_a_loud_127(rig):
    shutil.rmtree(rig["venv"])
    proc = _run(rig, "tasks", "list")
    assert proc.returncode == 127
    assert "no CLI environment" in proc.stderr
    assert "--ensure" in proc.stderr


def test_pythonpath_shadowing_the_deployed_package_is_announced(rig):
    worktree = rig["tmp"] / "worktree" / "src" / "cofounder_agent"
    (worktree / "poindexter").mkdir(parents=True)
    (worktree / "poindexter" / "__init__.py").write_text("", encoding="utf-8")
    proc = _run(rig, "tasks", "list", PYTHONPATH=f"/unrelated:{worktree}")
    assert proc.returncode == 0
    assert f"from PYTHONPATH ({worktree})" in proc.stderr

    quiet = _run(rig, "tasks", "list", PYTHONPATH=str(rig["tmp"] / "unrelated"))
    assert "PYTHONPATH" not in quiet.stderr


# ---------------------------------------------------------------------------
# install-host-cli.sh
# ---------------------------------------------------------------------------

# Installer-side sync fake: `--force` builds a venv whose python "imports"
# poindexter from the deploy clone (or from FAKE_IMPORT_ROOT), and a CLI.
_FAKE_SYNC_BUILDING = """#!/usr/bin/env bash
echo "sync $*" >> "$EVENTS_FILE"
venv="${POINDEXTER_CLI_VENV:-$HOME/.poindexter/cli-venv}"
if [[ "${1:-}" == --force ]]; then
  mkdir -p "$venv/bin"
  root="${FAKE_IMPORT_ROOT:-$(cd "$POINDEXTER_DEPLOY_ROOT/src/cofounder_agent/poindexter" && pwd -P)}"
  printf '#!/usr/bin/env bash\\necho %s\\n' "$root" > "$venv/bin/python"
  printf '#!/usr/bin/env bash\\necho "cli $*" >> "$EVENTS_FILE"\\n' > "$venv/bin/poindexter"
  chmod +x "$venv/bin/python" "$venv/bin/poindexter"
  exit "${FAKE_SYNC_EXIT:-0}"
fi
exit 0
"""


@pytest.fixture
def install_rig(tmp_path: Path) -> dict:
    if shutil.which("git") is None:
        pytest.skip("needs git")
    home = tmp_path / "home"
    home.mkdir()
    deploy = tmp_path / "deploy"
    (deploy / "src" / "cofounder_agent" / "poindexter").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(deploy)], check=True, timeout=30)
    linux = deploy / "scripts" / "linux"
    linux.mkdir(parents=True)
    shutil.copy2(LAUNCHER, linux / "poindexter-cli.sh")
    _write_exe(linux / "cli-venv-sync.sh", _FAKE_SYNC_BUILDING)
    bin_dir = home / ".local" / "bin"
    return {
        "tmp": tmp_path, "home": home, "deploy": deploy, "bin": bin_dir,
        "target": bin_dir / "poindexter", "launcher": linux / "poindexter-cli.sh",
        "events": tmp_path / "events",
        "env": {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(home),
            "POINDEXTER_DEPLOY_ROOT": str(deploy),
            "EVENTS_FILE": str(tmp_path / "events"),
        },
    }


def _install(rig: dict, **env_extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALLER)], env={**rig["env"], **env_extra},
        capture_output=True, text=True, timeout=60,
    )


def test_install_links_the_deploy_clone_launcher(install_rig):
    proc = _install(install_rig)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert install_rig["target"].is_symlink()
    assert os.readlink(install_rig["target"]) == str(install_rig["launcher"])
    assert "sync --force" in _events(install_rig), "install always builds and verifies fresh"
    assert "cli --help" in _events(install_rig), "the installed command is smoke-tested"
    assert "OK" in proc.stdout


def test_install_keeps_the_launcher_it_replaces_as_an_inert_backup(install_rig):
    install_rig["bin"].mkdir(parents=True)
    old = install_rig["target"]
    old.write_text("#!/usr/bin/env bash\n# newest-venv shim\n", encoding="utf-8")
    old.chmod(0o755)
    proc = _install(install_rig)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    (backup,) = install_rig["bin"].glob("poindexter.pre-host-cli-*")
    assert "newest-venv shim" in backup.read_text(encoding="utf-8")
    assert not os.access(backup, os.X_OK)
    assert install_rig["target"].is_symlink()


def test_install_is_idempotent(install_rig):
    assert _install(install_rig).returncode == 0
    proc = _install(install_rig)
    assert proc.returncode == 0
    assert "already links to the launcher" in proc.stdout
    assert not list(install_rig["bin"].glob("poindexter.pre-host-cli-*"))


def test_install_repoints_a_symlink_to_somewhere_else(install_rig):
    install_rig["bin"].mkdir(parents=True)
    install_rig["target"].symlink_to(install_rig["tmp"] / "old-venv" / "bin" / "poindexter")
    proc = _install(install_rig)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert os.readlink(install_rig["target"]) == str(install_rig["launcher"])


def test_install_refuses_a_clone_that_predates_the_launcher(install_rig):
    install_rig["launcher"].unlink()
    proc = _install(install_rig)
    assert proc.returncode == 1
    assert "predates the host-CLI launcher" in proc.stderr
    assert not install_rig["target"].exists()


def test_install_fails_when_the_cli_imports_another_tree(install_rig):
    elsewhere = install_rig["tmp"] / "operator-checkout" / "poindexter"
    elsewhere.mkdir(parents=True)
    proc = _install(install_rig, FAKE_IMPORT_ROOT=str(elsewhere))
    assert proc.returncode == 1
    assert "not the deploy clone" in proc.stderr


def test_install_warns_about_a_shell_function_that_would_shadow_it(install_rig):
    (install_rig["home"] / ".bashrc").write_text(
        'poindexter() {\n  python -m poindexter "$@"\n}\n', encoding="utf-8"
    )
    proc = _install(install_rig)
    assert proc.returncode == 0
    assert ".bashrc defines a 'poindexter' alias or function" in proc.stdout
