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


def _cfg(**values):
    """Install a SiteConfig the module-level helpers will read."""
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


@pytest.mark.parametrize("owner", ["", "unknown_owner", "wan", None])
def test_unknown_owner_falls_back_to_whole_gpu(scoped, owner):
    scoped(gpu_lock_per_device_enabled="true")
    assert gs.resolve_lock_keys(owner or "", None) == [gs.GPU_ADVISORY_LOCK_KEY]


def test_role_missing_from_the_map_falls_back_to_whole_gpu(scoped):
    scoped(
        gpu_lock_per_device_enabled="true",
        gpu_lock_scopes=json.dumps({"render": [0]}),  # qa_judge/llm_primary absent
    )
    assert gs.resolve_lock_keys("ollama", None) == [gs.GPU_ADVISORY_LOCK_KEY]


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


def test_default_map_ships_inert_everything_overlaps(scoped):
    """Enabling scoping alone must change nothing.

    `llm_primary` holds both cards in the shipped map, so it intersects both
    render and qa_judge. Concurrency only appears once primary is pinned
    (Phase 3) — which is what makes this safe to merge.
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
    # Inertness is primary overlapping EVERY other role — checking only
    # primary-vs-render passes even when primary is pinned to [0], which is
    # the Phase 3 config and a real behaviour change. (Caught by mutation.)
    assert not primary.isdisjoint(render), "primary must still serialise vs render"
    assert not primary.isdisjoint(judge), (
        "primary must still serialise vs the judge — otherwise enabling "
        "scoping alone grants concurrency, and this no longer ships inert"
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
