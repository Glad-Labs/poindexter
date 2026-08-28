"""content.detect_contradictions / content.revise_contradictions.

Ports the coverage of the deleted ``test_writer_self_review.py`` (the stage
these two atoms replaced) and adds the contract the split exists for: the two
halves are separate graph nodes, so detection can run without revision and the
detector's output crosses between them through the ``contradiction_review``
state channel rather than a hidden in-node call.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from modules.content.atoms import (
    content_detect_contradictions as detect_atom,
)
from modules.content.atoms import (
    content_revise_contradictions as revise_atom,
)


def _state(**over):
    base = {
        "content": "x" * 900,
        "title": "T",
        "topic": "Topic",
        "site_config": SimpleNamespace(get=lambda k, d=None: d),
        "database_service": SimpleNamespace(pool=None),
    }
    base.update(over)
    return base


@pytest.mark.unit
class TestAtomMetadata:
    def test_detect_declares_the_channel_it_produces(self):
        meta = detect_atom.ATOM_META
        assert meta.name == "content.detect_contradictions"
        assert "contradiction_review" in meta.produces

    def test_revise_requires_the_channel_detect_produces(self):
        """The seam between the two nodes. If these ever disagree the graph
        compiler's reachability check is what catches it."""
        assert "contradiction_review" in revise_atom.ATOM_META.requires
        assert "contradiction_review" in detect_atom.ATOM_META.produces

    def test_revise_produces_content(self):
        assert "content" in revise_atom.ATOM_META.produces


@pytest.mark.unit
@pytest.mark.asyncio
class TestDetect:
    async def test_passes_review_text_through_the_channel(self):
        with patch(
            "services.self_review.detect_contradictions",
            new=AsyncMock(return_value=("1. A conflicts with B", {"contradictions_found": 1})),
        ):
            out = await detect_atom.run(_state())
        assert out["contradiction_review"] == "1. A conflicts with B"

    async def test_clean_draft_yields_empty_channel(self):
        with patch(
            "services.self_review.detect_contradictions",
            new=AsyncMock(return_value=(None, {"contradictions_found": 0})),
        ):
            out = await detect_atom.run(_state())
        assert out["contradiction_review"] == ""

    async def test_never_edits_the_draft(self):
        """Detection is read-only — the whole point of splitting it out."""
        with patch(
            "services.self_review.detect_contradictions",
            new=AsyncMock(return_value=("1. x", {})),
        ):
            out = await detect_atom.run(_state())
        assert "content" not in out

    async def test_empty_content_skips(self):
        out = await detect_atom.run(_state(content=""))
        assert out == {"contradiction_review": ""}

    async def test_exception_is_non_fatal(self):
        """Mirrors the deleted stage's halts_on_failure=False."""
        with patch(
            "services.self_review.detect_contradictions",
            new=AsyncMock(side_effect=RuntimeError("model down")),
        ):
            out = await detect_atom.run(_state())
        assert out == {"contradiction_review": ""}


@pytest.mark.unit
@pytest.mark.asyncio
class TestRevise:
    async def test_accepted_revision_updates_content_and_length(self):
        revised = "y" * 880
        with patch(
            "services.self_review.revise_contradictions",
            new=AsyncMock(return_value=(revised, {"revised": True})),
        ):
            out = await revise_atom.run(_state(contradiction_review="1. x"))
        assert out["content"] == revised
        assert out["content_length"] == len(revised)

    async def test_rejected_revision_leaves_content_alone(self):
        """The contract rejected it, so the ORIGINAL draft must survive — the
        atom must not write back the discarded text."""
        with patch(
            "services.self_review.revise_contradictions",
            new=AsyncMock(return_value=("orig", {"revised": False,
                                                 "rejected_reason": "too long"})),
        ):
            out = await revise_atom.run(_state(contradiction_review="1. x"))
        assert out == {}

    async def test_no_detection_is_a_noop(self):
        """Empty channel = detect found nothing, so revise must not call an LLM.
        This is what lets the node sit unconditionally on the graph."""
        called = AsyncMock()
        with patch("services.self_review.revise_contradictions", new=called):
            out = await revise_atom.run(_state(contradiction_review=""))
        assert out == {}
        called.assert_not_awaited()

    async def test_exception_is_non_fatal(self):
        with patch(
            "services.self_review.revise_contradictions",
            new=AsyncMock(side_effect=RuntimeError("model down")),
        ):
            out = await revise_atom.run(_state(contradiction_review="1. x"))
        assert out == {}
