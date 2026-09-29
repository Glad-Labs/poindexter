"""Which compose file ``scripts/start-stack.sh`` launches — and that setup agrees.

On a public clone (no operator docker-compose.local.yml) start-stack.sh used to
fall back to a bare docker-compose.yml with no Prefect. Prefect is the only
dispatcher, so every task the quick start queued stayed ``pending`` forever.
The fallback is now docker-compose.consumer.yml.

``poindexter setup --auto`` must start the Postgres of the SAME file (and the
same compose project), or the CLI and the stack end up on two databases —
the other half of the same outage. ``cli/setup.py::_COMPOSE_FILES`` is the
Python side of the selection; these tests run the real script against a fake
``docker`` and hold the two in step.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from poindexter.cli import setup as setup_mod

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "scripts" / "start-stack.sh").is_file()
)
_START_STACK = _REPO_ROOT / "scripts" / "start-stack.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(tmp_path: Path, *, operator: bool, args: list[str]) -> str:
    """Run a copy of start-stack.sh in a fake checkout; return the docker argv."""
    project = tmp_path / "checkout"
    (project / "scripts").mkdir(parents=True)
    shutil.copy2(_START_STACK, project / "scripts" / "start-stack.sh")
    (project / "docker-compose.consumer.yml").write_text("services: {}\n")
    if operator:
        (project / "docker-compose.local.yml").write_text("services: {}\n")

    home = tmp_path / "home"
    (home / ".poindexter").mkdir(parents=True)
    (home / ".poindexter" / "bootstrap.toml").write_text(
        'database_url = "postgresql://poindexter:pw@localhost:5433/poindexter_brain"\n'
        'compose_project_name = "poindexter"\n'
        'local_postgres_password = "pw"\n'
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "docker-argv"
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(f'#!/usr/bin/env bash\nprintf "%s " "$@" > "{record}"\n')
    fake_docker.chmod(fake_docker.stat().st_mode | stat.S_IEXEC)

    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(home),
    }
    subprocess.run(
        ["bash", str(project / "scripts" / "start-stack.sh"), *args],
        cwd=tmp_path,  # deliberately NOT the checkout: selection is anchored on $0
        env=env, check=True, capture_output=True, text=True, timeout=60,
    )
    return record.read_text().strip()


def test_public_checkout_launches_the_consumer_stack(tmp_path):
    argv = _run(tmp_path, operator=False, args=["up", "-d"])
    assert argv == "compose -f docker-compose.consumer.yml up -d"


def test_operator_checkout_still_launches_its_own_stack(tmp_path):
    argv = _run(tmp_path, operator=True, args=["up", "-d"])
    assert argv == "compose -f docker-compose.local.yml up -d"


def test_setup_provisions_the_database_of_the_file_start_stack_launches(tmp_path):
    for operator in (False, True):
        root = tmp_path / ("op" if operator else "pub")
        root.mkdir()
        (root / "scripts").mkdir()
        (root / "scripts" / "start-stack.sh").write_text("")
        (root / "docker-compose.consumer.yml").write_text("services: {}\n")
        if operator:
            (root / "docker-compose.local.yml").write_text("services: {}\n")
        launched = _run(root, operator=operator, args=["config"]).split()[2]
        assert setup_mod.compose_file_for(root).name == launched


def test_retired_bare_compose_is_not_a_fallback():
    assert 'COMPOSE_FILE="docker-compose.yml"' not in _START_STACK.read_text(encoding="utf-8")
    assert "docker-compose.yml" not in setup_mod._COMPOSE_FILES


def test_backup_dir_is_created_as_the_invoking_user(tmp_path):
    """dockerd creates a missing bind-mount source root-owned, and the public
    stack's dump services (running as the host uid) then cannot write into it —
    they restart-looped on a fresh host. start-stack.sh creates it first."""
    _run(tmp_path, operator=False, args=["up", "-d"])
    backups = tmp_path / "home" / ".poindexter" / "backups" / "auto"
    assert backups.is_dir()
    assert backups.stat().st_uid == os.getuid()


def test_public_dump_services_run_as_the_host_user():
    import yaml

    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.consumer.yml").read_text(encoding="utf-8"))
    for name in ("backup-hourly", "backup-daily", "backup-offsite"):
        user = compose["services"][name].get("user", "")
        assert user.startswith("${POINDEXTER_HOST_UID"), f"{name} runs as {user!r}"
