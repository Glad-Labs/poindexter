"""Device scoping for the GPU advisory lock (poindexter#3457 Phase 2).

The pure half: role resolution and key derivation. The locking half lives in
test_gpu_scheduler.py.

Design invariant under test: **serialise iff the device sets intersect**, and
every unresolvable case falls back to the single whole-GPU key rather than
splitting (wrong-but-serialised costs throughput; wrong-and-split costs a
CUDA OOM).
"""
from __future__ import annotations

import json

import pytest

from services import gpu_scheduler as gs
from services.site_config import SiteConfig

#: Pinned in every fixture so these tests never depend on whether the RUNNER
#: is containerised. Without it `gpu_lock_node_id()` falls back to ambient
#: detection: it resolves a hostname on a bare host (green locally) and returns
#: "" on a containerised CI runner, which fails closed to the whole-GPU key and
#: broke 10 of these tests in CI while all passing on the dev box.
_TEST_NODE_ID = "test-node"


def _cfg(**values):
    """Install a SiteConfig the module-level helpers will read."""
    values.setdefault("gpu_lock_node_id", _TEST_NODE_ID)
    return SiteConfig(initial_config={str(k): str(v) for k, v in values.items()})


@pytest.fixture
def scoped(monkeypatch):
    def _apply(**values):
        cfg = _cfg(**values)
        monkeypatch.setattr(gs, "_sc", lambda: cfg)
        return cfg

    return _apply


# --- fail-closed: the default is exactly today's behaviour -------------------


def test_disabled_returns_the_single_whole_gpu_key(scoped):
    scoped(gpu_lock_per_device_enabled="false")
    assert gs.resolve_lock_keys("ollama", "anything") == [gs.GPU_ADVISORY_LOCK_KEY]


def test_disabled_is_the_default(scoped):
    """No setting at all must behave as today, not as scoped."""
    scoped()
    assert gs.resolve_lock_keys("image_gen", None) == [gs.GPU_ADVISORY_LOCK_KEY]


# NOTE: these two USED to assert a fallback to the whole-GPU key. That was the
# bug, not the contract — and because the tests agreed with the wrong design,
# no amount of mutation testing could surface it. They now assert the
# maximally-exclusive set; see the fail-closed tests at the end of this file.
@pytest.mark.parametrize("owner", ["", "unknown_owner", "wan", None])
def test_unknown_owner_blocks_every_card(scoped, owner):
    scoped(gpu_lock_per_device_enabled="true")
    keys = set(gs.resolve_lock_keys(owner or "", None))
    assert gs.GPU_ADVISORY_LOCK_KEY not in keys
    assert set(gs.resolve_lock_keys("image_gen", None)) <= keys
    assert set(gs.resolve_lock_keys("ollama", "qwen3-vl:30b")) <= keys


def test_role_missing_from_the_map_blocks_every_card(scoped):
    scoped(
        gpu_lock_per_device_enabled="true",
        gpu_lock_scopes=json.dumps({"render": [0]}),
    )
    keys = set(gs.resolve_lock_keys("ollama", None))
    assert gs.GPU_ADVISORY_LOCK_KEY not in keys
    assert set(gs.resolve_lock_keys("image_gen", None)) <= keys


def test_malformed_scope_map_falls_back_to_defaults(scoped):
    scoped(gpu_lock_per_device_enabled="true", gpu_lock_scopes="{not json")
    keys = gs.resolve_lock_keys("image_gen", None)
    assert keys == sorted({gs.device_lock_key(gs.gpu_lock_node_id(), 0)})


# --- role resolution ---------------------------------------------------------


@pytest.mark.parametrize("owner", ["image_gen", "video"])
def test_render_owners_map_to_the_render_role(scoped, owner):
    scoped()
    assert gs.resolve_lock_role(owner, None) == "render"


def test_ollama_without_an_endpoint_override_is_primary(scoped):
    scoped()
    assert gs.resolve_lock_role("ollama", "gemma-4-31B-it-qat:latest") == "llm_primary"


def test_ollama_with_an_endpoint_override_is_the_judge(scoped):
    scoped(
        **{
            "plugin.llm_provider.litellm": json.dumps(
                {"config": {"model_api_base_overrides": {
                    "ollama/qwen3-vl:30b": "http://host.docker.internal:11435"
                }}}
            )
        }
    )
    # Call sites pass the bare name; the map is keyed with the provider prefix.
    assert gs.resolve_lock_role("ollama", "qwen3-vl:30b") == "qa_judge"
    assert gs.resolve_lock_role("ollama", "ollama/qwen3-vl:30b") == "qa_judge"


def test_removing_the_override_restores_serialisation(scoped):
    """THE unpin safety property, as a test.

    Unpinning GPU 1 means deleting the endpoint override. The judge then
    resolves to `llm_primary`, whose set overlaps render's, so the two
    serialise again — automatically, with no code change.
    """
    overrides = {"config": {"model_api_base_overrides": {
        "ollama/qwen3-vl:30b": "http://host.docker.internal:11435"}}}
    pinned = scoped(
        gpu_lock_per_device_enabled="true",
        **{"plugin.llm_provider.litellm": json.dumps(overrides)},
    )
    judge = set(gs.resolve_lock_keys("ollama", "qwen3-vl:30b"))
    render = set(gs.resolve_lock_keys("image_gen", None))
    assert judge.isdisjoint(render), "pinned judge should not share render's card"

    # ... now unpin: the override is gone.
    unpinned = scoped(gpu_lock_per_device_enabled="true")
    assert unpinned is not pinned
    judge_after = set(gs.resolve_lock_keys("ollama", "qwen3-vl:30b"))
    render_after = set(gs.resolve_lock_keys("image_gen", None))
    assert not judge_after.isdisjoint(render_after), (
        "unpinned judge MUST overlap render again — otherwise two workloads "
        "share a card with no mutual exclusion"
    )


# --- overlap semantics -------------------------------------------------------


def test_default_map_grants_judge_render_concurrency(scoped):
    """Phase 3: the shipped map is now genuinely split.

    Phase 2 shipped `llm_primary: [0, 1]` so every set overlapped and enabling
    scoping changed nothing. Phase 3 narrows it to [0] because
    scripts/linux/ollama-primary.sh UUID-pins primary to GPU 0 and refuses to
    start unpinned — so the judge on GPU 1 is disjoint from BOTH render and
    primary, which is the whole point of the change.
    """
    scoped(
        gpu_lock_per_device_enabled="true",
        **{"plugin.llm_provider.litellm": json.dumps(
            {"config": {"model_api_base_overrides": {
                "ollama/qwen3-vl:30b": "http://x:11435"}}}
        )},
    )
    primary = set(gs.resolve_lock_keys("ollama", "some-writer"))
    render = set(gs.resolve_lock_keys("image_gen", None))
    judge = set(gs.resolve_lock_keys("ollama", "qwen3-vl:30b"))
    assert judge.isdisjoint(render), "judge must not queue behind renders"
    assert judge.isdisjoint(primary), "judge must not queue behind the writer"
    assert primary == render, "primary and render both hold GPU 0"


def test_every_declared_split_is_backed_by_an_enforced_pin():
    """A narrowed scope is a CLAIM about hardware. Something must enforce it.

    Declaring `llm_primary: [0]` while ollama-primary can still land on GPU 1
    is strictly worse than not splitting at all: the lock stops serialising
    primary against the judge while the hardware still lets them collide on
    one card. So if the shipped map gives one role a card another role lacks,
    the pin scripts that make that true must exist and must refuse to start
    unpinned.

    A coupling test on purpose — the config and the pin are one change and
    must never drift apart.
    """
    from pathlib import Path

    scopes = gs.DEFAULT_GPU_LOCK_SCOPES
    judge = set(scopes.get("qa_judge", []))
    primary = set(scopes.get("llm_primary", []))
    if judge.isdisjoint(primary):
        repo = Path(gs.__file__).resolve().parents[3]
        for script, card in (
            ("scripts/linux/ollama-primary.sh", "0"),
            ("scripts/linux/ollama-vision.sh", "1"),
        ):
            path = repo / script
            assert path.is_file(), (
                f"{script} must exist: the map claims a split only that pin "
                f"makes true"
            )
            body = path.read_text(encoding="utf-8")
            assert f"-i {card}" in body, f"{script} must resolve GPU {card}"
            assert "refusing to start unpinned" in body, (
                f"{script} must FAIL rather than start unpinned — a pin that "
                f"silently degrades to 'any GPU' turns the declared split into "
                f"two workloads sharing a card with no mutual exclusion"
            )
            assert "OLLAMA_VULKAN=false" in body, (
                f"{script} must disable Vulkan — Ollama's Vulkan backend "
                f"ignores CUDA_VISIBLE_DEVICES, so the pin would be a no-op"
            )


def test_pinning_primary_makes_judge_and_render_disjoint(scoped):
    """Phase 3 in one assertion — config only, no code change."""
    scoped(
        gpu_lock_per_device_enabled="true",
        gpu_lock_scopes=json.dumps(
            {"render": [0], "qa_judge": [1], "llm_primary": [0]}
        ),
        **{"plugin.llm_provider.litellm": json.dumps(
            {"config": {"model_api_base_overrides": {
                "ollama/qwen3-vl:30b": "http://x:11435"}}}
        )},
    )
    judge = set(gs.resolve_lock_keys("ollama", "qwen3-vl:30b"))
    render = set(gs.resolve_lock_keys("image_gen", None))
    primary = set(gs.resolve_lock_keys("ollama", "writer"))
    assert judge.isdisjoint(render)
    assert judge.isdisjoint(primary)
    assert primary == render, "primary and render both hold GPU 0"


def test_empty_device_set_takes_no_lock_at_all(scoped):
    """Managed API / serverless / CPU judge: nothing to contend for.

    First-class, not an edge case — it is why the cloud shapes need no
    special code path.
    """
    scoped(
        gpu_lock_per_device_enabled="true",
        gpu_lock_scopes=json.dumps({"render": [0], "qa_judge": [], "llm_primary": [0]}),
        **{"plugin.llm_provider.litellm": json.dumps(
            {"config": {"model_api_base_overrides": {"ollama/judge": "http://x"}}}
        )},
    )
    assert gs.resolve_lock_keys("ollama", "judge") == []


# --- key derivation ----------------------------------------------------------


def test_keys_are_node_scoped(scoped):
    """A GPU index is only unique within a host.

    Without this, two nodes sharing one Postgres serialise on the same key and
    the fleet gets one GPU's worth of throughput.
    """
    assert gs.device_lock_key("node-a", 0) != gs.device_lock_key("node-b", 0)
    assert gs.device_lock_key("node-a", 0) != gs.device_lock_key("node-a", 1)
    assert gs.device_lock_key("node-a", 0) == gs.device_lock_key("node-a", 0)


def test_keys_never_collide_with_the_whole_gpu_key_or_siblings():
    keys = {gs.device_lock_key("n", i) for i in range(8)}
    assert gs.GPU_ADVISORY_LOCK_KEY not in keys
    assert 0x50AC not in keys  # _SOCIAL_POST_LOCK_NS
    assert 0x4D44 not in keys  # _MEDIA_DISPATCH_LOCK_NS
    assert all(k < 2**63 - 1 for k in keys), "must stay a valid int64"


def test_keys_come_back_sorted(scoped):
    """Ascending order is the deadlock proof — callers acquire in this order."""
    scoped(
        gpu_lock_per_device_enabled="true",
        gpu_lock_scopes=json.dumps({"llm_primary": [3, 1, 2, 0]}),
    )
    keys = gs.resolve_lock_keys("ollama", "writer")
    assert keys == sorted(keys)
    assert len(keys) == 4


def test_node_id_prefers_the_explicit_setting(scoped):
    scoped(gpu_lock_node_id="worker-7")
    assert gs.gpu_lock_node_id() == "worker-7"


# --- node identity: the 2026-08-29 near-miss --------------------------------


def test_container_hostname_is_never_used_as_a_node_id(scoped, monkeypatch):
    """A container id is not a node identity, and using one loses the lock.

    Caught live before it did damage: `socket.gethostname()` inside a container
    returns the container id, so poindexter-worker (4cca811f97a6) and
    poindexter-prefect-worker (c7648be9330d) — two containers driving the SAME
    two cards — derived completely different keys for GPU 0. Different keys for
    one card is NO mutual exclusion, i.e. the exact CUDA OOM this lock exists
    to prevent. And a container id changes on every recreate, so it drifts.
    """
    scoped(gpu_lock_per_device_enabled="true", gpu_lock_node_id="")
    monkeypatch.setattr(gs, "_running_in_container", lambda: True)
    assert gs.gpu_lock_node_id() == "", "containerised + unset must be unusable"
    # ... and the caller must fall back rather than split on a bad identity.
    assert gs.resolve_lock_keys("image_gen", None) == [gs.GPU_ADVISORY_LOCK_KEY]


def test_explicit_node_id_wins_even_in_a_container(scoped, monkeypatch):
    """The operator declaring a stable identity is the supported fix."""
    scoped(gpu_lock_per_device_enabled="true", gpu_lock_node_id="pop-os")
    monkeypatch.setattr(gs, "_running_in_container", lambda: True)
    assert gs.gpu_lock_node_id() == "pop-os"
    assert gs.resolve_lock_keys("image_gen", None) == [
        gs.device_lock_key("pop-os", 0)
    ]


def test_bare_host_may_use_its_hostname(scoped, monkeypatch):
    scoped(gpu_lock_per_device_enabled="true", gpu_lock_node_id="")
    monkeypatch.setattr(gs, "_running_in_container", lambda: False)
    assert gs.gpu_lock_node_id() != ""


def test_undetectable_environment_is_treated_as_containerised(monkeypatch):
    """Cannot tell ⇒ demand the explicit setting. Conservative on purpose.

    `_running_in_container` swallows its own errors and answers True, so an
    environment it cannot classify still forces `gpu_lock_node_id` rather than
    trusting a hostname that might be a container id.
    """
    import builtins

    real_open = builtins.open

    def _deny_cgroup(path, *a, **k):
        if str(path).startswith("/proc/1/cgroup"):
            raise OSError("denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", _deny_cgroup)
    monkeypatch.setattr(gs.os_path_exists_for_test, "value", False, raising=False) \
        if hasattr(gs, "os_path_exists_for_test") else None
    # /.dockerenv may genuinely exist here; the assertion that matters is that
    # an unreadable cgroup never yields a confident "not a container".
    assert gs._running_in_container() is True


# --- fail-closed must BLOCK, not pick a third key (live bug, 2026-08-31) -----


def test_unresolvable_caller_takes_every_device_key_not_the_base_key(scoped):
    """The bug that shipped: fail-closed returned the whole-GPU key.

    Once ANY caller is scoped, the base key excludes nobody — it is simply a
    third key. Observed live with poindexter-worker holding 7777777777 and
    10738779002 at the same moment, i.e. two GPU workloads with no mutual
    exclusion. Fail closed has to mean "block every card".
    """
    scoped(gpu_lock_per_device_enabled="true")
    keys = gs.resolve_lock_keys("some_unknown_owner", None)
    assert gs.GPU_ADVISORY_LOCK_KEY not in keys, (
        "the base key excludes nobody once others are scoped"
    )
    render = set(gs.resolve_lock_keys("image_gen", None))
    judge = set(gs.resolve_lock_keys("ollama", "qwen3-vl:30b"))
    assert render <= set(keys) and judge <= set(keys), (
        "an unresolvable caller must block BOTH cards"
    )


def test_missing_role_also_takes_every_device_key(scoped):
    scoped(
        gpu_lock_per_device_enabled="true",
        gpu_lock_scopes=json.dumps({"render": [0], "qa_judge": [1]}),
    )
    keys = set(gs.resolve_lock_keys("ollama", "writer"))  # llm_primary absent
    assert gs.GPU_ADVISORY_LOCK_KEY not in keys
    assert set(gs.resolve_lock_keys("image_gen", None)) <= keys
    assert set(gs.resolve_lock_keys("ollama", "qwen3-vl:30b")) <= keys
