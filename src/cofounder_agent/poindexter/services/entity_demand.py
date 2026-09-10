"""Entity demand — how many people looked at the thing a topic names last month.

Every topic source except ``search_autocomplete`` / ``gsc_query_gap`` measures
*publication or conversation* (what HackerNews is discussing, what Dev.to
bloggers wrote, what our own corpus already covers). None of them can say
whether a human ever went looking for the subject, and the batch pre-rank —
an embedding cosine against the niche's goals — cannot either. That is how the
2026-08-09 GSC audit found ~45,500 impressions producing ~70 clicks: on-goal
subjects nobody searches for.

This module adds the one free, absolute, unauthenticated demand instrument
that exists: **Wikipedia pageviews**. The English article for an entity is
read by a measurable number of people per month (August 2026: RAG 34k,
GeForce RTX 50 series 31.5k, llama.cpp 10k, vLLM 5.4k, GGUF 3.8k), and that
number is a proxy for how many people are *interested in the entity right
now* — the demand the site's clicked pages all have in common. Google Trends
returns 429 unauthenticated and keyword-volume APIs are paid; Wikimedia's
REST API is public, rate-tolerant, and asks only for a descriptive User-Agent.

Two signals, combined deliberately:

* **Wikipedia views** → a bounded multiplier on the pre-rank score. Below
  ``topic_demand_wiki_min_views`` a month the entity is not demand and the
  factor is 1.0; from there it rises log-linearly to
  ``topic_demand_wiki_max_factor`` at 100× the floor. A nudge, never a lock —
  an off-goal entity with big traffic still loses to a strongly on-goal one.
* **Dual signal** — a candidate that came from a *Google* demand source
  (``search_autocomplete`` / ``gsc_query_gap``: people are typing it) AND
  names an entity with Wikipedia traffic (people are reading about it) gets
  ``topic_demand_dual_signal_factor`` on top. Two independent instruments
  agreeing is the strongest evidence this pipeline can currently gather.

Everything is fail-open and honest: any lookup failure yields factor 1.0 and
``_wiki_views: None`` in the score breakdown — *unknown*, never a fabricated
zero (``feedback_no_dummy_data``). Results (including "no article matched")
are cached in ``entity_demand_cache`` for ``topic_demand_wiki_cache_days`` so
a 30-minute sweep does not re-ask Wikimedia about the same 60 titles.

Entity resolution is deliberately simple: the candidate title is sent to the
Wikipedia search API and the top hit is accepted only if it shares a content
token with the query (a digit token or a ≥3-letter non-stopword). "rtx 5090
local llm performance" → "GeForce RTX 50 series" (shares "rtx"); "the five
days nobody was watching" → no match → unknown.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from services.title_searchability import STOPWORDS as _TITLE_STOPWORDS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings keys + code defaults (seeded in settings_defaults.py).
# ---------------------------------------------------------------------------

ENABLED_KEY = "topic_demand_wiki_enabled"
MIN_VIEWS_KEY = "topic_demand_wiki_min_views"
MAX_FACTOR_KEY = "topic_demand_wiki_max_factor"
LANG_KEY = "topic_demand_wiki_lang"
CACHE_DAYS_KEY = "topic_demand_wiki_cache_days"
TIMEOUT_KEY = "topic_demand_wiki_timeout_seconds"
DUAL_FACTOR_KEY = "topic_demand_dual_signal_factor"
GOOGLE_SOURCES_KEY = "topic_demand_google_sources"
CONCURRENCY_KEY = "topic_demand_wiki_concurrency"

DEFAULT_MIN_VIEWS = 1000
DEFAULT_MAX_FACTOR = 2.0
DEFAULT_LANG = "en"
DEFAULT_CACHE_DAYS = 7
DEFAULT_TIMEOUT_S = 5.0
DEFAULT_DUAL_FACTOR = 1.25
DEFAULT_GOOGLE_SOURCES = "search_autocomplete,gsc_query_gap"
DEFAULT_CONCURRENCY = 2

# Wikimedia asks for a descriptive UA with contact info; anonymous UAs get
# throttled or blocked. The code default is deliberately unbranded (OSS seed
# hygiene); each install sets its own name + contact in app_settings.
USER_AGENT_KEY = "topic_demand_wiki_user_agent"
DEFAULT_USER_AGENT = "topic-demand-scorer/1.0 (set topic_demand_wiki_user_agent to identify your install to Wikimedia)"

# The title gate's stoplist (function words, number words, "days"/"time"…)
# plus the query-shaped words autocomplete suggestions carry.
_STOPWORDS = _TITLE_STOPWORDS | frozenset(
    "vs versus guide top local best tutorial review explained".split()
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")
# A bare year is a date, not an entity: "java 2026" must not resolve to "Java".
_YEAR_RE = re.compile(r"^(19|20)\d\d$")


# ---------------------------------------------------------------------------
# Pure functions — the math and the guards, no I/O.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DemandSettings:
    enabled: bool = True
    min_views: int = DEFAULT_MIN_VIEWS
    max_factor: float = DEFAULT_MAX_FACTOR
    lang: str = DEFAULT_LANG
    cache_days: int = DEFAULT_CACHE_DAYS
    timeout_s: float = DEFAULT_TIMEOUT_S
    dual_factor: float = DEFAULT_DUAL_FACTOR
    google_sources: frozenset[str] = frozenset(DEFAULT_GOOGLE_SOURCES.split(","))
    concurrency: int = DEFAULT_CONCURRENCY
    user_agent: str = DEFAULT_USER_AGENT

    @classmethod
    def from_site_config(cls, site_config: Any) -> DemandSettings:
        """Read every tunable; any unreadable value falls back to the code
        default (these are ranking nudges, not gates — see the atom-level
        kill-switches for fail-closed semantics)."""
        if site_config is None:
            return cls()

        def _get(getter: str, key: str, default: Any) -> Any:
            try:
                return getattr(site_config, getter)(key, default)
            except Exception:  # noqa: BLE001 — stubbed site_config in tests
                # silent-ok: a ranking nudge falls back to its code default; the
                # atom-level kill-switches are the fail-closed gates, not this.
                return default

        sources = str(_get("get", GOOGLE_SOURCES_KEY, DEFAULT_GOOGLE_SOURCES) or "")
        return cls(
            enabled=bool(_get("get_bool", ENABLED_KEY, True)),
            min_views=max(1, int(_get("get_int", MIN_VIEWS_KEY, DEFAULT_MIN_VIEWS))),
            max_factor=max(1.0, float(_get("get_float", MAX_FACTOR_KEY, DEFAULT_MAX_FACTOR))),
            lang=(str(_get("get", LANG_KEY, DEFAULT_LANG) or DEFAULT_LANG).strip() or DEFAULT_LANG),
            cache_days=max(0, int(_get("get_int", CACHE_DAYS_KEY, DEFAULT_CACHE_DAYS))),
            timeout_s=max(0.5, float(_get("get_float", TIMEOUT_KEY, DEFAULT_TIMEOUT_S))),
            dual_factor=max(1.0, float(_get("get_float", DUAL_FACTOR_KEY, DEFAULT_DUAL_FACTOR))),
            google_sources=frozenset(s.strip() for s in sources.split(",") if s.strip()),
            concurrency=max(1, int(_get("get_int", CONCURRENCY_KEY, DEFAULT_CONCURRENCY))),
            user_agent=(str(_get("get", USER_AGENT_KEY, DEFAULT_USER_AGENT) or DEFAULT_USER_AGENT).strip()),
        )


def content_tokens(text: str) -> set[str]:
    """Lowercased tokens that can anchor an entity match: digit-bearing
    tokens of any length, or alphabetic tokens ≥3 chars not in the stoplist.
    Dotted names contribute both the whole (``llama.cpp``) and their parts."""
    out: set[str] = set()
    for raw in _TOKEN_RE.findall(text or ""):
        whole = raw.strip(".+-").lower()
        parts = [p for p in re.split(r"[.+-]", whole) if p] if any(c in whole for c in ".+-") else []
        for tok in [whole, *parts]:
            if not tok:
                continue
            if any(c.isdigit() for c in tok):
                out.add(tok)
            elif len(tok) >= 3 and tok not in _STOPWORDS:
                out.add(tok)
    return out


def distinctive_tokens(article_title: str) -> set[str]:
    """The article-title tokens strong enough to prove a match: digit-bearing,
    ≥4 letters, or an ALL-CAPS acronym as written (``RTX``, ``GGUF``, ``LLM``).
    A plain 3-letter word (``Gap`` in "Gap Inc.") proves nothing."""
    out: set[str] = set()
    for raw in _TOKEN_RE.findall(article_title or ""):
        whole = raw.strip(".+-")
        parts = [p for p in re.split(r"[.+-]", whole) if p] if any(c in whole for c in ".+-") else []
        for tok in [whole, *parts]:
            low = tok.lower()
            if not low or low in _STOPWORDS:
                continue
            if any(c.isdigit() for c in low) or len(low) >= 4 or (len(low) >= 2 and tok.isupper()):
                out.add(low)
    return out


def article_matches_query(query: str, article_title: str) -> bool:
    """Accept a search hit only when the query shares a *distinctive* token
    with the article title. Wikipedia search is fuzzy and will happily return
    *something* for "the gap nobody names" (→ "Gap Inc."); a shared digit
    token, ≥4-letter word, or acronym is the cheap proof the hit is about the
    thing the topic names."""
    return bool(content_tokens(query) & distinctive_tokens(article_title))


# Single plain words that name a Wikipedia article about the GENERAL concept
# ("Performance", "Memory", "Generation") — huge readership, no relation to
# the topic's demand. A lone one of these never resolves; inside a phrase or
# beside a digit it is fine ("memory bandwidth ddr5" still resolves on ddr5).
_GENERIC_SINGLE_WORDS = frozenset(
    """
    performance memory speed generation model models system systems data
    design development engineering software hardware network networks
    security cloud server servers code coding programming language languages
    learning training inference search content business money market
    marketing product products service services tool tools framework
    frameworks library libraries platform platforms strategy strategies
    process processes management quality testing test tests analysis
    architecture computer computers computing internet technology
    """.split()
)


_ENTITY_DIGIT_RE = re.compile(r"^(?=.*\d)(?:[a-z]*\d{3,}[a-z]*|[a-z]{2,}\d+[a-z]*|\d+[a-z]{2,})$")


_ORDINAL_RE = re.compile(r"^\d+(st|nd|rd|th)$")


def _is_entity_digit(low: str) -> bool:
    """A digit token that names a thing: ≥3 digits ("5090", "6400"), or
    letters+digits with ≥2 letters ("ddr5", "16gb", "rtx4090"). Excludes
    bare years, prices, versions and ordinals ("2026", "13b", "3.0", "27",
    "9th") — live sweeps resolved "$13b" to *13B (film)*, "3.0" to
    *0.0.0.0* and "9th circuit" to *9th Division*."""
    return (
        bool(_ENTITY_DIGIT_RE.match(low))
        and not _YEAR_RE.match(low)
        and not _ORDINAL_RE.match(low)
    )


# Article titles that are never THE entity: disambiguation pages and "List
# of …" aggregates (the latter carry the readership of every item on them —
# "DDR5 6400 … Ryzen 9" landed on *List of AMD Ryzen processors*, 73k views).
_NON_ENTITY_TITLE_RE = re.compile(r"^(list of |lists of )|\(disambiguation\)\s*$", re.IGNORECASE)


def resolution_candidates(title: str, *, max_candidates: int = 6) -> list[tuple[str, str]]:
    """Search queries to try for a title, most entity-shaped first, each
    tagged with the acceptance ``mode`` ``accept_hit`` applies.

    Whole titles resolve badly ("Rtx 5090 Local Llm Performance" → nothing;
    "FastAPI best practices" → "Coding best practices"), while the entity
    inside them resolves cleanly ("rtx 5090" → GeForce RTX 50 series,
    "fastapi" → FastAPI). Order:

    1. ``phrase`` — the content-word phrase (every non-stopword token, when
       ≥2 remain); the hit must be headed by its first word;
    2. ``digit`` — an entity digit token with its preceding content word
       ("rtx 5090"), then the token alone; a shared distinctive token is
       enough (product names put the family first: "GeForce RTX 50 series");
    3. ``token`` — dotted / mixed-case / ALL-CAPS tokens as written
       ("llama.cpp", "vLLM"); headed by the token;
    4. ``window`` — 2-word then 3-word windows over the content words
       ("cosine similarity"); EVERY window word must appear in the hit's
       head, because ordinary word pairs land on pop culture otherwise
       ("dark screen" → *Dark fantasy*, "ai boom" → *Boom, Boom, Boom!!*).

    Plain single words are never searched alone: "vram" is a person,
    "information" is read 70k times a month. Nothing entity-shaped → the
    title stays *unknown*, the honest answer for "The five days nobody was
    watching".
    """
    raw_tokens = [t.strip(".+-") for t in _TOKEN_RE.findall(title or "")]
    raw_tokens = [t for t in raw_tokens if t]
    lowers = [t.lower() for t in raw_tokens]
    content = [
        (t, low) for t, low in zip(raw_tokens, lowers, strict=True)
        if low not in _STOPWORDS and (len(low) >= 3 or any(c.isdigit() for c in low))
    ]
    out: list[tuple[str, str]] = []

    def _add(q: str, mode: str) -> None:
        q = " ".join(q.split())
        if q and q.lower() not in {o.lower() for o, _ in out}:
            out.append((q, mode))

    if len(content) >= 2:
        _add(" ".join(low for _, low in content), "phrase")
    for i, (_tok, low) in enumerate(content):
        if _is_entity_digit(low):
            if i > 0:
                _add(f"{content[i - 1][1]} {low}", "digit")
            # A pure number alone ("6400", "8000") is a number article, not a
            # product; only alphanumerics ("ddr5", "16gb") stand on their own.
            if any(c.isalpha() for c in low):
                _add(low, "digit")
    for tok, low in content:
        if any(c.isdigit() for c in low):
            # Digit-bearing tokens are the digit rule's business: "$13B" is
            # ALL-CAPS but a price, and it resolved to *13B (film)* live.
            continue
        if any(c in low for c in ".+") or (tok.isupper() and len(tok) >= 2) or (
            tok[:1].isupper() and any(c.isupper() for c in tok[1:]) and not tok.isupper()
        ):
            _add(low, "token")
    # 2-grams before 3-grams: the two-word window is where entities live
    # ("cosine similarity", "vector database"), and the first accepted hit
    # wins — so it must be tried before a longer window can land on a
    # coincidental title.
    lows = [low for _, low in content if not _YEAR_RE.match(low) and not any(c.isdigit() for c in low)]
    for n in (2, 3):
        for i in range(0, max(0, len(lows) - n + 1)):
            window = lows[i:i + n]
            if all(w in _GENERIC_SINGLE_WORDS for w in window):
                continue
            _add(" ".join(window), "window")
    return out[:max_candidates]


def _head_tokens(hit_title: str) -> list[str]:
    """Lowercased tokens of the article title with any trailing
    parenthetical disambiguator removed ("Stuck (2017 film)" → ["stuck"])."""
    head = re.sub(r"\s*\(.*\)\s*$", "", hit_title or "").strip()
    out: list[str] = []
    for raw in _TOKEN_RE.findall(head):
        whole = raw.strip(".+-").lower()
        for tok in [whole, *([p for p in re.split(r"[.+-]", whole) if p] if any(c in whole for c in ".+-") else [])]:
            if tok:
                out.append(tok)
    return out


def accept_hit(candidate: str, hit_title: str, mode: str = "phrase") -> bool:
    """Whether a search hit for ``candidate`` is about the thing it names.

    Always: they share a distinctive token that is not a generic concept
    word. Then by ``mode`` (see ``resolution_candidates``): ``digit`` needs
    nothing more; ``phrase`` / ``token`` need the article to be *headed* by
    the candidate's first word ("cosine similarity" → "Cosine similarity",
    "gguf quantization types" → "GGUF"); ``window`` needs EVERY window word
    in the article head ("spring boot" → "Spring Boot" passes, "dark screen"
    → "Dark fantasy" and "minus signs" → "Plus and minus signs" do not).
    Hyphen/dot compounds compare by their first part
    ("Retrieval-augmented" → "retrieval"). Disambiguation pages and "List
    of …" aggregates are never accepted.
    """
    if _NON_ENTITY_TITLE_RE.search(hit_title or ""):
        return False
    shared = content_tokens(candidate) & distinctive_tokens(hit_title)
    if not (shared - _GENERIC_SINGLE_WORDS):
        return False
    if mode == "digit":
        return True
    head = _head_tokens(hit_title)
    if not head:
        return False
    head_set = set(head) | {re.split(r"[.+-]", h)[0] for h in head}
    words = candidate.split()
    if mode == "window":
        return all((w in head_set or re.split(r"[.+-]", w)[0] in head_set) for w in words)
    head_first = {head[0], *re.split(r"[.+-]", head[0])[:1]}
    first = words[0]
    return first in head_first or re.split(r"[.+-]", first)[0] in head_first


def demand_factor(views: int | None, *, min_views: int, max_factor: float) -> float:
    """Bounded log-linear multiplier.

    ``None`` (unknown) and anything under ``min_views`` → 1.0. From the
    floor the factor rises with log10(views / min_views), reaching
    ``max_factor`` at 100× the floor and clamping there. With the defaults
    (1,000 / 2.0): 1k → 1.0, 10k → 1.5, 100k+ → 2.0.
    """
    if views is None or views < min_views or max_factor <= 1.0:
        return 1.0
    ratio = math.log10(views / float(min_views)) / 2.0
    return round(1.0 + (max_factor - 1.0) * max(0.0, min(1.0, ratio)), 4)


def combined_factor(
    views: int | None,
    *,
    source_name: str | None,
    settings: DemandSettings,
) -> tuple[float, dict[str, Any]]:
    """Demand multiplier + the breakdown entries that explain it.

    Returns ``(factor, {"_wiki_views", "_demand_factor", "_dual_signal"})``.
    ``_wiki_views`` is ``None`` when unknown — the breakdown must never
    read 0 for "we could not look".
    """
    base = demand_factor(views, min_views=settings.min_views, max_factor=settings.max_factor)
    dual = bool(
        views is not None
        and views >= settings.min_views
        and source_name in settings.google_sources
        and settings.dual_factor > 1.0
    )
    factor = round(base * (settings.dual_factor if dual else 1.0), 4)
    return factor, {"_wiki_views": views, "_demand_factor": factor, "_dual_signal": dual}


def pageviews_window(today: datetime | None = None) -> tuple[str, str]:
    """(start, end) as YYYYMMDD for the last 30 complete UTC days. Wikimedia
    publishes a day's counts the next day, so ``end`` is yesterday."""
    now = today or datetime.now(UTC)
    end = (now - timedelta(days=1)).date()
    start = end - timedelta(days=29)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def cache_key(query: str, lang: str) -> str:
    return f"{lang}:{' '.join((query or '').lower().split())}"


# ---------------------------------------------------------------------------
# The scorer — Wikipedia search → pageviews, with a DB cache.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityDemand:
    query: str
    wiki_title: str | None
    monthly_views: int | None
    cached: bool = False


class WikipediaDemandScorer:
    """Resolve topic titles to Wikipedia articles and monthly pageviews.

    ``http_client`` is an ``httpx.AsyncClient``-shaped object; when omitted
    the lifespan-shared client is used. Outside the lifespan (CLI, tests)
    with nothing injected, lookups fail-open to *unknown* rather than opening
    ad-hoc clients — no surprise egress from a unit test or a one-off script.
    ``pool`` is optional — without it nothing is cached, every call hits
    Wikimedia (tests, one-off scripts).
    """

    def __init__(
        self,
        *,
        settings: DemandSettings,
        pool: Any = None,
        http_client: Any = None,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._client = http_client

    # ---- HTTP -------------------------------------------------------------

    @staticmethod
    def shared_client_available() -> bool:
        """True when the lifespan wired ``services.http_client`` — the sweep
        skips lookups entirely (factor 1.0, views None) when it did not."""
        try:
            from services import http_client as _hc

            return _hc.http_client is not None
        except Exception:  # noqa: BLE001
            # silent-ok: "is the lifespan client wired?" — an import failure
            # here means no, and the sweep then ranks without demand.
            return False

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        headers = {"User-Agent": self._settings.user_agent, "Accept": "application/json"}
        client = self._client
        if client is None:
            try:
                from services.http_client import get_shared_http_client

                client = get_shared_http_client()
            except Exception:  # noqa: BLE001 — outside the lifespan (CLI / tests)
                # silent-ok: resolved to "no client" and raised loudly just below.
                client = None
        if client is None:
            raise RuntimeError(
                "no HTTP client: outside the FastAPI lifespan and none injected "
                "(pass http_client=... for CLI / one-off use)"
            )
        resp = await client.get(url, params=params, headers=headers, timeout=self._settings.timeout_s)
        resp.raise_for_status()
        return resp.json()

    async def _search_title(self, query: str) -> str | None:
        """Resolve a topic title to a Wikipedia article title, or None.

        Tries ``resolution_candidates`` in order, three hits each, and takes
        the first hit ``accept_hit`` approves. At most ``max_candidates``
        searches per title; the sweep caches the outcome either way.
        """
        lang = self._settings.lang
        for candidate, mode in resolution_candidates(query):
            data = await self._get_json(
                f"https://{lang}.wikipedia.org/w/api.php",
                {"action": "query", "list": "search", "srsearch": candidate,
                 "srlimit": 3, "format": "json"},
            )
            hits = (((data or {}).get("query") or {}).get("search") or [])
            for hit in hits:
                title = str(hit.get("title") or "").strip()
                if title and accept_hit(candidate, title, mode):
                    return title
        return None

    async def _monthly_views(self, title: str) -> int | None:
        lang = self._settings.lang
        start, end = pageviews_window()
        article = quote(title.replace(" ", "_"), safe="")
        data = await self._get_json(
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"{lang}.wikipedia/all-access/user/{article}/daily/{start}/{end}"
        )
        items = (data or {}).get("items") or []
        if not items:
            return None
        return int(sum(int(i.get("views") or 0) for i in items))

    # ---- cache ------------------------------------------------------------

    async def _cache_get(self, key: str) -> EntityDemand | None:
        if self._pool is None or self._settings.cache_days <= 0:
            return None
        try:
            row = await self._pool.fetchrow(
                "SELECT query_key, wiki_title, monthly_views, fetched_at "
                "FROM entity_demand_cache WHERE query_key = $1 "
                "AND fetched_at > now() - ($2::int * interval '1 day')",
                key, self._settings.cache_days,
            )
        except Exception as exc:  # noqa: BLE001 — cache is an optimisation
            logger.warning("[ENTITY_DEMAND] cache read failed: %s", exc)
            return None
        if row is None:
            return None
        return EntityDemand(
            query=key, wiki_title=row["wiki_title"],
            monthly_views=row["monthly_views"], cached=True,
        )

    async def _cache_put(self, key: str, lang: str, title: str | None, views: int | None) -> None:
        if self._pool is None or self._settings.cache_days <= 0:
            return
        try:
            await self._pool.execute(
                "INSERT INTO entity_demand_cache (query_key, lang, wiki_title, monthly_views, fetched_at) "
                "VALUES ($1, $2, $3, $4, now()) "
                "ON CONFLICT (query_key) DO UPDATE SET lang = EXCLUDED.lang, "
                "wiki_title = EXCLUDED.wiki_title, monthly_views = EXCLUDED.monthly_views, "
                "fetched_at = now()",
                key, lang, title, views,
            )
        except Exception as exc:  # noqa: BLE001 — cache is an optimisation
            logger.warning("[ENTITY_DEMAND] cache write failed: %s", exc)

    # ---- public -----------------------------------------------------------

    async def demand_for(self, query: str) -> EntityDemand:
        """Resolve one title. Never raises: an unknown is ``monthly_views=None``."""
        query = " ".join((query or "").split())
        if not query:
            return EntityDemand(query=query, wiki_title=None, monthly_views=None)
        key = cache_key(query, self._settings.lang)
        hit = await self._cache_get(key)
        if hit is not None:
            return hit
        title: str | None = None
        views: int | None = None
        try:
            title = await self._search_title(query)
            if title:
                views = await self._monthly_views(title)
        except Exception as exc:  # noqa: BLE001 — fail-open, breakdown says None
            logger.warning(
                "[ENTITY_DEMAND] lookup failed for %r (%s: %s) — factor 1.0, views unknown",
                query, type(exc).__name__, exc,
            )
            # Do not cache a transport failure: the next sweep should retry.
            return EntityDemand(query=query, wiki_title=title, monthly_views=None)
        # A confirmed "no article" IS cached — asking again next sweep won't help.
        await self._cache_put(key, self._settings.lang, title, views)
        return EntityDemand(query=query, wiki_title=title, monthly_views=views)

    async def demand_for_many(self, queries: list[str]) -> dict[str, EntityDemand]:
        """Resolve many titles with bounded concurrency; keyed by the query."""
        sem = asyncio.Semaphore(self._settings.concurrency)
        uniq = list(dict.fromkeys(" ".join((q or "").split()) for q in queries if q and q.strip()))

        async def _one(q: str) -> tuple[str, EntityDemand]:
            async with sem:
                return q, await self.demand_for(q)

        results = await asyncio.gather(*(_one(q) for q in uniq))
        return dict(results)


__all__ = [
    "DemandSettings",
    "accept_hit",
    "resolution_candidates",
    "EntityDemand",
    "WikipediaDemandScorer",
    "article_matches_query",
    "cache_key",
    "combined_factor",
    "content_tokens",
    "demand_factor",
    "pageviews_window",
]
