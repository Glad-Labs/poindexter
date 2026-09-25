"""Which cards an Ollama INSTANCE loads onto (gpu_scheduler, 2026-09-25).

The cold-load guard used to run the render-GPU reclaim ladder for every big
cold load, including the qwen3-vl judge cold-loading on the GPU-1-pinned
:11435 instance. That load never touches the render card, so the ladder
evicted and restarted media sidecars for nothing. ``ollama_host_devices`` /
``render_devices`` answer "does this load miss the render card?" from the
instance URL, using the same ``gpu_lock_scopes`` claim the GPU lock uses.

Invariant under test: **only a proven miss is a miss.** Every unknown returns
None (the caller then reclaims as before), because a skipped reclaim that was
needed is the 2026-08-25 CUDA OOM, and a needless one only costs sidecar
reloads.
"""
from __future__ import annotations

import json

import pytest

from poindexter.services import gpu_scheduler as gs
from poindexter.services.site_config import SiteConfig

_PRIMARY = "http://host.docker.internal:11434"
_JUDGE = "http://host.docker.internal:11435"

#: The operator box, as the prod rows read on 2026-09-25.
_PROD = {
    "gpu_lock_per_device_enabled": "true",
    "gpu_lock_scopes": json.dumps(
        {"render": [0], "qa_judge": [1], "llm_primary": [0]}
    ),
    "ollama_base_url": _PRIMARY,
    "ollama_vision_base_url": _JUDGE,
    "pipeline_gpu_index": "0",
}


@pytest.fixture
def config(monkeypatch):
    # SiteConfig.get falls back to the upper-cased env var for a missing key,
    # and worker containers export OLLAMA_BASE_URL. Keep every key these tests
    # leave unset actually unset.
    for key in (*_PROD, "gpu_lock_node_id"):
        monkeypatch.delenv(key.upper(), raising=False)

    def _apply(**values):
        cfg = SiteConfig(initial_config={str(k): str(v) for k, v in values.items()})
        monkeypatch.setattr(gs, "_sc", lambda: cfg)
        return cfg

    return _apply


# --- the operator box --------------------------------------------------------


@pytest.mark.unit
def test_judge_instance_misses_the_render_gpu(config):
    config(**_PROD)
    assert gs.ollama_host_devices(_JUDGE) == frozenset({1})
    assert gs.render_devices() == frozenset({0})


@pytest.mark.unit
def test_primary_instance_shares_the_render_gpu(config):
    config(**_PROD)
    assert gs.ollama_host_devices(_PRIMARY) == frozenset({0})
    assert not gs.ollama_host_devices(_PRIMARY).isdisjoint(gs.render_devices())


@pytest.mark.unit
def test_roles_come_from_the_instance_url(config):
    config(**_PROD)
    assert gs.ollama_host_role(_PRIMARY) == "llm_primary"
    assert gs.ollama_host_role(_JUDGE) == "qa_judge"


# --- unknown is None, never a guess ------------------------------------------


@pytest.mark.unit
def test_unrecognised_instance_is_unknown(config):
    """A third instance (another override target) has no declared placement."""
    config(**_PROD)
    assert gs.ollama_host_role("http://host.docker.internal:11436") == ""
    assert gs.ollama_host_devices("http://host.docker.internal:11436") is None


@pytest.mark.unit
@pytest.mark.parametrize("url", ["", None, "   "])
def test_blank_url_is_unknown(config, url):
    config(**_PROD)
    assert gs.ollama_host_devices(url) is None


@pytest.mark.unit
def test_unset_vision_url_leaves_the_judge_unknown(config):
    """Single-instance installs never set it; nothing may be inferred."""
    config(**{**_PROD, "ollama_vision_base_url": ""})
    assert gs.ollama_host_devices(_JUDGE) is None


@pytest.mark.unit
def test_scoping_off_trusts_no_placement(config):
    """The shipped two-card map describes the operator box, not every install.

    It becomes a claim about THIS hardware only when the operator turns
    device scoping on, after the pins are verified.
    """
    config(**{**_PROD, "gpu_lock_per_device_enabled": "false"})
    assert gs.ollama_host_devices(_JUDGE) is None
    assert gs.render_devices() is None


@pytest.mark.unit
def test_scoping_off_is_the_default(config):
    config(ollama_base_url=_PRIMARY, ollama_vision_base_url=_JUDGE)
    assert gs.ollama_host_devices(_JUDGE) is None
    assert gs.render_devices() is None


@pytest.mark.unit
def test_role_missing_from_the_map_is_unknown(config):
    config(**{**_PROD, "gpu_lock_scopes": json.dumps({"render": [0]})})
    assert gs.ollama_host_devices(_JUDGE) is None


@pytest.mark.unit
def test_render_missing_from_the_map_is_unknown(config):
    config(**{**_PROD, "gpu_lock_scopes": json.dumps({"qa_judge": [1]})})
    assert gs.render_devices() is None


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["{not json", "[0, 1]", '{"qa_judge": 1}'])
def test_malformed_map_is_unknown_not_the_defaults(config, raw):
    """The lock falls back to the defaults here; placement must not.

    The defaults split the judge from render, so reading a broken row as the
    defaults would claim a miss nobody declared.
    """
    config(**{**_PROD, "gpu_lock_scopes": raw})
    assert gs.ollama_host_devices(_JUDGE) is None
    assert gs.render_devices() is None


@pytest.mark.unit
def test_empty_scope_row_uses_the_shipped_map(config):
    """An unset row IS the shipped claim, same as for the lock."""
    config(**{**_PROD, "gpu_lock_scopes": ""})
    assert gs.ollama_host_devices(_JUDGE) == frozenset(
        gs.DEFAULT_GPU_LOCK_SCOPES["qa_judge"]
    )


# --- identity ----------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "spelling",
    [
        "http://host.docker.internal:11435/",
        "HTTP://Host.Docker.Internal:11435",
        "http://localhost:11435",
        "http://127.0.0.1:11435",
        "  http://host.docker.internal:11435  ",
    ],
)
def test_spellings_of_the_same_instance_match(config, spelling):
    """Case, a trailing slash and the loopback aliases the brain's
    ``localize_url`` rewrites never name a different instance."""
    config(**_PROD)
    assert gs.ollama_host_devices(spelling) == frozenset({1})


@pytest.mark.unit
def test_a_different_port_is_a_different_instance(config):
    config(**_PROD)
    assert gs.ollama_host_devices("http://host.docker.internal:21435") is None


@pytest.mark.unit
def test_primary_wins_when_the_vision_url_points_back_at_it(config):
    """Same server, same cards: the primary's, never the judge's."""
    config(**{**_PROD, "ollama_vision_base_url": _PRIMARY})
    assert gs.ollama_host_role(_PRIMARY) == "llm_primary"
    assert gs.ollama_host_devices(_PRIMARY) == frozenset({0})


@pytest.mark.unit
def test_unset_primary_url_means_the_code_default(config):
    """No ``ollama_base_url`` row: the primary is DEFAULT_OLLAMA_URL."""
    config(**{k: v for k, v in _PROD.items() if k != "ollama_base_url"})
    assert gs.ollama_host_role("http://localhost:11434") == "llm_primary"
    assert gs.ollama_host_role(_PRIMARY) == "llm_primary"


# --- the claim moves, the answer follows --------------------------------------


@pytest.mark.unit
def test_widening_the_judge_scope_reaches_the_render_gpu(config):
    """Unpinning the judge is the one edit the lock already requires."""
    config(**{**_PROD, "gpu_lock_scopes": json.dumps(
        {"render": [0], "qa_judge": [0, 1], "llm_primary": [0]}
    )})
    assert not gs.ollama_host_devices(_JUDGE).isdisjoint(gs.render_devices())


@pytest.mark.unit
def test_primary_pinned_off_the_render_gpu_misses_it(config):
    """Writer on one card, renders on the other: the primary is exempt too."""
    config(**{**_PROD, "gpu_lock_scopes": json.dumps(
        {"render": [0], "qa_judge": [1], "llm_primary": [1]}
    )})
    assert gs.ollama_host_devices(_PRIMARY).isdisjoint(gs.render_devices())


@pytest.mark.unit
def test_render_includes_the_card_the_ladder_measures(config):
    """If the two render settings disagree, keep every card either claims."""
    config(**{**_PROD, "pipeline_gpu_index": "1"})
    assert gs.render_devices() == frozenset({0, 1})
    assert not gs.ollama_host_devices(_JUDGE).isdisjoint(gs.render_devices())


@pytest.mark.unit
def test_an_empty_role_is_no_gpu(config):
    """The lock's "occupies no GPU" (a managed API) reads the same here."""
    config(**{**_PROD, "gpu_lock_scopes": json.dumps(
        {"render": [0], "qa_judge": [], "llm_primary": [0]}
    )})
    assert gs.ollama_host_devices(_JUDGE) == frozenset()


@pytest.mark.unit
def test_the_lock_still_reads_a_malformed_map_as_the_defaults(config):
    """The shared parser must not change the lock's own fallback."""
    config(**{**_PROD, "gpu_lock_scopes": "{not json", "gpu_lock_node_id": "n"})
    assert gs._configured_scopes() == gs.DEFAULT_GPU_LOCK_SCOPES
