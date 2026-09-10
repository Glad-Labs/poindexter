"""SearchAutocompleteSource — the first topic source that measures search DEMAND.

Every other source measures publication or conversation: ``hackernews`` (what's
being discussed), ``devto`` (what devs blog about), ``web_search`` (what's
published), ``internal_rag`` (what we already wrote). None of them can tell you
whether a human ever looked for the thing. That gap is why the 2026-08-09 GSC
audit found ~45,500 impressions producing ~70 clicks: the pipeline reliably
picked topical subjects that nobody searches for.

``gsc_query_gap`` is demand-driven but structurally circular — it can only see
queries the site ALREADY appears for, so it cannot discover an uncovered need.
It has contributed 0 topics.

Autocomplete closes that: the suggestions are literal query prefixes real people
type, straight from Google, free and unauthenticated. Seeding "local llm vram"
returns "local llm vram calculator", "local llm vram requirements",
"local llm 16gb vram", "local llm 8gb vram" — demand, in the exact cluster whose
human queries already convert at 7-20% CTR.

**Scoring is ordinal, never volume.** Autocomplete returns rank, not counts, so
this source must not invent an impressions-like number
(``feedback_no_dummy_data``). Earlier suggestions score higher, and the ceiling
is deliberately below ``gsc_query_gap``'s: when the two disagree, downstream
ranking should prefer measured impressions over inferred popularity.

Config (``plugin.topic_source.search_autocomplete`` in app_settings):

- ``enabled`` — seeded **false** in ``settings_defaults.py`` so the source ships
  inert; it makes unauthenticated requests to a third party, so a fresh install
  should opt in rather than discover the traffic later. The row must exist for
  that to hold: ``topic_sources/runner.py`` defaults a MISSING row to
  ``enabled=True``. The gate is the runner's, not this module's.
- ``config.seeds`` (default ``[]``) — seed phrases to expand. Empty means the
  source returns nothing and says so: there is no sensible generic default, and
  guessing a niche would be worse than doing nothing.
- ``config.seed_from_gsc_clicks`` (default true) — ALSO seed from the site's own
  queries that already earned clicks, so the source expands around what
  demonstrably converts instead of only what an operator remembered to type.
  Needs the GSC tap; contributes nothing when there is no click data yet.
- ``config.gsc_seed_limit`` (default 5) / ``config.gsc_seed_days`` (default 90).
- ``config.modifiers`` (default ``["how", "best", "vs", "why", "for"]``) —
  appended to each seed for a second pass. This is the keyword-research
  alphabet trick, trimmed: a full a-z sweep is 27 requests per seed and this
  runs unauthenticated against someone else's endpoint
  (``feedback_stability_over_speed``). Set ``[]`` to disable expansion.
- ``config.max_topics`` (default 25), ``config.concurrency`` (default 3),
  ``config.min_words`` (default 3) — one- and two-word suggestions are usually
  navigational or too broad to write a post against.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote_plus

import httpx

from plugins.topic_source import DiscoveredTopic
from services.topic_sources._filters import (
    brand_tokens_from_config,
    classify_category,
    is_junk_search_query,
)

logger = logging.getLogger(__name__)

# ``client=chrome`` returns noticeably more suggestions than ``client=firefox``
# for the same seed (13+ vs 10, measured 2026-08-09). Response content-type is
# text/javascript but the body is plain JSON: ``[seed, [suggestions], …]``.
_ENDPOINT = "https://suggestqueries.google.com/complete/search"

# Ceiling for this source's relevance_score, deliberately below the 5.0 that
# gsc_query_gap can reach from real impression counts. Ordinal popularity is
# weaker evidence than measured demand and should lose a tie.
_SCORE_CEILING = 3.0

_GSC_SEED_SQL = """
SELECT dimensions->>'query' AS query,
       SUM(metric_value) FILTER (WHERE metric_name = 'clicks') AS clicks
FROM external_metrics
WHERE source = 'google_search_console'
  AND dimensions ? 'query'
  AND dimensions->>'query' <> ''
  AND date > (NOW() - ($1::int || ' days')::interval)::date
GROUP BY dimensions->>'query'
HAVING SUM(metric_value) FILTER (WHERE metric_name = 'clicks') > 0
ORDER BY SUM(metric_value) FILTER (WHERE metric_name = 'clicks') DESC
LIMIT $2
"""


def _score_for_rank(rank: int) -> float:
    """Ordinal popularity → a bounded score. Rank 0 is Google's top suggestion."""
    return max(0.5, round(_SCORE_CEILING - 0.15 * rank, 2))


# Tokens too generic to anchor a suggestion to its seed — matching on one of
# these would let almost anything through as "related".
_WEAK_TOKENS = frozenset({
    "how", "best", "vs", "why", "for", "the", "a", "an", "to", "of", "in", "is",
    "and", "or", "with", "what", "when", "which", "do", "does",
})


def _retains_seed_token(seed: str, suggestion: str) -> bool:
    """True when ``suggestion`` keeps a meaningful word from ``seed``.

    Autocomplete drifts: seeding the brand-adjacent query "glad ai" returned
    "glade air freshener" — a real query, and utterly unrelated to this site.
    Requiring a shared token kills that while leaving genuine expansions alone
    ("local llm vram" → "local llm vram calculator"). Note "glad" vs "glade"
    does NOT match, which is exactly the case that motivated this.

    A seed of only weak tokens has nothing to anchor on, so nothing is dropped —
    fail open rather than silently return zero topics.
    """
    seed_tokens = {t for t in seed.lower().split() if t not in _WEAK_TOKENS}
    if not seed_tokens:
        return True
    return bool(seed_tokens & set(suggestion.lower().split()))


class SearchAutocompleteSource:
    """Turn Google autocomplete suggestions into topic candidates."""

    name = "search_autocomplete"

    async def extract(
        self,
        pool: Any,
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        # No `enabled` check here on purpose: topic_sources/runner.py owns that
        # gate (`_load_source_config` returns it separately) and hands `extract`
        # only the INNER config dict, which never carries the flag. Checking it
        # here would read a key that is never present and the source could never
        # run. Inertness is achieved by seeding the row with enabled=false in
        # settings_defaults.py — the runner defaults a MISSING row to
        # enabled=True, so a Python-side default cannot make a source inert.
        max_topics = int(config.get("max_topics", 25) or 25)
        concurrency = int(config.get("concurrency", 3) or 3)
        min_words = int(config.get("min_words", 3) or 3)
        modifiers = config.get("modifiers")
        if modifiers is None:
            modifiers = ["how", "best", "vs", "why", "for"]

        seeds = [str(s).strip() for s in (config.get("seeds") or []) if str(s).strip()]
        if config.get("seed_from_gsc_clicks", True):
            seeds += await self._gsc_seeds(pool, config)
        brand = brand_tokens_from_config(config.get("_site_config"))
        seeds = [s for s in dict.fromkeys(seeds) if not is_junk_search_query(s, brand_tokens=brand)]

        if not seeds:
            # Loud, not silent: an operator who enabled this source and gets
            # nothing needs to know it is waiting on seeds, not broken.
            logger.warning(
                "SearchAutocompleteSource: enabled but no seeds — set "
                "config.seeds (or let seed_from_gsc_clicks find click-earning "
                "queries once the GSC tap has data)",
            )
            return []

        probes = [(s, s) for s in seeds] + [
            (s, f"{s} {m}") for s in seeds for m in modifiers
        ]
        suggestions = await self._fetch_all(probes, concurrency=concurrency)

        seen: set[str] = set()
        topics: list[DiscoveredTopic] = []
        dropped_junk = 0
        for rank, phrase in suggestions:
            phrase = phrase.strip()
            key = phrase.lower()
            if not phrase or key in seen:
                continue
            if len(phrase.split()) < min_words:
                continue
            if is_junk_search_query(phrase, brand_tokens=brand):
                dropped_junk += 1
                continue
            seen.add(key)
            title = phrase.title()
            topics.append(
                DiscoveredTopic(
                    title=title,
                    category=classify_category(title),
                    source=self.name,
                    source_url=f"https://www.google.com/search?q={quote_plus(phrase)}",
                    relevance_score=_score_for_rank(rank),
                    description=(
                        "Google autocomplete suggests this query, so people are "
                        f"typing it (suggestion rank {rank + 1}). Ordinal "
                        "popularity — autocomplete reports no volume."
                    ),
                    keywords=[phrase],
                )
            )
            if len(topics) >= max_topics:
                break

        logger.info(
            "SearchAutocompleteSource: %d topics from %d seed(s) / %d probe(s); "
            "dropped %d junk",
            len(topics), len(seeds), len(probes), dropped_junk,
        )
        return topics

    async def _gsc_seeds(self, pool: Any, config: dict[str, Any]) -> list[str]:
        """Seeds from the site's own click-earning queries — expand what works."""
        if pool is None:
            return []
        days = int(config.get("gsc_seed_days", 90) or 90)
        limit = int(config.get("gsc_seed_limit", 5) or 5)
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(_GSC_SEED_SQL, days, limit)
        except Exception as exc:  # noqa: BLE001 — a missing tap must not fail the run
            logger.warning(
                "SearchAutocompleteSource: GSC seeding unavailable (%s); "
                "falling back to config.seeds only", exc,
            )
            return []
        out = [
            (r["query"] or "").strip()
            for r in rows
            if (r["query"] or "").strip()
        ]
        if out:
            logger.info("SearchAutocompleteSource: %d GSC click-seed(s): %s", len(out), out)
        return out

    async def _fetch_all(
        self, probes: list[tuple[str, str]], *, concurrency: int,
    ) -> list[tuple[int, str]]:
        """Fetch every ``(seed, probe)``. Returns (rank, suggestion), best first.

        One failing probe never sinks the run — Google throttles unauthenticated
        callers, and a partial harvest is still useful. A total wipeout is
        reported by the caller's count of zero topics.
        """
        sem = asyncio.Semaphore(max(1, concurrency))
        results: list[tuple[int, str]] = []

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            headers={"User-Agent": "Mozilla/5.0 (compatible; Poindexter/1.0)"},
        ) as client:

            async def _one(seed: str, probe: str) -> list[tuple[int, str]]:
                async with sem:
                    resp = await client.get(
                        _ENDPOINT, params={"client": "chrome", "q": probe},
                    )
                    resp.raise_for_status()
                    # content-type is text/javascript; the body is JSON.
                    payload = resp.json()
                if not isinstance(payload, list) or len(payload) < 2:
                    return []
                items = payload[1]
                if not isinstance(items, list):
                    return []
                return [
                    (i, str(s))
                    for i, s in enumerate(items)
                    if isinstance(s, str) and _retains_seed_token(seed, s)
                ]

            gathered = await asyncio.gather(
                *[_one(seed, probe) for seed, probe in probes],
                return_exceptions=True,
            )

        failures = 0
        for got in gathered:
            if isinstance(got, BaseException):
                failures += 1
                continue
            results.extend(got)
        if failures:
            logger.warning(
                "SearchAutocompleteSource: %d/%d probes failed (throttling?)",
                failures, len(probes),
            )
        # Best suggestion rank wins when the same phrase comes back from several
        # probes, so a phrase Google ranks first for its own seed isn't buried
        # by a worse rank under a modifier probe.
        best: dict[str, int] = {}
        for rank, phrase in results:
            key = phrase.strip().lower()
            if key and rank < best.get(key, 10**6):
                best[key] = rank
        by_key = {p.strip().lower(): p for _, p in results}
        return sorted(
            ((rank, by_key[key]) for key, rank in best.items()), key=lambda t: t[0],
        )


__all__ = ["SearchAutocompleteSource"]
