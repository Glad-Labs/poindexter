"""Contract tests for ``deploy-checkout-sync.sh``'s compose-apply + sweep.

The 2026-08-27 outage: compose recreated ``prometheus``/``brain-daemon``,
services that others declare ``depends_on: <it>: service_healthy`` (6 for
brain-daemon alone). The dependency wait raced its own recreate and died with
``No such container: <id>``, leaving ``worker`` and ``grafana`` CREATED and
never started. That failure never stamps the new config-hash either, so the
next pass re-ran the same recreate and lost the same race — a self-perpetuating
10-minute loop with the worker down the whole time.

Two guards, tested here:

- the apply is attempted TWICE (the second sees the dependency already
  recreated and healthy — verified live: attempt 1 failed, attempt 2 rc=0);
- any project container left in ``created`` is started. ``created`` means
  never-started, which is only ever an interrupted recreate — a deliberately
  stopped service is ``exited``. That filter is load-bearing: the parked
  ``voice-agent-livekit`` still shows up in ``compose ps -a`` as exited, so a
  broader filter would silently un-park voice.

Rig follows ``test_deploy_checkout_sync_mcp.py``: throwaway git origin +
deploy clone, recorders on PATH writing to one shared events file, and
``POINDEXTER_DEPLOY_ROOT`` pointed at the clone.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash + git",
)

_GIT_ID = ("-c", "user.name=t", "-c", "user.email=t@example.com")


def _repo_root() -> Path:
    return next(
        p
        for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "deploy-checkout-sync.sh").exists()
    )


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *_GIT_ID, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


# ``up`` consults UP_FAIL_TIMES (how many leading attempts exit non-zero) via a
# counter file, so a single fake covers "fails once then recovers" and "always
# fails". ``ps`` prints STRANDED_NAMES only for the --status=created query —
# printing for any other filter would let a test pass while the script asked
# the wrong question. The liveness query (``ps -q <svc>``, 6a-bis) is answered
# by the scenario docker when there is one, else with an id: without a
# scenario every rebuilt service reads as running. Running vs parked is
# covered in test_deploy_checkout_sync_recreate_scope.py.
_FAKE_START_STACK = """#!/usr/bin/env bash
echo "start-stack $*" >> "${EVENTS_FILE:-/dev/null}"
action="${1:-}"
if [ "$action" = "up" ]; then
  case "$*" in
    *--force-recreate*) [ -n "${FORCE_RECREATE_EXIT:-}" ] && exit "$FORCE_RECREATE_EXIT" ;;
  esac
  n=0
  [ -f "$UP_COUNT_FILE" ] && n="$(cat "$UP_COUNT_FILE")"
  n=$((n + 1)); echo "$n" > "$UP_COUNT_FILE"
  [ "$n" -le "${UP_FAIL_TIMES:-0}" ] && exit 1
  exit 0
fi
if [ "$action" = "config" ]; then  # the compose project the health gate is scoped to
  printf '{"name": "%s"}\n' "${FAKE_COMPOSE_PROJECT-glad-labs-website}"
  exit 0
fi
if [ "$action" = "ps" ]; then
  case "$*" in
    *--status=created*) printf '%s' "${STRANDED_NAMES:-}" \
      | tr ',' '\\n' | grep -v '^$' || true ;;
    "ps -q "*)
      if [ -n "${FAKE_DOCKER_SCENARIO:-}" ]; then
        docker ps -q --filter "label=com.docker.compose.service=${@: -1}"
      else
        echo 0123456789abcdef
      fi ;;
  esac
  exit 0
fi
exit 0
"""

_FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$EVENTS_FILE"
case "${1:-}" in
  inspect) echo true ;;  # the liveness query's `inspect -f {{.State.Running}}`
  start) exit "${FAKE_DOCKER_START_EXIT:-0}" ;;
  # `container inspect` gates the bounce loop; fail it so the loop skips
  # every RESTART_CONTAINERS entry and leaves the sweep as the only thing
  # under test here.
  container) exit 1 ;;
esac
exit 0
"""


def _build_rig(tmp_path: Path, extra_files: dict[str, str] | None = None) -> dict:
    home = tmp_path / "home"
    (home / ".poindexter").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def fake(name: str, body: str) -> None:
        f = bin_dir / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)

    fake("docker", _FAKE_DOCKER)
    fake("systemctl", "#!/usr/bin/env bash\nexit 1\n")  # unit absent -> step skipped
    fake("sudo", '#!/usr/bin/env bash\nwhile [[ "${1:-}" == -* ]]; do shift; done\nexec "$@"\n')

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)],
        check=True,
        timeout=60,
    )
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    stack = seed / "scripts" / "start-stack.sh"
    stack.parent.mkdir(parents=True)
    stack.write_text(_FAKE_START_STACK, encoding="utf-8")
    stack.chmod(0o755)
    svc = seed / "src" / "cofounder_agent" / "poindexter" / "services"
    svc.mkdir(parents=True)
    (svc / "foo.py").write_text("X = 1\n", encoding="utf-8")
    for rel, body in (extra_files or {}).items():
        f = seed / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "A")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    base_sha = _git(seed, "rev-parse", "HEAD")

    clone = tmp_path / "deploy-clone"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)],
        check=True,
        timeout=60,
    )
    (home / ".poindexter" / "deploy-last-restarted-sha").write_text(
        base_sha,
        encoding="utf-8",
    )
    return {
        "home": home,
        "bin": bin_dir,
        "events": tmp_path / "events",
        "seed": seed,
        "clone": clone,
        "counter": tmp_path / "upcount",
    }


def _advance_origin(rig: dict, rel: str = "src/cofounder_agent/poindexter/services/foo.py") -> str:
    """Commit a change to ``rel`` and push it. The default path maps to the
    auto-embed rebuild; ``poindexter/brain/...`` adds brain-daemon."""
    p = rig["seed"] / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("X = 2\n", encoding="utf-8")
    _git(rig["seed"], "add", "-A")
    _git(rig["seed"], "commit", "-q", "-m", "B")
    _git(rig["seed"], "push", "-q", "origin", "main")
    return _git(rig["seed"], "rev-parse", "HEAD")


def _run_sync(rig: dict, **env_extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash",
            str(_repo_root() / "scripts" / "linux" / "deploy-checkout-sync.sh"),
            "--no-flow-check",
        ],
        env={
            "PATH": f"{rig['bin']}:/usr/bin:/bin",
            "HOME": str(rig["home"]),
            "POINDEXTER_DEPLOY_ROOT": str(rig["clone"]),
            "EVENTS_FILE": str(rig["events"]),
            "UP_COUNT_FILE": str(rig["counter"]),
            "SYNC_APPLY_RETRY_SETTLE_SEC": "0",  # keep the retry pause out of the test
            **env_extra,
        },
        capture_output=True,
        text=True,
        timeout=180,
    )


def _events(rig: dict) -> list[str]:
    f = rig["events"]
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def _status(rig: dict) -> dict:
    return json.loads(
        (rig["home"] / ".poindexter" / "deploy-checkout-sync.status.json").read_text(
            encoding="utf-8"
        )
    )


def _ups(rig: dict) -> list[str]:
    """Compose-APPLY invocations only.

    Deliberately excludes the step-6a-bis force-recreate, which is a second,
    differently-shaped `start-stack up` in the same pass. Counting every `up`
    would make this guard read "a healthy apply ran twice" for a pass that
    applied once and then recreated what it rebuilt — losing the double-apply
    bug it exists to catch.
    """
    return [
        e for e in _events(rig) if e.startswith("start-stack up") and "--force-recreate" not in e
    ]


def _force_recreates(rig: dict) -> list[str]:
    """Step 6a-bis: the force-recreate of rebuilt services compose-apply left
    on the previous image (normally none)."""
    return [e for e in _events(rig) if e.startswith("start-stack up") and "--force-recreate" in e]


class TestRecreateRebuilt:
    """Step 6a-bis when it cannot compare images: recreate every rebuilt service.

    This rig's clone has no ``deploy_health_gate.py``, so the recreate check has
    no plan and must fall back to what the step did unconditionally before
    2026-09-28 — a same-tag rebuild must never silently keep the old image.
    (``TestRecreateCheck`` below covers the normal path, where the plan exists
    and compose-apply has already recreated whatever changed.)
    """

    def test_rebuilt_services_are_force_recreated(self, tmp_path):
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig)
        recreates = _force_recreates(rig)
        assert recreates, (
            "without a recreate plan, a pass that rebuilt images must "
            "force-recreate them — unknown is recreated, never assumed current"
        )
        assert "--no-deps" in recreates[0], (
            "scope it: a blanket --force-recreate bounces the whole stack"
        )

    def test_the_apply_itself_still_runs_exactly_once(self, tmp_path):
        """The recreate is an ADDITIONAL invocation, not a second apply."""
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig)
        assert len(_ups(rig)) == 1


# docker, answering from a JSON scenario:
#   {"containers": {service: <docker inspect doc>},
#    "tags": {image ref: {"index": <image ID>, "manifest": <platform manifest>}},
#    "others": [{"service", "project", "doc"}],
#    "game_mode": {"parked": "<csv>"}}   (absent = game mode off)
# enough for the real deploy_health_gate.py (recreate-plan, snapshot, verify).
# "containers" belong to the stack's project (glad-labs-website); "others" are
# another project's containers with the same service label, newer, so an
# unscoped `docker ps` lists them first, as docker does.
_FAKE_DOCKER_PY = r"""#!/usr/bin/env python3
import hashlib, json, os, sys

args = sys.argv[1:]
with open(os.environ["EVENTS_FILE"], "a", encoding="utf-8") as fh:
    fh.write("docker " + " ".join(args) + "\n")
with open(os.environ["FAKE_DOCKER_SCENARIO"], encoding="utf-8") as fh:
    scenario = json.load(fh)
containers = scenario.get("containers", {})


def container_id(doc):
    return hashlib.sha256(doc["Name"].encode()).hexdigest()


def positional(rest):
    out, skip = [], False
    for a in rest:
        if skip:
            skip = False
        elif a in ("--platform", "--format", "--filter", "-f"):
            skip = True
        elif not a.startswith("-"):
            out.append(a)
    return out


others = scenario.get("others", [])
if args[:2] == ["ps", "-a"]:
    project = next((a.split("=", 2)[2] for a in args if a.startswith("label=com.docker.compose.project=")), None)
    for a in args:
        if a.startswith("label=com.docker.compose.service="):
            svc = a.split("=", 2)[2]
            for other in others:
                if other["service"] == svc and project in (None, other["project"]):
                    print(other["doc"]["Name"].lstrip("/"))
            doc = containers.get(svc)
            if doc and project in (None, "glad-labs-website"):
                print(doc["Name"].lstrip("/"))
    sys.exit(0)
if args[:2] == ["ps", "-q"]:  # no -a: running containers only (the liveness query)
    for a in args:
        if a.startswith("label=com.docker.compose.service="):
            doc = containers.get(a.split("=", 2)[2])
            if doc and doc["State"]["Running"]:
                print(container_id(doc))
    sys.exit(0)
if args[:1] == ["inspect"]:
    wanted = positional(args[1:])[0]
    for doc in [*containers.values(), *(o["doc"] for o in others)]:
        if wanted in (doc["Name"].lstrip("/"), container_id(doc)):
            if "{{.State.Running}}" in args:
                print("true" if doc["State"]["Running"] else "false")
            else:
                print(json.dumps([doc]))
            sys.exit(0)
    sys.exit(1)
if args[:2] == ["image", "inspect"]:
    ref = positional(args[2:])[0]
    tag = scenario.get("tags", {}).get(ref)
    if not tag:
        print("Error response from daemon: No such image: " + ref, file=sys.stderr)
        sys.exit(1)
    if "{{json .RepoTags}}" in args:
        print(json.dumps(sorted(t for t, v in scenario["tags"].items() if v == tag)))
    else:
        print(tag["manifest"] if "--platform" in args else tag["index"])
    sys.exit(0)
if args[:1] == ["tag"]:
    # Persisted, so the snapshot's rollback tag can be read back. A container's
    # own image id resolves only under "own_images_exist": on the host a rebuild
    # deletes it (see test_deploy_health_gate.py's StoreDocker).
    src, dst = positional(args[1:])[:2]
    tags = scenario.setdefault("tags", {})
    entry = tags.get(src)
    if entry is None and scenario.get("own_images_exist"):
        entry = next(({"index": d["Image"], "manifest": d["ImageManifestDescriptor"]["digest"]}
                      for d in containers.values() if d["Image"] == src), None)
    if entry is None:
        print("Error response from daemon: No such image: " + src, file=sys.stderr)
        sys.exit(1)
    tags[dst] = entry
    with open(os.environ["FAKE_DOCKER_SCENARIO"], "w", encoding="utf-8") as fh:
        json.dump(scenario, fh)
    sys.exit(0)
if args[:1] == ["container"]:
    sys.exit(1)  # the bounce loop finds nothing to restart; not under test here
if args[:1] == ["exec"]:
    # psql through the postgres container. Only game mode is modelled: the
    # scenario's "game_mode" is {"parked": "<csv of services>"} while it is on.
    sql = args[-1]
    game = scenario.get("game_mode")
    if game and "game_mode_until" in sql:
        print(1)
    elif game and "game_mode_parked_services" in sql:
        print(game["parked"])
    elif game and "game_mode_container_prefix" in sql:
        print("poindexter-")
    sys.exit(0)
sys.exit(0)
"""

_M1 = "sha256:" + "1" * 64  # the manifest before the rebuild
_M2 = "sha256:" + "2" * 64  # after a rebuild that changed the image
_NOW_OR_LATER = "2099-01-01T00:00:00Z"  # created during this pass, i.e. by compose-apply
_BEFORE_THE_PASS = "2026-09-20T08:00:00Z"


def _doc(
    name: str,
    ref: str,
    *,
    manifest: str,
    created: str,
    status: str = "running",
    started: str | None = None,
) -> dict:
    return {
        "Name": f"/{name}",
        "State": {
            "Status": status,
            "Running": status == "running",
            "Restarting": False,
            "StartedAt": started or created,
            "Health": {"Status": "healthy"},
        },
        "Created": created,
        "RestartCount": 0,
        "Config": {"Image": ref, "Healthcheck": {"Test": ["CMD", "true"]}},
        "Image": "sha256:" + "f" * 64,  # the index digest: different every build, never compared
        "ImageManifestDescriptor": {
            "digest": manifest,
            "platform": {"os": "linux", "architecture": "amd64"},
        },
    }


class _ScenarioRig:
    """The real driver and the real ``deploy_health_gate.py`` over a scenario docker."""

    def _rig(self, tmp_path: Path, scenario: dict, gate_source: str | None = None) -> dict:
        gate = (
            gate_source
            if gate_source is not None
            else (_repo_root() / "scripts" / "linux" / "deploy_health_gate.py").read_text(
                encoding="utf-8"
            )
        )
        rig = _build_rig(tmp_path, extra_files={"scripts/linux/deploy_health_gate.py": gate})
        for name, body in (
            ("docker", _FAKE_DOCKER_PY),
            # The gate needs 3.9+; the self-hosted runners' /usr/bin/python3 is
            # 3.8. Production runs the host's python3 (3.12).
            ("python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n'),
        ):
            f = rig["bin"] / name
            f.write_text(body, encoding="utf-8")
            f.chmod(0o755)
        rig["scenario"] = tmp_path / "scenario.json"
        rig["scenario"].write_text(json.dumps(scenario), encoding="utf-8")
        return rig

    def _sync(self, rig: dict, **env: str) -> subprocess.CompletedProcess:
        return _run_sync(rig, FAKE_DOCKER_SCENARIO=str(rig["scenario"]), **env)

    @staticmethod
    def _log(rig: dict) -> str:
        return (rig["home"] / ".poindexter" / "deploy-checkout-sync.log").read_text(
            encoding="utf-8"
        )


class TestRecreateCheck(_ScenarioRig):
    """Step 6a-bis with the real ``recreate-plan`` in the clone.

    compose-apply recreates a container whenever its rebuilt image's content
    changed (it compares platform manifests), so the check normally recreates
    nothing. Before 2026-09-28 the step force-recreated every rebuilt service
    unconditionally, which started each one compose had just recreated a
    second time — every brain deploy bounced the brain twice.
    """

    def test_what_compose_apply_already_recreated_is_not_recreated_again(self, tmp_path):
        """The 2026-09-27 deploy of 49d4052c7, minus the second bounce: the
        brain was recreated by compose-apply (new manifest, created during the
        pass) and auto-embed's rebuild changed nothing. Neither is touched."""
        rig = self._rig(
            tmp_path,
            {
                "containers": {
                    "brain-daemon": _doc(
                        "poindexter-brain-daemon",
                        "glad-labs-website-brain-daemon",
                        manifest=_M2,
                        created=_NOW_OR_LATER,
                    ),
                    "auto-embed": _doc(
                        "poindexter-auto-embed",
                        "glad-labs-website-auto-embed",
                        manifest=_M1,
                        created=_BEFORE_THE_PASS,
                    ),
                },
                "tags": {
                    "glad-labs-website-brain-daemon": {
                        "index": "sha256:new-index",
                        "manifest": _M2,
                    },
                    "glad-labs-website-auto-embed": {"index": "sha256:new-index", "manifest": _M1},
                },
            },
        )
        _advance_origin(rig, "src/cofounder_agent/poindexter/brain/probe.py")
        proc = self._sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _force_recreates(rig) == [], (
            "a service already on its new image must not start twice"
        )
        log = self._log(rig)
        assert (
            "not recreating brain-daemon: compose-apply already recreated poindexter-brain-daemon"
            in log
        )
        assert "not recreating auto-embed: poindexter-auto-embed already runs" in log
        st = _status(rig)
        assert st["result"] == "deployed"
        assert "rebuilt: auto-embed brain-daemon" in st["detail"]
        assert "recreated after compose-apply" not in st["detail"]
        assert "health gate: all healthy (auto-embed brain-daemon)" in log, "still gated"

    def test_a_service_compose_left_on_the_old_image_is_recreated_alone(self, tmp_path):
        """The guarantee from 2026-09-22 survives: a same-tag rebuild never
        silently keeps the old image — and only that service is touched."""
        rig = self._rig(
            tmp_path,
            {
                "containers": {
                    "brain-daemon": _doc(
                        "poindexter-brain-daemon",
                        "glad-labs-website-brain-daemon",
                        manifest=_M1,
                        created=_BEFORE_THE_PASS,
                    ),
                    "auto-embed": _doc(
                        "poindexter-auto-embed",
                        "glad-labs-website-auto-embed",
                        manifest=_M2,
                        created=_NOW_OR_LATER,
                    ),
                },
                "tags": {
                    "glad-labs-website-brain-daemon": {
                        "index": "sha256:new-index",
                        "manifest": _M2,
                    },
                    "glad-labs-website-auto-embed": {"index": "sha256:new-index", "manifest": _M2},
                },
            },
        )
        _advance_origin(rig, "src/cofounder_agent/poindexter/brain/probe.py")
        self._sync(rig)
        assert _force_recreates(rig) == [
            "start-stack up -d --no-build --no-deps --force-recreate brain-daemon"
        ]
        assert (
            "[WARN]   recreating brain-daemon: poindexter-brain-daemon runs 111111111111"
            in self._log(rig)
        )
        st = _status(rig)
        assert st["result"] == "deployed"
        assert "recreated after compose-apply: brain-daemon" in st["detail"], (
            "a repair here means compose did not do its job — it must show in the status, not only the log"
        )

    def test_a_parked_service_is_neither_recreated_nor_gated(self, tmp_path):
        """voice-agent-livekit sits `exited` with its profile off. Naming it in
        `up --force-recreate` enables the profile and starts it; gating it would
        read `exited` as a broken image and roll it back."""
        rig = self._rig(
            tmp_path,
            {
                "containers": {
                    "voice-agent-livekit": _doc(
                        "poindexter-voice-agent-livekit",
                        "glad-labs-website-voice-agent-livekit",
                        manifest=_M1,
                        created="2026-07-26T11:00:00Z",
                        status="exited",
                        started="2026-07-31T14:45:35.05520044Z",
                    ),
                },
                "tags": {
                    "glad-labs-website-voice-agent-livekit": {
                        "index": "sha256:new-index",
                        "manifest": _M2,
                    }
                },
            },
        )
        _advance_origin(rig, "scripts/Dockerfile.voice-agent")
        proc = self._sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not [
            e for e in _events(rig) if e.startswith("start-stack up") and "voice-agent-livekit" in e
        ]
        # The snapshot may tag <repo>:rollback-<svc> (that is preserving, taken
        # before the build); a rollback re-tags the LIVE ref.
        assert not [
            e
            for e in _events(rig)
            if e.startswith("docker tag ")
            and e.split()[-1] == "glad-labs-website-voice-agent-livekit"
        ], "no rollback of a parked service"
        # Only the snapshot's note may name it; the gate itself never watches it.
        assert not [
            line
            for line in self._log(rig).splitlines()
            if "health gate" in line
            and "voice-agent-livekit" in line
            and "for voice-agent-livekit:" not in line
        ]
        st = _status(rig)
        # voice-agent-claude-code builds the same Dockerfile and has no
        # container at all: parked too.
        assert st["result"] == "deployed"
        assert "left parked: voice-agent-claude-code voice-agent-livekit" in st["detail"]

    def test_every_pass_logs_what_each_rebuilt_service_could_roll_back_to(self, tmp_path):
        """The rollback path went unexercised from 2026-09-13 to 09-28, and every
        rollback in that time would have failed ("No such image"); nothing said
        so because none fired. The snapshot now keeps each running image under
        <repo>:rollback-<svc> before the build and the pass logs, per service,
        whether it could: a service it cannot roll back is a WARN up front, not
        a surprise at the moment of a failed deploy."""
        rig = self._rig(
            tmp_path,
            {
                "containers": {
                    # its own image id is gone (a no-op rebuild earlier), but the
                    # ref names the same content: preserved through the ref
                    "brain-daemon": _doc(
                        "poindexter-brain-daemon",
                        "glad-labs-website-brain-daemon",
                        manifest=_M1,
                        created=_BEFORE_THE_PASS,
                    ),
                    # runs M1, the ref names M2, no image holds M1: nothing to keep
                    "auto-embed": _doc(
                        "poindexter-auto-embed",
                        "glad-labs-website-auto-embed",
                        manifest=_M1,
                        created=_BEFORE_THE_PASS,
                    ),
                },
                "tags": {
                    "glad-labs-website-brain-daemon": {"index": "sha256:new-index", "manifest": _M1},
                    "glad-labs-website-auto-embed": {"index": "sha256:new-index", "manifest": _M2},
                },
            },
        )
        _advance_origin(rig, "src/cofounder_agent/poindexter/brain/probe.py")
        proc = self._sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        log = self._log(rig)
        assert (
            "[INFO] health gate: rollback image for brain-daemon: "
            "glad-labs-website-brain-daemon:rollback-brain-daemon (manifest 111111111111, "
            "via glad-labs-website-brain-daemon, which names the same content)"
        ) in log
        assert "[WARN] health gate: no rollback image for auto-embed: " in log
        assert "snapshot failed" not in log
        assert (
            "docker tag glad-labs-website-brain-daemon glad-labs-website-brain-daemon:rollback-brain-daemon"
            in _events(rig)
        )
        snap = json.loads(
            (rig["home"] / ".poindexter" / "deploy-gate-snapshot.json").read_text(encoding="utf-8")
        )
        assert snap["brain-daemon"]["rollback_ref"] == "glad-labs-website-brain-daemon:rollback-brain-daemon"
        assert snap["auto-embed"]["rollback_ref"] == ""
        assert "health gate: all healthy (auto-embed brain-daemon)" in log

    def test_another_projects_same_named_container_is_never_taken_for_the_stacks(self, tmp_path):
        """2026-09-28: a worktree's `seedorder-repro` project (the consumer
        compose file, so the same service names) left an exited brain-daemon
        container NEWER than the stack's, which `docker ps` lists first.
        Unscoped, the snapshot recorded it, the recreate check saw "2
        containers" and bounced the stack's brain "to be safe", and the gate
        watched the exited one: a failed deploy, rolled back."""
        scenario = {
            "containers": {
                "brain-daemon": _doc(
                    "poindexter-brain-daemon",
                    "glad-labs-website-brain-daemon",
                    manifest=_M2,
                    created=_NOW_OR_LATER,
                ),
                "auto-embed": _doc(
                    "poindexter-auto-embed",
                    "glad-labs-website-auto-embed",
                    manifest=_M2,
                    created=_NOW_OR_LATER,
                ),
            },
            "tags": {
                "glad-labs-website-brain-daemon": {"index": "sha256:new-index", "manifest": _M2},
                "glad-labs-website-auto-embed": {"index": "sha256:new-index", "manifest": _M2},
            },
            "others": [
                {
                    "service": "brain-daemon",
                    "project": "seedorder-repro",
                    "doc": _doc(
                        "seedorder-repro-brain",
                        "seedorder-repro-brain-daemon",
                        manifest=_M1,
                        created=_NOW_OR_LATER,
                        status="exited",
                    ),
                }
            ],
        }
        rig = self._rig(tmp_path, scenario)
        _advance_origin(rig, "src/cofounder_agent/poindexter/brain/probe.py")
        proc = self._sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _force_recreates(rig) == [], "the stack's brain was already on its new image"
        log = self._log(rig)
        assert "health gate: all healthy (auto-embed brain-daemon)" in log
        assert "seedorder-repro" not in log
        snap = json.loads(
            (rig["home"] / ".poindexter" / "deploy-gate-snapshot.json").read_text(encoding="utf-8")
        )
        assert snap["brain-daemon"]["container"] == "poindexter-brain-daemon"
        assert not [e for e in _events(rig) if e.startswith("docker tag") and "seedorder" in e]
        lookups = [e for e in _events(rig) if e.startswith("docker ps -a ")]
        assert lookups and all("label=com.docker.compose.project=glad-labs-website" in e for e in lookups)

    def test_a_failed_recreate_withholds_the_marker(self, tmp_path):
        """Otherwise the pass records `deployed` over a service still on the
        old image, and the next pass has nothing left to retry."""
        rig = self._rig(
            tmp_path,
            {
                "containers": {
                    "brain-daemon": _doc(
                        "poindexter-brain-daemon",
                        "glad-labs-website-brain-daemon",
                        manifest=_M1,
                        created=_BEFORE_THE_PASS,
                    ),
                    "auto-embed": _doc(
                        "poindexter-auto-embed",
                        "glad-labs-website-auto-embed",
                        manifest=_M2,
                        created=_NOW_OR_LATER,
                    ),
                },
                "tags": {
                    "glad-labs-website-brain-daemon": {
                        "index": "sha256:new-index",
                        "manifest": _M2,
                    },
                    "glad-labs-website-auto-embed": {"index": "sha256:new-index", "manifest": _M2},
                },
            },
        )
        head = _advance_origin(rig, "src/cofounder_agent/poindexter/brain/probe.py")
        proc = self._sync(rig, FORCE_RECREATE_EXIT="1")
        assert proc.returncode == 1
        st = _status(rig)
        assert st["result"] == "error" and "recreate-rebuilt" in st["detail"]
        marker = rig["home"] / ".poindexter" / "deploy-last-restarted-sha"
        assert marker.read_text(encoding="utf-8").strip() != head

    def test_a_plan_that_cannot_run_recreates_every_live_rebuilt_service(self, tmp_path):
        """Fail-safe in one direction: no plan means the pre-2026-09-28
        behaviour for every service that is running, never "assume current".
        A parked one is still never named (test_deploy_checkout_sync_recreate_scope)."""
        rig = self._rig(
            tmp_path,
            {
                "containers": {
                    "brain-daemon": _doc(
                        "poindexter-brain-daemon",
                        "glad-labs-website-brain-daemon",
                        manifest=_M1,
                        created=_BEFORE_THE_PASS,
                    ),
                    "auto-embed": _doc(
                        "poindexter-auto-embed",
                        "glad-labs-website-auto-embed",
                        manifest=_M1,
                        created=_BEFORE_THE_PASS,
                    ),
                },
                "tags": {},
            },
            gate_source="import sys\nsys.exit(3)\n",
        )
        _advance_origin(rig, "src/cofounder_agent/poindexter/brain/probe.py")
        self._sync(rig)
        assert _force_recreates(rig) == [
            "start-stack up -d --no-build --no-deps --force-recreate auto-embed brain-daemon"
        ]
        assert "recreate check: could not compare images" in self._log(rig)


class TestGameModeReparkIsScopedToTheStack(_ScenarioRig):
    """Step 6c resolves each parked sidecar by its compose service label, then
    `docker stop`s it. Found by the label alone it is found in every project on
    the host, newest first: a throwaway project's running `image-gen-server`
    would be stopped in place of the stack's, which would stay warm on the GPU
    for the rest of the game, the very thing the step exists to prevent."""

    @staticmethod
    def _scenario() -> dict:
        return {
            "containers": {
                "image-gen-server": _doc(
                    "poindexter-image-gen-server",
                    "glad-labs-website-image-gen-server",
                    manifest=_M1,
                    created=_BEFORE_THE_PASS,
                ),
            },
            "tags": {},
            "others": [
                {
                    "service": "image-gen-server",
                    "project": "seedorder-repro",
                    "doc": _doc(
                        "seedorder-repro-image-gen",
                        "seedorder-repro-image-gen-server",
                        manifest=_M1,
                        created=_NOW_OR_LATER,  # newer, so an unscoped `docker ps` lists it first
                    ),
                }
            ],
            "game_mode": {"parked": "image-gen-server"},
        }

    @staticmethod
    def _stops(rig: dict) -> list[str]:
        return [e for e in _events(rig) if e.startswith("docker stop ")]

    def test_the_stack_is_repark_stopped_and_the_other_project_is_left_alone(self, tmp_path):
        rig = self._rig(tmp_path, self._scenario())
        _advance_origin(rig, "docs/note.md")  # no rebuild: the step runs on every pass
        proc = self._sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert self._stops(rig) == ["docker stop -t 20 poindexter-image-gen-server"]
        assert "game mode active: re-parked poindexter-image-gen-server" in self._log(rig)
        lookups = [e for e in _events(rig) if e.startswith("docker ps -a ")]
        assert lookups and all(
            "label=com.docker.compose.project=glad-labs-website" in e for e in lookups
        )

    def test_the_project_is_resolved_once_however_many_steps_use_it(self, tmp_path):
        """A pass that rebuilds AND re-parks needs the name twice."""
        rig = self._rig(tmp_path, self._scenario())
        _advance_origin(rig, "src/cofounder_agent/poindexter/brain/probe.py")
        proc = self._sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        configs = [e for e in _events(rig) if e.startswith("start-stack config ")]
        assert configs == ["start-stack config --format json --no-interpolate"]
        assert self._stops(rig) == ["docker stop -t 20 poindexter-image-gen-server"]

    def test_an_unresolvable_project_warns_once_and_never_fails_the_pass(self, tmp_path):
        scenario = self._scenario()
        scenario["others"] = []  # nothing to confuse it: this is the fallback path
        rig = self._rig(tmp_path, scenario)
        _advance_origin(rig, "src/cofounder_agent/poindexter/brain/probe.py")
        proc = self._sync(rig, FAKE_COMPOSE_PROJECT="")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        log = self._log(rig)
        assert log.count("[WARN] could not resolve the stack's compose project") == 1
        assert self._stops(rig) == ["docker stop -t 20 poindexter-image-gen-server"], (
            "unscoped is the old behaviour, not a reason to skip the re-park"
        )


class TestApplyRetry:
    def test_single_apply_when_it_succeeds_first_time(self, tmp_path):
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig)
        assert len(_ups(rig)) == 1, "a healthy apply must not be run twice"
        assert _status(rig)["result"] == "deployed"

    def test_transient_failure_is_retried_and_recovers(self, tmp_path):
        """The live shape: attempt 1 loses the dependency race, attempt 2 wins.
        The pass must then be a normal success — marker recorded, no error."""
        rig = _build_rig(tmp_path)
        head = _advance_origin(rig)
        proc = _run_sync(rig, UP_FAIL_TIMES="1")
        assert len(_ups(rig)) == 2, "a failed apply must be retried once"
        assert proc.returncode == 0
        st = _status(rig)
        assert st["result"] == "deployed"
        assert st["head"] == head
        marker = rig["home"] / ".poindexter" / "deploy-last-restarted-sha"
        assert marker.read_text(encoding="utf-8").strip() == head

    def test_persistent_failure_reports_and_withholds_marker(self, tmp_path):
        rig = _build_rig(tmp_path)
        head = _advance_origin(rig)
        proc = _run_sync(rig, UP_FAIL_TIMES="99")
        assert len(_ups(rig)) == 2, "retry is bounded at two attempts"
        assert proc.returncode == 1
        st = _status(rig)
        assert st["result"] == "error"
        assert "compose-apply" in st["detail"]
        marker = rig["home"] / ".poindexter" / "deploy-last-restarted-sha"
        assert marker.read_text(encoding="utf-8").strip() != head, (
            "a failed pass must retry next cycle, not record the marker"
        )


class TestStrandedSweep:
    def test_created_containers_are_started(self, tmp_path):
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig, STRANDED_NAMES="poindexter-worker,poindexter-grafana")
        started = [e for e in _events(rig) if e.startswith("docker start ")]
        assert started == [
            "docker start poindexter-worker",
            "docker start poindexter-grafana",
        ]

    def test_recovery_is_reported_even_on_a_clean_pass(self, tmp_path):
        """A silent self-heal is how a recurring fault stays invisible."""
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig, STRANDED_NAMES="poindexter-worker")
        st = _status(rig)
        assert st["result"] == "deployed"
        assert "recovered stranded: poindexter-worker" in st["detail"]

    def test_nothing_started_when_nothing_is_stranded(self, tmp_path):
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig)
        assert not [e for e in _events(rig) if e.startswith("docker start ")]

    def test_sweep_asks_only_for_created_state(self, tmp_path):
        """Guards the safety filter itself. Parked services (voice-agent) are
        listed by ``compose ps -a`` as ``exited``; querying anything broader
        than ``--status=created`` would un-park them."""
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig)
        ps_calls = [e for e in _events(rig) if e.startswith("start-stack ps")]
        sweep = [c for c in ps_calls if "{{.Name}}" in c]
        assert sweep, "the sweep must query compose for stranded containers"
        for call in sweep:
            assert "--status=created" in call
            assert "--status=exited" not in call
        # The only other compose query is 6a-bis's liveness read. It must list
        # RUNNING service containers only: `-a` or a status filter would count
        # a parked service's exited container as live, and 6a-bis would then
        # name it in a recreate, which starts it.
        for call in (c for c in ps_calls if c not in sweep):
            argv = call.split()
            assert argv[:3] == ["start-stack", "ps", "-q"], call
            assert not {"-a", "--all"} & set(argv) and "--status" not in call, call

    def test_sweep_runs_even_when_apply_failed(self, tmp_path):
        """The sweep is the safety net FOR a failed apply — if it only ran on
        success it would be absent exactly when it is needed."""
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig, UP_FAIL_TIMES="99", STRANDED_NAMES="poindexter-worker")
        assert "docker start poindexter-worker" in _events(rig)

    def test_failed_start_is_surfaced(self, tmp_path):
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        proc = _run_sync(
            rig,
            STRANDED_NAMES="poindexter-worker",
            FAKE_DOCKER_START_EXIT="1",
        )
        assert proc.returncode == 1
        assert "stranded-start" in _status(rig)["detail"]
