"""``deploy-checkout-sync.sh`` never names, recreates or gates a parked service.

Naming a service on ``docker compose up`` STARTS it, whatever its compose
``profiles:`` say (verified on compose 5.5.1: ``up -d --no-deps
--force-recreate <svc>`` recreated and started a stopped service whose profile
was inactive). ``voice-agent-livekit`` is in REBUILD_MAP and parked (``voice``
out of ``compose_profiles`` since 2026-08-19), so an edit to
``scripts/Dockerfile.voice-agent`` could un-park voice. stack#4153 made step
6a-bis ask a recreate plan, which calls such a service parked. The plan is not
the whole path, though. When it cannot run, the step recreates every rebuilt
service "to be safe". When a docker call fails, or two containers answer to
the label, the plan itself says "recreate to be safe". When compose-apply
fails there is no plan at all, and the health gate then watches the parked
service and rolls it back with ``up --force-recreate``.

So liveness decides first, from ``start-stack.sh ps -q <svc>``, which is
project-scoped and lists running service containers only, never ``compose
run`` one-offs. A rebuilt service is live if it was running before the pass
touched it OR after compose-apply. Only live services reach the plan, the
recreate and the gate. Run against the real script with a toy compose (below),
so "did the pass start it?" is an observable container state, not a recorded
argv. Contract:

- every rebuilt service is still BUILT;
- a live service the plan says is on the old image is force-recreated (the
  2026-09-22 guarantee);
- a service stopped before the pass and after compose-apply is never named,
  never asked about, never gated, and is reported, on every path: plan
  failed, plan says recreate, compose-apply failed;
- either liveness check alone loses a service (``TestLiveMeansBeforeOrAfter``);
- a state that cannot be read starts nothing and fails the pass loudly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("bash", "git", "sha256sum")),
    reason="needs bash + git + coreutils",
)

_GIT_ID = ("-c", "user.name=t", "-c", "user.email=t@example.com")

# Build inputs that land a service in $rebuild_services, taken from the real
# REBUILD_MAP (the script under test is the real one, so these must match it).
_BRAIN_SRC = "src/cofounder_agent/poindexter/brain/probe.py"  # brain-daemon + auto-embed
_VOICE_DOCKERFILE = "scripts/Dockerfile.voice-agent"  # both voice agents
_CHATTERBOX_SRC = "scripts/tts_sidecars/server.py"  # chatterbox
_WORKER_LOCK = "src/cofounder_agent/poetry.lock"  # every Dockerfile.worker service


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "deploy-checkout-sync.sh").exists()
    )


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *_GIT_ID, *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


# A toy compose. Each service's container state is the file $STATE_DIR/<svc>:
# "running", "exited", or "oneoff" (a running `compose run` container). No file
# means no container. It behaves like compose where the script relies on it:
#   - `up` WITH service names starts every one it names, profile or not. That
#     is the premise under test, verified on compose 5.5.1.
#   - `up` with NO names is compose-apply. APPLY_STARTS lists the services it
#     starts (an active-profile service parked by game mode); APPLY_KILLS lists
#     the ones whose recreated container it leaves dead; APPLY_EXIT fails it.
#   - `ps <svc>` lists running service containers. `-a` adds stopped AND
#     one-off containers, exactly as compose does, so a script that asked with
#     `-a` would be caught here and not only on the host. PS_LISTS_STOPPED=1
#     makes plain `ps` list stopped containers too.
#   - PS_FAIL makes `ps` fail for the services it lists.
_FAKE_START_STACK = r"""#!/usr/bin/env bash
echo "start-stack $*" >> "${EVENTS_FILE:-/dev/null}"
action="${1:-}"; shift || true
case "$action" in
  up)
    svcs=()
    for a in "$@"; do case "$a" in -*) ;; *) svcs+=("$a") ;; esac; done
    if [ "${#svcs[@]}" -eq 0 ]; then
      [ -n "${APPLY_EXIT:-}" ] && exit "$APPLY_EXIT"
      for s in ${APPLY_STARTS:-}; do printf running > "$STATE_DIR/$s"; done
      for s in ${APPLY_KILLS:-}; do printf exited > "$STATE_DIR/$s"; done
      exit 0
    fi
    for s in "${svcs[@]}"; do printf running > "$STATE_DIR/$s"; done
    exit 0 ;;
  config)  # the compose project the health gate is scoped to
    printf '{"name": "%s"}\n' "${FAKE_COMPOSE_PROJECT-glad-labs-website}"
    exit 0 ;;
  ps)
    case "$*" in *--status=created*) exit 0 ;; esac  # the stranded sweep: nothing stranded
    all=0; svcs=()
    for a in "$@"; do
      case "$a" in -a|--all) all=1 ;; -*) ;; *) svcs+=("$a") ;; esac
    done
    for s in "${svcs[@]}"; do
      for f in ${PS_FAIL:-}; do
        [ "$f" = "$s" ] && { echo "boom: cannot list $s" >&2; exit 1; }
      done
      case "$(cat "$STATE_DIR/$s" 2>/dev/null)" in
        running) ;;
        exited) [ "$all" = 1 ] || [ "${PS_LISTS_STOPPED:-0}" = 1 ] || continue ;;
        oneoff) [ "$all" = 1 ] || continue ;;
        *) continue ;;
      esac
      id="$(printf '%s' "$s" | sha256sum | cut -c1-64)"
      printf '%s' "$s" > "$STATE_DIR/.id-$id"
      echo "$id"
    done
    exit 0 ;;
esac
exit 0
"""

# `docker inspect -f '{{.State.Running}}' <id>` answers from the same state, so
# a container the pass started reads as running afterwards. `docker container
# inspect` (the bounce loop) fails: RESTART_CONTAINERS are absent here, which
# keeps bounced containers out of the health gate's unit list.
_FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "docker $*" >> "$EVENTS_FILE"
case "${1:-}" in
  inspect)
    id="${@: -1}"
    s="$(cat "$STATE_DIR/.id-$id" 2>/dev/null)" || { echo "No such object: $id" >&2; exit 1; }
    case "$(cat "$STATE_DIR/$s" 2>/dev/null)" in running|oneoff) echo true ;; *) echo false ;; esac
    exit 0 ;;
  container) exit 1 ;;
esac
exit 0
"""

# Stands in for scripts/linux/deploy_health_gate.py in the deploy clone. It
# records how it was called. `recreate-plan` answers "recreate" for the
# services in PLAN_RECREATE (the verdict the real plan gives for a stale image,
# AND for the paths it cannot see through — a failed docker call, two
# containers on one label), and "skip" for the rest (compose-apply already
# recreated them). PLAN_FAIL=1 makes it unable to run. `verify` says healthy.
_FAKE_GATE = """import os, sys
with open(os.environ["EVENTS_FILE"], "a", encoding="utf-8") as fh:
    fh.write("gate " + " ".join(sys.argv[1:]) + "\\n")
if sys.argv[1] == "recreate-plan":
    if os.environ.get("PLAN_FAIL"):
        sys.exit(3)
    stale = os.environ.get("PLAN_RECREATE", "").split()
    for svc in sys.argv[sys.argv.index("--services") + 1:]:
        action = "recreate" if svc in stale else "skip"
        print(f"{action}\\t{svc}\\tfake plan says {action}")
    sys.exit(0)
print("{}")
"""


def _build_rig(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    (home / ".poindexter").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "containers"
    state.mkdir()

    def fake(name: str, body: str) -> None:
        f = bin_dir / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)

    fake("docker", _FAKE_DOCKER)
    fake("systemctl", "#!/usr/bin/env bash\nexit 1\n")  # connector unit absent
    fake("sudo", '#!/usr/bin/env bash\nwhile [[ "${1:-}" == -* ]]; do shift; done\nexec "$@"\n')

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, timeout=60)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    stack = seed / "scripts" / "start-stack.sh"
    stack.parent.mkdir(parents=True)
    stack.write_text(_FAKE_START_STACK, encoding="utf-8")
    stack.chmod(0o755)
    gate = seed / "scripts" / "linux" / "deploy_health_gate.py"
    gate.parent.mkdir(parents=True)
    gate.write_text(_FAKE_GATE, encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "A")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    base_sha = _git(seed, "rev-parse", "HEAD")

    clone = tmp_path / "deploy-clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, timeout=60)
    (home / ".poindexter" / "deploy-last-restarted-sha").write_text(base_sha, encoding="utf-8")
    return {
        "home": home, "bin": bin_dir, "state": state, "events": tmp_path / "events",
        "seed": seed, "clone": clone, "base_sha": base_sha,
    }


def _advance_origin(rig: dict, *paths: str) -> str:
    seed = rig["seed"]
    for rel in paths:
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# changed {rel}\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "B")
    _git(seed, "push", "-q", "origin", "main")
    return _git(seed, "rev-parse", "HEAD")


def _containers(rig: dict, states: dict[str, str]) -> None:
    for svc, st in states.items():
        (rig["state"] / svc).write_text(st, encoding="utf-8")


def _run_sync(rig: dict, **env_extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_repo_root() / "scripts" / "linux" / "deploy-checkout-sync.sh"), "--no-flow-check"],
        env={
            "PATH": f"{rig['bin']}:/usr/bin:/bin",
            "HOME": str(rig["home"]),
            "POINDEXTER_DEPLOY_ROOT": str(rig["clone"]),
            "EVENTS_FILE": str(rig["events"]),
            "STATE_DIR": str(rig["state"]),
            "SYNC_APPLY_RETRY_SETTLE_SEC": "0",
            **env_extra,
        },
        capture_output=True, text=True, timeout=180,
    )


def _events(rig: dict) -> list[str]:
    f = rig["events"]
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def _names(event: str) -> set[str]:
    """Service names on a `start-stack <action> …` line (flags dropped)."""
    return {t for t in event.split()[2:] if not t.startswith("-")}


def _built(rig: dict) -> set[str]:
    return set().union(*(_names(e) for e in _events(rig) if e.startswith("start-stack build ")))


def _recreated(rig: dict) -> set[str]:
    return set().union(*(
        _names(e) for e in _events(rig) if e.startswith("start-stack up") and "--force-recreate" in e
    ))


def _ups_naming(rig: dict, svc: str) -> list[str]:
    """Every `up` that names ``svc``: any of them starts it."""
    return [e for e in _events(rig) if e.startswith("start-stack up") and svc in _names(e)]


def _gate_services(rig: dict, subcommand: str) -> list[set[str]]:
    """The --services of each health-gate call of ``subcommand``."""
    return [
        set(e.split("--services", 1)[1].split())
        for e in _events(rig) if e.startswith(f"gate {subcommand} ")
    ]


def _state(rig: dict, svc: str) -> str | None:
    f = rig["state"] / svc
    return f.read_text(encoding="utf-8") if f.exists() else None


def _status(rig: dict) -> dict:
    return json.loads(
        (rig["home"] / ".poindexter" / "deploy-checkout-sync.status.json").read_text(encoding="utf-8")
    )


def _marker(rig: dict) -> str:
    return (rig["home"] / ".poindexter" / "deploy-last-restarted-sha").read_text(encoding="utf-8").strip()


def _parked_voice_and_live_brain(rig: dict) -> None:
    _containers(rig, {
        "brain-daemon": "running", "auto-embed": "running", "voice-agent-livekit": "exited",
    })
    _advance_origin(rig, _BRAIN_SRC, _VOICE_DOCKERFILE)


class TestNeverNameAParkedService:
    def test_a_live_service_left_on_the_old_image_is_force_recreated(self, tmp_path):
        """The 2026-09-22 guarantee survives: a same-tag rebuild never keeps
        the old image silently."""
        rig = _build_rig(tmp_path)
        _containers(rig, {"brain-daemon": "running", "auto-embed": "running"})
        _advance_origin(rig, _BRAIN_SRC)
        proc = _run_sync(rig, PLAN_RECREATE="brain-daemon")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert {"brain-daemon", "auto-embed"} <= _built(rig)
        assert _recreated(rig) == {"brain-daemon"}
        recreate = next(e for e in _events(rig) if "--force-recreate" in e)
        assert "--no-deps" in recreate, "scoped: a blanket --force-recreate bounces the whole stack"
        assert _status(rig)["result"] == "deployed"

    def test_a_stopped_rebuilt_service_is_built_but_never_reaches_the_plan(self, tmp_path):
        """voice-agent-livekit is parked (exited, profile inactive). An edit to
        its Dockerfile refreshes the image and leaves voice parked — decided
        before the plan, so no verdict of the plan's can start it."""
        rig = _build_rig(tmp_path)
        _containers(rig, {"voice-agent-livekit": "exited"})
        _advance_origin(rig, _VOICE_DOCKERFILE)
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "voice-agent-livekit" in _built(rig), "the image must still be rebuilt"
        assert _ups_naming(rig, "voice-agent-livekit") == []
        assert _state(rig, "voice-agent-livekit") == "exited", "the deploy un-parked voice"
        assert not any("voice-agent-livekit" in s for s in _gate_services(rig, "recreate-plan"))
        assert _status(rig)["result"] == "deployed"

    def test_when_the_plan_cannot_run_only_live_services_are_recreated(self, tmp_path):
        """stack#4153's fallback: a plan that cannot run recreates every rebuilt
        service "to be safe". Right for a running one (never assume current);
        for a parked one it is the un-park."""
        rig = _build_rig(tmp_path)
        _parked_voice_and_live_brain(rig)
        proc = _run_sync(rig, PLAN_FAIL="1")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _recreated(rig) == {"brain-daemon", "auto-embed"}
        assert _ups_naming(rig, "voice-agent-livekit") == []
        assert _state(rig, "voice-agent-livekit") == "exited"

    def test_a_plan_verdict_of_recreate_cannot_reach_a_parked_service(self, tmp_path):
        """The plan says "recreate to be safe" whenever it cannot see: a failed
        docker call, or two containers on one service label. Liveness is read
        first, so that verdict never meets a parked service."""
        rig = _build_rig(tmp_path)
        _parked_voice_and_live_brain(rig)
        proc = _run_sync(rig, PLAN_RECREATE="voice-agent-livekit brain-daemon")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _recreated(rig) == {"brain-daemon"}
        assert _state(rig, "voice-agent-livekit") == "exited"

    def test_a_parked_service_is_reported_not_silently_skipped(self, tmp_path):
        rig = _build_rig(tmp_path)
        _containers(rig, {"voice-agent-livekit": "exited"})
        _advance_origin(rig, _VOICE_DOCKERFILE)
        proc = _run_sync(rig)
        # voice-agent-claude-code shares the Dockerfile and has no container.
        assert "left parked: voice-agent-claude-code voice-agent-livekit" in _status(rig)["detail"]
        assert "not recreating voice-agent-livekit: not running before this pass" in proc.stdout

    def test_a_ps_that_also_lists_stopped_containers_does_not_fool_it(self, tmp_path):
        """If a compose's plain `ps` listed stopped containers too, as `ps -a`
        does, a listed id would not prove the service is running. docker
        inspect still reads the parked container as not running."""
        rig = _build_rig(tmp_path)
        _containers(rig, {"voice-agent-livekit": "exited"})
        _advance_origin(rig, _VOICE_DOCKERFILE)
        proc = _run_sync(rig, PS_LISTS_STOPPED="1", PLAN_RECREATE="voice-agent-livekit")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _ups_naming(rig, "voice-agent-livekit") == []
        assert _state(rig, "voice-agent-livekit") == "exited"

    def test_a_running_one_off_container_does_not_make_its_service_live(self, tmp_path):
        """demo-recorder runs as `compose run --rm` (a one-off). A bake in
        flight is not the service running: recreating it would start a second,
        service-mode container of a batch job. `ps -q` leaves one-offs out;
        `ps -a -q` would not."""
        rig = _build_rig(tmp_path)
        _containers(rig, {
            "worker": "running", "prefect-worker": "running", "pipeline-bot": "running",
            "demo-recorder": "oneoff",
        })
        _advance_origin(rig, _WORKER_LOCK)
        proc = _run_sync(rig, PLAN_RECREATE="demo-recorder")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "demo-recorder" in _built(rig)
        assert _ups_naming(rig, "demo-recorder") == []
        assert _recreated(rig) == set()


class TestProfileGatedServicesAreRebuiltAndLeftStopped:
    """demo-recorder was kept out of REBUILD_MAP (stack#4144) because the
    recreate step would have started it, and voice-agent-claude-code builds the
    same Dockerfile as voice-agent-livekit but was never named. Both are
    profile-gated with no container on the operator host: rebuilt, never
    started."""

    @pytest.mark.parametrize(
        ("changed", "service"),
        [
            (_WORKER_LOCK, "demo-recorder"),
            (_VOICE_DOCKERFILE, "voice-agent-livekit"),
            (_VOICE_DOCKERFILE, "voice-agent-claude-code"),
        ],
    )
    def test_built_and_not_started(self, tmp_path, changed, service):
        rig = _build_rig(tmp_path)
        _advance_origin(rig, changed)
        proc = _run_sync(rig, PLAN_FAIL="1")  # even with the plan's fallback in play
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert service in _built(rig), f"{changed} changed and {service} was not rebuilt"
        assert _ups_naming(rig, service) == [], f"{service} was started by the deploy"
        assert _state(rig, service) is None


class TestLiveMeansBeforeOrAfterTheApply:
    """Either checkpoint on its own loses a service."""

    def test_a_service_the_apply_started_is_live(self, tmp_path):
        """"Before" alone would call it parked. chatterbox is parked by game
        mode, but its profile is active, so compose-apply starts it. It goes to
        the plan and in front of the gate. Step 6c parks it again afterwards."""
        rig = _build_rig(tmp_path)
        _containers(rig, {"chatterbox": "exited"})
        _advance_origin(rig, _CHATTERBOX_SRC)
        proc = _run_sync(rig, APPLY_STARTS="chatterbox")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _gate_services(rig, "recreate-plan") == [{"chatterbox"}]
        assert _gate_services(rig, "verify") == [{"chatterbox"}]

    def test_a_service_that_died_after_the_apply_is_still_gated(self, tmp_path):
        """"After" alone would call it parked. brain-daemon was running,
        compose-apply recreated it onto the new image, and it died. It must
        stay in front of the health gate, which rolls it back and pages.
        Otherwise it would sit dead behind a pass logged as clean."""
        rig = _build_rig(tmp_path)
        _containers(rig, {"brain-daemon": "running", "auto-embed": "running"})
        _advance_origin(rig, _BRAIN_SRC)
        proc = _run_sync(rig, APPLY_KILLS="brain-daemon")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _gate_services(rig, "verify") == [{"brain-daemon", "auto-embed"}]


class TestHealthGateScope:
    def test_the_gate_watches_only_live_services(self, tmp_path):
        rig = _build_rig(tmp_path)
        _parked_voice_and_live_brain(rig)
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _gate_services(rig, "verify") == [{"brain-daemon", "auto-embed"}]

    def test_a_failed_compose_apply_still_keeps_a_parked_service_out_of_the_gate(self, tmp_path):
        """No plan runs when compose-apply failed, so nothing reported voice as
        parked. The gate then read its `exited` as a failed deploy, and its
        rollback (`up --force-recreate`) would have started it."""
        rig = _build_rig(tmp_path)
        _parked_voice_and_live_brain(rig)
        proc = _run_sync(rig, APPLY_EXIT="1")
        assert proc.returncode == 1, "a failed apply still fails the pass"
        assert _gate_services(rig, "verify") == [{"brain-daemon", "auto-embed"}]
        assert _ups_naming(rig, "voice-agent-livekit") == []

    def test_nothing_live_means_nothing_gated(self, tmp_path):
        rig = _build_rig(tmp_path)
        _containers(rig, {"voice-agent-livekit": "exited"})
        _advance_origin(rig, _VOICE_DOCKERFILE)
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _gate_services(rig, "verify") == []


class TestUnreadableState:
    def test_an_unreadable_state_starts_nothing_and_withholds_the_marker(self, tmp_path):
        """No readable state is not "stopped" and not "running". The pass
        starts nothing it cannot see, says why, and retries on the next cycle."""
        rig = _build_rig(tmp_path)
        _containers(rig, {"voice-agent-livekit": "exited"})
        _advance_origin(rig, _VOICE_DOCKERFILE)
        proc = _run_sync(rig, PS_FAIL="voice-agent-livekit", PLAN_FAIL="1")
        assert proc.returncode == 1
        assert _ups_naming(rig, "voice-agent-livekit") == []
        assert _state(rig, "voice-agent-livekit") == "exited"
        assert _gate_services(rig, "verify") == []
        st = _status(rig)
        assert st["result"] == "error"
        assert "service-state" in st["detail"]
        assert _marker(rig) == rig["base_sha"], "a pass that could not decide must retry"


class TestGateIsScopedToTheStacksProject:
    """Every health-gate call names the stack's compose project, resolved by
    compose itself through start-stack.sh. Unscoped, the gate took the newest
    container with the service label from ANY project: on 2026-09-28 a
    worktree's `seedorder-repro` (the consumer compose file) left exited
    brain-daemon and worker containers newer than the stack's."""

    @staticmethod
    def _gate_calls(rig: dict) -> list[str]:
        return [e for e in _events(rig) if e.startswith("gate ")]

    def test_every_gate_call_carries_the_project(self, tmp_path):
        rig = _build_rig(tmp_path)
        _containers(rig, {"brain-daemon": "running", "auto-embed": "running"})
        _advance_origin(rig, _BRAIN_SRC)
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        calls = self._gate_calls(rig)
        assert {c.split()[1] for c in calls} == {"snapshot", "recreate-plan", "verify"}
        for call in calls:
            assert "--project glad-labs-website " in call, call
        assert "start-stack config --format json --no-interpolate" in _events(rig), (
            "the name comes from compose (start-stack.sh), with nothing interpolated"
        )

    @pytest.mark.parametrize("project", ["", "Grafana dashboard links will point at: x"],
                             ids=["empty", "not-a-project-name"])
    def test_an_unresolvable_project_is_said_and_the_gate_runs_unscoped(self, tmp_path, project):
        rig = _build_rig(tmp_path)
        _containers(rig, {"brain-daemon": "running", "auto-embed": "running"})
        _advance_origin(rig, _BRAIN_SRC)
        proc = _run_sync(rig, FAKE_COMPOSE_PROJECT=project)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        calls = self._gate_calls(rig)
        assert calls and not [c for c in calls if "--project glad-labs-website" in c]
        # the recorder joins argv with spaces: an empty --project value shows as two
        assert all(" --project  " in c for c in calls), calls
        log = (rig["home"] / ".poindexter" / "deploy-checkout-sync.log").read_text(encoding="utf-8")
        assert "[WARN] could not resolve the stack's compose project" in log
