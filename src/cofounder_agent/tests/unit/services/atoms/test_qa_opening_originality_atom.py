"""Unit tests for the qa.opening_originality atom.

Pins the atom contract: embeds the draft's opening, finds the nearest
published-post neighbor by cosine similarity, and flags the draft when its
opening is a near-duplicate of an existing post (the RAG self-echo class —
e.g. the 2026-06 "VRAM is the only currency that matters" cluster where four
posts opened near-verbatim because the two_pass writer grounds on prior posts).

Advisory-first (DB-gated via qa_gates.opening_originality.required_to_pass):
it SCORES on every run (visible on the QA Rails dashboard) but does not veto
until an operator graduates it.
"""

from __future__ import annotations

import pytest

# Import will fail until the atom file is created (RED).
from modules.content.atoms import qa_opening_originality
from modules.content.atoms._qa_rail_common import aggregate_rail_reviews
from modules.content.multi_model_qa import MultiModelQA

_ADVISORY_STATES = {"opening_originality": (True, False)}
_HARD_GATE_STATES = {"opening_originality": (True, True)}


def _patch_gates(monkeypatch, states):
    async def gates(self):
        return states
    monkeypatch.setattr(MultiModelQA, "_load_gate_states", gates)


class _Cfg:
    def __init__(self, enabled: bool = True, **extra):
        self._enabled = enabled
        self._extra = extra

    def get(self, key, default=None):
        if key == "opening_originality_enabled":
            return "true" if self._enabled else "false"
        return self._extra.get(key, default)

    def get_bool(self, key, default=False):
        if key == "opening_originality_enabled":
            return self._enabled
        return bool(self._extra.get(key, default))

    def get_float(self, key, default=0.0):
        return float(self._extra.get(key, default))


def _state(**over):
    base = {
        "content": (
            "# A totally novel intro\n\n"
            "Here is a fresh opening that resembles no prior post at all, "
            "written from scratch about a brand new subject."
        ),
        "topic": "Something new",
        "site_config": _Cfg(enabled=True),
    }
    base.update(over)
    return base


@pytest.mark.unit
class TestQaOpeningOriginalityAtom:
    def test_meta(self):
        m = qa_opening_originality.ATOM_META
        assert m.name == "qa.opening_originality"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_empty_content_noops(self):
        result = await qa_opening_originality.run({"content": " ", "site_config": _Cfg()})
        assert result == {}

    async def test_missing_site_config_noops(self):
        result = await qa_opening_originality.run({"content": "hello there"})
        assert result == {}

    async def test_rail_disabled_noops(self):
        result = await qa_opening_originality.run(_state(site_config=_Cfg(enabled=False)))
        assert result == {}

    async def test_review_emitted_on_pass(self, monkeypatch):
        """A novel opening (nearest neighbor far away) → approved=True."""
        async def mock_eval(*, content, site_config, pool):
            return (True, 0.80, "nearest published post 'foo' at cosine 0.20")
        monkeypatch.setattr(
            "modules.content.atoms.qa_opening_originality._evaluate", mock_eval,
        )
        out = await qa_opening_originality.run(_state())
        assert "qa_rail_reviews" in out
        rev = out["qa_rail_reviews"][0]
        assert rev["reviewer"] == "opening_originality"
        assert rev["approved"] is True
        assert rev["provider"] == "opening_originality_gate"

    async def test_review_emitted_on_fail(self, monkeypatch):
        """A near-duplicate opening → approved=False, feedback names the offender."""
        async def mock_eval(*, content, site_config, pool):
            return (
                False, 0.05,
                "opening near-duplicate of published post "
                "'choosing-a-quantization-format' (cosine 0.95)",
            )
        monkeypatch.setattr(
            "modules.content.atoms.qa_opening_originality._evaluate", mock_eval,
        )
        out = await qa_opening_originality.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["approved"] is False
        assert "choosing-a-quantization-format" in rev["feedback"]

    async def test_rail_exception_is_safe(self, monkeypatch):
        async def mock_eval(*, content, site_config, pool):
            raise RuntimeError("pgvector died")
        monkeypatch.setattr(
            "modules.content.atoms.qa_opening_originality._evaluate", mock_eval,
        )
        result = await qa_opening_originality.run(_state())
        assert result == {}

    async def test_advisory_first_does_not_veto(self, monkeypatch):
        """Seeded advisory (required_to_pass=false) → a near-dupe SCORES but
        must NOT veto the pass."""
        async def mock_eval(*, content, site_config, pool):
            return (False, 0.05, "opening near-duplicate of 'x' (cosine 0.95)")
        monkeypatch.setattr(
            "modules.content.atoms.qa_opening_originality._evaluate", mock_eval,
        )
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_opening_originality.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is True
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "opening_originality" not in decision["vetoed_by"]

    async def test_graduated_to_hard_veto(self, monkeypatch):
        """required_to_pass=true → a near-dupe becomes a real veto."""
        async def mock_eval(*, content, site_config, pool):
            return (False, 0.02, "opening near-duplicate of 'x' (cosine 0.98)")
        monkeypatch.setattr(
            "modules.content.atoms.qa_opening_originality._evaluate", mock_eval,
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_opening_originality.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is False
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "opening_originality" in decision["vetoed_by"]
        assert decision["approved"] is False


@pytest.mark.unit
class TestOpeningOriginalityDecision:
    """The pure similarity→verdict decision (no DB, no embeddings)."""

    def test_flags_near_duplicate(self):
        passed, score, reason = qa_opening_originality._decide(
            max_similarity=0.95, threshold=0.90, nearest_slug="sibling-post",
        )
        assert passed is False
        assert score < 20.0            # originality = (1 - 0.95) * 100 = 5
        assert "sibling-post" in reason

    def test_passes_novel_opening(self):
        passed, score, reason = qa_opening_originality._decide(
            max_similarity=0.30, threshold=0.90, nearest_slug="unrelated",
        )
        assert passed is True
        assert score > 60.0            # originality = (1 - 0.30) * 100 = 70

    def test_at_threshold_passes(self):
        """Only a STRICTLY greater similarity flags — boundary is a pass so a
        merely-related post isn't treated as a copy."""
        passed, _score, _reason = qa_opening_originality._decide(
            max_similarity=0.90, threshold=0.90, nearest_slug="edge",
        )
        assert passed is True

    def test_no_neighbor_passes(self):
        """Empty corpus / no neighbor found → max_similarity 0.0 → pass."""
        passed, score, _reason = qa_opening_originality._decide(
            max_similarity=0.0, threshold=0.90, nearest_slug=None,
        )
        assert passed is True
        assert score == 100.0


@pytest.mark.unit
class TestExtractOpening:
    """The opening-text extraction fed to the embedder."""

    def test_strips_leading_h1(self):
        content = "# Big Title\n\nThe real first sentence starts here, and it continues."
        opening = qa_opening_originality._extract_opening(content)
        assert opening.startswith("The real first sentence")
        assert "Big Title" not in opening

    def test_caps_length(self):
        content = "word " * 500
        opening = qa_opening_originality._extract_opening(content, max_chars=120)
        assert len(opening) <= 120

    def test_empty_returns_empty(self):
        assert qa_opening_originality._extract_opening("   ") == ""

    def test_strips_leading_html_image(self):
        """An image-first post (the-vram-currency-problem shape) must embed the
        prose opening, not the <img> tag. Pre-fix that post opened with the
        identical VRAM sentence yet scored only 0.749 (not ~0.90) because the
        extractor fed the embedder its leading ``<img>`` HTML."""
        content = (
            '<img src="https://cdn.example/x.webp" alt="White cubes fill a glass">\n\n'
            "If you are running local LLMs, you know that VRAM is the only currency."
        )
        opening = qa_opening_originality._extract_opening(content)
        assert opening.startswith("If you are running local LLMs")
        assert "<img" not in opening

    def test_strips_leading_markdown_image(self):
        content = (
            "![a chart of throughput](https://cdn.example/y.png)\n\n"
            "The actual analysis begins in this very sentence about inference."
        )
        opening = qa_opening_originality._extract_opening(content)
        assert opening.startswith("The actual analysis begins")
        assert "![a chart" not in opening

    def test_strips_heading_then_image(self):
        content = (
            "# Redundant Title\n\n"
            "<img src='z.webp' alt='hero'>\n\n"
            "Prose that is the true opening of the post continues at length here."
        )
        opening = qa_opening_originality._extract_opening(content)
        assert opening.startswith("Prose that is the true opening")
        assert "Redundant Title" not in opening
        assert "<img" not in opening

    def test_keeps_prose_that_merely_mentions_image_word(self):
        """Precision guard: a real prose opening is never stripped just because
        it contains the word 'image' or a mid-sentence link."""
        content = "The image of a lone GPU says a lot about local inference costs."
        opening = qa_opening_originality._extract_opening(content)
        assert opening.startswith("The image of a lone GPU")
