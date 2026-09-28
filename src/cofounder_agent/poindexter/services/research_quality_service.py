"""
Research Quality Service — filter, dedup and rank a tier of web-search results.

``ResearchService.build_context`` (``services/research_service.py``) runs every
web tier through :meth:`ResearchQualityService.filter_and_score` before it
renders ``RECENT WEB SOURCES``. Two production paths reach it that way: the
canonical_blog writer's ``research_context`` (``modules/content/writer_core.py``)
and the two_pass writer's ``[EXTERNAL_NEEDED]`` lookups (``research_topic``).
It sees the DuckDuckGo results ``WebResearcher.search`` returns, after
``research_service`` has dropped the ones whose page could not be fetched, and
decides which of them reach the writer and in what order:

- **thin snippets are dropped** (``research_min_snippet_length`` /
  ``research_min_snippet_words``). In the stored corpora these were Telegram
  channel previews, cookie banners, a YouTube page footer and "Visit the post
  for more." aggregator stubs.
- **near-duplicate snippets collapse to one source**
  (``research_dedup_similarity_threshold``), keeping the highest-scoring copy.
  Almost every real duplicate is the same work served from several hosts: arXiv
  html + abs + Hugging Face papers, a publisher PDF + its article page + RePEc,
  one post syndicated to LinkedIn or Substack.
- **survivors are ranked** by a weighted average of domain credibility,
  snippet quality, recency, uniqueness and the search engine's own order (the
  five ``research_*_weight`` settings). The search order is weighted so that it
  holds unless an institutional (tier-1) host sits right behind, or several
  signals agree.

Every tunable is read from ``app_settings`` on each call rather than frozen at
construction, so the ``AppContainer.research_quality_service`` cached instance
and ``research_service``'s per-instance one behave identically.

History: the only production caller was the content_agent research agent,
deleted with the WorkflowExecutor tree in Glad-Labs/poindexter#367. The service
sat unwired from then until 2026-09-28 while its settings, tests and a dedup fix
(glad-labs-stack#3965) kept arriving. It was written for Serper, whose results
carried ``link`` / ``date`` / ``type`` keys; nothing produces that shape any
more. Replaying the 181 web tiers stored in ``pipeline_versions`` through it
exposed defects that had never mattered while nothing called it:

- dedup only collapsed pairs, not the triplets that are the commonest real
  duplicate;
- the recency and uniqueness weights scaled constants, so they could not
  reorder anything;
- query relevance substring-matched the topic's first three words ("to"
  matches "tomato");
- tier-2 domains matched exactly, so ``docs.github.com`` missed ``github.com``;
- credibility leaned on top-level domains. Every ``.org`` scored like a
  university, and every TLD outside .com/.net/.io/.co sat on a 0.5 floor, so
  fatsil.org outranked a textbook author's own page while shopify.engineering
  and hatchet.run lost their top slots;
- the score had no notion of relevance beyond word overlap with the topic.
  Ranked on its own components, it replaced DuckDuckGo's first result in 102
  of 181 replayed tiers, half by a margin under 0.03. A live check the same day
  showed 13 of 19, one of them anthropic.com, the primary source for its own
  post, dropping below an aggregator that sat at #5. The search order is now
  a fifth component (``research_search_rank_weight``).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlparse

from poindexter.services.logger_config import get_logger
from poindexter.services.site_config import SiteConfig

logger = get_logger(__name__)


# DuckDuckGo leads a snippet with the page's date when it knows one:
# "3 weeks ago - ...", "February 15, 2026 - ...", "Mar 15, 2024 · ...",
# "Jan 12, 2021 ... ...". 318 of the 815 web sources in the stored research
# corpora (2026-06-23 -> 09-26) carried one. The trailing separator is required,
# so prose that merely opens with a month ("May 2026 bring ...") is not a date.
_DATE_SEP = r"\s*(?:[-–—·•]|\.\.\.|…)"
_RELATIVE_DATE = re.compile(
    rf"^\s*(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago{_DATE_SEP}",
    re.IGNORECASE,
)
_ABSOLUTE_DATE = re.compile(
    rf"^\s*([A-Za-z]{{3,9}})\.?\s+(?:(\d{{1,2}}),?\s+)?(\d{{4}}){_DATE_SEP}"
)
_ISO_DATE = re.compile(rf"^\s*(\d{{4}})-(\d{{2}})-(\d{{2}}){_DATE_SEP}")
_UNIT_DAYS = {
    "minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365,
}
_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"), ("feb", "february"), ("mar", "march"),
            ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
            ("aug", "august"), ("sep", "sept", "september"),
            ("oct", "october"), ("nov", "november"), ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}

_SPAM_MARKERS = (
    "click here", "buy now", "limited time", "sponsored",
    "advertisement", "promoted", "error 404", "not found",
)


def _published_age_days(snippet: str, today: date) -> float | None:
    """Age in days of the date a search snippet leads with, or None if none.

    Relative dates ("3 days ago") are converted with a 30-day month and a
    365-day year. A date in the future (an event page) counts as age 0.
    """
    match = _RELATIVE_DATE.match(snippet)
    if match:
        return int(match[1]) * _UNIT_DAYS[match[2].lower()]
    published: date | None = None
    match = _ABSOLUTE_DATE.match(snippet)
    if match:
        month = _MONTHS.get(match[1].lower())
        if month is None:
            return None
        published = _safe_date(int(match[3]), month, int(match[2] or 1))
    else:
        match = _ISO_DATE.match(snippet)
        if match:
            published = _safe_date(int(match[1]), int(match[2]), int(match[3]))
    if published is None:
        return None
    return float(max(0, (today - published).days))


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:  # "Feb 30, 2026" — not a date, so no recency signal
        return None


def _significant_terms(text: str) -> list[str]:
    """Distinct words of 4+ characters, lowercased, in first-seen order.

    The same notion of a "significant" topic word that
    ``ResearchService._find_references`` uses.
    """
    return list(dict.fromkeys(re.findall(r"\b\w{4,}\b", text.lower())))


def _domain_set(listed: list[str], default: frozenset[str]) -> set[str]:
    """A domain-tier setting's entries, lowercased; the defaults when it is empty."""
    return {d.lower() for d in listed} or set(default)


def _host_in(host: str, domains: set[str]) -> bool:
    """True when ``host`` is one of ``domains`` or a subdomain of one.

    ``docs.github.com`` is under ``github.com``, and ``mit.edu`` is under the
    bare suffix ``edu``, so one rule serves both tiers.
    """
    return any(host == d or host.endswith("." + d) for d in domains)


@dataclass
class ScoredSource:
    """A web research source with its quality scores (each 0.0-1.0)."""

    title: str
    url: str
    snippet: str
    domain: str
    domain_credibility: float
    snippet_quality: float
    recency_score: float
    uniqueness_score: float
    overall_score: float  # weighted average of the component scores
    content: str = ""  # extracted page text, carried through for the writer
    search_rank_score: float = 1.0  # 1.0 for the first result searched, 0.0 the last
    search_position: int = 0  # index in the search results; a kept duplicate takes its cluster's best


class ResearchQualityService:
    """Filter, dedup and rank a tier of web-search results.

    Input is ``WebResearcher``'s result shape: ``{title, url, snippet,
    content}``. Every tunable is read from ``app_settings`` at call time (#198).
    """

    # Code defaults for the app_settings of the same name. settings_defaults.py
    # seeds each one at exactly these values. overall_score is the weighted
    # AVERAGE of the components, so the weights are relative and need not sum
    # to one.
    CREDIBILITY_WEIGHT = 0.4
    SNIPPET_QUALITY_WEIGHT = 0.3
    RECENCY_WEIGHT = 0.2
    UNIQUENESS_WEIGHT = 0.1
    SEARCH_RANK_WEIGHT = 0.4
    MIN_SNIPPET_LENGTH = 50
    MIN_SNIPPET_WORDS = 10
    SIMILARITY_THRESHOLD = 0.7  # word-level similarity at which two snippets are one source
    RECENCY_FRESH_DAYS = 7
    RECENCY_RECENT_DAYS = 365

    # Domain credibility tiers. The shipped defaults suit a tech / developer
    # audience; other niches (legal, medical, finance) replace them through the
    # comma-separated research_tier1_domains / research_tier2_domains settings.
    # An entry matches that host and its subdomains. Tier 1 holds suffixes whose
    # registration is restricted to institutions. "org" was here until
    # 2026-09-28: anyone can register one. In the replay of 181 stored web
    # tiers, ranked on quality alone, a suffix-only .org took the top slot from
    # DuckDuckGo's first result in 26 of them, fatsil.org over a textbook
    # author's own page among them.
    _DEFAULT_TIER_1_DOMAINS = frozenset({
        "edu",  # Educational institutions
        "gov",  # Government
        "ac.uk",  # UK academic
    })
    _DEFAULT_TIER_2_DOMAINS = frozenset({
        "medium.com",
        "dev.to",
        "github.com",
        "stackoverflow.com",
        "wikipedia.org",
        "arxiv.org",  # Academic papers
        "research.google.com",
        "aws.amazon.com",
        "cloud.google.com",
        "microsoft.com",
        "apple.com",
    })

    def __init__(self, *, site_config: SiteConfig):
        self._site_config = site_config
        self.logger = logger

    # -- tunables (read on every access) ------------------------------------

    def _weight(self, key: str, default: float) -> float:
        # Builds the key as f"research_{key}_weight", which the literal-key
        # settings_phantom_read_lint cannot see; TestScoringWeightSettings ties
        # each seeded key to this read instead.
        return self._site_config.get_float(f"research_{key}_weight", default)

    @property
    def credibility_weight(self) -> float:
        return self._weight("credibility", self.CREDIBILITY_WEIGHT)

    @property
    def snippet_quality_weight(self) -> float:
        return self._weight("snippet_quality", self.SNIPPET_QUALITY_WEIGHT)

    @property
    def recency_weight(self) -> float:
        return self._weight("recency", self.RECENCY_WEIGHT)

    @property
    def uniqueness_weight(self) -> float:
        return self._weight("uniqueness", self.UNIQUENESS_WEIGHT)

    @property
    def search_rank_weight(self) -> float:
        return self._weight("search_rank", self.SEARCH_RANK_WEIGHT)

    @property
    def min_snippet_length(self) -> int:
        return self._site_config.get_int("research_min_snippet_length", self.MIN_SNIPPET_LENGTH)

    @property
    def min_snippet_words(self) -> int:
        return self._site_config.get_int("research_min_snippet_words", self.MIN_SNIPPET_WORDS)

    @property
    def similarity_threshold(self) -> float:
        return self._site_config.get_float(
            "research_dedup_similarity_threshold", self.SIMILARITY_THRESHOLD
        )

    @property
    def recency_fresh_days(self) -> int:
        return self._site_config.get_int("research_recency_fresh_days", self.RECENCY_FRESH_DAYS)

    @property
    def recency_recent_days(self) -> int:
        return self._site_config.get_int("research_recency_recent_days", self.RECENCY_RECENT_DAYS)

    # The key stays a literal at each get_list call so settings_phantom_read_lint
    # can see the read; routing it through a helper's parameter would hide it.
    @property
    def tier1_domains(self) -> set[str]:
        return _domain_set(
            self._site_config.get_list("research_tier1_domains"),
            self._DEFAULT_TIER_1_DOMAINS,
        )

    @property
    def tier2_domains(self) -> set[str]:
        return _domain_set(
            self._site_config.get_list("research_tier2_domains"),
            self._DEFAULT_TIER_2_DOMAINS,
        )

    # -- the pipeline -------------------------------------------------------

    def filter_and_score(
        self, results: list[dict], query: str | None = None
    ) -> list[ScoredSource]:
        """Filter, dedup and rank web-search results.

        Args:
            results: ``WebResearcher`` result dicts: ``title``, ``url``,
                ``snippet`` and (from ``search``) ``content``, in the order
                the search engine ranked them.
            query: The research topic, for snippet relevance.

        Returns:
            ScoredSource objects, highest ``overall_score`` first.
        """
        if not results:
            return []

        today = datetime.now(timezone.utc).date()
        sources = []
        for position, result in enumerate(results):
            if not self._is_valid_result(result):
                continue
            url = result["url"]
            snippet = result["snippet"]
            domain = self._extract_domain(url)
            source = ScoredSource(
                title=result.get("title") or "",
                url=url,
                snippet=snippet,
                domain=domain,
                domain_credibility=self._score_domain_credibility(domain),
                snippet_quality=self._score_snippet_quality(snippet, query),
                recency_score=self._score_recency(_published_age_days(snippet, today)),
                # Placeholders, both 1.0 for every source so neither can sway
                # which copy _deduplicate keeps; they get their real values
                # once the duplicates are gone. Rank must stay out of that
                # choice: a top-ranked Amazon listing would otherwise beat
                # the university press's own page for the same book.
                uniqueness_score=1.0,
                search_rank_score=1.0,
                search_position=position,
                overall_score=0.0,
                content=result.get("content") or "",
            )
            source.overall_score = self._overall_score(source)
            sources.append(source)

        kept = self._deduplicate(sources)
        self._score_search_rank(kept, len(results))
        self._score_uniqueness(kept)
        for source in kept:
            source.overall_score = self._overall_score(source)
        kept.sort(key=lambda s: s.overall_score, reverse=True)

        self.logger.info(
            "ResearchQualityService: Processed %d results, kept %d "
            "(%d thin snippet, %d duplicate)",
            len(results), len(kept), len(results) - len(sources), len(sources) - len(kept),
        )
        return kept

    def _is_valid_result(self, result: dict) -> bool:
        """True when a result has a URL and a snippet substantial enough to cite.

        The snippet must reach ``research_min_snippet_length`` characters and
        ``research_min_snippet_words`` words.
        """
        snippet = result.get("snippet") or ""
        if not result.get("url") or not snippet:
            return False
        return (
            len(snippet) >= self.min_snippet_length
            and len(snippet.split()) >= self.min_snippet_words
        )

    def _extract_domain(self, url: str) -> str:
        """Lowercased host of ``url`` without a leading ``www.``, or ""."""
        try:
            host = urlparse(url).hostname or ""
        except ValueError:  # malformed URL (e.g. an unclosed IPv6 bracket)
            return ""
        return host.removeprefix("www.")

    def _overall_score(self, source: ScoredSource) -> float:
        """Weighted average of the five component scores (0.0-1.0).

        An average, not a sum, so the weights are relative: adding one never
        pushes the score past 1, and all-zero weights tie every source (the
        stable sort then keeps search order).
        """
        weighted = (
            (source.domain_credibility, self.credibility_weight),
            (source.snippet_quality, self.snippet_quality_weight),
            (source.recency_score, self.recency_weight),
            (source.uniqueness_score, self.uniqueness_weight),
            (source.search_rank_score, self.search_rank_weight),
        )
        total = sum(weight for _, weight in weighted)
        if total <= 0:
            return 0.0
        return sum(score * weight for score, weight in weighted) / total

    def _score_domain_credibility(self, domain: str) -> float:
        """Score domain credibility (0.0-1.0).

        - Tier 1 (``research_tier1_domains``; .edu / .gov / .ac.uk): 0.95
        - Tier 2 (``research_tier2_domains``; curated hosts): 0.85
        - Any other host: 0.65, neutral
        - No host at all: 0.5

        Every judgment lives in the two settings. Unlisted hosts share one
        neutral score because a top-level domain says little once registration
        is open. Until 2026-09-28, other .com/.net/.io/.co hosts scored 0.65,
        every remaining TLD 0.5, and seven hardcoded publications 0.75-0.8.
        Ranked on quality alone in the stored-corpus replay, that 0.5 floor
        pushed DuckDuckGo's first result out of the top slot in 16 tiers,
        shopify.engineering and the original hatchet.run article among them.
        To lift a publication, list it in tier 2.
        """
        if not domain:
            return 0.5

        host = domain.lower()
        if _host_in(host, self.tier1_domains):
            return 0.95
        if _host_in(host, self.tier2_domains):
            return 0.85
        return 0.65

    def _score_snippet_quality(self, snippet: str, query: str | None = None) -> float:
        """Score snippet quality (0.0-1.0).

        Starts at 0.5. Adds up to 0.3 for length and up to 0.2 for the share of
        the query's significant words the snippet contains as whole words.
        Subtracts 0.3 for ad / error-page phrasing.
        """
        if not snippet:
            return 0.0

        score = 0.5

        word_count = len(snippet.split())
        if word_count > 30:
            score += 0.3
        elif word_count > 20:
            score += 0.2

        terms = _significant_terms(query) if query else []
        if terms:
            words = set(re.findall(r"\w+", snippet.lower()))
            matched = sum(1 for term in terms if term in words)
            score += (matched / len(terms)) * 0.2

        if any(marker in snippet.lower() for marker in _SPAM_MARKERS):
            score -= 0.3

        return min(1.0, max(0.0, score))

    def _score_recency(self, age_days: float | None) -> float:
        """Score recency (0.0-1.0) from the age of the snippet's leading date.

        No date: 0.7 (neutral). Up to ``research_recency_fresh_days`` old: 0.9.
        Up to ``research_recency_recent_days`` old: 0.8. Older: 0.6.
        """
        if age_days is None:
            return 0.7
        if age_days <= self.recency_fresh_days:
            return 0.9
        if age_days <= self.recency_recent_days:
            return 0.8
        return 0.6

    def _deduplicate(self, sources: list[ScoredSource]) -> list[ScoredSource]:
        """Collapse near-duplicate snippets, keeping the highest-scoring copy.

        Greedy over the sources in descending score order: a source is kept
        unless its snippet reaches ``research_dedup_similarity_threshold``
        against one already kept. Comparing against everything kept, not just
        the first match, is what collapses a triplet (arXiv html + abs + a
        Hugging Face papers page) to one source. Ties keep input order.
        """
        threshold = self.similarity_threshold
        kept: list[ScoredSource] = []
        for source in sorted(sources, key=lambda s: s.overall_score, reverse=True):
            match = next(
                (
                    other for other in kept
                    if self._calculate_similarity(source.snippet, other.snippet) >= threshold
                ),
                None,
            )
            if match is None:
                kept.append(source)
            else:
                # The copy kept stands for the whole work. The search engine
                # ranked the work, not the host, so it takes the best position
                # any copy had.
                match.search_position = min(match.search_position, source.search_position)
        return kept

    def _score_search_rank(self, sources: list[ScoredSource], searched: int) -> None:
        """Set each source's search-rank score from its search position, in place.

        1.0 for the first of the ``searched`` results, falling evenly to 0.0 for
        the last. The search engine's order is the strongest relevance signal
        we have: without it the other components, most of them weak, replaced
        DuckDuckGo's first result in 102 of 181 replayed tiers, half of them by
        a margin under 0.03. At the default weight a tier-1 host right behind a
        result can pass it, and so can a combination of signals; a tier-2 host
        alone cannot. Tier 2 lists user-generated platforms (github.com,
        medium.com, dev.to), and in the evidence its promotions were a coin
        flip: dev.to over bun.com's own site, a GitHub page mirroring Hacker
        News over the Authors Guild's post about its own lawsuit.
        """
        last = max(1, searched - 1)
        for source in sources:
            source.search_rank_score = 1.0 - source.search_position / last

    def _score_uniqueness(self, sources: list[ScoredSource]) -> None:
        """Set each source's uniqueness, in place.

        Uniqueness is 1 minus the snippet's highest similarity to any other
        surviving source, so a source that half-overlaps another ranks below
        one that adds something new. A lone source is fully unique.
        """
        for source in sources:
            closest = max(
                (
                    self._calculate_similarity(source.snippet, other.snippet)
                    for other in sources
                    if other is not source
                ),
                default=0.0,
            )
            source.uniqueness_score = 1.0 - closest

    def _calculate_similarity(self, text_a: str, text_b: str) -> float:
        """Similarity between two research snippets (0.0-1.0).

        WORD-level, with ``autojunk`` off. ``SequenceMatcher`` compares
        CHARACTERS when handed strings, and past 200 elements its autojunk
        heuristic discards every element occurring in more than 1% of the
        sequence — across a snippet that is every common letter, and 198 of
        200 sampled snippets clear that floor.

        The collapse is triggered by SCATTERED differences, which is exactly
        what a re-scrape or a lightly-reworded syndication looks like.
        Measured 2026-09-23 over 120 real snippet pairs, **75 flipped the
        dedup verdict** at the 0.7 threshold — pairs that are 83-87% alike
        scored 0.32-0.45 and were kept as distinct sources, padding
        ``research_context`` with the same facts twice. A pair differing in
        only one word per forty barely moves (0.954 vs 0.976), which is why
        a sparse-edit spot check misses this entirely.
        """
        if not text_a or not text_b:
            return 0.0

        matcher = SequenceMatcher(
            None, text_a.lower().split(), text_b.lower().split(), autojunk=False,
        )
        return matcher.ratio()
