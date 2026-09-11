"""content.generate_title — the searchable-entity gate (2026-09-07).

The LLM + originality calls are patched at their ``services.title_generation``
names (the atom imports them inside ``run``), so these tests exercise the
gate's decision path: pass-through, one corrective regeneration, the
finding on a still-unsearchable title, and the advisory mode.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.atoms import content_generate_title as atom

_ORIGINAL = {"is_original": True, "similar_titles": [], "max_similarity": 0.0}


def _site_config(**overrides: Any) -> MagicMock:
    values = {
        "title_searchable_entity_enabled": True,
        "title_searchable_entity_mode": "regenerate",
    }
    values.update(overrides)
    sc = MagicMock()
    sc.get_bool.side_effect = lambda k, d=False: values.get(k, d)
    sc.get.side_effect = lambda k, d="": values.get(k, d)
    sc.get_int.side_effect = lambda k, d=0: values.get(k, d)
    return sc


def _state(**extra: Any) -> dict[str, Any]:
    db = MagicMock()
    db.pool = object()
    db.update_task = AsyncMock()
    base = {
        "content": "# Prefect stuck flows\n\nThe worker wedged on a checkpoint.",
        "topic": "prefect stuck flow reclaim",
        "tags": ["prefect"],
        "task_id": "t-1",
        "database_service": db,
        "site_config": _site_config(),
    }
    base.update(extra)
    return base


async def _run(state: dict[str, Any], titles: list[str]):
    """Run the atom with ``generate_canonical_title`` returning ``titles`` in order."""
    gen = AsyncMock(side_effect=list(titles) + [titles[-1]] * 3)
    with patch("poindexter.services.title_generation.generate_canonical_title", gen), patch(
        "poindexter.services.title_generation.check_title_originality",
        AsyncMock(return_value=dict(_ORIGINAL)),
    ), patch(
        "poindexter.services.title_generation.choose_canonical_title",
        side_effect=lambda topic, content, llm_title=None, **kw: llm_title or topic,
    ), patch(
        "poindexter.services.title_avoidance.build_avoidance_block_for_pool",
        AsyncMock(return_value="AVOID-BLOCK"),
    ), patch("utils.findings.emit_finding") as finding:
        result = await atom.run(state)
    return result, gen, finding


@pytest.mark.asyncio
async def test_searchable_title_passes_through_with_one_llm_call():
    result, gen, finding = await _run(_state(), ["Prefect Stuck Flows: Reclaiming Wedged Runs"])
    assert result["title"] == "Prefect Stuck Flows: Reclaiming Wedged Runs"
    assert gen.await_count == 1
    finding.assert_not_called()


@pytest.mark.asyncio
async def test_unsearchable_title_is_regenerated_once_with_the_directive():
    result, gen, finding = await _run(
        _state(), ["The Stuck Task", "Prefect Stuck Flows: A Checkpoint Reclaim Bug"],
    )
    assert result["title"] == "Prefect Stuck Flows: A Checkpoint Reclaim Bug"
    assert gen.await_count == 2
    # The retry carries the corrective block AND the original avoidance block.
    retry_block = gen.await_args_list[1].kwargs["avoidance_block"]
    assert "SEARCHABILITY" in retry_block and "AVOID-BLOCK" in retry_block
    assert "'The Stuck Task'" in retry_block
    assert "prefect" in retry_block
    finding.assert_not_called()


@pytest.mark.asyncio
async def test_keyword_match_counts_as_searchable_without_regeneration():
    # "stuck" is not a proper noun, but it is in the task's tag set.
    state = _state(tags=["stuck flows"], topic="prefect stuck flow reclaim")
    result, gen, _ = await _run(state, ["The stuck task"])
    assert result["title"] == "The stuck task"
    assert gen.await_count == 1


@pytest.mark.asyncio
async def test_still_unsearchable_after_regen_ships_original_and_emits_finding():
    result, gen, finding = await _run(
        _state(tags=[], topic="a thing that happened"),
        ["The Gap Nobody Names", "The five days nobody was watching"],
    )
    assert result["title"] == "The Gap Nobody Names"
    assert gen.await_count == 2
    finding.assert_called_once()
    kw = finding.call_args.kwargs
    assert kw["kind"] == "title_no_searchable_entity"
    assert kw["extra"]["regenerated_title"] == "The five days nobody was watching"
    assert kw["dedup_key"] == "title_no_searchable_entity:t-1"


@pytest.mark.asyncio
async def test_advisory_mode_never_regenerates_but_records():
    state = _state(tags=[], topic="a thing that happened")
    state["site_config"] = _site_config(title_searchable_entity_mode="advisory")
    result, gen, finding = await _run(state, ["The Gap Nobody Names"])
    assert result["title"] == "The Gap Nobody Names"
    assert gen.await_count == 1
    finding.assert_called_once()
    assert finding.call_args.kwargs["extra"]["mode"] == "advisory"


@pytest.mark.asyncio
async def test_disabled_gate_is_inert():
    state = _state(tags=[], topic="a thing that happened")
    state["site_config"] = _site_config(title_searchable_entity_enabled=False)
    result, gen, finding = await _run(state, ["The Gap Nobody Names"])
    assert result["title"] == "The Gap Nobody Names"
    assert gen.await_count == 1
    finding.assert_not_called()


def test_heading_terms_pull_entities_from_article_headings():
    text = "# Why Ollama evicts phi4:14b\n\nprose\n\n## The 32GB cliff on the RTX 5090\n"
    terms = atom._heading_terms(text)
    assert "Ollama" in terms and "32GB" in terms and "5090" in terms
