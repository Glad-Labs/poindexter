"""Tests for the end-to-end retrieval eval (step 2 of the poindexter#1033 plan).

The eval exists to be *trusted as a number*, so these tests concentrate on the
ways a scoring harness lies: reporting a clean sheet when it scored nothing,
counting an outage as a quality regression, and calling a truncated hit a hit.
"""

from __future__ import annotations

import pytest

from services.model_eval.golden_sets.retrieval import (
    _DEICTIC,
    _META,
    LEGACY_PREVIEW_CHARS,
    _pick_span,
    build_retrieval_golden_set,
)
from services.model_eval.metrics import recall_at_k
from services.model_eval.types import GoldenCase, GoldenSet
from services.retrieval_eval import _contains_span, score_retrieval


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
class TestRecallAtK:
    def test_hit_inside_k(self):
        assert recall_at_k([False, True, False], 3) == 1.0

    def test_hit_outside_k_is_a_miss(self):
        assert recall_at_k([False, False, True], 2) == 0.0

    def test_no_hits(self):
        assert recall_at_k([False, False], 5) == 0.0

    def test_empty_ranking_is_a_miss_not_a_crash(self):
        """A retriever returning nothing must score 0, never raise — an
        exception here would abort a whole eval run mid-corpus."""
        assert recall_at_k([], 5) == 0.0


# --------------------------------------------------------------------------
# span containment — the payload-aware half of the eval
# --------------------------------------------------------------------------
class TestContainsSpan:
    def test_exact_substring(self):
        assert _contains_span("alpha beta gamma delta", "beta gamma")

    def test_truncated_payload_does_not_contain_a_later_span(self):
        """The #1033 failure, expressed as a metric: the right document came
        back, but the delivered text stopped before the answer."""
        chunk = "opening words " * 40 + "THE ANSWER involves quantization budgets"
        assert not _contains_span(chunk[:LEGACY_PREVIEW_CHARS],
                                  "THE ANSWER involves quantization budgets")
        assert _contains_span(chunk, "THE ANSWER involves quantization budgets")

    def test_whitespace_normalized_payload_still_counts(self):
        """Overlap fallback: a windowed or re-wrapped payload must not score a
        false miss just because the bytes differ."""
        span = "retrieval matched on four thousand characters and returned five hundred"
        payload = "  retrieval   matched on FOUR thousand\ncharacters and\nreturned five hundred  "
        assert _contains_span(payload, span)

    def test_unrelated_payload_is_not_a_match(self):
        assert not _contains_span("completely different subject matter here",
                                  "retrieval matched on four thousand characters")

    def test_empty_span_is_never_a_match(self):
        assert not _contains_span("anything at all", "")


# --------------------------------------------------------------------------
# question quality filters
# --------------------------------------------------------------------------
class TestQuestionFilters:
    @pytest.mark.parametrize(
        "bad",
        [
            "What does the passage suggest about leverage in negotiations?",
            "What advisory issue is mentioned in the passage?",
            "Which components are named in the excerpt?",
            "What does the above describe?",
            "What is claimed in the text about GPU scheduling?",
        ],
    )
    def test_meta_questions_rejected(self, bad):
        """Observed live at ~3/10 before this filter — these name no subject,
        so a retrieval miss on them measures the question, not the retriever."""
        assert _META.search(bad)

    @pytest.mark.parametrize(
        "good",
        [
            "What are the two top priorities in TierPoint's 2026 data center trends report?",
            "Why did qa.vision's image-relevance leg produce zero scores in June 2026?",
            "Which title does publish_post_from_task prefer when promoting a task?",
        ],
    )
    def test_specific_questions_kept(self, good):
        assert not _META.search(good)
        assert not _DEICTIC.search(good)

    def test_deictic_without_meta_word_rejected(self):
        assert _DEICTIC.search("What does this approach improve?")


class TestPickSpan:
    def test_returns_none_when_chunk_shorter_than_span(self):
        assert _pick_span("tiny", 400, __import__("random").Random(1)) is None

    def test_span_is_a_real_slice_of_the_chunk(self):
        rng = __import__("random").Random(7)
        chunk = "para one text here.\n\n" + ("body sentence. " * 200)
        got = _pick_span(chunk, 300, rng)
        assert got is not None
        start, span = got
        assert len(span) == 300
        assert chunk[start : start + 300] == span


# --------------------------------------------------------------------------
# golden-set construction — fail loud, never silently empty
# --------------------------------------------------------------------------
class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *a, **k):
        return self._rows


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, rows):
        self._conn = _FakeConn(rows)

    def acquire(self):
        return _FakeAcquire(self._conn)


class _Cfg:
    def __init__(self, **over):
        self._d = {
            "retrieval_eval_golden_size": "10",
            "retrieval_eval_span_chars": "200",
            "retrieval_eval_min_chunk_chars": "500",
            "retrieval_eval_per_source_cap": "50",
            "retrieval_eval_seed": "5",
        }
        self._d.update(over)

    def get(self, k, d=None):
        return self._d.get(k, d)

    def get_bool(self, k, d=False):
        v = self._d.get(k)
        return d if v is None else str(v).strip().lower() in {"1", "true", "yes", "on"}

    def get_float(self, k, d=0.0):
        v = self._d.get(k)
        return d if v is None else float(v)

    def get_int(self, k, d=0):
        v = self._d.get(k)
        return d if v is None else int(v)


def _row(src, sid, text):
    return {"source_table": src, "source_id": sid, "chunk_index": 0, "chunk_text": text}


class TestGoldenSetBuild:
    @pytest.mark.asyncio
    async def test_empty_corpus_raises_rather_than_returning_zero_cases(self):
        """An eval that scored nothing must not be able to report a clean run —
        the same 'a check that scanned nothing has not passed' rule the CI
        lints follow."""
        with pytest.raises(RuntimeError, match="no chunks with chunk_text"):
            await build_retrieval_golden_set(
                pool=_FakePool([]), site_config=_Cfg(), question_fn=None
            )

    @pytest.mark.asyncio
    async def test_all_questions_filtered_raises(self):
        rows = [_row("posts", f"p{i}", "word " * 400) for i in range(5)]

        async def always_meta(span):
            return "What does the passage explain about this?"

        with pytest.raises(RuntimeError, match="Refusing to return an empty set"):
            await build_retrieval_golden_set(
                pool=_FakePool(rows), site_config=_Cfg(), question_fn=always_meta
            )

    @pytest.mark.asyncio
    async def test_region_label_splits_on_the_legacy_preview_boundary(self):
        rows = [_row("posts", f"p{i}", "alpha beta gamma delta epsilon " * 300)
                for i in range(20)]

        async def q(span):
            return "Which alpha beta gamma values does the pipeline emit?"

        gs = await build_retrieval_golden_set(
            pool=_FakePool(rows), site_config=_Cfg(), question_fn=q
        )
        assert gs.cases
        for c in gs.cases:
            expected = "head" if c.payload["span_start"] < LEGACY_PREVIEW_CHARS else "deep"
            assert c.payload["region"] == expected

    @pytest.mark.asyncio
    async def test_version_is_stable_for_the_same_corpus(self):
        rows = [_row("posts", f"p{i}", "alpha beta gamma delta " * 300) for i in range(20)]

        async def q(span):
            return "Which alpha beta gamma delta values are configured?"

        a = await build_retrieval_golden_set(
            pool=_FakePool(rows), site_config=_Cfg(), question_fn=q)
        b = await build_retrieval_golden_set(
            pool=_FakePool(rows), site_config=_Cfg(), question_fn=q)
        assert a.version == b.version


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------
class _Node:
    def __init__(self, text, md):
        self.text = text
        self.metadata = md


class _NWS:
    def __init__(self, node, score=1.0):
        self.node = node
        self.score = score


class _FakeRetriever:
    def __init__(self, results, raises=False):
        self._results = results
        self._raises = raises

    async def aretrieve(self, query):
        if self._raises:
            raise ConnectionError("embed endpoint down")
        return self._results


@pytest.fixture
def gold_case():
    span = "quantization budgets dominate the VRAM ceiling for local inference"
    return GoldenCase(
        query="What dominates the VRAM ceiling for local inference?",
        candidates=[],
        payload={
            "source_table": "posts", "source_id": "p1", "chunk_index": 0,
            "span_start": 1500, "span_text": span, "chunk_chars": 4000,
            "region": "deep",
        },
    )


class TestScoreRetrieval:
    @pytest.mark.asyncio
    async def test_hit_at_rank_one_with_full_payload(self, monkeypatch, gold_case):
        span = gold_case.payload["span_text"]
        node = _Node("lead in text. " * 50 + span, {"source_table": "posts", "source_id": "p1"})

        async def fake_get(*a, **k):
            return _FakeRetriever([_NWS(node)])

        monkeypatch.setattr("services.rag_engine.get_rag_retriever", fake_get)
        res = await score_retrieval(
            pool=None, site_config=_Cfg(),
            golden_set=GoldenSet("t", 1, [gold_case]),
        )
        assert res.detail["overall"]["recall@1"] == 1.0
        assert res.detail["overall"]["payload_contains_span"] == 1.0
        # The span sits past char 500, so the legacy preview would have missed it.
        assert res.detail["overall"]["legacy_payload_contains_span"] == 0.0

    @pytest.mark.asyncio
    async def test_right_doc_truncated_payload_counts_as_payload_miss(
        self, monkeypatch, gold_case
    ):
        """The #1033 regression guard: retrieval can rank correctly while the
        consumer still receives text without the answer. recall must be 1.0 and
        payload_contains_span must be 0.0 — collapsing them would hide it."""
        node = _Node("filler. " * 40, {"source_table": "posts", "source_id": "p1"})

        async def fake_get(*a, **k):
            return _FakeRetriever([_NWS(node)])

        monkeypatch.setattr("services.rag_engine.get_rag_retriever", fake_get)
        res = await score_retrieval(
            pool=None, site_config=_Cfg(), golden_set=GoldenSet("t", 1, [gold_case])
        )
        assert res.detail["overall"]["recall@1"] == 1.0
        assert res.detail["overall"]["payload_contains_span"] == 0.0

    @pytest.mark.asyncio
    async def test_retriever_outage_is_counted_not_scored_as_zero(
        self, monkeypatch, gold_case
    ):
        """A retriever raising on every case must surface as errors, not as
        recall 0.0 — otherwise an outage reads as a quality regression."""
        async def fake_get(*a, **k):
            return _FakeRetriever([], raises=True)

        monkeypatch.setattr("services.rag_engine.get_rag_retriever", fake_get)
        res = await score_retrieval(
            pool=None, site_config=_Cfg(), golden_set=GoldenSet("t", 1, [gold_case])
        )
        assert res.detail["errors"] == 1
        assert res.n_cases == 0

    @pytest.mark.asyncio
    async def test_other_chunk_of_right_document_still_counts(self, monkeypatch, gold_case):
        """Matching is at (source_table, source_id): a different chunk of the
        right document is a real hit for consumers, and penalising it would
        measure chunk-boundary luck."""
        node = _Node("some other part of the same post",
                     {"source_table": "posts", "source_id": "p1"})

        async def fake_get(*a, **k):
            return _FakeRetriever([_NWS(node)])

        monkeypatch.setattr("services.rag_engine.get_rag_retriever", fake_get)
        res = await score_retrieval(
            pool=None, site_config=_Cfg(), golden_set=GoldenSet("t", 1, [gold_case])
        )
        assert res.detail["overall"]["recall@1"] == 1.0

    @pytest.mark.asyncio
    async def test_region_stratification_reported(self, monkeypatch, gold_case):
        head = GoldenCase(
            query="q2", candidates=[],
            payload={**gold_case.payload, "source_id": "p2",
                     "span_start": 10, "region": "head"},
        )
        node = _Node("nothing relevant", {"source_table": "posts", "source_id": "zzz"})

        async def fake_get(*a, **k):
            return _FakeRetriever([_NWS(node)])

        monkeypatch.setattr("services.rag_engine.get_rag_retriever", fake_get)
        res = await score_retrieval(
            pool=None, site_config=_Cfg(),
            golden_set=GoldenSet("t", 1, [gold_case, head]),
        )
        assert res.detail["by_region"]["deep"]["n"] == 1
        assert res.detail["by_region"]["head"]["n"] == 1
        assert "deep_head_recall_gap" in res.detail
