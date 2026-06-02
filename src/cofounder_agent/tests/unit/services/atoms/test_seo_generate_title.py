"""Tests for services/atoms/seo_generate_title.py."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.atoms import seo_generate_title as atom
from services.atoms import _seo_common as sc

pytestmark = pytest.mark.asyncio


def _state(**over):
    s = {
        "content": "Body about the regex validator bug and the fix.",
        "topic": "regex validator",
        "title": "Regex Validator",
        "tags": ["regex", "validators"],
        "site_config": MagicMock(),
    }
    s.update(over)
    return s


def test_atom_meta_contract():
    assert atom.ATOM_META.name == "seo.generate_title"
    assert atom.ATOM_META.produces == ("seo_title",)
    assert "content" in atom.ATOM_META.requires


async def test_generates_and_caps_title(monkeypatch):
    long = "A Very Long SEO Title That Definitely Exceeds Sixty Characters For Sure Indeed"
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=f'"{long}"'))
    out = await atom.run(_state())
    assert out["seo_title"]
    assert len(out["seo_title"]) <= 60
    assert '"' not in out["seo_title"]


async def test_empty_content_noops():
    assert await atom.run(_state(content="")) == {}


async def test_llm_failure_degrades(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(side_effect=RuntimeError("boom")))
    warned = MagicMock()
    monkeypatch.setattr(sc, "degraded", warned)
    out = await atom.run(_state())
    assert out["seo_title"].startswith("Regex Validator")
    warned.assert_called_once()


async def test_blank_llm_output_falls_back(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value="   "))
    out = await atom.run(_state())
    assert out["seo_title"]  # non-empty via fallback
