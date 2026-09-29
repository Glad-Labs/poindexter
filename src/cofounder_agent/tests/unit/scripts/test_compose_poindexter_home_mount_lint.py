"""Tests for ``scripts/ci/compose_poindexter_home_mount_lint.py``.

The lint exists because worker, pipeline-bot and prefect-worker mounted the
host's whole ``~/.poindexter`` read-write at ``/root/.poindexter``
(glad-labs-stack#4186). That directory holds the master key and code the host
executes. Four layers:

1. **Repo contract**: the live compose files pass.
2. **Negative control**: every spelling of the mounts that were removed, and
   the obvious neighbours (an ancestor, a protected entry, ``..``), fails.
3. **Scanner fidelity**: the stdlib line scanner reads each real compose file
   the way PyYAML does, so it cannot pass by missing entries.
4. **Image consistency**: Dockerfile.worker keeps ``/root`` closed, and no
   service built from it mounts anything under ``/root``.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit._nonempty import nonempty

REPO_ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "scripts" / "ci" / "compose_poindexter_home_mount_lint.py").exists()
)
LINTER_PATH = REPO_ROOT / "scripts" / "ci" / "compose_poindexter_home_mount_lint.py"
# Named here as well as found by the lint's own glob, so
# tests/unit/infrastructure/test_ci_runs_when_its_inputs_change.py can see
# which files this test reads.
COMPOSE_FILES = (
    REPO_ROOT / "docker-compose.local.yml",
    REPO_ROOT / "docker-compose.consumer.yml",
    REPO_ROOT / "docker-compose.yml",
)
WORKER_DOCKERFILE = REPO_ROOT / "src" / "cofounder_agent" / "Dockerfile.worker"


def _load_linter():
    spec = importlib.util.spec_from_file_location("compose_poindexter_home_mount_lint", LINTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_linter()


def _findings(entry: str, service: str = "worker") -> list[tuple[str, str | None]]:
    """``(kind, entry)`` findings for one short-syntax volume entry."""
    mount = LINT.mount_from_short(entry, file="t.yml", line=1, service=service)
    assert mount is not None, entry
    return [(f.kind, f.entry) for f in LINT.check_mount(mount)]


def _present(paths=COMPOSE_FILES) -> list[Path]:
    return [p for p in paths if p.is_file()]


# ---------------------------------------------------------------------------
# 1. Repo contract
# ---------------------------------------------------------------------------


def test_repo_passes_lint_in_process(capsys) -> None:
    rc = LINT.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "OK (" in out


def test_repo_passes_lint_subprocess() -> None:
    proc = subprocess.run(
        [sys.executable, str(LINTER_PATH)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_lint_scans_the_compose_files_this_test_names() -> None:
    """The lint finds files by glob; this pins that the glob still sees them."""
    assert set(_present()) <= set(LINT.compose_files(REPO_ROOT))
    assert LINT.compose_files(REPO_ROOT), "the lint's glob matched no compose file"


# ---------------------------------------------------------------------------
# 2. Negative control
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        # The two spellings removed by #4186 (local and consumer compose).
        "${USERPROFILE:-${HOME}}/.poindexter:/root/.poindexter",
        "${USERPROFILE:-.}/.poindexter:/root/.poindexter",
        # Read-only is still the whole directory.
        "${USERPROFILE:-${HOME}}/.poindexter:/root/.poindexter:ro",
        "~/.poindexter:/data",
        "$HOME/.poindexter/:/data",
        "/home/someone/.poindexter:/data",
        # A literal Windows path: the drive colon is not a field separator.
        "C:\\Users\\someone\\.poindexter:/root/.poindexter",
        # A default hidden inside another variable, and a dot-dot walk back up.
        "${POINDEXTER_DATA:-${HOME}/.poindexter}:/data",
        "${HOME}/.poindexter/podcast/..:/data",
        # Ancestors hand over the directory too.
        "${HOME}:/host-home",
        "~:/host-home",
        "/home:/homes:ro",
        "/:/host-root",
    ],
)
def test_whole_directory_and_ancestor_mounts_fail(entry: str) -> None:
    assert ("whole", None) in _findings(entry)


@pytest.mark.parametrize(
    ("entry", "protected"),
    [
        ("${HOME}/.poindexter/bootstrap.toml:/etc/bootstrap.toml:ro", "bootstrap.toml"),
        ("${HOME}/.poindexter/bootstrap.toml.bak-20260921:/b:ro", "bootstrap.toml.bak-20260921"),
        ("${USERPROFILE:-${HOME}}/.poindexter/deploy/glad-labs-stack:/code", "deploy"),
        ("${HOME}/.poindexter/deploy-sync:/launcher", "deploy-sync"),
        ("${HOME}/.poindexter/cli-venv:/venv:ro", "cli-venv"),
        ("${HOME}/.poindexter/scripts/dr-backup:/dr", "scripts"),
        ("${HOME}/.poindexter/worktrees:/wt", "worktrees"),
        # A dot-dot walk from an allowed entry into a protected one.
        ("${HOME}/.poindexter/podcast/../deploy-sync:/p", "deploy-sync"),
    ],
)
def test_protected_entries_fail(entry: str, protected: str) -> None:
    assert _findings(entry) == [("protected", protected)]


def test_an_undeclared_entry_fails() -> None:
    assert _findings("${HOME}/.poindexter/ci-runner-app.pem:/key:ro") == [
        ("undeclared", "ci-runner-app.pem")
    ]


@pytest.mark.parametrize("entry", ["../..:/up", "./a/../../b:/up", "../.env:/env:ro"])
def test_a_source_that_climbs_out_of_the_project_dir_fails(entry: str) -> None:
    assert _findings(entry) == [("escapes-project", None)]


@pytest.mark.parametrize(
    "entry",
    [
        "${USERPROFILE:-${HOME}}/.poindexter/podcast:/home/appuser/.poindexter/podcast",
        "${USERPROFILE:-.}/.poindexter/video:/home/appuser/.poindexter/video",
        "${POINDEXTER_BACKUP_ROOT:-${USERPROFILE:-${HOME}}/.poindexter/backups}:/home/appuser/.poindexter/backups",
        "${USERPROFILE:-${HOME}}/.poindexter/comfyui/models/vae:/comfyui/models/vae:ro",
        # Not ~/.poindexter at all: the deployed code tree, named volumes, sockets.
        "${POINDEXTER_DEPLOY_ROOT:-.}/src/cofounder_agent:/app:ro",
        "./infrastructure/grafana/provisioning/alerting:/etc/grafana-alerting:rw",
        "prometheus-rules:/etc/prometheus/rules",
        "/var/run/docker.sock:/var/run/docker.sock",
        "${USERPROFILE:-${HOME}}/.claude:/config/claude:ro",
    ],
)
def test_declared_and_unrelated_mounts_pass(entry: str) -> None:
    assert _findings(entry) == []


# --- exemptions are pinned to one mount and one finding ---------------------


def test_the_offsite_config_snapshot_is_exempt_only_read_only() -> None:
    entry = "${USERPROFILE:-${HOME}}/.poindexter:/config/poindexter"
    assert _findings(entry + ":ro", service="backup-offsite") == []
    assert _findings(entry, service="backup-offsite") == [("whole", None)]


def test_an_exemption_does_not_follow_the_target_to_another_source() -> None:
    """Same service and container path, different host dir: not exempt."""
    entry = "${USERPROFILE:-${HOME}}/.poindexter/deploy-sync:/config/poindexter:ro"
    assert _findings(entry, service="backup-offsite") == [("protected", "deploy-sync")]


def test_the_brain_deploy_clone_mount_is_the_only_protected_exception() -> None:
    clone = "${USERPROFILE:-${HOME}}/.poindexter/deploy/glad-labs-stack:/host-deploy:rw"
    assert _findings(clone, service="brain-daemon") == []
    assert _findings(clone, service="worker") == [("protected", "deploy")]
    key = "${USERPROFILE:-${HOME}}/.poindexter/bootstrap.toml:/host-deploy:ro"
    assert _findings(key, service="brain-daemon") == [("protected", "bootstrap.toml")]


def test_no_protected_entry_is_allowed() -> None:
    assert not [e for e in LINT.ALLOWED_ENTRIES if LINT._protected(e)]


def test_main_refuses_a_protected_entry_in_the_allowlist(monkeypatch, capsys) -> None:
    monkeypatch.setitem(LINT.ALLOWED_ENTRIES, "deploy-sync", "convenient")
    assert LINT.main() == 1
    assert "deploy-sync" in capsys.readouterr().out


def test_every_exemption_carries_a_reason() -> None:
    for (service, target), exemption in nonempty(LINT.EXEMPT.items(), "LINT.EXEMPT"):
        assert exemption.reason.strip(), (service, target)
        if exemption.kind == "whole":
            assert exemption.read_only, f"{service}:{target} is a whole-dir exemption"


# ---------------------------------------------------------------------------
# 3. Scanner fidelity
# ---------------------------------------------------------------------------


def _yaml_mounts(text: str, name: str) -> list[tuple[str, str, str, bool]]:
    """What PyYAML says each service mounts, normalized like the lint."""
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(text) or {}
    out = []
    for service, body in (data.get("services") or {}).items():
        for entry in (body or {}).get("volumes") or []:
            if isinstance(entry, str):
                mount = LINT.mount_from_short(entry, file=name, line=0, service=service)
            else:
                fields = {
                    k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in entry.items()
                }
                mount = LINT.mount_from_long(fields, file=name, line=0, service=service)
            if mount is not None:
                out.append((mount.service, mount.source, mount.target, mount.read_only))
    return out


def _scanned(text: str, name: str) -> list[tuple[str, str, str, bool]]:
    return [(m.service, m.source, m.target, m.read_only) for m in LINT.scan_mounts(text, file=name)]


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_the_scanner_reads_each_real_compose_file_like_a_yaml_parser(path: Path) -> None:
    if not path.is_file():
        pytest.skip(
            f"{path.name} is not in this tree (the public mirror strips the operator stack)"
        )
    text = path.read_text(encoding="utf-8")
    assert _scanned(text, path.name) == _yaml_mounts(text, path.name)


def test_the_yaml_comparison_saw_real_mounts() -> None:
    """Guard the guard: an empty comparison above would pass vacuously."""
    consumer = REPO_ROOT / "docker-compose.consumer.yml"
    assert len(_scanned(consumer.read_text(encoding="utf-8"), consumer.name)) >= 40


SYNTHETIC = """\
x-common: &common
  restart: unless-stopped
services:
  # a comment at service level
  alpha:
    <<: *common
    image: busybox
    command: >
      sh -c 'echo volumes: not a key here'
    volumes:
      - "${HOME}/.poindexter/podcast:/p"   # quoted, trailing comment
      - '/srv/data:/data:ro,z'
      # a comment inside the list
      - type: bind
        source: ${HOME}/.poindexter
        target: /whole
        read_only: true
        bind:
          propagation: rprivate
      - type: volume
        source: named
        target: /named
      - /anonymous
    environment:
      A: "1"
  beta:
    volumes:
    - ${HOME}/.poindexter/video:/v
    - "hash:/in # quotes is data"
    - O'Brien:/plain # an apostrophe is not a quote, so this is a comment
volumes:
  named: {}
"""


def test_the_scanner_matches_yaml_on_tricky_syntax() -> None:
    assert _scanned(SYNTHETIC, "s.yml") == _yaml_mounts(SYNTHETIC, "s.yml")
    services = {m.service for m in LINT.scan_mounts(SYNTHETIC, file="s.yml")}
    assert services == {"alpha", "beta"}


def test_a_long_syntax_whole_dir_bind_is_caught() -> None:
    mounts = LINT.scan_mounts(SYNTHETIC, file="s.yml")
    whole = [m for m in mounts if m.target == "/whole"]
    assert len(whole) == 1 and whole[0].read_only
    assert [(f.kind, f.entry) for f in LINT.check_mount(whole[0])] == [("whole", None)]


@pytest.mark.parametrize(
    "volumes",
    ['    volumes: ["${HOME}/.poindexter:/x"]\n', "    volumes: *shared\n"],
    ids=["flow-list", "alias"],
)
def test_the_scanner_fails_loud_on_syntax_it_cannot_read(volumes: str) -> None:
    text = "services:\n  svc:\n    image: busybox\n" + volumes
    with pytest.raises(LINT.ComposeScanError):
        LINT.scan_mounts(text, file="s.yml")


@pytest.mark.parametrize(
    ("spec", "fields"),
    [
        ("${A:-${B}}/x:/y:ro", ["${A:-${B}}/x", "/y", "ro"]),
        ("C:\\Users\\x\\.poindexter:/data:ro", ["C:\\Users\\x\\.poindexter", "/data", "ro"]),
        ("C:/Users/x:/data", ["C:/Users/x", "/data"]),
        # A one-letter named volume with a mode is not a drive path.
        ("c:/data:ro", ["c", "/data", "ro"]),
    ],
)
def test_short_syntax_splits_like_compose(spec: str, fields: list[str]) -> None:
    assert LINT.split_top_level_colons(spec) == fields


@pytest.mark.parametrize(
    ("text", "env", "expected"),
    [
        ("${A:-x}", {}, "x"),
        ("${A:-x}", {"A": ""}, "x"),
        ("${A-x}", {"A": ""}, ""),
        ("${A:+y}", {"A": "1"}, "y"),
        ("${A:+y}", {}, ""),
        ("${A:?need A}", {"A": "v"}, "v"),
        ("$$HOME", {"HOME": "/h"}, "$HOME"),
        ("$HOME/x", {"HOME": "/h"}, "/h/x"),
        ("${A:-${B:-${C}}/z}", {"C": "c"}, "c/z"),
    ],
)
def test_interpolation_follows_compose(text: str, env: dict[str, str], expected: str) -> None:
    assert LINT.interpolate(text, env) == expected


# ---------------------------------------------------------------------------
# 4. The worker image and its services agree that /root is closed
# ---------------------------------------------------------------------------


def test_worker_image_keeps_root_closed() -> None:
    """``chmod 0711 /root`` existed only so appuser could reach /root mounts."""
    dockerfile = WORKER_DOCKERFILE.read_text(encoding="utf-8")
    code = "\n".join(line for line in dockerfile.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"chmod\s+\S+\s+/root\b", code)


def _worker_image_services(path: Path) -> set[str]:
    yaml = pytest.importorskip("yaml")
    services = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("services") or {}
    return {
        name
        for name, body in services.items()
        if isinstance((body or {}).get("build"), dict)
        and body["build"].get("dockerfile") == "Dockerfile.worker"
    }


def test_no_worker_image_service_mounts_anything_under_root() -> None:
    """appuser can't traverse the image's /root (0700): a mount there is dead, or a hole."""
    checked: set[str] = set()
    under_root: list[str] = []
    for path in nonempty(_present(), "compose files"):
        names = _worker_image_services(path)
        checked |= names
        under_root += [
            f"{path.name}: {m.service} mounts {m.source} at {m.target}"
            for m in LINT.scan_mounts(path.read_text(encoding="utf-8"), file=path.name)
            if m.service in names and (m.target == "/root" or m.target.startswith("/root/"))
        ]
    assert {"worker", "pipeline-bot", "prefect-worker"} <= checked
    assert under_root == []
