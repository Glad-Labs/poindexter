"""writer_self_review minimal-edit contract (Glad-Labs/poindexter#1000).

Prod task 1bdf0360 (2026-08-07): the contradiction-revise pass returned 87
lines of its own deliberation — "Role: Reviewer checking for internal
contradictions.", "Wait, let me re-read carefully…", "I'll provide the
original text." — with the article fused onto the last bullet. It was
13,567 chars from a ~4,760-char draft (2.85x) and rode to awaiting_approval
at quality 94, because the only length guard was a FLOOR (>= 0.7x).

A contradiction fix edits sentences. It never triples the article, and it
never narrates its own reasoning. These tests pin both bounds plus the
scaffold-shape rejection, and that a rejected revision keeps the ORIGINAL
draft (which was clean) rather than shipping the dump.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.self_review import self_review_and_revise

_DRAFT = "This draft has enough substance for a cross-section review. " * 15
_CONTRADICTIONS = "1. SECTION A conflicts with SECTION B: the claims disagree."

# Verbatim shape of the 1bdf0360 leak, fused onto the article with no blank
# line — exactly how it arrived in prod.
_DELIBERATION_DUMP = (
    "*   Role: Reviewer checking for internal contradictions.\n"
    '    *   Input: A draft titled "Why Revenue is Exhaust".\n'
    "    *   Task: Fix specific contradictions and *nothing else*.\n"
    "    *   Output Format: Revised draft only. Preserve image markers.\n"
    "    *   Constraint 1: Fix *only* the identified contradictions.\n"
    "    *   Constraint 2: Output only the revised draft.\n"
    "    *   Section 1: MRR charts hide the struggle.\n"
    "    *   Section 2: Money is a byproduct of excellence.\n"
    "    Wait, let me re-read carefully. The prompt says fix contradictions.\n"
    "    Let's double-check the sections one more time.\n"
    "    If the user's own analysis concludes PASS, there is nothing to fix.\n"
    "    Is there any other interpretation? I don't think so.\n"
    "    I'll provide the original text." + _DRAFT
)


def _result(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    return r


def _sc(*, min_ratio: float = 0.7, max_ratio: float = 1.3) -> MagicMock:
    sc = MagicMock()

    def _get(key: str, default: object = None) -> object:
        if key == "enable_writer_self_review":
            return "true"
        if key == "writer_self_review_model":
            return "ollama/gemma-4-31B-it-qat:latest"
        return default

    def _get_float(key: str, default: float = 0.0) -> float:
        if key == "writer_self_review_min_length_ratio":
            return min_ratio
        if key == "writer_self_review_max_length_ratio":
            return max_ratio
        return default

    sc.get.side_effect = _get
    sc.get_float.side_effect = _get_float
    sc.get_int.return_value = 8000
    return sc


async def _run(revision: str, site_config: MagicMock | None = None):
    """Drive one self-review round-trip whose revise call returns ``revision``."""
    dispatch = AsyncMock(side_effect=[_result(_CONTRADICTIONS), _result(revision)])
    findings: list[dict] = []
    with patch(
             "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm, \
         patch("utils.findings.emit_finding", side_effect=lambda **kw: findings.append(kw)):
        pm.return_value.get_prompt.return_value = "PROMPT"
        out, stats = await self_review_and_revise(
            _DRAFT, "A Title", "A Topic",
            pool=object(),
            site_config=site_config or _sc(),
        )
    return out, stats, findings


@pytest.mark.unit
class TestMinimalEditContract:
    async def test_deliberation_dump_is_rejected_and_original_kept(self):
        """THE regression: the verbatim prod leak must not become the draft."""
        out, stats, findings = await _run(_DELIBERATION_DUMP)
        assert out == _DRAFT, "the clean original draft must survive"
        assert stats["revised"] is False
        assert "rejected_reason" in stats
        assert [f["kind"] for f in findings] == ["self_review_revision_rejected"]

    async def test_overlong_revision_rejected(self):
        """A revise pass that triples the article has failed, whatever it
        returned — the old guard had no ceiling at all."""
        out, stats, _ = await _run("z" * (len(_DRAFT) * 3))
        assert out == _DRAFT
        assert stats["revised"] is False
        assert "too long" in stats["rejected_reason"]

    async def test_too_short_revision_still_rejected(self):
        """Pre-existing floor behaviour is preserved."""
        out, stats, _ = await _run("z" * 10)
        assert out == _DRAFT
        assert stats["revised"] is False
        assert "too short" in stats["rejected_reason"]

    async def test_legitimate_minimal_edit_is_accepted(self):
        """A same-size revision — the normal case — still flows through."""
        revision = _DRAFT.replace("substance", "substance and clarity")
        out, stats, findings = await _run(revision)
        assert out == revision.strip()  # the stage strips surrounding space
        assert stats["revised"] is True
        assert findings == []

    async def test_bounds_are_db_tunable(self):
        """Widening the ceiling admits a revision the default would reject
        (the knobs are real app_settings, not constants)."""
        revision = "z" * int(len(_DRAFT) * 1.8)
        out, stats, _ = await _run(revision, site_config=_sc(max_ratio=2.0))
        assert out == revision.strip()
        assert stats["revised"] is True
