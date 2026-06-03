"""Tests for services/atoms/seo_generate_description.py."""
from unittest.mock import AsyncMock, MagicMock

from services.atoms import _seo_common as sc
from services.atoms import seo_generate_description as atom

# No module-level asyncio mark: ``asyncio_mode = "auto"`` (pyproject.toml)
# already auto-marks coroutine tests. An explicit mark wrongly tagged the
# sync tests here, emitting a PytestWarning (Glad-Labs/glad-labs-stack#997).


def _state(**over):
    s = {
        "content": "First paragraph about the fix.\n\n# H\n\nMore.",
        "topic": "regex validator",
        "seo_title": "Fixing the Regex Validator",
        "site_config": MagicMock(),
    }
    s.update(over)
    return s


def test_atom_meta_requires_seo_title():
    assert atom.ATOM_META.name == "seo.generate_description"
    assert "seo_title" in atom.ATOM_META.requires
    assert atom.ATOM_META.produces == ("seo_description",)


async def test_caps_at_160(monkeypatch):
    long = "word " * 60
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=long))
    out = await atom.run(_state())
    assert len(out["seo_description"]) <= 160


async def test_blank_falls_back(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=""))
    out = await atom.run(_state())
    assert out["seo_description"].startswith("First paragraph")


async def test_llm_failure_degrades(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(side_effect=RuntimeError("x")))
    warned = MagicMock()
    monkeypatch.setattr(sc, "degraded", warned)
    out = await atom.run(_state())
    assert out["seo_description"]
    warned.assert_called_once()


async def test_empty_content_noops():
    assert await atom.run(_state(content="")) == {}
