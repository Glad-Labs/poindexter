"""Tests for services/atoms/seo_extract_keywords.py."""
from unittest.mock import AsyncMock, MagicMock

from modules.content.atoms import _seo_common as sc
from modules.content.atoms import seo_extract_keywords as atom

# No module-level asyncio mark: ``asyncio_mode = "auto"`` (pyproject.toml)
# already auto-marks coroutine tests. An explicit mark wrongly tagged the
# sync tests here, emitting a PytestWarning (Glad-Labs/glad-labs-stack#997).


def _state(**over):
    s = {
        "content": "regex validator firing eight times per post markdown links prose",
        "topic": "regex validator",
        "seo_title": "Regex Validator Bug",
        "site_config": MagicMock(),
    }
    s.update(over)
    return s


def test_atom_meta_produces():
    assert atom.ATOM_META.name == "seo.extract_keywords"
    assert set(atom.ATOM_META.produces) >= {"seo_keywords", "seo_keywords_list"}


async def test_dedupes_lowercases_caps_and_sets_flag(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(
        return_value="Regex, regex, Validator, markdown, prose, links, post"))
    out = await atom.run(_state())
    kws = out["seo_keywords_list"]
    assert kws == [k for i, k in enumerate(kws) if k not in kws[:i]]  # deduped
    assert all(k == k.lower() for k in kws)
    assert len(kws) <= 10
    assert out["seo_keywords"] == ", ".join(kws)
    assert out["stages"]["4_seo_metadata_generated"] is True


async def test_drops_hallucinated_keyword(monkeypatch):
    # "blockchain" is absent from the content → must be dropped
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value="regex, blockchain, validator"))
    out = await atom.run(_state())
    assert "blockchain" not in out["seo_keywords_list"]
    assert "regex" in out["seo_keywords_list"]


async def test_backfills_when_under_three(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value="regex"))
    monkeypatch.setattr(sc, "fallback_keywords", lambda s, count=10: ["validator", "markdown", "prose"])
    out = await atom.run(_state())
    assert len(out["seo_keywords_list"]) >= 3


async def test_llm_failure_degrades(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(sc, "degraded", MagicMock())
    monkeypatch.setattr(sc, "fallback_keywords", lambda s, count=5: ["Regex", "Validator"])
    out = await atom.run(_state())
    assert out["seo_keywords_list"] == ["regex", "validator"]
    sc.degraded.assert_called_once()
