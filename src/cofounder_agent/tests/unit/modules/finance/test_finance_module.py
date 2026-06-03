"""Unit tests for ``modules.finance.finance_module`` lifecycle wiring
relevant to Glad-Labs/poindexter#565.

Pins:
- ``register_probes`` registers the Mercury poll-staleness probe on a real
  :class:`plugins.probe_registry.BrainProbeRegistry` (the contract the F1
  docstring TODO'd), with the expected fqid + interval.
- ``register_probes(None)`` is a safe no-op (worker process passes None).
- ``register_probes`` fails loud on a non-registry argument
  (feedback_no_silent_defaults).
- ``refresh_module_metrics`` returns the finance refresh coroutine so the
  exporter's generic loop can await it.
"""

from __future__ import annotations

import inspect

import pytest

from modules.finance.finance_module import FinanceModule
from plugins.probe_registry import BrainProbeRegistry


@pytest.mark.unit
def test_register_probes_adds_poll_staleness_probe():
    registry = BrainProbeRegistry()
    FinanceModule().register_probes(registry)

    assert "finance.poll_staleness" in registry
    probe = registry._probes["finance.poll_staleness"]
    assert probe.module == "finance"
    assert probe.name == "poll_staleness"
    assert probe.interval_seconds == 300
    # The registered callable is the run function the brain will invoke.
    from modules.finance.probes import run_finance_poll_staleness_probe

    assert probe.callable is run_finance_poll_staleness_probe
    assert "stall" in probe.description.lower()


@pytest.mark.unit
def test_register_probes_none_is_noop():
    # The worker lifespan passes None when the brain subsystem isn't hosted.
    FinanceModule().register_probes(None)  # must not raise


@pytest.mark.unit
def test_register_probes_rejects_non_registry():
    class _NotARegistry:
        pass

    with pytest.raises(RuntimeError, match="BrainProbeRegistry"):
        FinanceModule().register_probes(_NotARegistry())


@pytest.mark.unit
def test_refresh_module_metrics_returns_awaitable():
    # The exporter loop calls the hook and awaits whatever is awaitable.
    result = FinanceModule().refresh_module_metrics(pool=object())
    assert inspect.isawaitable(result)
    # Close the coroutine we deliberately didn't await (avoids a warning).
    result.close()
