"""Tests for modules/content/atoms/seo_generate_all_metadata.py (poindexter#734).

Covers:
- ATOM_META contract (name, produces, requires)
- Successful structured JSON response → all three fields populated
- LLM returns JSON wrapped in a markdown code fence
- LLM returns JSON embedded in prose
- JSON parse failure → all fields fall back to programmatic derivations
- Missing individual fields → per-field programmatic fallback
- LLM failure (exception) → all fields degrade
- Empty content → no-op (returns {})
- Keywords anti-hallucination guard + floor backfill
- stages flag is set
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.atoms import _seo_common as sc
from modules.content.atoms import seo_generate_all_metadata as atom

# asyncio_mode = "auto" in pyproject.toml auto-marks coroutine tests.


def _state(**over):
    s = {
        "content": (
            "We shipped a fix for the regex validator bug. "
            "It was firing eight times per post and slowing everything down. "
            "The root cause was an unanchored pattern in the content_validator. "
            "Fixing it cut validation time by 80 percent."
        ),
        "topic": "regex validator bug fix",
        "title": "The Regex Validator Bug",
        "tags": ["regex validator", "bug fix", "content pipeline"],
        "site_config": MagicMock(),
        "database_service": None,
    }
    s.update(over)
    return s


_GOOD_JSON = (
    '{"title": "Regex Validator Bug Fix", '
    '"description": "We fixed a regex validator bug that was firing 8x per post. '
    'The unanchored pattern cut validation time by 80 percent.", '
    '"keywords": "regex validator, bug fix, content pipeline, validation, performance"}'
)


# ---------------------------------------------------------------------------
# ATOM_META contract
# ---------------------------------------------------------------------------

def test_atom_meta_name():
    assert atom.ATOM_META.name == "seo.generate_all_metadata"


def test_atom_meta_produces_all_three_plus_stages():
    produces = set(atom.ATOM_META.produces)
    assert {"seo_title", "seo_description", "seo_keywords", "seo_keywords_list", "stages"} <= produces


def test_atom_meta_requires_content():
    assert "content" in atom.ATOM_META.requires


def test_atom_meta_version():
    assert atom.ATOM_META.version == "1.0.0"


def test_atom_meta_tier():
    assert atom.ATOM_META.capability_tier == "cheap_critic"


# ---------------------------------------------------------------------------
# Happy-path: well-formed JSON response
# ---------------------------------------------------------------------------

async def test_all_fields_populated_from_json(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=_GOOD_JSON))
    out = await atom.run(_state())
    assert out["seo_title"]
    assert len(out["seo_title"]) <= 60
    assert out["seo_description"]
    assert len(out["seo_description"]) <= 160
    assert out["seo_keywords"]
    assert isinstance(out["seo_keywords_list"], list)
    assert len(out["seo_keywords_list"]) >= 1
    assert out["stages"].get("4_seo_metadata_generated") is True


async def test_json_in_markdown_fence(monkeypatch):
    fenced = f"```json\n{_GOOD_JSON}\n```"
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=fenced))
    out = await atom.run(_state())
    assert out["seo_title"]
    assert out["seo_description"]
    assert out["seo_keywords_list"]


async def test_json_embedded_in_prose(monkeypatch):
    prose = f"Sure, here you go:\n{_GOOD_JSON}\nHope that helps!"
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=prose))
    out = await atom.run(_state())
    assert out["seo_title"]
    assert out["seo_description"]


async def test_stages_flag_merged_with_existing(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=_GOOD_JSON))
    out = await atom.run(_state(stages={"3_qa_passed": True}))
    assert out["stages"]["3_qa_passed"] is True
    assert out["stages"]["4_seo_metadata_generated"] is True


# ---------------------------------------------------------------------------
# Title clamping
# ---------------------------------------------------------------------------

async def test_title_clamped_to_60_chars(monkeypatch):
    long_title = "A" * 80
    json_resp = (
        f'{{"title": "{long_title}", '
        '"description": "A good description here that is just long enough.", '
        '"keywords": "regex validator, bug fix"}}'
    )
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=json_resp))
    out = await atom.run(_state())
    assert len(out["seo_title"]) <= 60


# ---------------------------------------------------------------------------
# Missing individual fields → per-field fallbacks
# ---------------------------------------------------------------------------

async def test_missing_title_falls_back(monkeypatch):
    j = '{"description": "A good description.", "keywords": "regex validator"}'
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=j))
    out = await atom.run(_state())
    # Falls back to programmatic title derived from canonical_title/title/topic
    assert out["seo_title"]
    assert out["seo_description"] == "A good description."
    assert out["seo_keywords_list"]


async def test_missing_description_falls_back(monkeypatch):
    j = '{"title": "Regex Validator Bug Fix", "keywords": "regex validator"}'
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=j))
    out = await atom.run(_state())
    assert out["seo_title"]
    # Fallback description comes from first paragraph of content
    assert out["seo_description"]
    assert len(out["seo_description"]) <= 160


async def test_missing_keywords_falls_back(monkeypatch):
    j = '{"title": "Regex Validator Bug Fix", "description": "A good description."}'
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=j))
    monkeypatch.setattr(sc, "fallback_keywords", lambda state, count=5: ["regex", "validator", "bug"])
    out = await atom.run(_state())
    assert out["seo_keywords_list"] == ["regex", "validator", "bug"]


# ---------------------------------------------------------------------------
# JSON parse failure → all fields fall back
# ---------------------------------------------------------------------------

async def test_unparseable_json_degrades_all(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value="not json at all"))
    warned = MagicMock()
    monkeypatch.setattr(sc, "degraded", warned)
    monkeypatch.setattr(sc, "fallback_keywords", lambda state, count=5: ["regex", "validator"])
    out = await atom.run(_state())
    assert out["seo_title"]
    assert out["seo_description"]
    assert out["seo_keywords_list"]
    assert out["stages"]["4_seo_metadata_generated"] is True
    warned.assert_called_once()


# ---------------------------------------------------------------------------
# LLM exception → all fields degrade
# ---------------------------------------------------------------------------

async def test_llm_failure_degrades_all_fields(monkeypatch):
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(side_effect=RuntimeError("timeout")))
    warned = MagicMock()
    monkeypatch.setattr(sc, "degraded", warned)
    monkeypatch.setattr(sc, "fallback_keywords", lambda state, count=5: ["regex", "validator"])
    out = await atom.run(_state())
    assert out["seo_title"]
    assert out["seo_description"]
    assert out["seo_keywords_list"]
    assert out["stages"]["4_seo_metadata_generated"] is True
    warned.assert_called_once()


# ---------------------------------------------------------------------------
# Empty content → early exit
# ---------------------------------------------------------------------------

async def test_empty_content_noops():
    assert await atom.run(_state(content="")) == {}


async def test_missing_site_config_noops():
    assert await atom.run(_state(site_config=None)) == {}


# ---------------------------------------------------------------------------
# Keywords anti-hallucination guard
# ---------------------------------------------------------------------------

async def test_keywords_hallucination_filtered(monkeypatch):
    # Keywords that aren't in the content should be dropped; floor fills from fallback
    j = (
        '{"title": "Regex Fix", "description": "We fixed a regex validator bug.", '
        '"keywords": "unicorn magic, rainbow sparkles, regex validator, bug fix"}'
    )
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=j))
    monkeypatch.setattr(
        sc, "fallback_keywords",
        lambda state, count=5: ["content pipeline", "performance", "fix"],
    )
    out = await atom.run(_state())
    kws = out["seo_keywords_list"]
    # Hallucinated keywords not in content should be stripped
    assert "unicorn magic" not in kws
    assert "rainbow sparkles" not in kws
    # Real keywords present in content should survive
    assert any("regex" in k for k in kws)


async def test_keywords_floor_backfill(monkeypatch):
    # Provide only 1 passing keyword → backfill to floor of 3
    j = (
        '{"title": "Regex Fix", "description": "A description.", '
        '"keywords": "regex validator"}'
    )
    monkeypatch.setattr(sc, "run_seo_llm", AsyncMock(return_value=j))
    monkeypatch.setattr(
        sc, "fallback_keywords",
        lambda state, count=5: ["performance", "pipeline", "content"],
    )
    out = await atom.run(_state())
    assert len(out["seo_keywords_list"]) >= 3
