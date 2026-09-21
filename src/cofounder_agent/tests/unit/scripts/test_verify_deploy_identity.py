"""Deploy-identity verifier — the parsing and precision rules, without docker.

The tool answers "is every container running the code on disk", which the
existing deploy safety layers do not: the health gate, the restart-loop probe
and `docker ps` all report a container running month-old code as healthy.

These tests pin the two decisions that make it usable rather than noise —
container-level dedup, and excluding `pyproject.toml` from the dependency
signal — plus the scan floor. The docker-dependent half is exercised against a
live stack, not here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts" / "linux" / "verify_deploy_identity.py").is_file():
            return parent
    raise RuntimeError("could not locate scripts/linux/verify_deploy_identity.py")


REPO = _repo_root()
SCRIPT = REPO / "scripts" / "linux" / "verify_deploy_identity.py"


@pytest.fixture(scope="module")
def mod():
    sys.path.insert(0, str(REPO / "scripts" / "ci"))
    spec = importlib.util.spec_from_file_location("verify_deploy_identity", SCRIPT)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: @dataclass resolves the defining module through
    # sys.modules, and without this the decorator raises on import.
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


COMPOSE_A = """\
services:
  worker:
    build:
      context: ./src/cofounder_agent
      dockerfile: Dockerfile
    container_name: poindexter-worker
    volumes:
      - ./src/cofounder_agent:/app:ro
  loki:
    image: grafana/loki:3.3.2
    container_name: poindexter-loki
"""

# The SAME container, declared again in the consumer compose file.
COMPOSE_B = """\
services:
  worker:
    build:
      context: ./src/cofounder_agent
      dockerfile: Dockerfile
    container_name: poindexter-worker
"""


def _write_compose(tmp_path: Path) -> Path:
    (tmp_path / "docker-compose.local.yml").write_text(COMPOSE_A)
    (tmp_path / "docker-compose.consumer.yml").write_text(COMPOSE_B)
    (tmp_path / "src" / "cofounder_agent").mkdir(parents=True)
    return tmp_path


def test_a_container_in_two_compose_files_is_counted_once(mod, tmp_path):
    """Dedup is by CONTAINER, not service name.

    The first cut compared a service name against a container name, so it never
    matched: every shared service was counted twice and the summary claimed 71
    running containers on a stack that has 48.
    """
    services = mod.parse_services(_write_compose(tmp_path))
    containers = [s.container for s in services]
    assert containers.count("poindexter-worker") == 1
    assert len(containers) == len(set(containers))


def test_services_without_a_container_name_are_ignored(mod, tmp_path):
    services = mod.parse_services(_write_compose(tmp_path))
    assert {s.container for s in services} == {"poindexter-worker", "poindexter-loki"}


def test_build_and_bind_mount_are_both_captured(mod, tmp_path):
    services = mod.parse_services(_write_compose(tmp_path))
    worker = next(s for s in services if s.container == "poindexter-worker")
    assert worker.dockerfile is not None, "a built service must carry its Dockerfile"
    assert worker.bind_sources, "a bind-mounted service must carry its mount"
    loki = next(s for s in services if s.container == "poindexter-loki")
    assert loki.dockerfile is None, "an image-only service bakes nothing here"


def test_pyproject_is_not_a_dependency_signal(mod):
    """release-please rewrites pyproject's `version =` on every release.

    Including it made the tool report every bind-mounted service stale after
    every release — measured on 0.143.0, whose entire diff was the version
    line. poetry.lock changes iff the RESOLVED dependency set changes, which
    is what the image actually bakes.
    """
    assert "pyproject.toml" not in mod.DEP_MANIFESTS
    assert "poetry.lock" in mod.DEP_MANIFESTS
    assert "Dockerfile" in mod.DEP_MANIFESTS


def test_scan_floor_refuses_to_pass_on_an_empty_tree(mod, tmp_path, capsys):
    """A run that inspected nothing has not verified anything.

    Same doctrine as scripts/ci/lib_scan_floor: exit 2 (could not check), never
    0 (all current).
    """
    argv = sys.argv
    try:
        sys.argv = ["verify_deploy_identity.py", "--repo", str(tmp_path)]
        code = mod.main()
    finally:
        sys.argv = argv
    assert code == 2, "no compose file under --repo must be 'could not check', not 'clean'"
    assert "no compose file" in capsys.readouterr().err
