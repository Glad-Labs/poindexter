"""
Research Quality Service

Provides filtering, deduplication, and scoring of research sources
from Serper API results to improve research quality.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from services.logger_config import get_logger
from services.site_config import SiteConfig

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# ``set_site_config()`` for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc


logger = get_logger(__name__)


@dataclass
class ScoredSource:
    """A research source with quality score"""

    title: str
    url: str
    snippet: str
    domain: str
    domain_credibility: float  # 0.0-1.0
    snippet_quality: float  # 0.0-1.0
    recency_score: float  # 0.0-1.0
    uniqueness_score: float  # 0.0-1.0
    overall_score: float  # 0.0-1.0 (weighted average)

    def to_context_string(self) -> str:
        """Format as context for LLM"""
        return (
            f"Title: {self.title}\n"
            f"Link: {self.url}\n"
            f"Snippet: {self.snippet}\n"
            f"Credibility: {self.domain_credibility:.0%}\n"
            f"---"
        )


class ResearchQualityService:
    """
    Improves research quality through:
    - Source credibility scoring (domain analysis)
    - Snippet quality validation
    - Deduplication of similar results
    - Result filtering and ranking
    """

    # Weights for overall source score — tunable via app_settings so
    # different niches can prioritize differently (e.g. a news blog
    # weighs recency higher, a how-to site weighs credibility higher). (#198)
    @staticmethod
    def _weight(key: str, default: float) -> float:
        try:
            _sc = site_config
            return _sc.get_float(f"research_{key}_weight", default)
        except Exception:
            return default

    # Class-level snapshots, re-resolved on each instance init below.
    CREDIBILITY_WEIGHT = 0.4
    SNIPPET_QUALITY_WEIGHT = 0.3
    RECENCY_WEIGHT = 0.2
    UNIQUENESS_WEIGHT = 0.1

    # Domain credibility tiers — shipped defaults matched to a tech /
    # developer audience. Customers in other niches (legal, medical,
    # finance) want completely different tier lists. Both tiers are
    # overridable via app_settings as comma-separated domain lists (#198):
    #   research_tier1_domains, research_tier2_domains
    _DEFAULT_TIER_1_DOMAINS = {
        "edu",  # Educational institutions
        "gov",  # Government
        "ac.uk",  # UK academic
        "org",  # Non-profits/trusted orgs
    }

    _DEFAULT_TIER_2_DOMAINS = {
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
    }

    # Keep class attrs as the default snapshot; __init__ re-resolves per instance.
    TIER_1_DOMAINS = _DEFAULT_TIER_1_DOMAINS
    TIER_2_DOMAINS = _DEFAULT_TIER_2_DOMAINS

    # Minimum snippet length to be useful — tunable via app_settings.
    MIN_SNIPPET_LENGTH = 50
    MIN_SNIPPET_WORDS = 10

    # Similarity threshold for deduplication — tunable via app_settings.
    SIMILARITY_THRESHOLD = 0.7  # 70% similar = duplicate

    def __init__(self):
        self.logger = logger
        # Resolve tunables from app_settings on init. Re-instantiate the
        # service to pick up app_settings changes.
        self.credibility_weight = self._weight("credibility", self.CREDIBILITY_WEIGHT)
        self.snippet_quality_weight = self._weight("snippet_quality", self.SNIPPET_QUALITY_WEIGHT)
        self.recency_weight = self._weight("recency", self.RECENCY_WEIGHT)
        self.uniqueness_weight = self._weight("uniqueness", self.UNIQUENESS_WEIGHT)
        try:
            _sc = site_config
            self.min_snippet_length = _sc.get_int(
                "research_min_snippet_length", self.MIN_SNIPPET_LENGTH
            )
            self.min_snippet_words = _sc.get_int(
                "research_min_snippet_words", self.MIN_SNIPPET_WORDS
            )
            self.similarity_threshold = _sc.get_float(
                "research_dedup_similarity_threshold", self.SIMILARITY_THRESHOLD
            )
            # Domain tier overrides — comma-separated, lowercased. Empty
            # setting keeps shipped defaults.
            _t1 = _sc.get("research_tier1_domains", "")
            _t2 = _sc.get("research_tier2_domains", "")
            self.tier1_domains = (
                {d.strip().lower() for d in _t1.split(",") if d.strip()}
                if _t1 else set(self._DEFAULT_TIER_1_DOMAINS)
            )
            self.tier2_domains = (
                {d.strip().lower() for d in _t2.split(",") if d.strip()}
                if _t2 else set(self._DEFAULT_TIER_2_DOMAINS)
            )
        except Exception:
            self.min_snippet_length = self.MIN_SNIPPET_LENGTH
            self.min_snippet_words = self.MIN_SNIPPET_WORDS
            self.similarity_threshold = self.SIMILARITY_THRESHOLD
            self.tier1_domains = set(self._DEFAULT_TIER_1_DOMAINS)
            self.tier2_domains = set(self._DEFAULT_TIER_2_DOMAINS)

    def filter_and_score(
        self, results: list[dict], query: str | None = None
    ) -> list[ScoredSource]:
        """
        Filter and score research results

        Args:
            results: List of result dicts from Serper API
                     Expected keys: 'title', 'link', 'snippet'
            query: Original search query (for scoring)

        Returns:
            List of ScoredSource objects, sorted by score (descending)
        """
        if not results:
            return []

        # Convert to scored sources
        sources = []
        for result in results:
            if not self._is_valid_result(result):
                continue

            title = result.get("title", "Unknown")
            url = result.get("link", "")
            snippet = result.get("snippet", "")

            if not url or not snippet:
                continue

            # Extract domain
            domain = self._extract_domain(url)

            # Score components
            credibility = self._score_domain_credibility(domain)
            snippet_quality = self._score_snippet_quality(snippet, query)
            recency = self._score_recency(result)
            uniqueness = 1.0  # Will be adjusted after deduplication

            # Overall score (weights settings-backed, see __init__)
            overall = (
                credibility * self.credibility_weight
                + snippet_quality * self.snippet_quality_weight
                + recency * self.recency_weight
                + uniqueness * self.uniqueness_weight
            )

            source = ScoredSource(
                title=title,
                url=url,
                snippet=snippet,
                domain=domain,
                domain_credibility=credibility,
                snippet_quality=snippet_quality,
                recency_score=recency,
                uniqueness_score=uniqueness,
                overall_score=overall,
            )

            sources.append(source)

        # Deduplicate similar sources
        sources = self._deduplicate(sources)

        # Recalculate uniqueness scores
        sources = self._recalculate_uniqueness(sources)

        # Sort by overall score
        sources.sort(key=lambda s: s.overall_score, reverse=True)

        # Log filtering results
        self.logger.info(
            f"ResearchQualityService: Processed {len(results)} results, "
            f"kept {len(sources)} high-quality sources"
        )

        return sources

    def _is_valid_result(self, result: dict) -> bool:
        """
        Check if result is valid and useful

        Filters out:
        - Featured snippets (usually low quality)
        - Results with missing fields
        - Results with very short snippets
        """
        # Skip featured snippets
        if result.get("type") == "featured_snippet":
            return False

        # Must have essentials
        if not result.get("link") or not result.get("snippet"):
            return False

        # Minimum snippet quality
        snippet = result.get("snippet", "")
        if len(snippet) < self.min_snippet_length:
            return False

        if len(snippet.split()) < self.min_snippet_words:
            return False

        return True

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL"""
        match = re.search(r"https?://(?:www\.)?([^/]+)", url)
        return match.group(1) if match else ""

    def _score_domain_credibility(self, domain: str) -> float:
        """
        Score domain credibility (0.0-1.0)

        Scoring:
        - Tier 1 (.edu, .gov, etc): 0.95
        - Tier 2 (major publishers): 0.85
        - Well-known domains: 0.75
        - Unknown: 0.5
        """
        if not domain:
            return 0.5

        domain_lower = domain.lower()

        # Check tier 1 (education, government)
        for tier1_domain in self.tier1_domains:
            if domain_lower.endswith(f".{tier1_domain}"):
                return 0.95

        # Check tier 2 (major publications)
        if domain_lower in self.tier2_domains:
            return 0.85

        # Check common tech/business domains
        common_domains = {
            "hacker-news.firebaseapp.com": 0.8,
            "techcrunch.com": 0.8,
            "arstechnica.com": 0.8,
            "forbes.com": 0.75,
            "businessinsider.com": 0.75,
            "bloomberg.com": 0.8,
            "theverge.com": 0.75,
        }

        if domain_lower in common_domains:
            return common_domains[domain_lower]

        # Prefer established domains (.com, .net, .io)
        if domain_lower.endswith((".com", ".net", ".io", ".co")):
            return 0.65

        # Lesser-known sources
        return 0.5

    def _score_snippet_quality(self, snippet: str, query: str | None = None) -> float:
        """
        Score snippet quality (0.0-1.0)

        Factors:
        - Length (longer = more informative)
        - Contains query terms (relevance)
        - Doesn't look like spam/ads
        """
        if not snippet:
            return 0.0

        score = 0.5

        # Reward longer snippets (more context)
        word_count = len(snippet.split())
        if word_count > 30:
            score += 0.3
        elif word_count > 20:
            score += 0.2

        # Check for query relevance if provided
        if query:
            query_words = query.lower().split()[:3]  # First 3 words
            snippet_lower = snippet.lower()
            matches = sum(1 for word in query_words if word in snippet_lower)
            if matches > 0:
                score += (matches / len(query_words)) * 0.2

        # Penalize obvious ads/spam
        spam_keywords = [
            "click here",
            "buy now",
            "limited time",
            "sponsored",
            "advertisement",
            "promoted",
            "error 404",
            "not found",
        ]
        if any(spam in snippet.lower() for spam in spam_keywords):
            score -= 0.3

        return min(1.0, max(0.0, score))

    def _score_recency(self, result: dict) -> float:
        """
        Score based on recency (if available)

        If 'date' field is available, prefer newer content.
        Otherwise, return neutral score.
        """
        # Serper API doesn't always include date, so return neutral
        # Could enhance this if date parsing is available
        date_str = result.get("date")

        if not date_str:
            return 0.7  # Neutral score

        # Very recent (< 1 week): boost
        if "hour" in date_str or "day" in date_str:
            return 0.9

        # Recent (< 1 month): good
        if "week" in date_str or "month" in date_str:
            return 0.8

        # Older (> 1 month): okay
        return 0.6

    def _deduplicate(self, sources: list[ScoredSource]) -> list[ScoredSource]:
        """
        Remove nearly-duplicate results

        Uses snippet similarity to detect duplicates.
        Keeps higher-scoring version of duplicates.
        """
        if len(sources) <= 1:
            return sources

        kept = []
        removed_indices = set()

        for i, source_a in enumerate(sources):
            if i in removed_indices:
                continue

            similarity = 0.0
            similar_idx = None

            # Check against other sources
            for j, source_b in enumerate(sources):
                if i >= j or j in removed_indices:
                    continue

                # Calculate snippet similarity
                similarity = self._calculate_similarity(source_a.snippet, source_b.snippet)

                if similarity >= self.similarity_threshold:
                    similar_idx = j
                    break

            if similar_idx is not None:
                # Keep the higher-scoring one, remove the other
                if source_a.overall_score >= sources[similar_idx].overall_score:
                    # source_a wins: discard the duplicate, keep source_a
                    removed_indices.add(similar_idx)
                    kept.append(source_a)
                else:
                    # source_b wins: discard source_a
                    removed_indices.add(i)
                    continue
            else:
                kept.append(source_a)

        return kept

    def _calculate_similarity(self, text_a: str, text_b: str) -> float:
        """Calculate similarity between two text snippets (0.0-1.0)"""
        if not text_a or not text_b:
            return 0.0

        # Use sequence matching for similarity
        matcher = SequenceMatcher(None, text_a.lower(), text_b.lower())
        return matcher.ratio()

    def _recalculate_uniqueness(self, sources: list[ScoredSource]) -> list[ScoredSource]:
        """Recalculate uniqueness scores after deduplication"""
        # After deduplication, all remaining sources have high uniqueness
        for source in sources:
            source.uniqueness_score = 0.95

        return sources

    def format_context(self, sources: list[ScoredSource]) -> str:
        """
        Format scored sources into context string for content generation

        Args:
            sources: Scored research sources

        Returns:
            Formatted string for LLM context
        """
        if not sources:
            return "No research sources available."

        context_lines = []
        for i, source in enumerate(sources, 1):
            context_lines.append(
                f"{i}. {source.title}\n" f"   URL: {source.url}\n" f"   {source.snippet}\n"
            )

        header = f"RESEARCH SOURCES (Top {len(sources)} results):\n\n"

        return header + "\n".join(context_lines)
