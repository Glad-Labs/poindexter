"""``install-deploy-sync.sh`` puts the deploy driver behind the launcher.

Glad-Labs/glad-labs-stack#4172. The installer is the one idempotent way the
host gets (1) an installed COPY of the launcher, outside every git tree, (2) a
last-known-good driver that was proven on this host, and (3) the unit renders:
deploy-sync execs the installed launcher, and the docker watchdog and (since
#4188) the GPU scraper run the deploy clone's copies.

Driven with a fake ``sudo`` (exec-through) and a recording ``systemctl`` on
PATH, ``POINDEXTER_UNIT_DIR`` at a tmp dir and a throwaway deploy clone holding
the real launcher, scripts and unit templates (the seam
``test_install_session_timers`` uses).
"""

from __future__ import annotations

import getpass
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash + git",
)

_GIT_ID = ("-c", "user.name=t", "-c", "user.email=t@example.com")
_FROM_REPO = (
    "scripts/linux/deploy-sync-launcher.sh",
    "scripts/linux/deploy-checkout-sync.sh",
    "scripts/linux/docker-watchdog.sh",
    "infrastructure/systemd/poindexter-deploy-sync.service",
    "infrastructure/systemd/poindexter-deploy-sync.timer",
    "infrastructure/systemd/poindexter-docker-watchdog.service",
    "infrastructure/systemd/poindexter-docker-watchdog.timer",
    "scripts/gpu-scraper.py",
    "infrastructure/systemd/poindexter-gpu-scraper.service",
)
_SCRAPER = "poindexter-gpu-scraper.service"
# The generic deploy-clone path the unit templates ship with.
_TEMPLATE_CLONE = "/home/poindexter/.poindexter/deploy/glad-labs-stack"


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "install-deploy-sync.sh").exists()
    )


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *_GIT_ID, *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


def _repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    for rel, text in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        f.chmod(0o755)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return _git(path, "rev-parse", "HEAD")


def _rig(tmp_path: Path, *, previous_exec: str | None = "operator") -> dict:
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "systemctl-calls"
    for name, body in (
        ("sudo", '#!/usr/bin/env bash\nexec "$@"\n'),
        ("systemctl", f'#!/usr/bin/env bash\necho "$@" >> "{calls}"\nexit 0\n'),
    ):
        f = bin_dir / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)
    root = _repo_root()
    clone = tmp_path / "deploy-clone"
    _repo(clone, {rel: (root / rel).read_text(encoding="utf-8") for rel in _FROM_REPO})

    # The unit this host runs today, pointing at a driver in an operator checkout.
    operator = tmp_path / "operator-checkout"
    operator_driver = operator / "scripts/linux/deploy-checkout-sync.sh"
    operator_text = (root / "scripts/linux/deploy-checkout-sync.sh").read_text(encoding="utf-8") + "\n# the copy this host ran\n"
    operator_sha = _repo(operator, {"scripts/linux/deploy-checkout-sync.sh": operator_text})
    units = tmp_path / "units"
    units.mkdir()
    if previous_exec == "operator":
        (units / "poindexter-deploy-sync.service").write_text(
            f"[Service]\nUser=someone\nExecStart={operator_driver}\n", encoding="utf-8",
        )
    elif previous_exec is not None:
        (units / "poindexter-deploy-sync.service").write_text(
            f"[Service]\nUser=someone\nExecStart={previous_exec}\n", encoding="utf-8",
        )
    return {
        "home": home, "bin": bin_dir, "calls": calls, "clone": clone, "units": units,
        "state": home / ".poindexter" / "deploy-sync", "operator_driver": operator_driver,
        "operator_text": operator_text, "operator_sha": operator_sha,
    }


def _install(rig: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_repo_root() / "scripts/linux/install-deploy-sync.sh"), *args],
        env={
            "PATH": f"{rig['bin']}:/usr/bin:/bin",
            "HOME": str(rig["home"]),
            "POINDEXTER_DEPLOY_ROOT": str(rig["clone"]),
            "POINDEXTER_UNIT_DIR": str(rig["units"]),
        },
        capture_output=True, text=True, timeout=120,
    )


def _systemctl(rig: dict) -> list[str]:
    return rig["calls"].read_text(encoding="utf-8").splitlines() if rig["calls"].exists() else []


def _directive(unit_text: str, key: str) -> str:
    return next(ln.split("=", 1)[1] for ln in unit_text.splitlines() if ln.startswith(f"{key}="))


class TestInstall:
    def test_installs_a_copy_of_the_launcher_outside_every_git_tree(self, tmp_path):
        rig = _rig(tmp_path)
        proc = _install(rig, "--no-start")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        launcher = rig["state"] / "deploy-sync-launcher.sh"
        assert launcher.is_file() and not launcher.is_symlink(), "a symlink into the clone would ride its resets"
        assert launcher.read_bytes() == (rig["clone"] / "scripts/linux/deploy-sync-launcher.sh").read_bytes()
        assert launcher.stat().st_mode & 0o111
        in_git = subprocess.run(["git", "-C", str(launcher.parent), "rev-parse"], capture_output=True)
        assert in_git.returncode != 0, "the installed launcher must not live in a git work tree"

    def test_seeds_last_known_good_from_the_driver_the_host_runs_today(self, tmp_path):
        rig = _rig(tmp_path)
        proc = _install(rig, "--no-start")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (rig["state"] / "last-known-good.sh").read_text(encoding="utf-8") == rig["operator_text"]
        meta = (rig["state"] / "last-known-good.meta").read_text(encoding="utf-8")
        assert f"commit={rig['operator_sha']}" in meta
        assert f"seeded from {rig['operator_driver']}" in meta

    def test_never_seeds_from_the_deploy_clone(self, tmp_path):
        """A fallback identical to the copy being judged could rescue nothing."""
        rig = _rig(tmp_path, previous_exec=None)
        proc = _install(rig, "--no-start")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not (rig["state"] / "last-known-good.sh").exists()
        assert "first clean pass creates" in proc.stdout

    def test_a_previous_driver_that_does_not_parse_is_not_seeded(self, tmp_path):
        rig = _rig(tmp_path)
        rig["operator_driver"].write_text("if then fi\n", encoding="utf-8")
        proc = _install(rig, "--no-start")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not (rig["state"] / "last-known-good.sh").exists()
        assert "fails bash -n" in proc.stdout

    def test_a_rerun_keeps_the_existing_last_known_good_copy(self, tmp_path):
        rig = _rig(tmp_path)
        assert _install(rig, "--no-start").returncode == 0
        lkg = rig["state"] / "last-known-good.sh"
        lkg.write_text(rig["operator_text"] + "# promoted since\n", encoding="utf-8")
        proc = _install(rig, "--no-start")  # the unit now points at the launcher
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert lkg.read_text(encoding="utf-8").endswith("# promoted since\n")
        assert "keeping the existing last-known-good" in proc.stdout

    def test_renders_both_units_for_this_host(self, tmp_path):
        rig = _rig(tmp_path)
        proc = _install(rig, "--no-start")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        root = _repo_root()
        for unit, exec_start in (
            ("poindexter-deploy-sync.service", str(rig["state"] / "deploy-sync-launcher.sh")),
            ("poindexter-docker-watchdog.service", str(rig["clone"] / "scripts/linux/docker-watchdog.sh")),
        ):
            rendered = (rig["units"] / unit).read_text(encoding="utf-8")
            assert _directive(rendered, "User") == getpass.getuser()
            assert _directive(rendered, "ExecStart") == exec_start
            template = (root / "infrastructure/systemd" / unit).read_text(encoding="utf-8")
            keep = [ln for ln in template.splitlines() if not ln.startswith(("User=", "ExecStart="))]
            assert [ln for ln in rendered.splitlines() if not ln.startswith(("User=", "ExecStart="))] == keep, (
                f"{unit}: only User= and ExecStart= are host-specific"
            )
        for timer in ("poindexter-deploy-sync.timer", "poindexter-docker-watchdog.timer"):
            assert (rig["units"] / timer).read_text(encoding="utf-8") == (
                root / "infrastructure/systemd" / timer).read_text(encoding="utf-8")

    def test_reloads_and_restarts_the_timers(self, tmp_path):
        rig = _rig(tmp_path)
        assert _install(rig, "--no-start").returncode == 0
        calls = _systemctl(rig)
        assert "daemon-reload" in calls
        assert "enable poindexter-deploy-sync.timer poindexter-docker-watchdog.timer" in calls
        assert "restart poindexter-deploy-sync.timer poindexter-docker-watchdog.timer" in calls
        assert not any(c.startswith("start ") for c in calls), "--no-start runs no pass"

    def test_runs_one_pass_through_the_launcher_unless_told_not_to(self, tmp_path):
        rig = _rig(tmp_path)
        proc = _install(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "start poindexter-deploy-sync.service" in _systemctl(rig)
        assert "deploy driver launcher:" in proc.stdout, "ends with the launcher's report"


class TestGpuScraper:
    """#4188: the scraper reads its code once, at start. The deploy pass restarts
    it when a file it loads changes, which only helps once it runs the clone."""

    def test_renders_the_scraper_onto_the_deploy_clone(self, tmp_path):
        rig = _rig(tmp_path)
        proc = _install(rig, "--no-start")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        rendered = (rig["units"] / _SCRAPER).read_text(encoding="utf-8")
        template = (_repo_root() / "infrastructure/systemd" / _SCRAPER).read_text(encoding="utf-8")
        host = ("User=", "WorkingDirectory=", "ExecStart=")
        assert _directive(rendered, "User") == getpass.getuser()
        assert _directive(rendered, "WorkingDirectory") == str(rig["clone"])
        # The template's command on this host's clone, so the interpreter and
        # arguments cannot drift between the installer and the template.
        assert _directive(rendered, "ExecStart") == _directive(template, "ExecStart").replace(
            _TEMPLATE_CLONE, str(rig["clone"]))
        assert [ln for ln in rendered.splitlines() if not ln.startswith(host)] == [
            ln for ln in template.splitlines() if not ln.startswith(host)
        ], "only User=, WorkingDirectory= and ExecStart= are host-specific"

    def test_a_host_that_ran_the_scraper_gets_it_restarted_onto_the_clone(self, tmp_path):
        """The old unit ran the working checkout. A running scraper keeps that
        tree's code until it restarts; a stopped one must stay stopped."""
        rig = _rig(tmp_path)
        (rig["units"] / _SCRAPER).write_text(
            "[Service]\nUser=someone\nWorkingDirectory=/home/someone/glad-labs-website\n"
            "ExecStart=/usr/bin/python3 /home/someone/glad-labs-website/scripts/gpu-scraper.py\n",
            encoding="utf-8",
        )
        proc = _install(rig, "--no-start")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        calls = _systemctl(rig)
        assert f"try-restart {_SCRAPER}" in calls
        assert calls.index("daemon-reload") < calls.index(f"try-restart {_SCRAPER}"), (
            "the restart must load the new unit file"
        )
        assert not any(c.startswith(("enable", "start ")) and _SCRAPER in c for c in calls), (
            "enablement is the operator's, as it was"
        )
        assert str(rig["clone"]) in _directive((rig["units"] / _SCRAPER).read_text(encoding="utf-8"), "ExecStart")

    def test_a_host_that_never_ran_the_scraper_gets_the_unit_but_not_enabled(self, tmp_path):
        """gpu_metrics is optional and the scraper needs host python3-asyncpg +
        python3-httpx, so the installer does not switch it on."""
        rig = _rig(tmp_path)
        proc = _install(rig, "--no-start")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (rig["units"] / _SCRAPER).is_file()
        assert not any(_SCRAPER in c for c in _systemctl(rig)), _systemctl(rig)
        assert "installed but NOT enabled" in proc.stdout
        assert f"systemctl enable --now {_SCRAPER}" in proc.stdout


class TestRefusals:
    def test_no_deploy_clone(self, tmp_path):
        rig = _rig(tmp_path)
        shutil.rmtree(rig["clone"])
        proc = _install(rig, "--no-start")
        assert proc.returncode == 1
        assert "no deploy clone" in proc.stderr
        assert not (rig["units"] / "poindexter-docker-watchdog.service").exists()

    def test_a_clone_that_predates_the_launcher(self, tmp_path):
        rig = _rig(tmp_path)
        (rig["clone"] / "scripts/linux/deploy-sync-launcher.sh").unlink()
        proc = _install(rig, "--no-start")
        assert proc.returncode == 1
        assert "predates the launcher" in proc.stderr
        assert _systemctl(rig) == []

    def test_unknown_arguments(self, tmp_path):
        rig = _rig(tmp_path)
        proc = _install(rig, "--now")
        assert proc.returncode == 2
        assert _systemctl(rig) == []
