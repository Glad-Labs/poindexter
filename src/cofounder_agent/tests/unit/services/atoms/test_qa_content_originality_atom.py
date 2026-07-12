"""Unit tests for the qa.content_originality atom.

Pins the atom contract: embeds the draft's opening, finds the nearest
published-post neighbor by cosine similarity, and flags the draft when its
opening is a near-duplicate of an existing post (the RAG self-echo class —
e.g. the 2026-06 "VRAM is the only currency that matters" cluster where four
posts opened near-verbatim because the two_pass writer grounds on prior posts).

Advisory-first (DB-gated via qa_gates.content_originality.required_to_pass):
it SCORES on every run (visible on the QA Rails dashboard) but does not veto
until an operator graduates it.
"""

from __future__ import annotations

import pytest

# Import will fail until the atom file is created (RED).
from modules.content.atoms import qa_content_originality
from modules.content.atoms._qa_rail_common import aggregate_rail_reviews
from modules.content.multi_model_qa import MultiModelQA

_ADVISORY_STATES = {"content_originality": (True, False)}
_HARD_GATE_STATES = {"content_originality": (True, True)}


def _patch_gates(monkeypatch, states):
    async def gates(self):
        return states
    monkeypatch.setattr(MultiModelQA, "_load_gate_states", gates)


class _Cfg:
    def __init__(self, enabled: bool = True, **extra):
        self._enabled = enabled
        self._extra = extra

    def get(self, key, default=None):
        if key == "content_originality_enabled":
            return "true" if self._enabled else "false"
        return self._extra.get(key, default)

    def get_bool(self, key, default=False):
        if key == "content_originality_enabled":
            return self._enabled
        return bool(self._extra.get(key, default))

    def get_float(self, key, default=0.0):
        return float(self._extra.get(key, default))

    def get_int(self, key, default=0):
        return int(self._extra.get(key, default))


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
        m = qa_content_originality.ATOM_META
        assert m.name == "qa.content_originality"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_empty_content_noops(self):
        result = await qa_content_originality.run({"content": " ", "site_config": _Cfg()})
        assert result == {}

    async def test_missing_site_config_noops(self):
        result = await qa_content_originality.run({"content": "hello there"})
        assert result == {}

    async def test_rail_disabled_noops(self):
        result = await qa_content_originality.run(_state(site_config=_Cfg(enabled=False)))
        assert result == {}

    async def test_review_emitted_on_pass(self, monkeypatch):
        """A novel opening (nearest neighbor far away) → approved=True."""
        async def mock_eval(*, content, site_config, pool):
            return (True, 0.80, "nearest published post 'foo' at cosine 0.20")
        monkeypatch.setattr(
            "modules.content.atoms.qa_content_originality._evaluate", mock_eval,
        )
        out = await qa_content_originality.run(_state())
        assert "qa_rail_reviews" in out
        rev = out["qa_rail_reviews"][0]
        assert rev["reviewer"] == "content_originality"
        assert rev["approved"] is True
        assert rev["provider"] == "content_originality_gate"

    async def test_review_emitted_on_fail(self, monkeypatch):
        """A near-duplicate opening → approved=False, feedback names the offender."""
        async def mock_eval(*, content, site_config, pool):
            return (
                False, 0.05,
                "opening near-duplicate of published post "
                "'choosing-a-quantization-format' (cosine 0.95)",
            )
        monkeypatch.setattr(
            "modules.content.atoms.qa_content_originality._evaluate", mock_eval,
        )
        out = await qa_content_originality.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["approved"] is False
        assert "choosing-a-quantization-format" in rev["feedback"]

    async def test_rail_exception_is_safe(self, monkeypatch):
        async def mock_eval(*, content, site_config, pool):
            raise RuntimeError("pgvector died")
        monkeypatch.setattr(
            "modules.content.atoms.qa_content_originality._evaluate", mock_eval,
        )
        result = await qa_content_originality.run(_state())
        assert result == {}

    async def test_advisory_first_does_not_veto(self, monkeypatch):
        """Seeded advisory (required_to_pass=false) → a near-dupe SCORES but
        must NOT veto the pass."""
        async def mock_eval(*, content, site_config, pool):
            return (False, 0.05, "opening near-duplicate of 'x' (cosine 0.95)")
        monkeypatch.setattr(
            "modules.content.atoms.qa_content_originality._evaluate", mock_eval,
        )
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_content_originality.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is True
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "content_originality" not in decision["vetoed_by"]

    async def test_graduated_to_hard_veto(self, monkeypatch):
        """required_to_pass=true → a near-dupe becomes a real veto."""
        async def mock_eval(*, content, site_config, pool):
            return (False, 0.02, "opening near-duplicate of 'x' (cosine 0.98)")
        monkeypatch.setattr(
            "modules.content.atoms.qa_content_originality._evaluate", mock_eval,
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_content_originality.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is False
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "content_originality" in decision["vetoed_by"]
        assert decision["approved"] is False


@pytest.mark.unit
class TestOpeningOriginalityDecision:
    """The pure similarity→verdict decision (no DB, no embeddings)."""

    def test_flags_near_duplicate(self):
        passed, score, reason = qa_content_originality._decide(
            max_similarity=0.95, threshold=0.90, nearest_slug="sibling-post",
        )
        assert passed is False
        assert score < 20.0            # originality = (1 - 0.95) * 100 = 5
        assert "sibling-post" in reason

    def test_passes_novel_opening(self):
        passed, score, reason = qa_content_originality._decide(
            max_similarity=0.30, threshold=0.90, nearest_slug="unrelated",
        )
        assert passed is True
        assert score > 60.0            # originality = (1 - 0.30) * 100 = 70

    def test_at_threshold_passes(self):
        """Only a STRICTLY greater similarity flags — boundary is a pass so a
        merely-related post isn't treated as a copy."""
        passed, _score, _reason = qa_content_originality._decide(
            max_similarity=0.90, threshold=0.90, nearest_slug="edge",
        )
        assert passed is True

    def test_no_neighbor_passes(self):
        """Empty corpus / no neighbor found → max_similarity 0.0 → pass."""
        passed, score, _reason = qa_content_originality._decide(
            max_similarity=0.0, threshold=0.90, nearest_slug=None,
        )
        assert passed is True
        assert score == 100.0


@pytest.mark.unit
class TestChunkDraft:
    """Whole-post chunking fed to the embedder (paragraph merge/window)."""

    def test_merges_short_paragraphs_to_floor(self):
        # Three one-line paragraphs below the 200-char floor merge into one chunk.
        content = "Short one.\n\nShort two.\n\nShort three."
        chunks = qa_content_originality._chunk_draft(content, min_chars=200, max_chars=600)
        assert len(chunks) == 1
        assert "Short one." in chunks[0] and "Short three." in chunks[0]

    def test_windows_long_paragraph(self):
        # A single paragraph above the 600-char max yields multiple chunks.
        content = "word " * 400  # ~2000 chars, one paragraph
        chunks = qa_content_originality._chunk_draft(content, min_chars=200, max_chars=600)
        assert len(chunks) >= 3
        assert all(len(c) <= 600 for c in chunks)

    def test_strips_leading_media(self):
        content = (
            '<img src="x.webp" alt="hero">\n\n'
            + ("The real analysis paragraph is long enough to stand alone. " * 6)
        )
        chunks = qa_content_originality._chunk_draft(content, min_chars=200, max_chars=600)
        assert chunks and "<img" not in chunks[0]

    def test_empty_returns_empty(self):
        assert qa_content_originality._chunk_draft("   ", min_chars=200, max_chars=600) == []

    def test_keeps_prose_that_merely_mentions_image_word(self):
        """Precision guard: a prose paragraph is never dropped just because it
        contains the word 'image' — ``_LEADING_IMAGE_RE`` is anchored at the
        paragraph START, so only a genuine leading media token is stripped."""
        content = "The image of a lone GPU says a lot about local inference costs."
        chunks = qa_content_originality._chunk_draft(content, min_chars=10, max_chars=600)
        assert chunks and chunks[0].startswith("The image of a lone GPU")


@pytest.mark.unit
class TestWholePostEvaluate:
    async def test_flags_on_worst_chunk(self, monkeypatch):
        """Max-over-chunks: a novel opening but a body chunk that lifts a
        published post still flags, naming the worst chunk's offender."""
        # Two chunks: chunk 0 novel (sim 0.20), chunk 1 a lift (sim 0.94).
        sims = iter([("novel-post", 0.20), ("the-vram-currency-problem", 0.94)])
        monkeypatch.setattr(
            qa_content_originality, "_chunk_draft",
            lambda content, *, min_chars, max_chars: ["chunk zero", "chunk one"],
        )

        async def fake_embed(text, *, site_config):
            return [0.0]
        monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

        class _Conn:
            async def fetchrow(self, _sql, _vec):
                slug, sim = next(sims)
                return {"slug": slug, "similarity": sim}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _Pool:
            def acquire(self):
                return _Conn()

        passed, _score, reason = await qa_content_originality._evaluate(
            content="x", site_config=_Cfg(content_originality_max_similarity=0.83),
            pool=_Pool(),
        )
        assert passed is False
        assert "the-vram-currency-problem" in reason  # worst chunk named
        assert "0.94" in reason

    async def test_novel_whole_post_passes(self, monkeypatch):
        """Every chunk far from the corpus → pass."""
        monkeypatch.setattr(
            qa_content_originality, "_chunk_draft",
            lambda content, *, min_chars, max_chars: ["a", "b"],
        )

        async def fake_embed(text, *, site_config):
            return [0.0]
        monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

        class _Conn:
            async def fetchrow(self, _sql, _vec):
                return {"slug": "unrelated", "similarity": 0.22}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _Pool:
            def acquire(self):
                return _Conn()

        passed, _score, _reason = await qa_content_originality._evaluate(
            content="x", site_config=_Cfg(content_originality_max_similarity=0.83),
            pool=_Pool(),
        )
        assert passed is True


@pytest.mark.unit
class TestSeriesExclusion:
    """Graduation-safety: a draft in a templated recurring series is exempt from
    the HARD veto (still SCORES advisory). No-op today — the rail is advisory AND
    dev_diary runs no qa.* atoms — so this only bites after a future graduation."""

    def test_excluded_series_matches(self):
        assert qa_content_originality._series_excluded(
            "dev_diary", {"dev_diary", "narrate_bundle"}
        ) is True

    def test_included_series_not_excluded(self):
        assert qa_content_originality._series_excluded(
            "canonical_blog", {"dev_diary"}
        ) is False

    def test_blank_never_excluded(self):
        assert qa_content_originality._series_excluded("", {"dev_diary"}) is False

    async def test_resolve_prefers_in_state_slug(self):
        slug = await qa_content_originality._resolve_template_slug(
            {"template_slug": "dev_diary", "task_id": "t1"}, pool=None
        )
        assert slug == "dev_diary"

    async def test_resolve_falls_back_to_task_row(self):
        """template_slug is not a PipelineState channel, so on the graph_def path
        it's resolved from pipeline_tasks by the always-present task_id."""
        class _Conn:
            async def fetchrow(self, _sql, _tid):
                return {"template_slug": "dev_diary"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

        class _Pool:
            def acquire(self):
                return _Conn()

        slug = await qa_content_originality._resolve_template_slug(
            {"task_id": "t1"}, pool=_Pool()
        )
        assert slug == "dev_diary"

    async def test_resolve_fail_closed_without_task(self):
        assert await qa_content_originality._resolve_template_slug({}, pool=None) == ""

    async def test_hard_veto_skipped_for_excluded_series(self, monkeypatch):
        """Graduated to hard-block, an excluded-series draft that lifts a sibling
        is DOWNGRADED to advisory (flag, not veto)."""
        async def mock_eval(*, content, site_config, pool):
            return (False, 0.05, "content near-duplicate of 'x' (cosine 0.95)")
        monkeypatch.setattr(
            "modules.content.atoms.qa_content_originality._evaluate", mock_eval,
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_content_originality.run(
            _state(
                template_slug="dev_diary",
                site_config=_Cfg(
                    content_originality_excluded_series="dev_diary,narrate_bundle"
                ),
            )
        )
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is True  # downgraded despite the hard gate
        assert "series-excluded:dev_diary" in rev["feedback"]
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "content_originality" not in decision["vetoed_by"]

    async def test_hard_veto_kept_for_included_series(self, monkeypatch):
        """A non-excluded series (canonical_blog) still hard-vetoes when graduated —
        exclusion doesn't blanket-disable the veto."""
        async def mock_eval(*, content, site_config, pool):
            return (False, 0.02, "content near-duplicate of 'x' (cosine 0.98)")
        monkeypatch.setattr(
            "modules.content.atoms.qa_content_originality._evaluate", mock_eval,
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_content_originality.run(
            _state(
                template_slug="canonical_blog",
                site_config=_Cfg(
                    content_originality_excluded_series="dev_diary,narrate_bundle"
                ),
            )
        )
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is False
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "content_originality" in decision["vetoed_by"]
