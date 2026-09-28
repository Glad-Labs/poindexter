"""deploy_health_gate — watch a rebuilt service come up, roll back when it does not."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
GATE = REPO_ROOT / "scripts" / "linux" / "deploy_health_gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("deploy_health_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _inspect_json(*, status="running", restarting=False, restarts=0, health="healthy", has_hc=True, image_id="sha256:new"):
    return json.dumps([{
        "State": {"Status": status, "Restarting": restarting, "ExitCode": 1 if restarting else 0,
                  "StartedAt": "t", **({"Health": {"Status": health}} if has_hc else {})},
        "RestartCount": restarts,
        "Config": {"Image": "glad-labs-website-x", **({"Healthcheck": {"Test": ["CMD", "true"]}} if has_hc else {})},
        "Image": image_id,
    }])


class FakeDocker:
    """argv -> (rc, out, err); `inspect` answers come from a queue so a service can change over polls."""

    def __init__(self, inspects: list[str]):
        self.inspects = list(inspects)
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            return 0, "poindexter-x\n", ""
        if argv[:2] == ["docker", "inspect"]:
            body = self.inspects.pop(0) if len(self.inspects) > 1 else self.inspects[0]
            return 0, body, ""
        if argv[:2] == ["docker", "logs"]:
            return 0, "Traceback\nModuleNotFoundError: No module named '_voice_paths'\n", ""
        if argv[:2] == ["docker", "tag"]:
            return 0, "", ""
        if argv[:2] == ["docker", "exec"]:
            return 0, "", ""
        if "up" in argv:
            return 0, "", ""
        return 1, "", f"unexpected {argv}"

    def alerts(self):
        return [a for a in self.calls if a[:2] == ["docker", "exec"] and any("alert_events" in x for x in a)]

    def tags(self):
        return [a for a in self.calls if a[:2] == ["docker", "tag"]]

    def ups(self):
        return [a for a in self.calls if "up" in a]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _verify(mod, fake, services, *, rollback=True, timeout=300, settle=30, snap=None):
    clock = Clock()
    default = {"x": {"container": "poindexter-x", "image_ref": "glad-labs-website-x", "image_id": "sha256:old"}}
    return mod.verify(services, default if snap is None else snap,
                      sha="abc123def", timeout=timeout, settle=settle, do_rollback=rollback,
                      stack_cmd=["bash", "start-stack.sh"], run=fake, clock=clock, sleep=clock.sleep), fake


def test_healthy_after_a_few_polls_is_ok_and_writes_nothing():
    mod = _load()
    fake = FakeDocker([_inspect_json(health="starting"), _inspect_json(health="starting"), _inspect_json(health="healthy")])
    res, fake = _verify(mod, fake, ["x"])
    assert res["x"]["verdict"] == "healthy" and mod.exit_code(res) == 0
    assert fake.alerts() == [] and fake.tags() == []


def test_no_healthcheck_service_needs_the_settle_window():
    mod = _load()
    fake = FakeDocker([_inspect_json(has_hc=False, health=None)])
    clock = Clock()
    v, _, _ = mod.wait_for("x", timeout=300, settle=30, run=fake, clock=clock, sleep=clock.sleep)
    assert v == "healthy" and clock.t >= 30


def test_rollback_disabled_pages_but_leaves_the_image():
    mod = _load()
    fake = FakeDocker([_inspect_json(health="unhealthy")])
    res, fake = _verify(mod, fake, ["x"], rollback=False)
    assert res["x"]["verdict"] == "failed:unhealthy" and res["x"]["rolled_back"] is False
    assert mod.exit_code(res) == 1 and fake.tags() == [] and len(fake.alerts()) == 1
    assert "sev=critical" in " ".join(fake.alerts()[0])


def test_bounced_containers_are_verified_by_name_and_never_rolled_back():
    mod = _load()
    fake = FakeDocker([_inspect_json(status="restarting", restarting=True, restarts=5)])
    res, fake = _verify(mod, fake, ["container:poindexter-worker"], snap={})
    assert res["container:poindexter-worker"]["container"] == "poindexter-worker"
    assert res["container:poindexter-worker"]["rolled_back"] is False and fake.tags() == []
    assert len(fake.alerts()) == 1 and "sev=critical" in " ".join(fake.alerts()[0])


def test_timeout_without_a_verdict_is_a_warning_not_a_rollback():
    mod = _load()
    fake = FakeDocker([_inspect_json(health="starting")])
    res, fake = _verify(mod, fake, ["x"], timeout=60)
    assert res["x"]["verdict"] == "timeout" and res["x"]["rolled_back"] is False and mod.exit_code(res) == 1
    assert len(fake.alerts()) == 1 and "sev=warning" in " ".join(fake.alerts()[0])


def test_missing_snapshot_means_no_rollback_but_still_a_page():
    mod = _load()
    fake = FakeDocker([_inspect_json(status="exited")])
    res, fake = _verify(mod, fake, ["x"], snap={})
    assert res["x"]["rolled_back"] is False and "no previous image" in res["x"]["rollback_note"]
    assert fake.tags() == [] and "rollback FAILED" in " ".join(fake.alerts()[0])


def test_a_snapshot_without_a_rollback_image_is_never_rolled_back_by_id():
    """The pre-2026-09-28 snapshot shape: an image id and nothing holding it.
    Re-tagging that id is what could never work on this store; it is not a
    fallback."""
    mod = _load()
    fake = FakeDocker([_inspect_json(status="exited")])
    res, fake = _verify(mod, fake, ["x"])  # the default snap: image_id only
    assert res["x"]["rolled_back"] is False and "no previous image was preserved" in res["x"]["rollback_note"]
    assert fake.tags() == [] and fake.ups() == []


def test_verdict_table():
    mod = _load()
    def info(**kw):
        return mod.inspect("c", lambda argv: (0, _inspect_json(**kw), ""))
    assert mod.verdict(None, running_for=0, settle=30) == "pending"
    assert mod.verdict(info(health="healthy"), running_for=0, settle=30) == "healthy"
    assert mod.verdict(info(health="starting"), running_for=100, settle=30) == "pending"
    assert mod.verdict(info(restarts=2), running_for=0, settle=30).startswith("failed:restarted 2x")
    assert mod.verdict(info(status="exited"), running_for=0, settle=30).startswith("failed:exited")
    assert mod.verdict(info(has_hc=False, health=None), running_for=10, settle=30) == "pending"
    assert mod.verdict(info(has_hc=False, health=None), running_for=31, settle=30) == "healthy"


def test_settings_fall_back_when_psql_is_unavailable():
    mod = _load()
    assert mod.read_int_setting("deploy_health_gate_seconds", 300, run=lambda argv: (1, "", "down")) == 300
    assert mod.read_bool_setting("deploy_rollback_on_unhealthy", True, run=lambda argv: (0, "false\n", "")) is False
    assert mod.read_int_setting("k", 7, run=lambda argv: (0, "not-an-int\n", "")) == 7


@pytest.mark.parametrize("results,code", [({"a": {"verdict": "healthy", "rolled_back": False}}, 0),
                                          ({"a": {"verdict": "failed:x", "rolled_back": True}}, 2),
                                          ({"a": {"verdict": "timeout", "rolled_back": False}}, 1)])
def test_exit_codes(results, code):
    assert _load().exit_code(results) == code


def test_settings_and_alerts_use_psql_variables_not_spliced_sql():
    mod = _load()
    seen = []

    def run(argv):
        seen.append(argv)
        return 0, "42\n", ""

    assert mod.read_setting("deploy_health_gate_seconds", "300", run=run) == "42"
    argv = seen[0]
    assert argv[-1] == "SELECT value FROM app_settings WHERE key = :'k'"
    assert "-v" in argv and "k=deploy_health_gate_seconds" in argv
    mod.write_alert(service="x", sha="s", severity="critical", title="t'; DROP TABLE posts; --", body="$$ body $$", run=run)
    sql = seen[1][-1]
    assert "DROP TABLE" not in sql and "$$" not in sql
    assert any(a.startswith("ann=") and "DROP TABLE" in a for a in seen[1])


# ── image identity + the recreate check (deploy step 6a-bis, 2026-09-28) ──
#
# Digests from the throwaway-project measurement that settled the question
# (containerd image store, compose 5.5.1): an unchanged Dockerfile rebuilt
# twice gave two image IDs around one platform manifest; changing one COPYed
# file gave a new manifest. Only the manifest tracks content.
V1_MANIFEST = "sha256:bc853ed87891b2d51d21815c9e89b33aedaf789e7a419775668413a0f07f1750"
V2_MANIFEST = "sha256:5a387e7d1229c72abaf7b7ef4e0542889e5bb0c6544efaa8cc93f340b8f4ce9a"
BUILD1_INDEX = "sha256:ec9898678dad" + "0" * 52
BUILD2_INDEX = "sha256:81c9317f771f" + "0" * 52
REF = "glad-labs-website-brain-daemon"
# The 2026-09-27 deploy of 49d4052c7: compose-apply began 21:40:24Z and
# recreated the brain, which started at 21:40:36Z.
APPLY_BEGAN = "2026-09-27T21:40:24Z"


def _container(*, status="running", started="2026-09-27T21:40:36.518811927Z",
               created="2026-09-27T21:40:35.901234567Z", image_id=BUILD1_INDEX,
               manifest: str | None = V1_MANIFEST, platform=None, ref=REF):
    doc = {
        "Name": "/poindexter-brain-daemon",
        "State": {"Status": status, "Running": status == "running", "Restarting": False,
                  "StartedAt": started, "Health": {"Status": "healthy"}},
        "Created": created,
        "RestartCount": 0,
        "Config": {"Image": ref, "Healthcheck": {"Test": ["CMD", "true"]}},
        "Image": image_id,
    }
    if manifest is not None:
        doc["ImageManifestDescriptor"] = {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": manifest,
            "size": 669,
            "platform": {"architecture": "amd64", "os": "linux"} if platform is None else platform,
        }
    return json.dumps([doc])


class IdentityDocker:
    """`docker ps` / `inspect` / `image inspect` as the recreate check sees them.

    ``tag_index`` answers a plain ``image inspect <ref>`` (the image ID — an OCI
    index digest under the containerd store); ``tag_manifest`` answers one with
    ``--platform`` (that platform's manifest digest).
    """

    def __init__(self, *, container: str | None = None, names="poindexter-brain-daemon\n",
                 tag_index=BUILD2_INDEX, tag_manifest=V1_MANIFEST, ps_rc=0, inspect_rc=0, image_rc=0):
        self.container = container if container is not None else _container()
        self.names, self.tag_index, self.tag_manifest = names, tag_index, tag_manifest
        self.ps_rc, self.inspect_rc, self.image_rc = ps_rc, inspect_rc, image_rc
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            return (0, self.names, "") if self.ps_rc == 0 else (self.ps_rc, "", "Cannot connect to the Docker daemon")
        if argv[:2] == ["docker", "inspect"]:
            return (0, self.container, "") if self.inspect_rc == 0 else (1, "", "Error: No such object")
        if argv[:3] == ["docker", "image", "inspect"]:
            if self.image_rc:
                return self.image_rc, "", "Error response from daemon: No such image: glad-labs-website-brain-daemon"
            return 0, (self.tag_manifest if "--platform" in argv else self.tag_index) + "\n", ""
        return 1, "", f"unexpected {argv}"

    def image_inspects(self):
        return [a for a in self.calls if a[:3] == ["docker", "image", "inspect"]]


def test_a_rebuild_that_changed_nothing_is_the_same_image():
    """The false positive behind the 2026-09-22 diagnosis.

    The container runs build 1's index, the tag now names build 2's — two
    different image IDs — but both carry the same linux/amd64 manifest, so
    nothing it runs changed. Comparing IDs said "rebuilt and never recreated"
    for five such services, and the deploy then force-recreated every rebuilt
    service on every pass.
    """
    mod = _load()
    fake = IdentityDocker(container=_container(image_id=BUILD1_INDEX, manifest=V1_MANIFEST),
                          tag_index=BUILD2_INDEX, tag_manifest=V1_MANIFEST)
    ident = mod.image_identity("poindexter-brain-daemon", run=fake)
    assert ident["same"] is True
    assert (ident["running"], ident["tagged"], ident["image_ref"]) == (V1_MANIFEST, V1_MANIFEST, REF)
    assert fake.image_inspects() == [
        ["docker", "image", "inspect", "--platform", "linux/amd64", REF, "--format", "{{.Id}}"]
    ], "the tag must be resolved to the container's own platform manifest, not to its index"


def test_a_content_change_is_a_different_image():
    mod = _load()
    fake = IdentityDocker(container=_container(manifest=V1_MANIFEST), tag_manifest=V2_MANIFEST)
    ident = mod.image_identity("poindexter-brain-daemon", run=fake)
    assert ident["same"] is False
    assert (ident["running"], ident["tagged"]) == (V1_MANIFEST, V2_MANIFEST)


def test_the_superseded_image_is_never_looked_up():
    """Under the containerd store a same-tag rebuild deletes the old image
    record while the container still runs it (`docker image inspect <old id>`
    -> "No such image", measured 2026-09-28). The running side must come from
    the container's own manifest descriptor or the comparison can never be
    made at exactly the moment it matters."""
    mod = _load()
    fake = IdentityDocker(container=_container(image_id=BUILD1_INDEX), tag_manifest=V2_MANIFEST)
    mod.image_identity("poindexter-brain-daemon", run=fake)
    assert not [a for a in fake.image_inspects() if BUILD1_INDEX in a]


def test_classic_store_compares_image_ids():
    """No manifest descriptor (classic graphdriver store): `.Image` / `.Id`
    are the comparison, as before."""
    mod = _load()
    fake = IdentityDocker(container=_container(manifest=None, image_id="sha256:old"), tag_index="sha256:new")
    ident = mod.image_identity("poindexter-brain-daemon", run=fake)
    assert ident["same"] is False and (ident["running"], ident["tagged"]) == ("sha256:old", "sha256:new")
    assert fake.image_inspects() == [["docker", "image", "inspect", REF, "--format", "{{.Id}}"]]


def test_the_platform_variant_is_kept():
    mod = _load()
    fake = IdentityDocker(container=_container(platform={"os": "linux", "architecture": "arm64", "variant": "v8"}))
    mod.image_identity("poindexter-brain-daemon", run=fake)
    assert fake.image_inspects()[0][3:5] == ["--platform", "linux/arm64/v8"]


@pytest.mark.parametrize("fake_kwargs", [
    {"inspect_rc": 1},                                   # container vanished
    {"container": _container(ref="")},                   # no image ref recorded
    {"container": _container(platform={"os": "linux"})},  # descriptor without an architecture
    {"image_rc": 1},                                     # tag missing / CLI without --platform
    {"container": _container(manifest=None, image_id="")},  # classic store, no image recorded
], ids=["inspect-fails", "no-image-ref", "no-platform", "tag-lookup-fails", "no-running-image"])
def test_an_undecidable_comparison_is_none_never_a_match(fake_kwargs):
    mod = _load()
    ident = mod.image_identity("poindexter-brain-daemon", run=IdentityDocker(**fake_kwargs))
    assert ident["same"] is None
    assert ident["why"], "an unknown must say why"


def test_plan_skips_what_compose_apply_already_recreated():
    """The double bounce this exists to stop.

    2026-09-27, deploy of 49d4052c7: compose-apply logged `Container
    poindexter-brain-daemon Recreate` and the brain started on the new image
    at 21:40:36Z; step 6a-bis then force-recreated it again and it started a
    second time at 21:41:58Z — every brain deploy from 09-22 on.
    """
    mod = _load()
    fake = IdentityDocker(container=_container(created="2026-09-27T21:40:35.9Z", manifest=V2_MANIFEST),
                          tag_manifest=V2_MANIFEST)
    action, why = mod.plan_recreate("brain-daemon", since=mod._epoch(APPLY_BEGAN), run=fake)
    assert action == "skip"
    assert "already recreated" in why and "poindexter-brain-daemon" in why


def test_plan_skips_a_rebuild_that_changed_nothing():
    mod = _load()
    fake = IdentityDocker(container=_container(created="2026-09-20T08:00:00Z", manifest=V1_MANIFEST),
                          tag_manifest=V1_MANIFEST)
    action, why = mod.plan_recreate("brain-daemon", since=mod._epoch(APPLY_BEGAN), run=fake)
    assert action == "skip" and "changed nothing" in why


def test_plan_recreates_what_compose_left_on_the_previous_image():
    """The guarantee the step was written for: a same-tag rebuild never keeps
    the old image silently."""
    mod = _load()
    fake = IdentityDocker(container=_container(created="2026-09-20T08:00:00Z", manifest=V1_MANIFEST),
                          tag_manifest=V2_MANIFEST)
    action, why = mod.plan_recreate("brain-daemon", since=mod._epoch(APPLY_BEGAN), run=fake)
    assert action == "recreate"
    assert "bc853ed87891" in why and "5a387e7d1229" in why, "name both images"


@pytest.mark.parametrize("fake_kwargs", [
    {"ps_rc": 1},
    {"inspect_rc": 1},
    {"image_rc": 1},
    # Two containers answer to the service label (a second compose project):
    # judging the wrong one could skip a stale container.
    {"names": "poindexter-brain-daemon\nci-1234-brain-daemon-1\n"},
], ids=["ps-fails", "inspect-fails", "tag-lookup-fails", "ambiguous-container"])
def test_plan_recreates_when_it_cannot_tell(fake_kwargs):
    """Fail-safe points one way: an unneeded restart is cheaper than stale code
    that looks healthy."""
    mod = _load()
    action, why = mod.plan_recreate("brain-daemon", since=mod._epoch(APPLY_BEGAN), run=IdentityDocker(**fake_kwargs))
    assert action == "recreate" and "to be safe" in why


def test_plan_leaves_a_service_with_no_container_parked():
    """No container after a successful `up -d` means the service's profile is
    off. `up --force-recreate <svc>` would enable the profile and start it."""
    mod = _load()
    action, why = mod.plan_recreate("voice-agent-livekit", since=mod._epoch(APPLY_BEGAN), run=IdentityDocker(names=""))
    assert action == "parked" and "profile" in why


def test_plan_leaves_a_stopped_container_parked():
    """poindexter-voice-agent-livekit has sat `exited` since 2026-07-31 with
    the `voice` profile off. A Dockerfile.voice-agent change used to reach
    `up -d --force-recreate voice-agent-livekit` and un-park it."""
    mod = _load()
    fake = IdentityDocker(container=_container(status="exited", started="2026-07-31T14:45:35.05520044Z",
                                               manifest=V1_MANIFEST), tag_manifest=V2_MANIFEST)
    action, why = mod.plan_recreate("voice-agent-livekit", since=mod._epoch(APPLY_BEGAN), run=fake)
    assert action == "parked" and "exited" in why
    assert fake.image_inspects() == [], "a parked service is not compared, let alone recreated"


def test_a_container_this_deploy_started_is_never_parked():
    """Started by this pass and already dead is a failed deploy, not a parked
    service — it must stay in front of the health gate."""
    mod = _load()
    fake = IdentityDocker(container=_container(status="exited", started="2026-09-27T21:40:36Z",
                                               manifest=V2_MANIFEST), tag_manifest=V2_MANIFEST)
    action, _ = mod.plan_recreate("brain-daemon", since=mod._epoch(APPLY_BEGAN), run=fake)
    assert action == "skip"


def test_without_since_a_stopped_container_is_compared_not_parked():
    mod = _load()
    fake = IdentityDocker(container=_container(status="exited", started="2026-07-31T14:45:35Z",
                                               manifest=V1_MANIFEST), tag_manifest=V2_MANIFEST)
    action, _ = mod.plan_recreate("voice-agent-livekit", since=None, run=fake)
    assert action == "recreate"


class OneOffDocker(IdentityDocker):
    """Answers a label-only `docker ps -a` the way docker does: a `compose run`
    container of the service carries the service label too, and is newest, so
    it is listed first. Filtering on com.docker.compose.oneoff=False leaves it
    out."""

    def __call__(self, argv):
        if argv[:3] == ["docker", "ps", "-a"] and "label=com.docker.compose.oneoff=False" not in argv:
            self.calls.append(argv)
            return 0, "glad-labs-website-brain-daemon-run-1a2b3c4d5e6f\n" + self.names, ""
        return super().__call__(argv)


def test_a_compose_run_one_off_is_not_the_service():
    """Counted as the service, a one-off made the plan say "2 containers …
    recreating to be safe" and bounce a live service for a manual `run` beside
    it, and the gate could watch the one-off instead of the service. For
    demo-recorder, whose bakes ARE one-offs, a bake in flight would be judged
    as the service itself."""
    mod = _load()
    fake = OneOffDocker(container=_container(created="2026-09-20T08:00:00Z", manifest=V1_MANIFEST),
                        tag_manifest=V1_MANIFEST)
    action, why = mod.plan_recreate("brain-daemon", since=mod._epoch(APPLY_BEGAN), run=fake)
    assert action == "skip", why
    assert mod.find_container("brain-daemon", run=fake) == "poindexter-brain-daemon"


def test_recreate_plan_cli_prints_one_tab_separated_line_per_service(monkeypatch, capsys):
    """The shell reads it with `IFS=$'\\t' read -r action svc why`; a reason
    must never add a field or a line."""
    mod = _load()
    seen = {}

    def fake_plan(services, *, since=None):
        seen["args"] = (services, since)
        return [("skip", "brain-daemon", "compose-apply already\trecreated\nit"),
                ("parked", "voice-agent-livekit", "no container")]

    monkeypatch.setattr(mod, "recreate_plan", fake_plan)
    rc = mod.main(["recreate-plan", "--since", "1790602824", "--services", "brain-daemon", "voice-agent-livekit"])
    assert rc == 0 and seen["args"] == (["brain-daemon", "voice-agent-livekit"], 1790602824.0)
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("\t") for line in lines] == [
        ["skip", "brain-daemon", "compose-apply already recreated it"],
        ["parked", "voice-agent-livekit", "no container"],
    ]


def test_recreate_plan_keeps_the_order_it_was_given():
    mod = _load()
    fake = IdentityDocker(names="")
    assert [svc for _, svc, _ in mod.recreate_plan(["b", "a", "c"], since=1.0, run=fake)] == ["b", "a", "c"]


def test_docker_timestamps_parse_as_instants():
    mod = _load()
    assert mod._epoch("2026-09-27T21:40:36.518811927Z") == pytest.approx(1790545236.518811)
    assert mod._epoch("2026-09-27T17:40:36-04:00") == mod._epoch("2026-09-27T21:40:36Z")
    assert mod._epoch("0001-01-01T00:00:00Z") < 0, "never started reads as long ago, not as unknown"
    for bad in ("", None, "garbage", "2026-09-27T21:40:36"):  # the last has no zone: decline, don't guess
        assert mod._epoch(bad) is None


def test_inspect_exposes_what_the_recreate_check_needs():
    mod = _load()
    info = mod.inspect("c", lambda argv: (0, _container(), ""))
    assert info["manifest_digest"] == V1_MANIFEST and info["platform"] == "linux/amd64"
    assert info["created"] == "2026-09-27T21:40:35.901234567Z" and info["image_ref"] == REF
    bare = mod.inspect("c", lambda argv: (0, _container(manifest=None), ""))
    assert bare["manifest_digest"] == "" and bare["platform"] == ""


# ── the rollback image survives the rebuild (2026-09-28) ──────────────────
#
# Every rollback from 2026-09-13 to 09-28 would have failed. The snapshot
# recorded the running container's image id, and under the containerd image
# store a same-tag rebuild deletes that image record as it moves the tag, even
# while the container still runs it, so `docker tag <that id> <ref>` answered
# "No such image". FakeDocker above answers every `docker tag` with success,
# which is how these tests stayed green; StoreDocker models the store as it was
# measured on this host (Docker 29.8, compose 5.5.1, a throwaway project).

V3_MANIFEST = "sha256:" + "3" * 64
LIVE_REF = "glad-labs-website-x"
ROLLBACK_REF = "glad-labs-website-x:rollback-x"
STACK = ["bash", "start-stack.sh"]


def _tagged(ref: str) -> str:
    """docker's normalisation: a ref with no tag is ``:latest``."""
    name = ref.split("@", 1)[0]
    return name if name.rfind(":") > name.rfind("/") else f"{name}:latest"


class StoreDocker:
    """docker + compose for one service ``x``, over a model of the containerd image store.

    As measured: a build mints a new image ID every time and DELETES the record
    it moves the tag off when no other tag names it, even while a container
    runs it (the classic store keeps it, untagged). ``docker tag`` over an
    existing tag keeps the image it moved off, untagged. ``docker image rm``
    refuses an image a container uses and an id that several tags name, and
    removing by id an image with one tag left removes that tag too.
    ``up --force-recreate`` recreates the container from what the ref names.
    """

    def __init__(self, *, containerd: bool = True):
        self.containerd = containerd
        self.images: dict[str, str] = {}   # image id -> platform manifest (its content)
        self.tags: dict[str, str] = {}     # normalised tag -> image id
        self.running: dict | None = None   # {"image_id", "manifest"} of poindexter-x
        self.other_users: set[str] = set()  # image ids other containers still use
        self.broken: set[str] = set()      # manifests whose containers crash-loop
        self.recreate_from: str | None = None  # a compose that ignores the re-tag
        self.up_rc, self.up_err = 0, ""
        self.calls: list[list[str]] = []
        self._builds = 0

    # -- the world, as the test drives it ------------------------------------
    def build(self, manifest: str, *, broken: bool = False) -> str:
        self._builds += 1
        new = f"sha256:{self._builds:04x}" + "e" * 60
        self.images[new] = manifest
        old = self.tags.get(_tagged(LIVE_REF))
        self.tags[_tagged(LIVE_REF)] = new
        if self.containerd and old and old not in self.tags.values():
            del self.images[old]  # even while a container still runs it
        if broken:
            self.broken.add(manifest)
        return new

    def up(self) -> None:
        image_id = self.recreate_from or self.tags[_tagged(LIVE_REF)]
        self.running = {"image_id": image_id, "manifest": self.images[image_id]}

    # -- the docker CLI -------------------------------------------------------
    def _resolve(self, name: str) -> str | None:
        if name.startswith("sha256:"):
            return name if name in self.images else None
        return self.tags.get(_tagged(name))

    def _names(self, image_id: str) -> list[str]:
        return sorted(t for t, i in self.tags.items() if i == image_id)

    def _used(self, image_id: str) -> bool:
        return image_id in self.other_users or bool(self.running and self.running["image_id"] == image_id)

    @staticmethod
    def _no_such(name: str):
        return 1, "", f"Error response from daemon: No such image: {name}"

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            ours = "label=com.docker.compose.service=x" in argv and self.running
            return 0, "poindexter-x\n" if ours else "", ""
        if argv[:2] == ["docker", "inspect"]:
            return (0, self._container(), "") if self.running else (1, "", "Error: No such object")
        if argv[:3] == ["docker", "image", "inspect"]:
            return self._image_inspect(argv[3:])
        if argv[:3] == ["docker", "image", "rm"]:
            return self._rm(argv[3])
        if argv[:2] == ["docker", "tag"]:
            image_id = self._resolve(argv[2])
            if image_id is None:
                return self._no_such(argv[2])
            self.tags[_tagged(argv[3])] = image_id  # what it named before stays, untagged
            return 0, "", ""
        if argv[:2] == ["docker", "logs"]:
            return 0, "Traceback\nModuleNotFoundError: No module named '_voice_paths'\n", ""
        if argv[:2] == ["docker", "exec"]:
            return 0, "", ""
        if argv[:len(STACK)] == STACK and "up" in argv:
            if self.up_rc:
                return self.up_rc, "", self.up_err
            self.up()
            return 0, "", ""
        return 1, "", f"unexpected {argv}"

    def _container(self) -> str:
        manifest = self.running["manifest"]
        bad = manifest in self.broken
        doc = {
            "Name": "/poindexter-x",
            "State": {"Status": "restarting" if bad else "running", "Restarting": bad, "ExitCode": 1 if bad else 0,
                      "StartedAt": "2026-09-28T17:00:00Z", "Health": {"Status": "unhealthy" if bad else "healthy"}},
            "Created": "2026-09-28T17:00:00Z",
            "RestartCount": 3 if bad else 0,
            "Config": {"Image": LIVE_REF, "Healthcheck": {"Test": ["CMD", "true"]}},
            "Image": self.running["image_id"],
        }
        if self.containerd:
            doc["ImageManifestDescriptor"] = {"digest": manifest, "platform": {"os": "linux", "architecture": "amd64"}}
        return json.dumps([doc])

    def _image_inspect(self, rest: list[str]):
        platform = fmt = name = None
        args = iter(rest)
        for a in args:
            if a == "--platform":
                platform = next(args)
            elif a == "--format":
                fmt = next(args)
            else:
                name = a
        image_id = self._resolve(name)
        if image_id is None:
            return self._no_such(name)
        if fmt == "{{json .RepoTags}}":
            return 0, json.dumps(self._names(image_id)) + "\n", ""
        if platform:
            return 0, self.images[image_id] + "\n", ""
        return 0, image_id + "\n", ""

    def _rm(self, name: str):
        if name.startswith("sha256:"):
            if name not in self.images:
                return self._no_such(name)
            names = self._names(name)
            if self._used(name):
                return 1, "", f"Error response from daemon: conflict: unable to delete {name[7:19]} (cannot be forced) - image is being used by running container"
            if len(names) > 1:
                return 1, "", f"Error response from daemon: conflict: unable to delete {name[7:19]} (must be forced) - image is referenced in multiple repositories"
            for tag in names:
                del self.tags[tag]
            del self.images[name]
            return 0, f"Deleted: {name}\n", ""
        image_id = self.tags.get(_tagged(name))
        if image_id is None:
            return self._no_such(name)
        if len(self._names(image_id)) == 1 and self._used(image_id):
            return 1, "", f"Error response from daemon: conflict: unable to delete {name} (must be forced) - container 922bd4867a1a is using its referenced image {image_id[7:19]}"
        del self.tags[_tagged(name)]
        if image_id not in self.tags.values():
            del self.images[image_id]
        return 0, f"Untagged: {_tagged(name)}\n", ""

    def live_retags(self) -> list[list[str]]:
        """``docker tag`` calls that point the LIVE ref somewhere — a rollback."""
        return [a for a in self.calls if a[:2] == ["docker", "tag"] and _tagged(a[3]) == _tagged(LIVE_REF)]

    def alerts(self) -> list[list[str]]:
        return [a for a in self.calls if a[:2] == ["docker", "exec"] and any("alert_events" in x for x in a)]


def _v1_running(store: StoreDocker | None = None) -> StoreDocker:
    store = store if store is not None else StoreDocker()
    store.build(V1_MANIFEST)
    store.up()
    return store


def _deploy(mod, store: StoreDocker, manifest: str, *, broken: bool = False, rollback: bool = True):
    """One deploy pass as the sync runs it: snapshot, build, compose-apply, gate."""
    snap = mod.snapshot(["x"], run=store)
    store.build(manifest, broken=broken)
    store.up()
    clock = Clock()
    res = mod.verify(["x"], snap, sha="abc123def", timeout=300, settle=30, do_rollback=rollback,
                     stack_cmd=STACK, run=store, clock=clock, sleep=clock.sleep)
    return snap, res


def test_the_store_model_deletes_the_image_a_rebuild_moves_off():
    """Positive control. If the fake stops reproducing the host, every rollback
    test below passes whatever the code does, which is how FakeDocker hid this."""
    store = _v1_running()
    old = store.running["image_id"]
    store.build(V2_MANIFEST)
    assert store.running["image_id"] == old, "the container still runs it"
    assert store(["docker", "image", "inspect", old, "--format", "{{.Id}}"])[0] == 1
    rc, _, err = store(["docker", "tag", old, LIVE_REF])
    assert rc == 1 and "No such image" in err


def test_a_broken_build_is_rolled_back_onto_the_image_it_replaced():
    """The chatterbox shape (2026-09-13) end to end: snapshot, a rebuild that
    deletes the running image's record, compose-apply onto the broken build,
    gate. Every rollback before this fix died on `docker tag <snapshot id>`."""
    mod = _load()
    store = _v1_running()
    snap = mod.snapshot(["x"], run=store)
    store.build(V2_MANIFEST, broken=True)
    store.up()
    assert store._names(snap["x"]["image_id"]) == [ROLLBACK_REF], (
        "after the build, the rollback tag is the only thing keeping the pre-rebuild image"
    )
    clock = Clock()
    res = mod.verify(["x"], snap, sha="abc123def", timeout=300, settle=30, do_rollback=True,
                     stack_cmd=STACK, run=store, clock=clock, sleep=clock.sleep)
    r = res["x"]
    assert r["verdict"].startswith("failed:restarting") and r["rolled_back"] is True, r
    assert r["after_rollback"] == "healthy" and mod.exit_code(res) == 2
    assert store.running["manifest"] == V1_MANIFEST, "back on what it ran before the rebuild"
    assert store.live_retags() == [["docker", "tag", ROLLBACK_REF, LIVE_REF]]
    assert [a for a in store.calls if "up" in a] == [[*STACK, "up", "-d", "--no-build", "--force-recreate", "x"]]
    assert len(store.alerts()) == 1
    cmd = " ".join(store.alerts()[0])
    assert "sev=critical" in cmd and "rolled back" in cmd and "ModuleNotFoundError" in cmd
    assert "fp=deploy_health_gate:x:abc123def" in cmd
    assert ":'sev'" in store.alerts()[0][-1] and "critical" not in store.alerts()[0][-1]  # values never spliced into the SQL


def test_the_containers_own_image_already_gone_is_preserved_through_the_ref():
    """The usual state on this host: an earlier rebuild that changed nothing
    minted a new image ID, compose rightly left the container on the old one,
    and that record was gone before this snapshot. The ref names the same
    content, so that is what is kept."""
    mod = _load()
    store = _v1_running()
    store.build(V1_MANIFEST)  # a no-op rebuild; compose leaves the container alone
    assert store._resolve(store.running["image_id"]) is None
    snap, res = _deploy(mod, store, V2_MANIFEST, broken=True)
    assert snap["x"]["rollback_ref"] == ROLLBACK_REF
    assert "via glad-labs-website-x, which names the same content" in snap["x"]["rollback_note"]
    assert res["x"]["rolled_back"] is True and store.running["manifest"] == V1_MANIFEST


def test_when_no_image_holds_what_it_runs_nothing_is_preserved_and_the_page_says_so():
    """A build compose-apply never applied: the container runs V1, whose record
    that build deleted, and the ref names V2. There is nothing to roll back to,
    and rolling back to V2 would be a lie; say so instead."""
    mod = _load()
    store = _v1_running()
    store.build(V2_MANIFEST)  # built, never applied
    snap, res = _deploy(mod, store, V3_MANIFEST, broken=True)
    assert snap["x"]["rollback_ref"] == ""
    assert "no image holds that any more" in snap["x"]["rollback_note"]
    [note] = mod.snapshot_notes(snap)
    assert note.startswith(mod.NO_ROLLBACK_NOTE + "x: ") and "page without rolling it back" in note
    assert "Error response from daemon" not in note and not re.search(r"[0-9a-f]{64}", note), "one readable line"
    r = res["x"]
    assert r["rolled_back"] is False and "no previous image was preserved before the rebuild" in r["rollback_note"]
    assert store.live_retags() == [] and store.running["manifest"] == V3_MANIFEST
    assert "rollback FAILED" in " ".join(store.alerts()[0])


def test_one_rollback_image_per_service_and_nothing_accumulates():
    """The tag moves every snapshot, and the image it held is deleted rather
    than left behind: `docker tag` over a tag keeps the old image untagged
    (measured), so without the release every deploy would leave one."""
    mod = _load()
    store = _v1_running()
    v4 = "sha256:" + "4" * 64
    for before, after in ((V1_MANIFEST, V2_MANIFEST), (V2_MANIFEST, V3_MANIFEST), (V3_MANIFEST, v4)):
        _, res = _deploy(mod, store, after)
        assert res["x"]["verdict"] == "healthy" and store.running["manifest"] == after
        assert sorted(store.tags) == sorted([_tagged(LIVE_REF), ROLLBACK_REF])
        assert store.images[store.tags[ROLLBACK_REF]] == before, "it holds the image this deploy replaced"
        assert len(store.images) == 2, f"the live image and one rollback image, nothing else: {store.images}"


def test_the_release_never_deletes_an_image_still_named_or_still_used():
    """`docker image rm <id>` on an image with one tag left removes that tag,
    and after a rollback whose recreate failed that tag is the live one. Only
    an image nothing names is deleted, and never by force."""
    mod = _load()
    store = StoreDocker()
    kept = store.build(V1_MANIFEST)
    store.tags[ROLLBACK_REF] = kept
    running = store.build(V2_MANIFEST)
    store.tags[_tagged(LIVE_REF)] = kept  # the rollback re-tagged; its recreate never happened
    store.running = {"image_id": running, "manifest": V2_MANIFEST}
    ref, _ = mod.preserve("x", "poindexter-x", mod.inspect("poindexter-x", store), run=store)
    assert ref == ROLLBACK_REF and store.tags[ROLLBACK_REF] == running
    assert store.tags[_tagged(LIVE_REF)] == kept and kept in store.images, "the live tag and its image survive"

    store = StoreDocker()
    used = store.build(V1_MANIFEST)
    store.tags[ROLLBACK_REF] = used
    store.build(V2_MANIFEST)
    store.up()
    store.other_users.add(used)  # e.g. a stopped one-off still on it
    mod.preserve("x", "poindexter-x", mod.inspect("poindexter-x", store), run=store)
    assert used in store.images, "an image a container uses is never removed"
    rms = [a for a in store.calls if a[:3] == ["docker", "image", "rm"]]
    assert rms and not [a for a in rms if "-f" in a or "--force" in a]


@pytest.mark.parametrize("tamper,why", [
    (lambda s: s(["docker", "image", "rm", ROLLBACK_REF]), f"the rollback image {ROLLBACK_REF} is gone"),
    (lambda s: s(["docker", "tag", LIVE_REF, ROLLBACK_REF]), "now names 5a387e7d1229, not the preserved bc853ed87891"),
], ids=["pruned", "re-pointed"])
def test_a_rollback_image_that_no_longer_holds_the_snapshot_is_not_used(tamper, why):
    """`docker image prune -a` between the snapshot and the gate removes the
    tag; a tag re-pointed by hand names something else. Re-tagging from either
    would put an unknown image live, so the rollback refuses and says why."""
    mod = _load()
    store = _v1_running()
    snap = mod.snapshot(["x"], run=store)
    store.build(V2_MANIFEST, broken=True)
    store.up()
    tamper(store)
    ok, note = mod.rollback("x", snap["x"], STACK, run=store)
    assert ok is False and why in note
    assert store.live_retags() == [] and not [a for a in store.calls if "up" in a]


def test_a_rollback_counts_only_when_the_new_container_runs_the_preserved_image():
    """A recreate that did not land on the re-tagged image (here compose is
    pinned to the broken build) has rolled nothing back, whatever `up` returned."""
    mod = _load()
    store = _v1_running()
    snap = mod.snapshot(["x"], run=store)
    store.recreate_from = store.build(V2_MANIFEST, broken=True)
    store.up()
    ok, note = mod.rollback("x", snap["x"], STACK, run=store)
    assert ok is False and "runs 5a387e7d1229, not the preserved bc853ed87891" in note


def test_a_failed_recreate_reports_composes_error_not_its_progress():
    """compose writes progress to stderr too; the first line of it names nothing."""
    mod = _load()
    store = _v1_running()
    snap = mod.snapshot(["x"], run=store)
    store.build(V2_MANIFEST, broken=True)
    store.up()
    store.up_rc, store.up_err = 1, (" Container poindexter-x  Recreate\n Container poindexter-x  Recreated\n"
                                    "Error response from daemon: driver failed programming external connectivity\n")
    ok, note = mod.rollback("x", snap["x"], STACK, run=store)
    assert ok is False and note.endswith("the recreate failed: driver failed programming external connectivity")


def test_the_classic_store_preserves_and_restores_by_image_id():
    """No manifest descriptor: the image ID is the content key, and a rebuild
    leaves the old image untagged instead of deleting it."""
    mod = _load()
    store = _v1_running(StoreDocker(containerd=False))
    v1 = store.running["image_id"]
    snap, res = _deploy(mod, store, V2_MANIFEST, broken=True)
    assert snap["x"]["manifest_digest"] == "" and snap["x"]["rollback_ref"] == ROLLBACK_REF
    assert snap["x"]["rollback_note"].startswith("image ")
    assert res["x"]["rolled_back"] is True and store.running["image_id"] == v1
    assert not [a for a in store.calls if "--platform" in a], "no platform manifests to ask for"


def test_snapshot_records_what_runs_and_the_tag_that_keeps_it():
    mod = _load()
    store = _v1_running()
    snap = mod.snapshot(["x", "parked"], run=store)
    assert snap["x"] == {
        "container": "poindexter-x", "image_ref": LIVE_REF, "image_id": store.running["image_id"],
        "manifest_digest": V1_MANIFEST, "platform": "linux/amd64",
        "rollback_ref": ROLLBACK_REF, "rollback_note": "manifest bc853ed87891, the image it was created from",
    }
    assert snap["parked"]["container"] == "" and snap["parked"]["rollback_ref"] == ""
    assert mod.snapshot_notes(snap) == [
        f"rollback image for x: {ROLLBACK_REF} (manifest bc853ed87891, the image it was created from)",
        "nothing to preserve for parked: no container",
    ]


def test_a_stopped_container_without_a_rollback_image_says_it_is_stopped():
    """Parked voice sits exited on an image no tag names any more. Its note
    must not read like a live service at risk."""
    mod = _load()
    store = _v1_running()
    store.build(V2_MANIFEST)
    doc = json.loads(store._container())
    doc[0]["State"].update({"Status": "exited", "Restarting": False})
    snap = mod.snapshot(["x"], run=lambda argv: (0, json.dumps(doc), "") if argv[:2] == ["docker", "inspect"] else store(argv))
    assert snap["x"]["rollback_ref"] == ""
    assert snap["x"]["rollback_note"].endswith("(poindexter-x is exited; gated only if this pass starts it)")


@pytest.mark.parametrize("image_ref,expected", [
    ("glad-labs-website-x", "glad-labs-website-x:rollback-x"),
    ("poindexter-backup:latest", "poindexter-backup:rollback-x"),
    ("localhost:5000/team/app:1.2", "localhost:5000/team/app:rollback-x"),
    ("localhost:5000/team/app", "localhost:5000/team/app:rollback-x"),
    ("app@sha256:" + "a" * 64, "app:rollback-x"),
    ("sha256:" + "a" * 64, ""),
    ("a" * 64, ""),
    ("", ""),
])
def test_rollback_ref_for(image_ref, expected):
    assert _load().rollback_ref_for(image_ref, "x") == expected


def test_services_sharing_an_image_ref_each_keep_their_own_rollback_image():
    """backup-daily / -hourly / -offsite all run poindexter-backup, and need
    not all run the same build when the snapshot is taken."""
    mod = _load()
    assert [mod.rollback_ref_for("poindexter-backup:latest", s) for s in ("backup-daily", "backup-hourly")] == [
        "poindexter-backup:rollback-backup-daily", "poindexter-backup:rollback-backup-hourly"]


def test_snapshot_cli_prints_the_json_on_stdout_and_the_notes_on_stderr(monkeypatch, capsys):
    """The deploy sync writes stdout to the file the verify half reads, and
    logs stderr one line per note."""
    mod = _load()
    snap = {"x": {"container": "c", "rollback_ref": "r:rollback-x", "rollback_note": "manifest abc,\nthe image"},
            "y": {"container": "", "rollback_ref": "", "rollback_note": "no container"}}
    monkeypatch.setattr(mod, "snapshot", lambda services: snap)
    assert mod.main(["snapshot", "--services", "x", "y"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == snap
    assert err.splitlines() == ["rollback image for x: r:rollback-x (manifest abc, the image)",
                                "nothing to preserve for y: no container"]


def test_a_page_without_a_rollback_says_how_to_put_the_preserved_image_back():
    """deploy_rollback_on_unhealthy=false pages instead of rolling back; the
    page carries the two commands that do it by hand."""
    mod = _load()
    fake = FakeDocker([_inspect_json(health="unhealthy")])
    snap = {"x": {"container": "poindexter-x", "image_ref": LIVE_REF, "rollback_ref": ROLLBACK_REF,
                  "manifest_digest": V1_MANIFEST, "platform": "linux/amd64"}}
    _, fake = _verify(mod, fake, ["x"], rollback=False, snap=snap)
    body = " ".join(fake.alerts()[0])
    assert f"docker tag {ROLLBACK_REF} {LIVE_REF} && bash start-stack.sh up -d --no-build --force-recreate x" in body
