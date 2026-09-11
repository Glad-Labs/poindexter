"""``_load_core_sample`` logs at the level a failure deserves (poindexter#1046 step 7).

A plain ``pip install poindexter`` runs ``get_core_samples()`` on ``--help``; two
failures are expected in some builds and must be one WARNING line, not a traceback:
the sample's own module missing (the public mirror strips operator-only files the
``_SAMPLES`` list still names) and an optional extra missing (providers raise an
``ImportError`` with an actionable message). A real defect keeps the traceback.
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

from poindexter.plugins import registry


@pytest.fixture
def samples() -> dict[str, list]:
    return {"taps": [], "llm_providers": []}


def test_missing_sample_module_is_one_warning(samples, caplog):
    with caplog.at_level(logging.WARNING, logger=registry.logger.name):
        registry._load_core_sample(
            samples, "taps", "poindexter.services.taps.definitely_not_shipped_xyz", "Tap"
        )
    records = [r for r in caplog.records if "definitely_not_shipped_xyz" in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert "not shipped in this build" in records[0].getMessage()
    assert records[0].exc_info is None, "no traceback for an expected strip"
    assert samples["taps"] == []


def test_missing_optional_dependency_is_one_warning(samples, caplog, monkeypatch):
    mod = types.ModuleType("poindexter.plugins._needs_extra_probe")

    class _Provider:
        def __init__(self) -> None:
            raise ImportError(
                "ProbeProvider requires the 'probe_extra' package, which is not installed.\n"
                "Second line that must not be logged."
            )

    mod.ProbeProvider = _Provider  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "poindexter.plugins._needs_extra_probe", mod)
    with caplog.at_level(logging.WARNING, logger=registry.logger.name):
        registry._load_core_sample(
            samples, "llm_providers", "poindexter.plugins._needs_extra_probe", "ProbeProvider"
        )
    records = [r for r in caplog.records if "ProbeProvider" in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert "requires the 'probe_extra' package" in records[0].getMessage()
    assert "Second line" not in records[0].getMessage()
    assert records[0].exc_info is None


def test_transitive_missing_module_names_it(samples, caplog, monkeypatch):
    mod = types.ModuleType("poindexter.plugins._transitive_probe")

    class _Tap:
        def __init__(self) -> None:
            import prometheus_client_definitely_absent  # noqa: F401

    mod.Tap = _Tap  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "poindexter.plugins._transitive_probe", mod)
    with caplog.at_level(logging.WARNING, logger=registry.logger.name):
        registry._load_core_sample(samples, "taps", "poindexter.plugins._transitive_probe", "Tap")
    records = [r for r in caplog.records if "_transitive_probe" in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert "prometheus_client_definitely_absent" in records[0].getMessage()


def test_real_defect_keeps_the_traceback(samples, caplog, monkeypatch):
    mod = types.ModuleType("poindexter.plugins._broken_probe")

    class _Tap:
        def __init__(self) -> None:
            raise RuntimeError("constructor exploded")

    mod.Tap = _Tap  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "poindexter.plugins._broken_probe", mod)
    with caplog.at_level(logging.WARNING, logger=registry.logger.name):
        registry._load_core_sample(samples, "taps", "poindexter.plugins._broken_probe", "Tap")
    records = [r for r in caplog.records if "_broken_probe" in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None, "a real defect keeps its traceback"


def test_success_appends_an_instance(samples, monkeypatch):
    mod = types.ModuleType("poindexter.plugins._ok_probe")

    class _Tap:
        pass

    mod.Tap = _Tap  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "poindexter.plugins._ok_probe", mod)
    registry._load_core_sample(samples, "taps", "poindexter.plugins._ok_probe", "Tap")
    assert len(samples["taps"]) == 1 and isinstance(samples["taps"][0], _Tap)
