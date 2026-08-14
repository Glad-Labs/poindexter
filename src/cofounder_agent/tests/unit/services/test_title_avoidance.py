"""Tests for services.title_avoidance (poindexter#1043).

The behaviour under test exists because the previous mechanism — dumping the
last 20 published titles into the prompt under an "AVOID SIMILARITY" banner —
measurably did not work. The live corpus at the time (151 posts, 120 days):

- canonical_blog (62): 50% opened with a leading "The", 38% carried a colon
  subtitle — WITH the avoid-list in place.
- dev_diary (82): 36% joined two ideas with "and", 17% opened on a gerund —
  with no avoidance mechanism at all.

So the tests below pin two things: that the profiler actually names those
habits when handed a corpus shaped like the real one, and that the raw dump
does not come back by default.
"""

from __future__ import annotations

import pytest

from services.title_avoidance import (
    DEFAULT_PATTERN_THRESHOLD,
    TitleCorpusProfile,
    analyze_title_patterns,
    build_avoidance_block,
    fetch_recent_titles,
    get_mode,
    get_recent_count,
    render_avoidance_block,
)

# Verbatim slice of the real published corpus that motivated the change.
REAL_DEV_DIARY_TITLES = [
    "Chasing Driver Truth and VRAM Ghosts",
    "Locking the GPU and silencing the noise",
    "The Gap Between Merged and Working",
    "The Danger of Lazy Imports and 126-Day Freezes",
    "The Silence of the Taps",
    "VRAM Poisoning and the P4 Architect",
    "Hunting Ghosts in the Middleware",
    "Bounded Waits and Invisible Failures",
    "Silent Failures and Crying Wolf",
    "The Trillion Dollar Regex Bug",
    "Fighting Namespace Blindness and Cardinality Explosions",
    "Theater, Blackwell Kernels, and the SEO Demand Floor",
    "Grounding titles and breaking the duplication loop",
    "The Linux Cutover and the Briefing Echo",
    "Closing the silent data-loss gap",
    "Hunting the Silent Excepts",
    "Naming Lies and Frozen Tails",
    "The Shift to Native Telemetry",
    "The Shift to a Native UI",
    "The Timeout That Fired in Zero Milliseconds",
]

REAL_CANONICAL_BLOG_TITLES = [
    "Five Days of Autonomy: Deconstructing the Hugging Face Agent Intrusion",
    "Solving Retrieval Mismatch: Why Asymmetric Embedding Matters for RAG",
    "Building Poindexter: A Philosophy for Open Source AI Content Pipelines",
    "The Cost of Novelty: Why Originality Requires a Search, Not a Template",
    "Moving From Prompting to Wrangling: 5 Agentic Engineering Patterns",
    "Why LLM Rankers Fail at Absolute Quality: Lessons From the Dot Incident",
    "Beyond Rented Data: Leveraging First-Party Knowledge for Technical AI",
    "Stop Burning GitHub Actions Minutes: A Guide to Tiered CI Design",
    "The $1.65 Trillion Secret: Uncovering the Off-Balance-Sheet Debt",
    "The Gap Nobody Names",
]


class _StubSiteConfig:
    """Minimal site_config double keyed off a plain dict."""

    def __init__(self, values: dict | None = None):
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_int(self, key, default=None):
        return int(self._values.get(key, default))

    def get_float(self, key, default=None):
        return float(self._values.get(key, default))

    def get_bool(self, key, default=False):
        val = self._values.get(key, default)
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("true", "1", "yes", "on")


# ---------------------------------------------------------------------------
# analyze_title_patterns
# ---------------------------------------------------------------------------


class TestAnalyzeTitlePatterns:
    def test_empty_corpus_reports_no_findings(self):
        profile = analyze_title_patterns([])
        assert profile.sample_size == 0
        assert not profile.has_findings

    def test_blank_titles_are_not_counted_as_sample(self):
        profile = analyze_title_patterns(["", "   ", None])  # type: ignore[list-item]
        assert profile.sample_size == 0

    def test_names_the_and_compound_habit_on_the_real_dev_diary_corpus(self):
        """The 36%-of-dev_diary habit must be surfaced by name."""
        profile = analyze_title_patterns(REAL_DEV_DIARY_TITLES)
        descriptions = [d for d, _ in profile.patterns]
        assert any('join two ideas with "and"' in d for d in descriptions), (
            f"the 'and' compound is the dominant dev_diary habit but was not "
            f"named; got {descriptions}"
        )

    def test_names_the_colon_subtitle_habit_on_the_real_blog_corpus(self):
        """The 38%-of-canonical_blog habit must be surfaced by name."""
        profile = analyze_title_patterns(REAL_CANONICAL_BLOG_TITLES)
        descriptions = [d for d, _ in profile.patterns]
        assert any("colon subtitle" in d for d in descriptions), (
            f"the colon subtitle is the dominant canonical_blog habit but was "
            f"not named; got {descriptions}"
        )

    def test_names_the_leading_the_habit(self):
        profile = analyze_title_patterns(REAL_DEV_DIARY_TITLES)
        descriptions = [d for d, _ in profile.patterns]
        assert any('leading "The"' in d for d in descriptions)

    def test_patterns_are_ordered_strongest_first(self):
        profile = analyze_title_patterns(REAL_DEV_DIARY_TITLES)
        shares = [share for _, share in profile.patterns]
        assert shares == sorted(shares, reverse=True)

    def test_threshold_suppresses_incidental_shapes(self):
        """One question title in ten is not a habit."""
        titles = ["Why We Moved"] + [f"Plain Title {i}" for i in range(9)]
        profile = analyze_title_patterns(titles, pattern_threshold=0.20)
        assert not any(
            "question" in d for d, _ in profile.patterns
        ), "a 10% shape was reported as a habit"

    def test_threshold_reports_a_shape_that_clears_it(self):
        titles = ["Why We Moved", "How We Fixed It"] + [
            f"Plain Title {i}" for i in range(3)
        ]
        profile = analyze_title_patterns(titles, pattern_threshold=0.20)
        assert any("question" in d for d, _ in profile.patterns)

    def test_gerund_opener_detected(self):
        titles = [
            "Hunting Ghosts in the Middleware",
            "Locking the GPU and silencing the noise",
            "Chasing Driver Truth and VRAM Ghosts",
            "Plain Title",
        ]
        profile = analyze_title_patterns(titles)
        assert any('"-ing" verb' in d for d, _ in profile.patterns)

    def test_short_ing_nouns_do_not_count_as_gerund_openers(self):
        """"King"/"Ring" are nouns, not the Hunting/Chasing opener."""
        titles = ["King of the Cluster", "Ring Buffer Overruns", "Plain Title"]
        profile = analyze_title_patterns(titles)
        assert not any('"-ing" verb' in d for d, _ in profile.patterns)

    def test_trailing_colon_is_not_a_subtitle_split(self):
        titles = ["What We Shipped:", "Another Title:", "Third Title:"]
        profile = analyze_title_patterns(titles)
        assert not any("colon subtitle" in d for d, _ in profile.patterns)

    def test_listicle_detected(self):
        titles = ["5 Ways to Ship Faster", "7 Reasons Tests Fail", "Plain Title"]
        profile = analyze_title_patterns(titles)
        assert any("lead with a number" in d for d, _ in profile.patterns)

    def test_overused_vocabulary_surfaced(self):
        titles = [
            "Silent Failures and Crying Wolf",
            "Hunting the Silent Excepts",
            "Closing the silent data-loss gap",
            "Something Else Entirely",
        ]
        profile = analyze_title_patterns(titles, lexical_min_count=3)
        assert "silent" in profile.overused_terms

    def test_word_repeated_inside_one_title_is_not_a_corpus_habit(self):
        """A single title saying "gap" twice does not make it corpus-wide."""
        titles = ["The Gap and the Gap Behind the Gap", "Other", "Another"]
        profile = analyze_title_patterns(titles, lexical_min_count=3)
        assert "gap" not in profile.overused_terms

    def test_structural_stopwords_never_reported_as_overused(self):
        profile = analyze_title_patterns(
            REAL_DEV_DIARY_TITLES, lexical_min_count=2
        )
        for banned in ("the", "and", "of", "in", "to"):
            assert banned not in profile.overused_terms

    def test_overused_terms_are_capped(self):
        """Beyond a handful this reads as a banned-word list, not guidance."""
        titles = [
            f"alpha beta gamma delta epsilon zeta eta theta iota {i}"
            for i in range(5)
        ]
        profile = analyze_title_patterns(titles, lexical_min_count=2)
        assert len(profile.overused_terms) <= 6


# ---------------------------------------------------------------------------
# render_avoidance_block
# ---------------------------------------------------------------------------


class TestRenderAvoidanceBlock:
    def test_empty_profile_renders_nothing(self):
        assert render_avoidance_block(TitleCorpusProfile(sample_size=0)) == ""

    def test_habits_render_without_listing_any_source_title(self):
        """The whole point: describe the habits, never show the corpus.

        Showing twenty titles that are themselves half "The …" primes the
        pattern harder than one sentence suppresses it.
        """
        profile = analyze_title_patterns(REAL_DEV_DIARY_TITLES)
        block = render_avoidance_block(profile)

        assert "TITLE VARIETY" in block
        for title in REAL_DEV_DIARY_TITLES:
            assert title not in block, (
                f"source title {title!r} leaked into the prompt block — that is "
                f"the priming behaviour this module replaced"
            )

    def test_accuracy_outranks_variety_is_stated(self):
        """Without this the model will invent a title to be different."""
        profile = analyze_title_patterns(REAL_DEV_DIARY_TITLES)
        block = render_avoidance_block(profile).lower()
        assert "accuracy" in block

    def test_near_duplicates_render_verbatim(self):
        """Confirmed collisions ARE specific titles to dodge."""
        block = render_avoidance_block(
            TitleCorpusProfile(sample_size=0),
            near_duplicates=["The Shift to Native Telemetry"],
        )
        assert "The Shift to Native Telemetry" in block

    def test_near_duplicates_render_even_with_no_profile_findings(self):
        """A collision outranks the variety strategy — it always shows."""
        block = render_avoidance_block(
            TitleCorpusProfile(sample_size=0), near_duplicates=["Taken Title"]
        )
        assert block, "a confirmed duplicate produced no guidance at all"

    def test_legacy_titles_render_only_when_asked(self):
        block = render_avoidance_block(
            TitleCorpusProfile(sample_size=0),
            legacy_titles=["Old One", "Old Two"],
        )
        assert "AVOID SIMILARITY" in block
        assert "Old One" in block


# ---------------------------------------------------------------------------
# build_avoidance_block — settings-aware
# ---------------------------------------------------------------------------


class TestBuildAvoidanceBlock:
    def test_default_mode_is_patterns_not_the_dump(self):
        block = build_avoidance_block(REAL_DEV_DIARY_TITLES)
        assert "TITLE VARIETY" in block
        assert "AVOID SIMILARITY" not in block
        assert REAL_DEV_DIARY_TITLES[0] not in block

    def test_titles_mode_restores_legacy_behaviour(self):
        """The escape hatch back to the pre-2026-08 dump, no deploy needed."""
        sc = _StubSiteConfig({"title_avoidance_mode": "titles"})
        block = build_avoidance_block(REAL_DEV_DIARY_TITLES, site_config=sc)
        assert "AVOID SIMILARITY" in block
        assert REAL_DEV_DIARY_TITLES[0] in block
        assert "TITLE VARIETY" not in block

    def test_both_mode_renders_habits_and_dump(self):
        sc = _StubSiteConfig({"title_avoidance_mode": "both"})
        block = build_avoidance_block(REAL_DEV_DIARY_TITLES, site_config=sc)
        assert "TITLE VARIETY" in block
        assert "AVOID SIMILARITY" in block

    def test_off_mode_renders_nothing(self):
        sc = _StubSiteConfig({"title_avoidance_mode": "off"})
        assert build_avoidance_block(REAL_DEV_DIARY_TITLES, site_config=sc) == ""

    def test_off_mode_still_renders_confirmed_duplicates(self):
        """Turning off the variety strategy must not disable collision avoidance."""
        sc = _StubSiteConfig({"title_avoidance_mode": "off"})
        block = build_avoidance_block(
            REAL_DEV_DIARY_TITLES,
            site_config=sc,
            near_duplicates=["Taken Title"],
        )
        assert "Taken Title" in block

    def test_unknown_mode_falls_back_to_patterns(self):
        sc = _StubSiteConfig({"title_avoidance_mode": "nonsense"})
        assert get_mode(sc) == "patterns"

    def test_pattern_threshold_setting_is_honoured(self):
        """An operator raising the bar gets fewer STRUCTURAL habits named.

        Structural and lexical are independent axes with their own settings —
        raising the pattern threshold must not silence over-used vocabulary.
        """
        strict = _StubSiteConfig({"title_avoidance_pattern_threshold": "0.95"})
        profile = analyze_title_patterns(
            REAL_DEV_DIARY_TITLES, pattern_threshold=0.95
        )
        assert profile.patterns == []

        block = build_avoidance_block(REAL_DEV_DIARY_TITLES, site_config=strict)
        assert 'leading "The"' not in block
        assert "silent" in block, "the lexical axis is separately configured"

    def test_both_axes_off_renders_nothing(self):
        """Only when BOTH axes are out of range is there nothing to say."""
        silent = _StubSiteConfig({
            "title_avoidance_pattern_threshold": "0.95",
            "title_avoidance_lexical_min_count": "99",
        })
        assert build_avoidance_block(
            REAL_DEV_DIARY_TITLES, site_config=silent
        ) == ""

    def test_lexical_min_count_setting_is_honoured(self):
        loose = _StubSiteConfig({"title_avoidance_lexical_min_count": "2"})
        block = build_avoidance_block(REAL_DEV_DIARY_TITLES, site_config=loose)
        assert "already carrying several recent titles" in block

    def test_unreadable_site_config_falls_back_to_defaults(self):
        """A stubbed site_config must degrade to guidance, not to silence."""

        class _Boom:
            def get(self, *a, **k):
                raise RuntimeError("no settings")

            get_int = get_float = get

        block = build_avoidance_block(REAL_DEV_DIARY_TITLES, site_config=_Boom())
        assert "TITLE VARIETY" in block

    def test_missing_site_config_uses_module_defaults(self):
        assert get_recent_count(None) == 20
        assert get_mode(None) == "patterns"
        assert DEFAULT_PATTERN_THRESHOLD == 0.20


# ---------------------------------------------------------------------------
# fetch_recent_titles
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, rows, captured):
        self._rows = rows
        self._captured = captured

    async def fetch(self, sql, *args):
        self._captured["sql"] = sql
        self._captured["args"] = args
        return self._rows


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, rows, captured):
        self._conn = _FakeConn(rows, captured)

    def acquire(self):
        return _FakeAcquire(self._conn)


class TestFetchRecentTitles:
    @pytest.mark.asyncio
    async def test_returns_titles_newest_first(self):
        captured: dict = {}
        pool = _FakePool([{"title": "A"}, {"title": "B"}], captured)
        assert await fetch_recent_titles(pool) == ["A", "B"]

    @pytest.mark.asyncio
    async def test_null_titles_are_dropped(self):
        captured: dict = {}
        pool = _FakePool([{"title": "A"}, {"title": None}], captured)
        assert await fetch_recent_titles(pool) == ["A"]

    @pytest.mark.asyncio
    async def test_window_size_comes_from_app_settings(self):
        """The hardcoded LIMIT 20 became a tunable — bind it, don't inline it."""
        captured: dict = {}
        pool = _FakePool([], captured)
        sc = _StubSiteConfig({"title_avoidance_recent_count": "7"})
        await fetch_recent_titles(pool, site_config=sc)
        assert captured["args"] == (7,)
        assert "$1" in captured["sql"], "limit must be a bind param, not inlined"

    @pytest.mark.asyncio
    async def test_none_pool_returns_empty_without_a_finding(self):
        """No pool is bootstrap/test, not a failure worth paging on."""
        assert await fetch_recent_titles(None) == []

    @pytest.mark.asyncio
    async def test_zero_window_short_circuits(self):
        captured: dict = {}
        pool = _FakePool([{"title": "A"}], captured)
        sc = _StubSiteConfig({"title_avoidance_recent_count": "0"})
        assert await fetch_recent_titles(pool, site_config=sc) == []
        assert "sql" not in captured

    @pytest.mark.asyncio
    async def test_db_error_is_degraded_and_never_silent(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(
            "utils.findings.emit_finding",
            lambda **kw: findings.append(kw),
        )

        class _BoomPool:
            def acquire(self):
                raise RuntimeError("pool unavailable")

        result = await fetch_recent_titles(_BoomPool(), source="test.source")

        assert result == []
        assert len(findings) == 1
        assert findings[0]["kind"] == "title_history_lookup_failed"
        assert findings[0]["severity"] == "info"
        assert findings[0]["extra"]["source"] == "test.source"


# ---------------------------------------------------------------------------
# Internal-corpus similarity (poindexter#1044)
# ---------------------------------------------------------------------------

from services.title_avoidance import (  # noqa: E402
    DEFAULT_INTERNAL_SIMILARITY_THRESHOLD,
    InternalSimilarityReport,
    check_internal_similarity,
    fetch_taken_titles,
    normalize_title_for_similarity,
    score_internal_similarity,
)

# Pairs measured on the live corpus 2026-08-14. These are the cases the gate
# exists for — if a change stops catching them, the gate is decorative.
REAL_NEAR_DUPLICATE_PAIRS = [
    ("The Shift to Native Telemetry", "The Shift to a Native UI"),
    (
        "The 32GB Threshold: How the RTX 5090 Redefines Local LLM Development",
        "The 70B Threshold: How the RTX 5090 Rewrites the Home Lab",
    ),
    ("Hunting Ghosts in the Middleware",
     "Hunting ghosts in the metrics and tightening the CI ratchet"),
    ("Silent Failures and Crying Wolf", "Where the Silent Failures Were Hiding"),
]

# Genuinely distinct posts that must NOT be flagged. Same corpus, p90 is 0.368
# — these sit in the bulk of the distribution.
REAL_DISTINCT_PAIRS = [
    ("The Silence of the Taps", "VRAM Poisoning and the P4 Architect"),
    ("Bounded Waits and Invisible Failures", "The Trillion Dollar Regex Bug"),
    ("The Poindexter Philosophy", "Fighting Namespace Blindness and Cardinality Explosions"),
    ("Closing the silent data-loss gap", "The five days nobody was watching"),
]


class TestNormalizeTitleForSimilarity:
    def test_casefolds_and_strips_punctuation(self):
        assert (
            normalize_title_for_similarity("The Shift to Native Telemetry!")
            == "the shift to native telemetry"
        )

    def test_cosmetic_variants_normalize_identically(self):
        """A reader sees one title; the raw-string compare would see two."""
        a = normalize_title_for_similarity("The Shift to Native Telemetry")
        b = normalize_title_for_similarity("the shift to native telemetry...")
        assert a == b

    def test_collapses_whitespace(self):
        assert normalize_title_for_similarity("A   B\tC") == "a b c"

    def test_empty_is_safe(self):
        assert normalize_title_for_similarity("") == ""


class TestScoreInternalSimilarity:
    @pytest.mark.parametrize("candidate,existing", REAL_NEAR_DUPLICATE_PAIRS)
    def test_catches_real_near_duplicates(self, candidate, existing):
        report = score_internal_similarity(candidate, [existing])
        assert report.is_duplicate, (
            f"{candidate!r} vs {existing!r} scored {report.max_similarity:.3f}, "
            f"under the {report.threshold} threshold — this is a pair that "
            f"actually shipped twice"
        )

    @pytest.mark.parametrize("a,b", REAL_DISTINCT_PAIRS)
    def test_passes_genuinely_distinct_titles(self, a, b):
        report = score_internal_similarity(a, [b])
        assert not report.is_duplicate, (
            f"{a!r} vs {b!r} scored {report.max_similarity:.3f} — flagging "
            f"distinct posts burns an LLM call and erodes trust in the gate"
        )

    def test_exact_match_scores_one(self):
        report = score_internal_similarity("The Silence of the Taps",
                                           ["The Silence of the Taps"])
        assert report.max_similarity == pytest.approx(1.0)
        assert report.is_duplicate

    def test_matches_ordered_most_similar_first(self):
        report = score_internal_similarity(
            "The Shift to Native Telemetry",
            ["The Shift to a Native UI", "The Shift to Native Telemetry"],
        )
        assert report.matches[0] == "The Shift to Native Telemetry"

    def test_threshold_is_honoured(self):
        pair = REAL_NEAR_DUPLICATE_PAIRS[0]
        assert not score_internal_similarity(
            pair[0], [pair[1]], threshold=0.99
        ).is_duplicate

    def test_empty_corpus_is_not_a_duplicate(self):
        report = score_internal_similarity("Anything", [])
        assert not report.is_duplicate
        assert report.corpus_size == 0

    def test_empty_title_is_not_a_duplicate(self):
        assert not score_internal_similarity("", ["Something"]).is_duplicate

    def test_blank_corpus_entries_are_skipped(self):
        report = score_internal_similarity("Real Title", ["", "   ", None])  # type: ignore[list-item]
        assert not report.is_duplicate

    def test_max_similarity_reported_even_when_under_threshold(self):
        """The score is the tuning signal — it must survive a passing verdict."""
        report = score_internal_similarity(
            "The Silence of the Taps", ["VRAM Poisoning and the P4 Architect"]
        )
        assert not report.is_duplicate
        assert report.max_similarity > 0.0

    def test_default_threshold_matches_calibration(self):
        assert DEFAULT_INTERNAL_SIMILARITY_THRESHOLD == 0.58


class TestFetchTakenTitles:
    @pytest.mark.asyncio
    async def test_queries_published_and_in_flight_statuses(self):
        """An awaiting_approval title is taken — a reader will see it."""
        captured: dict = {}
        pool = _FakePool([{"title": "A"}], captured)
        await fetch_taken_titles(pool)
        statuses = captured["args"][0]
        assert "published" in statuses
        assert "approved" in statuses
        assert "awaiting_approval" in statuses

    @pytest.mark.asyncio
    async def test_excludes_the_calling_task(self):
        """A re-run must not match the title it wrote last time."""
        captured: dict = {}
        pool = _FakePool([], captured)
        await fetch_taken_titles(pool, exclude_task_id="task-42")
        assert captured["args"][1] == "task-42"

    @pytest.mark.asyncio
    async def test_no_exclusion_passes_none(self):
        captured: dict = {}
        pool = _FakePool([], captured)
        await fetch_taken_titles(pool)
        assert captured["args"][1] is None

    @pytest.mark.asyncio
    async def test_corpus_limit_comes_from_settings(self):
        captured: dict = {}
        pool = _FakePool([], captured)
        sc = _StubSiteConfig({"title_internal_corpus_limit": "42"})
        await fetch_taken_titles(pool, site_config=sc)
        assert captured["args"][2] == 42

    @pytest.mark.asyncio
    async def test_unreadable_corpus_returns_none_not_empty(self):
        """None and [] mean different things: unread vs read-and-empty.

        Returning [] would make an unreadable corpus indistinguishable from a
        clean one, which is the fake-pass this contract exists to prevent.
        """
        class _BoomPool:
            def acquire(self):
                raise RuntimeError("pool unavailable")

        assert await fetch_taken_titles(_BoomPool()) is None

    @pytest.mark.asyncio
    async def test_unreadable_corpus_emits_a_finding(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(
            "utils.findings.emit_finding", lambda **kw: findings.append(kw)
        )

        class _BoomPool:
            def acquire(self):
                raise RuntimeError("pool unavailable")

        await fetch_taken_titles(_BoomPool(), source="test.source")
        assert len(findings) == 1
        assert findings[0]["kind"] == "title_corpus_lookup_failed"

    @pytest.mark.asyncio
    async def test_none_pool_returns_none(self):
        assert await fetch_taken_titles(None) is None


class TestCheckInternalSimilarity:
    @pytest.mark.asyncio
    async def test_flags_a_duplicate_against_the_corpus(self):
        captured: dict = {}
        pool = _FakePool([{"title": "The Shift to a Native UI"}], captured)
        report = await check_internal_similarity(
            pool, "The Shift to Native Telemetry"
        )
        assert report.is_duplicate
        assert report.matches == ["The Shift to a Native UI"]

    @pytest.mark.asyncio
    async def test_master_switch_disables_the_check(self):
        captured: dict = {}
        pool = _FakePool([{"title": "The Shift to a Native UI"}], captured)
        sc = _StubSiteConfig({"title_internal_similarity_enabled": False})
        report = await check_internal_similarity(
            pool, "The Shift to Native Telemetry", site_config=sc
        )
        assert not report.is_duplicate
        assert "sql" not in captured, "disabled check must not hit the DB"

    @pytest.mark.asyncio
    async def test_threshold_comes_from_settings(self):
        captured: dict = {}
        pool = _FakePool([{"title": "The Shift to a Native UI"}], captured)
        sc = _StubSiteConfig({"title_internal_similarity_threshold": "0.99"})
        report = await check_internal_similarity(
            pool, "The Shift to Native Telemetry", site_config=sc
        )
        assert not report.is_duplicate

    @pytest.mark.asyncio
    async def test_unreadable_corpus_is_degraded_not_clean(self):
        """Fail OPEN but say so — never a fabricated pass."""
        class _BoomPool:
            def acquire(self):
                raise RuntimeError("pool unavailable")

        report = await check_internal_similarity(_BoomPool(), "Any Title")
        assert report.degraded is True
        assert not report.is_duplicate

    @pytest.mark.asyncio
    async def test_clean_title_is_not_degraded(self):
        captured: dict = {}
        pool = _FakePool([{"title": "Something Entirely Different"}], captured)
        report = await check_internal_similarity(pool, "Sourdough Starter Notes")
        assert report.degraded is False
        assert not report.is_duplicate


class TestInternalSimilarityReport:
    def test_is_duplicate_follows_matches(self):
        assert not InternalSimilarityReport().is_duplicate
        assert InternalSimilarityReport(matches=["x"]).is_duplicate
