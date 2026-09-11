"""``content.plan_image_markers`` tops up illustrations instead of short-circuiting.

The regression (2026-09-02 → 09-08): the atom ran the Image Decision Agent ONLY
when the draft carried no ``[IMAGE-N]`` markers. Once the two_pass writer prompt
offered ``[SCREENSHOT:]``, a single screenshot marker made "markers present"
true, the agent never ran, and every canonical_blog draft that took the offer
shipped with one dashboard capture and zero generated illustrations.

Three properties pinned here:

1. Evidence markers do not spend the illustration budget — the agent is asked
   for the FULL remaining count, numbered after the writer's markers.
2. The merged body is renumbered in document order and ``image_plans`` is
   rebuilt from it, so numbers and plans can never disagree.
3. A failed top-up keeps the writer's markers and says so in the finding.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from poindexter.modules.content.atoms import content_plan_image_markers
from poindexter.modules.content.atoms._image_helpers import _plan_and_inject_placeholders
from poindexter.modules.content.atoms._writer_markers import (
    is_evidence_desc,
    number_inline_markers,
    renumber_placeholders,
)
from poindexter.services.site_config import SiteConfig

_UNLOAD = "poindexter.services.llm_providers.ollama_unload.maybe_unload_writer_before_image_gen"
_PLAN = "poindexter.modules.content.atoms._image_helpers.plan_and_inject_placeholders"


def _sc(**overrides: str) -> SiteConfig:
    cfg = {"writer_max_inline_images": "3", "writer_max_evidence_per_kind": "1"}
    cfg.update(overrides)
    return SiteConfig(initial_config=cfg)


# --- pure helpers ------------------------------------------------------------


class TestEvidenceBudget:
    def test_screenshot_no_longer_spends_an_illustration_slot(self):
        body = "[SCREENSHOT: qa-rails]\n[IMAGE: a]\n[IMAGE: b]\n[IMAGE: c]\n"
        out = number_inline_markers(body, 3, max_evidence_per_kind=1)
        # All three illustrations survive alongside the screenshot.
        assert "[IMAGE-1: screenshot:qa-rails]" in out
        assert "[IMAGE-2: a]" in out and "[IMAGE-3: b]" in out and "[IMAGE-4: c]" in out

    def test_each_evidence_kind_has_its_own_cap(self):
        body = "[SCREENSHOT: a]\n[SCREENSHOT: b]\n[CHART: x]\n[CHART: y]\n"
        out = number_inline_markers(body, 3, max_evidence_per_kind=1)
        assert "screenshot:a" in out and "screenshot:b" not in out
        assert "chart:x" in out and "chart:y" not in out
        # Numbering stays one dense document-order sequence.
        assert "[IMAGE-1: screenshot:a]" in out and "[IMAGE-2: chart:x]" in out
        assert "IMAGE-3" not in out

    def test_illustration_cap_is_unaffected_by_evidence(self):
        body = "[IMAGE: a]\n[SCREENSHOT: s]\n[IMAGE: b]\n[IMAGE: c]\n"
        out = number_inline_markers(body, 2, max_evidence_per_kind=1)
        assert "[IMAGE-1: a]" in out and "[IMAGE-2: screenshot:s]" in out
        assert "[IMAGE-3: b]" in out
        assert "c]" not in out  # third illustration stripped, screenshot kept

    def test_legacy_shared_cap_when_no_evidence_budget_given(self):
        """``max_evidence_per_kind=None`` keeps the old single-cap shape."""
        body = "[SCREENSHOT: a]\n[SCREENSHOT: b]\n[IMAGE: c]\n"
        out = number_inline_markers(body, 2)
        assert "screenshot:a" in out and "screenshot:b" in out
        assert "IMAGE-3" not in out and "[IMAGE: c]" not in out

    @pytest.mark.parametrize(
        "desc,expected",
        [
            ("screenshot:qa-rails", True),
            ("CHART: llm-decode-vs-delivered", True),
            ("  Screenshot:qa-rails", True),
            ("a server rack", False),
            ("", False),
        ],
    )
    def test_is_evidence_desc(self, desc, expected):
        assert is_evidence_desc(desc) is expected


class TestRenumber:
    def test_renumbers_in_document_order(self):
        body = "[IMAGE-2: b ||image_gen:flat||]\ntext\n[IMAGE-1: screenshot:qa]\n[IMAGE-7: c]"
        out = renumber_placeholders(body)
        assert out == "[IMAGE-1: b ||image_gen:flat||]\ntext\n[IMAGE-2: screenshot:qa]\n[IMAGE-3: c]"

    def test_bare_placeholder_survives(self):
        assert renumber_placeholders("[IMAGE-5]\n[IMAGE-9: x]") == "[IMAGE-1]\n[IMAGE-2: x]"

    def test_no_placeholders_is_identity(self):
        assert renumber_placeholders("plain [link](u) text") == "plain [link](u) text"


# --- the injector: numbering offset + already-illustrated sections ---------


class _Img:
    def __init__(self, heading, prompt="p"):
        self.section_heading = heading
        self.prompt = prompt
        self.source = "image_gen"
        self.style = "flat"
        self.position = "after_heading"
        self.reasoning = ""


class _Plan:
    def __init__(self, images):
        self.images = images
        self.featured_image = None
        self.error = ""


@pytest.mark.asyncio
async def test_injector_numbers_from_start_num_and_skips_illustrated_sections():
    body = (
        "Intro paragraph.\n\n"
        "## Alpha\n\nAlpha text.\n\n[IMAGE-1: screenshot:qa-rails]\n\nMore alpha.\n\n"
        "## Beta\n\nBeta text.\n\n"
        "## Gamma\n\nGamma text.\n"
    )
    plan = _Plan([_Img("Alpha", "a1"), _Img("Beta", "b1"), _Img("Gamma", "g1")])
    with patch(
        "poindexter.services.image_decision_agent.plan_images", new=AsyncMock(return_value=plan),
    ) as pi:
        out, info = await _plan_and_inject_placeholders(
            body, "topic", "technology", site_config=_sc(), max_images=2, start_num=2,
        )
    # Asked for exactly the remaining slots.
    assert pi.await_args.kwargs["max_images"] == 2
    # Alpha already carries the screenshot → skipped; Beta/Gamma get 2 and 3.
    assert "[IMAGE-2: b1 ||image_gen:flat||]" in out
    assert "[IMAGE-3: g1 ||image_gen:flat||]" in out
    assert out.count("[IMAGE-") == 3
    assert "a1" not in out
    assert info is None


@pytest.mark.asyncio
async def test_injector_with_no_slots_left_never_calls_the_agent():
    with patch("poindexter.services.image_decision_agent.plan_images", new=AsyncMock()) as pi:
        out, info = await _plan_and_inject_placeholders(
            "## A\n\ntext", "t", "c", site_config=_sc(), max_images=0,
        )
    pi.assert_not_awaited()
    assert out == "## A\n\ntext" and info is None


# --- the atom: top-up end to end ---------------------------------------------


@pytest.mark.asyncio
async def test_atom_tops_up_after_a_writer_screenshot():
    """The regression itself: one screenshot marker used to suppress the agent."""
    captured = {}

    async def fake_plan(content, topic, category, *, site_config, max_images, start_num):
        captured["max_images"] = max_images
        captured["start_num"] = start_num
        # Agent puts its picks BEFORE the writer's screenshot in the body.
        return (
            content.replace("## Beta", "[IMAGE-2: a desk ||image_gen:flat||]\n\n## Beta"),
            None,
        )

    with patch(_UNLOAD, new=AsyncMock(return_value=[])), patch(_PLAN, new=fake_plan):
        out = await content_plan_image_markers.run({
            "content": "Intro.\n\n## Beta\n\ntext\n\n## Gamma\n\n[SCREENSHOT: qa-rails]\n\nmore",
            "topic": "quality gates",
            "site_config": _sc(),
        })

    # Screenshot did not spend an illustration slot: all 3 remain, numbered after it.
    assert captured == {"max_images": 3, "start_num": 2}
    # Renumbered in document order; plans rebuilt from the final body.
    assert "[IMAGE-1: a desk ||image_gen:flat||]" in out["content"]
    assert "[IMAGE-2: screenshot:qa-rails]" in out["content"]
    plans = {p["num"]: p for p in out["image_plans"]}
    assert set(plans) == {"1", "2"}
    assert "screenshot_target" not in plans["1"]
    assert plans["2"]["screenshot_target"] == "qa-rails"


@pytest.mark.asyncio
async def test_atom_skips_the_agent_when_illustrations_fill_the_cap():
    with patch(_UNLOAD, new=AsyncMock(return_value=[])), patch(_PLAN, new=AsyncMock()) as plan:
        out = await content_plan_image_markers.run({
            "content": "Intro.\n\n[IMAGE: a]\n[IMAGE: b]\n[SCREENSHOT: qa-rails]\n",
            "topic": "t",
            "site_config": _sc(writer_max_inline_images="2"),
        })
    plan.assert_not_awaited()
    assert [p["num"] for p in out["image_plans"]] == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_atom_asks_for_only_the_remaining_slots():
    captured = {}

    async def fake_plan(content, topic, category, *, site_config, max_images, start_num):
        captured.update(max_images=max_images, start_num=start_num)
        return content, None

    with patch(_UNLOAD, new=AsyncMock(return_value=[])), patch(_PLAN, new=fake_plan):
        await content_plan_image_markers.run({
            "content": "Intro.\n\n[IMAGE: a]\n[CHART: llm-decode-vs-delivered]\n",
            "topic": "t",
            "site_config": _sc(),
        })
    # One illustration placed of three → two left, numbered after both markers.
    assert captured == {"max_images": 2, "start_num": 3}


@pytest.mark.asyncio
async def test_failed_topup_keeps_writer_markers_and_says_so():
    findings = []

    async def fake_plan(content, topic, category, *, site_config, **_kw):
        return content, {"agent_error": "Timeout: 300 s"}

    with patch(_UNLOAD, new=AsyncMock(return_value=[])), patch(_PLAN, new=fake_plan), patch(
        "poindexter.utils.findings.emit_finding", new=lambda **kw: findings.append(kw),
    ):
        out = await content_plan_image_markers.run({
            "content": "Intro.\n\n## A\n\n[SCREENSHOT: qa-rails]\n\ntext",
            "topic": "t",
            "task_id": "abcdef12-0000",
            "site_config": _sc(),
        })

    # The writer's marker survives the failed top-up.
    assert out["image_plans"][0]["screenshot_target"] == "qa-rails"
    assert out["stages"]["2c_image_agent_error"] == "Timeout: 300 s"
    assert len(findings) == 1
    assert "only its 1 writer-placed marker(s)" in findings[0]["body"]
    assert findings[0]["extra"]["writer_markers"] == 1
