"""Unit tests for ``plugins.probe_registry.BrainProbeRegistry``.

Module v1 Phase 4 (Glad-Labs/poindexter#490 — closes #239). The
registry is constructed once per worker lifespan, each module's
``register_probes(registry)`` writes into it, and the
``/api/modules/probes`` route surfaces the collected specs.
"""

from __future__ import annotations

import pytest

from plugins.probe_registry import BrainProbeRegistry, RegisteredProbe


async def _noop_probe() -> dict[str, str]:
    return {"ok": "true"}


@pytest.mark.unit
def test_register_returns_registered_probe():
    registry = BrainProbeRegistry()
    probe = registry.register(
        module="content",
        name="embedding_backlog",
        callable=_noop_probe,
        description="Embedding backlog depth under threshold",
        interval_seconds=120,
    )
    assert isinstance(probe, RegisteredProbe)
    assert probe.module == "content"
    assert probe.name == "embedding_backlog"
    assert probe.fqid == "content.embedding_backlog"
    assert probe.interval_seconds == 120


@pytest.mark.unit
def test_register_default_interval_is_300():
    """5-minute cadence matches the brain daemon's existing
    monitor_services loop."""
    registry = BrainProbeRegistry()
    probe = registry.register(
        module="content", name="stale_tasks", callable=_noop_probe,
    )
    assert probe.interval_seconds == 300


@pytest.mark.unit
def test_register_rejects_duplicate_fqid():
    """Per ``feedback_no_silent_defaults`` a duplicate registration
    is a bug — the registry refuses to silently overwrite."""
    registry = BrainProbeRegistry()
    registry.register(
        module="content", name="stale_tasks", callable=_noop_probe,
        description="first",
    )
    with pytest.raises(ValueError, match="duplicate probe registration"):
        registry.register(
            module="content", name="stale_tasks", callable=_noop_probe,
            description="second",
        )


@pytest.mark.unit
def test_register_rejects_empty_module_or_name():
    registry = BrainProbeRegistry()
    with pytest.raises(ValueError, match="both module and name"):
        registry.register(module="", name="x", callable=_noop_probe)
    with pytest.raises(ValueError, match="both module and name"):
        registry.register(module="x", name="", callable=_noop_probe)


@pytest.mark.unit
def test_probes_returns_insertion_order():
    """Deterministic iteration order keeps ``/api/modules/probes``
    output stable for snapshot tests + operator log parsing."""
    registry = BrainProbeRegistry()
    registry.register(module="a", name="z", callable=_noop_probe)
    registry.register(module="a", name="y", callable=_noop_probe)
    registry.register(module="b", name="x", callable=_noop_probe)
    fqids = [p.fqid for p in registry.probes()]
    assert fqids == ["a.z", "a.y", "b.x"]


@pytest.mark.unit
def test_by_module_filters_correctly():
    registry = BrainProbeRegistry()
    registry.register(module="content", name="a", callable=_noop_probe)
    registry.register(module="finance", name="b", callable=_noop_probe)
    registry.register(module="content", name="c", callable=_noop_probe)
    assert [p.fqid for p in registry.by_module("content")] == [
        "content.a", "content.c",
    ]
    assert [p.fqid for p in registry.by_module("finance")] == [
        "finance.b",
    ]
    assert registry.by_module("nonexistent") == []


@pytest.mark.unit
def test_contains_and_len_and_iter():
    registry = BrainProbeRegistry()
    assert len(registry) == 0
    assert "anything" not in registry
    registry.register(module="m", name="n", callable=_noop_probe)
    assert len(registry) == 1
    assert "m.n" in registry
    assert "other" not in registry
    # __iter__ yields RegisteredProbe instances
    items = list(registry)
    assert len(items) == 1
    assert items[0].fqid == "m.n"
