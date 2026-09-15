"""``codebase`` is retired: nothing registers it, so nothing can schedule it.

Superseded by ``InternalRagSource`` (poindexter#822), which mines the same
``embeddings`` table with storyworthy ranking. Measured 2026-09-15 on this
install: InternalRagSource produced **6,508** topic records across two taps,
CodebaseSource produced **0** — it had no ``external_taps`` row, and a plugin
row only PERMITS a source, it never schedules one.

The trap these tests close is the one the retired guardrails rails had: the
module's config block advertised ``enabled (default true)``, so every config
surface read as if it were live while nothing could ever run it. Reviving it
must take TWO deliberate steps — re-register AND create a tap row — so one edit
cannot resurrect a source that produces nothing.
"""
from __future__ import annotations

import pathlib

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parents[4]
_MODULE = _BACKEND / "poindexter" / "services" / "topic_sources" / "codebase.py"
_PYPROJECT = _BACKEND / "pyproject.toml"
_REGISTRY = _BACKEND / "poindexter" / "plugins" / "registry.py"


# Both DECLARATION sites are asserted, not the live registry: entry points are
# resolved from installed distribution metadata, which is a build artifact and
# stays stale until a reinstall. Testing what ships is environment-independent;
# testing the live registry would pass or fail on how recently someone ran pip.
def test_codebase_has_no_entry_point_declaration():
    text = _PYPROJECT.read_text()
    assert "poindexter.services.topic_sources.codebase" not in text, (
        "the entry point is the REAL registration path -- _SAMPLES is only the "
        "core-samples fallback, which is why removing just the sample left it live"
    )


def test_codebase_is_not_in_core_samples():
    """The fallback registry must not re-add what the entry point dropped."""
    text = _REGISTRY.read_text()
    live = [
        ln for ln in text.splitlines()
        if "CodebaseSource" in ln and not ln.lstrip().startswith("#")
    ]
    assert live == [], f"codebase still registered in _SAMPLES: {live}"


def test_internal_rag_is_the_live_successor():
    """It resolves via a handler branch, not the registry — so assert the branch."""
    handler = (
        _BACKEND / "poindexter" / "services" / "integrations" / "handlers"
        / "tap_builtin_topic_source.py"
    )
    src = handler.read_text()
    assert 'source_name == "internal_rag"' in src
    assert "InternalRagSource" in src


def test_an_unregistered_source_fails_loud():
    """Never a silent no-op — the operator must be told the name is unknown."""
    handler = (
        _BACKEND / "poindexter" / "services" / "integrations" / "handlers"
        / "tap_builtin_topic_source.py"
    )
    src = handler.read_text()
    assert "is not a " in src and "registered topic_source plugin" in src


@pytest.mark.skipif(not _MODULE.is_file(), reason="module stripped from this tree")
def test_the_module_says_it_is_retired():
    """The docstring must not still read as a live, enabled source."""
    text = _MODULE.read_text()
    assert "RETIRED" in text
    assert "InternalRagSource" in text, "a retirement note must name the successor"


@pytest.mark.skipif(not _MODULE.is_file(), reason="module stripped from this tree")
def test_the_class_still_exists_so_revival_stays_possible():
    """Kept deliberately — retiring the claim, not deleting the approach."""
    assert "class CodebaseSource" in _MODULE.read_text()
