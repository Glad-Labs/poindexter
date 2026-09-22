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


# ── first-real-use bugs (2026-09-22) ──────────────────────────────────────
#
# The tool shipped, was pointed at the live stack, and got the answer wrong
# three ways at once. All three made it report a stale container as current,
# which is the single failure mode it exists to prevent.


def test_timestamps_are_compared_as_instants_not_strings(mod):
    """docker emits UTC (`...Z`); git emits the committer's offset (`-04:00`).

    The first cut truncated both to 19 chars — dropping the offset — and
    compared the remainders, so a commit at 08:36 EDT (12:36 UTC) read as
    "08:36" against a UTC image time. Four hours in the direction that hides
    staleness.
    """
    image = mod._instant("2026-09-22T04:00:35.495908049Z")
    commit = mod._instant("2026-09-22T08:36:03-04:00")
    assert image is not None and commit is not None
    assert commit > image, "12:36Z is after 04:00Z — a string compare said otherwise"
    # Nanosecond precision from docker must not break parsing.
    assert mod._instant("2026-09-22T04:00:35.495908049Z").year == 2026


def test_a_stamp_without_a_zone_is_refused(mod):
    """Declining to judge beats guessing a zone."""
    assert mod._instant("2026-09-22T04:00:35") is None
    assert mod._instant("") is None
    assert mod._instant("not a timestamp") is None


def test_dockerfile_path_resolves_against_the_build_context(mod, tmp_path):
    """`dockerfile: poindexter/brain/Dockerfile` is relative to the CONTEXT.

    Resolving a slashed path against the repo root pointed at a file that does
    not exist, so `is_file()` was False and the image check was skipped in
    silence — hiding brain-daemon, the service the tool was written for.
    """
    (tmp_path / "docker-compose.local.yml").write_text(
        "services:\n"
        "  brain-daemon:\n"
        "    build:\n"
        "      context: ./src/cofounder_agent\n"
        "      dockerfile: poindexter/brain/Dockerfile\n"
        "    container_name: poindexter-brain-daemon\n"
    )
    (tmp_path / "src" / "cofounder_agent" / "poindexter" / "brain").mkdir(parents=True)
    svc = next(s for s in mod.parse_services(tmp_path) if s.container == "poindexter-brain-daemon")
    assert svc.dockerfile == (
        tmp_path / "src" / "cofounder_agent" / "poindexter" / "brain" / "Dockerfile"
    ), "a slashed dockerfile: must resolve under the build context, not the repo root"


def test_dependency_manifests_are_scoped_to_what_the_image_installs(mod, tmp_path):
    """A `COPY . .` image bakes every lock file in the tree.

    Matching them all let the BRAIN's poetry.lock bump flag the worker, which
    installs from a different lock entirely. Only manifests beside the
    Dockerfile (or at the context root) are the ones an image installs from.
    """
    ctx = tmp_path / "src" / "cofounder_agent"
    brain = ctx / "poindexter" / "brain"
    brain.mkdir(parents=True)
    worker_df = ctx / "Dockerfile"
    worker_df.write_text("FROM python\n")
    files = [ctx / "poetry.lock", brain / "poetry.lock", ctx / "app.py"]

    kept = mod._install_manifests(files, worker_df, tmp_path)
    assert ctx / "poetry.lock" in kept, "the image's own lock counts"
    assert brain / "poetry.lock" not in kept, "another image's lock must not"
    assert ctx / "app.py" not in kept, "only dependency manifests here"


# ── third round of first-use bugs (2026-09-22) ────────────────────────────
#
# Reported `48/48 current` while three containers ran images that had been
# rebuilt out from under them and five more had their Dockerfile bumped hours
# earlier. Same failure mode as the first two rounds: stale read as current.


def test_the_dockerfile_is_an_input_to_its_own_image(mod, tmp_path, monkeypatch):
    """A Dockerfile is not among its own COPY targets, so omitting it meant a
    Dockerfile-only change could never flag a non-bind-mounted service — and a
    base-image bump is exactly a Dockerfile-only change."""
    df = tmp_path / "Dockerfile.thing"
    df.write_text("FROM python\n")
    monkeypatch.setattr(mod, "collect_images", lambda repo: [])
    assert df in mod.closure_for(tmp_path, df), (
        "a Dockerfile must be part of its own image's input set"
    )


def test_a_container_on_a_superseded_image_needs_recreate(mod, tmp_path, monkeypatch):
    """Rebuild-without-recreate leaves the container on an image the tag no
    longer names — often one that no longer exists locally.

    Timestamps cannot see this: the container can be NEWER than the image it is
    running. On 2026-09-22 a deploy pass rebuilt brain-daemon and never
    recreated it, and this check reported the stack fully current.
    """
    responses = {
        "{{.State.Running}}": "true",
        "{{.Image}}": "sha256:aaaaaaaaaaaaold",
        "{{.Config.Image}}": "glad-labs-website-thing",
        "{{.Id}}": "sha256:bbbbbbbbbbbbnew",
        "{{.State.StartedAt}}": "2026-09-22T12:00:00Z",
        "{{json .Mounts}}": "[]",
    }

    def fake_sh(*args):
        for key, value in responses.items():
            if key in args:
                return value
        return ""

    monkeypatch.setattr(mod, "sh", fake_sh)
    df = tmp_path / "Dockerfile"
    df.write_text("FROM python\n")
    svc = mod.Service("thing", "poindexter-thing", df, [])
    out = mod.check(tmp_path, svc)

    assert out["status"] == "needs-recreate"
    assert "never recreated" in " ".join(out["notes"])


def test_matching_image_ids_are_not_flagged(mod, tmp_path, monkeypatch):
    """The complement: a container on the image its tag names is not disturbed."""
    same = "sha256:ccccccccccccsame"
    responses = {
        "{{.State.Running}}": "true",
        "{{.Image}}": same,
        "{{.Config.Image}}": "glad-labs-website-thing",
        "{{.Id}}": same,
        "{{.State.StartedAt}}": "2026-09-22T12:00:00Z",
        "{{.Created}}": "2026-09-22T12:00:00Z",
        "{{json .Mounts}}": "[]",
    }

    def fake_sh(*args):
        for key, value in responses.items():
            if key in args:
                return value
        return ""

    monkeypatch.setattr(mod, "sh", fake_sh)
    monkeypatch.setattr(mod, "collect_images", lambda repo: [])
    monkeypatch.setattr(mod, "last_change", lambda repo, files: "")
    df = tmp_path / "Dockerfile"
    df.write_text("FROM python\n")
    out = mod.check(tmp_path, mod.Service("thing", "poindexter-thing", df, []))
    assert out["status"] == "current"
