"""
Content Validator — programmatic quality gate for AI-generated content.

Runs hard rules against generated content BEFORE it can be published.
No LLM judgment — deterministic pattern matching that catches:
- Fabricated people, quotes, and statistics
- False claims about the company
- Unverifiable citations
- Impossible timeframes and metrics

Usage:
    from modules.content.content_validator import validate_content
    result = validate_content(title, content, topic, site_config=site_config)
    if not result.passed:
        # Reject — content has quality issues
"""

import re
import time as _time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from services.logger_config import get_logger
from utils.crawler_ua import build_crawler_ua

if TYPE_CHECKING:
    import httpx

# #272 Phase-2g: the module-level ``site_config`` global + ``set_site_config``
# setter are DELETED. injection is now mandatory — the public entry points
# (``validate_content`` / ``_check_code_block_density`` / ``verify_content_urls``)
# all accept a ``site_config`` param and callers thread the run-bound instance.
# Seam 1 Wave 3f (#667): the import-time ``COMPANY_FACTS = _get_company_facts()``
# call below now returns ``{}`` when no site_config is available (instead of
# building a bare SiteConfig()); the pipeline always threads a real instance.
# ``content_validator`` is therefore removed from ``di_wiring.WIRED_MODULES``.


# Lifespan-bound shared httpx.AsyncClient — main.py wires this via
# set_http_client() at startup. ``verify_content_urls`` prefers it
# when wired so the connection pool stays warm across the per-task
# URL-validation stage (5-50 URLs per content task).
http_client: "httpx.AsyncClient | None" = None


def set_http_client(client: "httpx.AsyncClient | None") -> None:
    """Wire the lifespan-bound shared httpx.AsyncClient."""
    global http_client
    http_client = client


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Prometheus counter — per-rule warning emission (GH-91)
# ---------------------------------------------------------------------------
#
# Matt, 2026-04-20: the validator was already emitting warnings but nothing
# downstream counted them. Without aggregate visibility in Grafana,
# patterns like "unlinked_citation spiking across the week" go unnoticed
# until a reader points at an embarrassing post. This counter fixes that.
#
# Wrapped in a try/except so tests that stub out prometheus_client (or
# envs that never pulled the package) don't explode. If the dependency
# is missing we fall back to a no-op shim — nothing else in the module
# depends on the counter actually recording.

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]

    CONTENT_VALIDATOR_WARNINGS_TOTAL = _Counter(
        "content_validator_warnings_total",
        "Total warnings emitted by content_validator, labeled by rule category",
        ["rule"],
    )
except Exception:  # pragma: no cover — exercised only when prometheus_client is absent
    class _NoopCounter:
        def labels(self, **_kwargs):  # noqa: D401 — trivial shim
            return self

        def inc(self, _amount: float = 1.0) -> None:
            return None

    CONTENT_VALIDATOR_WARNINGS_TOTAL = _NoopCounter()  # type: ignore[assignment]


# Keywords that, when present in an unlinked-citation match, promote the
# warning to critical (Matt's call: "named source without a URL" is the
# hallucinated-attribution pattern, worse than a generic weasel).
_NAMED_SOURCE_KEYWORDS = (
    "medium",
    "article",
    "blog post",
    "documentation",
    "paper",
    "study",
)

# ============================================================================
# COMPANY FACTS — ground truth for fact-checking (configurable)
# Override via environment variables for your own brand
# ============================================================================


def _get_company_facts(site_config: Any = None) -> dict:
    """Load company facts from DB (site_config) with env fallback.

    Seam 1 Wave 3f (#667): SiteConfig() fallback removed. When called at
    import time (``COMPANY_FACTS = _get_company_facts()`` below) without
    a site_config, returns an empty dict — the facts will be populated when
    the pipeline calls validate_content with the run-bound instance.
    """
    if site_config is None:
        return {}
    _sc = site_config
    return {
        "company_name": _sc.get("company_name", "My Company"),
        "founded_date": _sc.get("company_founded_date", "2025-01-01"),
        "founded_year": _sc.get_int("company_founded_year", 2025),
        "age_months": _sc.get_int("company_age_months", 12),
        "team_size": _sc.get_int("company_team_size", 1),
        "founder_name": _sc.get("company_founder_name", "Founder"),
        "known_employees": set(),
        "real_products": set(_sc.get("company_products", "").split(",")) if _sc.get("company_products") else set(),
        "real_tech": {"fastapi", "next.js", "postgresql", "ollama", "vercel", "grafana"},
    }


# Loaded at module import time — returns {} when no site_config available;
# real values are populated per-call via validate_content(site_config=...).
COMPANY_FACTS = _get_company_facts()
_COMPANY_NAME = COMPANY_FACTS.get("company_name", "My Company")
# Back-compat alias for the pre-2026-07 operator-flavored name.
GLAD_LABS_FACTS = COMPANY_FACTS

# People names that should NEVER appear (fabricated by LLMs)
FAKE_NAME_PATTERNS = [
    r"\b(?:Sarah|John|Emily|David|Michael|Jennifer|James|Jessica|Robert|Lisa)\s+[A-Z][a-z]+(?:,\s*(?:CEO|CTO|VP|Director|Lead|Head|Chief|Manager|Founder|Co-founder))",
    r"\b(?:Dr\.|Prof\.)\s+[A-Z][a-z]+\s+[A-Z][a-z]+",
    rf"(?:CEO|CTO|VP|Director|Lead Architect|Head of|Chief)\s+(?:at|of)\s+(?:{re.escape(_COMPANY_NAME)})",
]

# Fake statistic patterns
FAKE_STAT_PATTERNS = [
    r"\b\d{1,3}%\s+(?:reduction|increase|improvement|decrease|growth|decline|boost|drop|surge|rise)",
    r"(?:according to|a\s+\d{4}\s+(?:study|report|survey))\s+(?:by|from|conducted)",
    r"\b(?:McKinsey|Gartner|Forrester|Deloitte|BCG|Bain|Accenture)\s+(?:report|study|survey|research|found|estimates)",
    r"(?:research|data|studies)\s+(?:shows?|suggests?|indicates?|reveals?|confirms?)\s+that\s+\d",
]

# Impossible claims about the company (uses configurable company name)
_CN = re.escape(_COMPANY_NAME)
COMPANY_IMPOSSIBLE = [
    rf"(?:{_CN}|our|we)\s+(?:has|have)\s+(?:been|spent)\s+(?:\w+\s+)*(?:years?|decade)",
    rf"(?:{_CN}|our)\s+(?:team|staff|employees|engineers|developers)\s+of\s+\d{{2,}}",
    rf"(?:{_CN}|our)\s+(?:clients?|customers?|users?)\s+(?:include|such as|like)\s+[A-Z]",
    rf"(?:{_CN}|we)\s+(?:processed|handled|served|generated)\s+(?:\d+\s*(?:million|billion|thousand))",
    rf"(?:{_CN}|our)\s+(?:revenue|profit|valuation|funding)",
]
# Back-compat alias for the pre-2026-07 operator-flavored name.
GLAD_LABS_IMPOSSIBLE = COMPANY_IMPOSSIBLE

# Fabricated quote patterns
FAKE_QUOTE_PATTERNS = [
    r'["\u201c][^"\u201d]{10,200}["\u201d]\s*(?:says?|said|explains?|explained|recalls?|recalled|notes?|noted|adds?|added)\s+[A-Z][a-z]+',
    r'(?:says?|said|explains?|recalled)\s+[A-Z][a-z]+\s+[A-Z][a-z]+(?:,\s*(?:CEO|CTO|VP|founder|director))',
]

# Hallucinated internal links — phrases that claim internal content exists
HALLUCINATED_LINK_PATTERNS = [
    r"\b(?:our|my|the)\s+(?:guide|article|post|tutorial|deep.dive|report)\s+on\s+[a-z]",
    r"(?:as\s+(?:we|I)\s+(?:discussed|explored|covered|explained|wrote))\s+in\s+(?:our|a\s+previous)",
    r"(?:check\s+out|see|read)\s+our\s+(?:guide|post|article|tutorial)",
]

# Unlinked citations — references to papers/studies/research by name without a URL.
# These are almost always hallucinated by the LLM. Real citations need real links.
# Matches patterns like "introduced in Paper Title", "according to Study Name".
# Only flags if the citation-like text is NOT inside a markdown link [text](url).
UNLINKED_CITATION_PATTERNS = [
    # "introduced in <Paper Title>" / "proposed in <Title>" — catches ALL-CAPS
    # acronyms (I-DLM), acronym-colon-title (I-DLM: Introspective...), and
    # plain title-case paper names.
    r"(?:introduced|proposed|described|presented|outlined|documented|published)\s+in\s+(?!\[)(?:[A-Z][A-Za-z0-9\-]*(?::\s+)?[A-Za-z]+(?:\s+[A-Za-z]+){1,10})",
    # "described in 'Paper Title'" — quoted paper references (real papers are linked)
    r"(?:described|referenced|cited|mentioned)\s+in\s+['\"\u2018\u201c][A-Z][^'\"\u2019\u201d]{15,100}['\"\u2019\u201d]",
    # "according to Title Case Source" (not followed by a link)
    r"(?:according\s+to|as\s+(?:highlighted|noted|reported|described|shown)\s+(?:in|by))\s+(?!\[)(?:[A-Z][A-Za-z0-9\-]*(?:\s+[A-Za-z]+){1,6})",
    # Bare paper-style titles with colon: "Word Word: Subtitle With Title Case".
    # Case-sensitive — every Capital must actually be uppercase. Without
    # ``(?-i:...)`` IGNORECASE makes ``[A-Z]`` match lowercase too, so this
    # pattern fired on every "the core idea is simple:..." prose phrase
    # AND every "[Beyond Autocomplete:...]" markdown link (the engine
    # would start the match one char into "Beyond" to bypass ``(?<!\[)``).
    # ``\b`` anchors the start to a word boundary; that, plus the case-
    # sensitive capture, prevents IGNORECASE from defeating the lookbehind.
    r"(?<!\[)\b(?-i:[A-Z][A-Za-z0-9\-]*(?:\s+[A-Z][a-z]+){1,}:\s+[A-Z][a-z]+(?:\s+[A-Za-z]+){2,})(?!\])",
    # "et al." references — almost certainly fabricated.
    r"\b[A-Z][a-z]+\s+et\s+al\.?\s*(?:\(\d{4}\)|\[\d+\])?",
    # arXiv IDs without accompanying URL: "arXiv:2401.12345"
    r"\barXiv:\s*\d{4}\.\d{4,5}(?!\s*[\]\)])",
    # DOI without link: "doi:10.xxxx/..."
    r"\bdoi:\s*10\.\d{4,}/[^\s\]\)]+",
]

# Brand contradiction — we are Ollama-only, never promote paid cloud APIs
BRAND_CONTRADICTION_PATTERNS = [
    r"(?:pay(?:ing)?\s+(?:for|per)\s+(?:token|API|inference))\s+(?:to|with|from)\s+(?:OpenAI|Anthropic|Google)",
    r"(?:OpenAI|Anthropic)\s+(?:API|pricing|bill|invoice|subscription|cost)",
    r"(?:bill|invoice|cost)\s+from\s+(?:OpenAI|Anthropic|Google\s+Cloud)",
]

# Fabricated personal experience patterns — AI pretending to be a person
FABRICATED_EXPERIENCE_PATTERNS = [
    # "I was on a call with...", "I sat down with a client..."
    r"\bI\s+(?:was|sat|had|got)\s+(?:on\s+a\s+call|in\s+a\s+meeting|talking|chatting)\s+with\s+(?:a\s+)?(?:startup|client|founder|engineer|developer|CTO|CEO|team)",
    # "at my company", "at my current company", "at our company"
    r"\b(?:at|for)\s+(?:my|our)\s+(?:current\s+)?(?:company|startup|org|organization|employer|firm|agency)",
    # "a client of mine", "one of my clients", "a founder I know"
    r"\b(?:a\s+(?:client|customer|founder|friend|colleague)\s+(?:of\s+mine|I\s+(?:know|work|met)))",
    # "last week I...", "last month we...", "recently I..."
    r"\b(?:last\s+(?:week|month|year|quarter)|recently|the\s+other\s+day)\s+(?:I|we)\s+(?:was|were|had|got|built|deployed|switched|migrated)",
    # Fabricated dollar amounts in anecdotes: "$1,200/month", "saved us $X"
    r"\b(?:cost(?:ing)?|saved?|spent|paying|bill(?:ed)?)\s+(?:us\s+)?\$[\d,]+(?:/(?:month|year|mo|yr))?",
    # "he said", "she told me" — fabricated dialogue
    r'["\u201c][^"\u201d]{10,150}["\u201d]\s*(?:he|she|they)\s+(?:said|told|explained|replied|asked)',
]

# Leaked image generation prompts — italic descriptions after images
LEAKED_IMAGE_PROMPT_PATTERNS = [
    r"(?:^|\n)\s*:\s*\*[A-Z][^*]{30,}\*",  # `: *A split-screen comparison...*`
    r"(?:^|\n)\s*\*(?:A |An |Imagine |Visual |Split|Close)[^*]{40,}\*",  # standalone `*A description...*`
]

# Citation artifacts (#532) — bracketed numeric refs and parenthetical
# academic citations the LLM emits from training on papers/Wikipedia. Real
# sources in our content must be Markdown links, not these dangling markers.
CITATION_ARTIFACT_PATTERNS = [
    # Bare numeric citation bracket "[12]" NOT part of a Markdown link/ref
    # (excludes ``[n](url)``, ``[n]:`` ref-def, and reference-style ``[t][n]``).
    r"(?<!\])\[\d{1,3}\](?![\(\:\[])",
    # "(Smith, 2023)" — author-comma-year.
    r"\([A-Z][A-Za-z]+,\s+\d{4}[a-z]?\)",
    # "(Smith et al., 2024)" / "(Smith and Jones, 2024)".
    r"\([A-Z][A-Za-z]+\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z]+|&\s+[A-Z][A-Za-z]+),?\s+\d{4}[a-z]?\)",
]

# Placeholder citation artifacts (#766) — the niche writer (glm-4.7), told to
# cite the SOURCES / internal snippets "inline as markdown links", invents a
# *placeholder* citation when it has a claim but no real URL: a bracketed label
# echoing the prompt's own vocabulary ("[INTERNAL SNIPPET]") or a markdown link
# whose href is a placeholder word ("[text](internal_context_link)",
# "[text](url)") rather than a real URL. Unlike the advisory citation_artifact /
# unlinked_citation rules above, these are UNAMBIGUOUS draft artifacts — no
# reader-facing prose ever contains them — so they hard-reject (critical). The
# rule is scanned with code spans blanked (see the call site) so a markdown
# tutorial that shows "[text](url)" as an EXAMPLE does not fire. Closes the gap
# that shipped "[INTERNAL SNIPPET]" + a dead "[...](internal_context_link)" past
# the advisory LLM critic on a 2026-06-11 niche rerun.
PLACEHOLDER_CITATION_PATTERNS = [
    # Bracketed label echoing the writer prompt's "internal snippet(s)" /
    # "internal source(s)" vocabulary. "internal" is REQUIRED so the bare word
    # "snippet"/"source" as legitimate link text ("[snippet](real-url)") never
    # fires; the optional trailing index covers "[INTERNAL SNIPPET 2]".
    r"\[\s*internal\s+snippets?(?:\s+\d{1,3})?\s*\]",
    r"\[\s*internal\s+sources?(?:\s+\d{1,3})?\s*\]",
    # Wikipedia-style unresolved-citation placeholder.
    r"\[\s*citation\s+needed\s*\]",
    # Markdown link whose href is a placeholder word rather than a real
    # URL/path: "[text](url)" "[text](link)" "[text](source)" "[text](citation)"
    # "[text](internal_context_link)" "[text](insert url here)"
    # "[text](your link here)". The href is anchored between ``(`` and ``)`` so a
    # real link such as "(https://x/url)" never matches.
    r"\]\(\s*(?:url|link|source|citation|internal[_\s\-]?(?:context[_\s\-]?)?link|insert[_\s\-]?(?:url|link)(?:[_\s\-]?here)?|your[_\s\-]?(?:url|link)[_\s\-]?here)\s*\)",
    # Bare *parenthetical* placeholder the writer drops in lieu of a real link —
    # "(source)", "(citation)", "(citation needed)", "(add source)". The whole
    # parenthetical must be ONLY the placeholder vocabulary (anchored ``\(`` …
    # ``\)`` around the word) so legitimate prose like "(source code on GitHub)"
    # or "(a reference to the handler)" never fires; ``(?<!\])`` skips the
    # "](source)" markdown-href form already caught by the rule above. Closes
    # finding #3 of the 2026-06-19 pipeline validation: the gemma writer shipped
    # 2 literal "(source)" placeholders past every rail to the awaiting_approval
    # draft (had to be hand-fixed before publish).
    r"(?<!\])\(\s*(?:sources?|citations?|cite|references?|links?|urls?)\s*\)",
    r"(?<!\])\(\s*(?:source|citation|reference)s?\s+needed\s*\)",
    r"(?<!\])\(\s*(?:add|insert)\s+(?:a\s+)?(?:source|citation|link|reference|url)s?\s*\)",
]

# Leaked internal path tokens (#532) — poindexter's OWN source identifiers must
# never appear in published niche content (AI/gaming/hardware). Their presence
# means the writer regurgitated system/repo context. Kept to UNAMBIGUOUS
# internal tokens so generic coding prose doesn't false-positive; dev_diary
# (founder voice about the system) opts out via applies_to_niches.
LEAKED_PATH_TOKEN_PATTERNS = [
    r"\bsrc/cofounder_agent[\w./\-]*",
    r"\bcofounder_agent[/\w.]*",
    r"\bglad-labs-stack\b",
]

# Removed 2026-05-01: FIRST_PERSON_TITLE_PATTERNS — Matt killed the title
# pronoun gate after it became the dominant rejection reason (65 of 91
# programmatic-validator vetoes in a 24h window were "Title contains
# first-person pronoun"). The body-side voice rules in quality_scorers
# (`first_person_claims` for "I/we built/created/etc") still enforce
# newsroom voice in the post body — only the *title* check is gone.
# Original rule + tests preserved in git history if revival is needed.

# Known-wrong facts are now loaded from the `fact_overrides` DB table.
# Manage via pgAdmin or API — no redeployment needed.
# Cached in-memory with a 5-minute TTL.
_fact_overrides_cache: list[tuple[str, str, str]] = []
_fact_overrides_ts: float = 0.0
_FACT_OVERRIDES_TTL = 300  # seconds


def _load_fact_overrides_sync() -> list[tuple[str, str, str]]:
    """Load active fact overrides from DB (sync, cached).

    Returns list of (pattern, correct_fact, severity) tuples.
    Falls back to empty list if DB unavailable.
    """
    global _fact_overrides_cache, _fact_overrides_ts
    now = _time.time()
    if _fact_overrides_cache and (now - _fact_overrides_ts) < _FACT_OVERRIDES_TTL:
        return _fact_overrides_cache

    try:
        import asyncio
        import sys
        from pathlib import Path

        # brain.bootstrap.resolve_database_url() is the canonical DSN
        # resolver — no os.getenv in services (project-wide rule).
        try:
            _proj = Path(__file__).resolve()
            for _p in _proj.parents:
                if (_p / "brain" / "bootstrap.py").is_file():
                    if str(_p) not in sys.path:
                        sys.path.insert(0, str(_p))
                    break
            from brain.bootstrap import resolve_database_url
            db_url = resolve_database_url() or ""
        except Exception:
            db_url = ""
        if not db_url:
            return _fact_overrides_cache

        async def _fetch():
            import asyncpg
            conn = await asyncpg.connect(db_url, timeout=5)
            try:
                rows = await conn.fetch(
                    "SELECT pattern, correct_fact, severity FROM fact_overrides WHERE active = true"
                )
                return [(r["pattern"], r["correct_fact"], r["severity"]) for r in rows]
            finally:
                await conn.close()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, _fetch())
                result = future.result(timeout=5)
        else:
            result = asyncio.run(_fetch())

        _fact_overrides_cache = result
        _fact_overrides_ts = now
        logger.debug("[VALIDATOR] Loaded %d fact overrides from DB", len(result))
    except Exception as e:
        logger.warning("[VALIDATOR] fact_overrides DB load failed (using cache): %s", e)

    return _fact_overrides_cache


# Legacy alias — kept so nothing breaks if referenced externally
KNOWN_WRONG_HARDWARE_PATTERNS: list[tuple[str, str]] = []


# Filler phrases the writer falls back on when it has nothing specific to
# say. Every post I audited on 2026-04-11 had at least one of these. They
# add nothing and signal "AI-generated" to any attentive reader. Warning
# level — they hurt quality but don't rise to the fabrication bar.
FILLER_PHRASE_PATTERNS = [
    r"\bmany organizations have found\b",
    r"\bmany companies have found\b",
    r"\bmany (?:teams|developers|users) have discovered\b",
    r"\bthe landscape (?:of .+? )?is constantly evolving\b",
    r"\bin today'?s (?:fast-paced|rapidly evolving|digital|modern)\b",
    r"\bthe (?:journey|possibilities) (?:is|are) (?:rewarding|endless)\b",
    r"\bthe future of .+? is (?:here|local|bright|within reach)\b",
    r"\bunlock the (?:full )?potential of\b",
]

# LLM image placeholder artifacts — [IMAGE-1: description], [IMAGE: ...], etc.
IMAGE_PLACEHOLDER_PATTERNS = [
    r"\[IMAGE(?:-\d+)?:\s*[^\]]+\]",  # [IMAGE-1: description] or [IMAGE: description]
    r"\[FIGURE(?:-\d+)?:\s*[^\]]+\]",  # [FIGURE-1: description]
    r"\[DIAGRAM(?:-\d+)?:\s*[^\]]+\]",  # [DIAGRAM: description]
    r"\[CHART(?:-\d+)?:\s*[^\]]+\]",  # [CHART: description]
    r"\[SCREENSHOT(?:-\d+)?:\s*[^\]]+\]",  # [SCREENSHOT: description]
]

# Unresolved internal-link placeholders — [posts/abc-123] / [posts/{slug}] /
# [POST_ID: x] left in the body because the writer hinted at the template
# shape but the resolution step (internal_link_coherence + slug substitution)
# never converted them into real Markdown links. Showed up in the screenshot
# Matt flagged on 2026-05-12 (poindexter#489): a post with quality_score=90
# shipped with literal `[posts/<something>]` strings in the body that the
# reader sees as broken brackets.
#
# Critical rather than warning — these are visible to the reader, they make
# the site look broken, and they undercut the same "trust the byline" guard
# IMAGE_PLACEHOLDER_PATTERNS protects.
#
# Notes on the angle-bracket form: ``[posts/<uuid>]`` literally is caught
# upstream by ``_strip_html`` (which removes any ``<token>`` it sees), so
# only the bracket-without-angles shape can reach the regex. Still worth
# scanning the curly-brace and POST_ID variants — those survive cleanly.
#
# Negative lookahead ``(?!\()`` on the slug variant skips legit Markdown
# links of the shape ``[posts/foo](/posts/foo)`` — the bracket-only form
# is the bug.
PLACEHOLDER_MARKER_PATTERNS = [
    # Curly-brace template still unsubstituted.
    r"\[posts?/\{[^\]}]{1,80}\}\]",
    # Bare bracketed UUID / slug — `[posts/abc-123]` not followed by `(`.
    # Requires the slug/UUID to look "real" (≥3 chars, lowercase/digit/
    # hyphen/underscore) so genuine prose like "[posts/...]" doesn't fire.
    r"\[posts?/[a-z0-9_\-]{3,}\](?!\()",
    # Alternative shapes a model might emit when asked for "post-id tokens".
    r"\[POSTS?[_\s\-]?ID[:\-=]\s*[^\]]{1,80}\]",
]

# Orphaned attribution fragments (poindexter#532). The writer occasionally
# drops the SUBJECT of an attribution clause, leaving a sentence that opens
# with a bare third-person attribution verb — e.g. a paragraph that reads
# "... is enough. points out that this 'AI-enabled' approach misses the
# mark." (the "<Name>" before "points out that" was orphaned away). A
# grammatically complete sentence never opens with a *lowercase* verb, so
# a lowercase attribution verb sitting right on a sentence boundary is the
# tell. Case-sensitive via ``(?-i:...)`` because ``_check_patterns`` forces
# IGNORECASE — without the scoped flag "He Points Out That" prose would
# match too. Requiring a trailing "that" keeps the false-positive rate low
# (the orphaned fragment is always "<verb> that …"). Warning-level first
# per the issue; the GH-91 per-category threshold promotes to critical once
# several fire in one post.
_CITATION_VERB_ALT = (
    r"points\s+out|argues|notes|explains|adds|writes|claims|states"
    r"|observes|contends|maintains|recalls|continues|concludes|reports"
)
ORPHANED_ATTRIBUTION_PATTERNS = [
    # Orphan mid-paragraph: sentence terminator + space, then a lowercase
    # attribution verb. Fixed-width lookbehind (``[.!?]\s``) as Python's re
    # requires.
    r"(?<=[.!?]\s)(?-i:(?:" + _CITATION_VERB_ALT + r"))\s+that\b",
    # Orphan at the very start of a line (prior sentence ended the previous
    # paragraph). ``_check_patterns`` scans each line independently, so ``^``
    # anchors to the line start.
    r"^\s*(?-i:(?:" + _CITATION_VERB_ALT + r"))\s+that\b",
]

# Leaked internal path tokens (poindexter#532). Internal reference tokens
# like ``[memory/...]`` (the auto-memory store) or ``[brain/...]`` sometimes
# bleed into reader-facing prose. Sibling rule to PLACEHOLDER_MARKER_PATTERNS
# (which owns ``[posts/...]``). Anchored on a known set of internal-store
# namespaces inside square brackets followed by a slash so genuine prose
# brackets ("[logs everything]") don't fire. The negative lookahead
# ``(?!\()`` skips legitimate Markdown links of the shape ``[memory/x](url)``.
# Warning-level first per the issue.
INTERNAL_PATH_LEAK_PATTERNS = [
    r"\[(?:memory|brain|brain_knowledge|audit_log|app_settings|pipeline_versions|pipeline_tasks)/[^\]]{1,120}\](?!\()",
]

# Leaked reasoning / chat-template control tokens (#1283).
#
# A generation-boundary stripper (services/llm_providers/thinking_models.py
# ::strip_reasoning_artifacts) already removes these before persistence, but
# that layer can be bypassed (e.g. the ``two_pass`` writer calling a JSON-mode
# helper that skips the stripper). This rule is defence-in-depth: a draft
# whose prose body contains these tokens after stripping is almost certainly
# a whole-article reasoning-channel leak (quality_score can still reach 91 on
# such drafts because no earlier rule covered them).
#
# Fence-aware: the rule's check function strips triple-backtick and inline
# single-backtick spans BEFORE scanning so a technical tutorial that shows
# ``<|im_start|>`` as a literal example in a code block does not fire.
#
# Does NOT exclude dev_diary — reasoning tokens are never legitimate in any
# niche's published prose. Operators can scope via the DB applies_to_niches
# column if a future niche proves otherwise.
REASONING_TOKEN_LEAK_PATTERNS = [
    # Mangled / proper Harmony channel headers (GLM-4 / ZhipuAI format).
    r"<\|channel\|?>",
    r"<channel\|>",
    # Chat-turn header (broken Ollama gemma template form).
    r"<\|turn\|?>",
    # Generic message boundary.
    r"<\|message\|?>",
    # ChatML / Qwen / Mistral instruction markers.
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    # Open-source thinking blocks (DeepSeek, QwQ, etc.).
    r"<think>",
    r"</think>",
    # Harmony thinking-channel markers.
    r"<\|thinking\|>",
    r"<\|/thinking\|>",
]

# Leaked planning/outline scaffold (#1963).
#
# The writer model (notably gemma-4-31B) intermittently emits its planning
# notes / echoed prompt instructions as a bulleted preamble BEFORE the article
# (prod task 0f70f736, 2026-06-28: the body opened with "* Topic:", "* Key
# elements from sources:", "Avoid 'delve'", "Vary sentence length.", "No
# placeholder brackets.## The Current Ollama Model Stack"). The reasoning-token
# rule above only catches CONTROL-TOKEN leaks; a plain-Markdown planning
# scaffold passed every rail to awaiting_approval at quality 82.
#
# ``modules/content/atoms/content_normalize_draft.strip_leaked_planning_scaffold``
# removes the common (heading-anchored) case before QA runs; this rule is the
# QA-gate safety net for any residual scaffold (e.g. no heading anchored the
# article, so the strip left it in place). The call site requires >= 2 tells to
# fire — a single benign "vary sentence length" mention in a writing-tips post
# must never hard-reject — and blanks code spans first so a post that shows
# these rules as a code example is safe. Mirror of
# content_normalize_draft._SCAFFOLD_TELL_RE (strip there, detect here), the same
# split-of-duties the reasoning-leak case uses (strip in thinking_models, detect
# here).
LEAKED_PLANNING_SCAFFOLD_RE = re.compile(
    r"(?im)(?:"
    r"key\s+elements?\s+from\s+sources"
    r"|models?\s+used\s*/?\s*tested"
    r"|vary\s+sentence\s+length"
    r"|no\s+placeholder\s+brackets"
    r"|avoid\s+[\"'“]?delve"
    r"|concluding\s+paragraph"
    r"|\*\s*(?:voice|citations?|structure)\s*:\s*\*"
    r"|^[ \t]*[*+\-][ \t]+\*?(?:topic|voice|citations?|structure|tone|audience"
    r"|outline|writer\s+model|reviser|vision\s+qa|key\s+elements?)\b[ \t]*:"
    r")"
)

# Stock-LLM transition words used at sentence start (poindexter#232).
# Local LLMs (gemma3, glm, qwen) over-use these as paragraph openers,
# which both reads as AI-generated and chews into EEAT trust signals.
# The writer prompt already discourages them; this list is the
# deterministic catch when models ignore the rule. Patterns are
# matched only at sentence start so a mid-sentence "moreover" inside
# a quoted argument doesn't trip the rule. Case-sensitive on purpose
# — we're catching the sentence-opener form, not every casing.
BANNED_TRANSITION_OPENERS = (
    "Furthermore",
    "Moreover",
    "Additionally",
    "Notably",
    "In conclusion",
    "In summary",
    r"It is important to note that",
    r"It['’]s worth noting that",
    "As mentioned earlier",
)


# LLM-tell vocabulary — words and phrases that almost-exclusively signal
# machine-generated prose. From Matt's 2026-05-19 anti-LLM-tells list
# (``feedback_writing_anti_llm_tells`` memory). The writer prompts
# (``skills/content/blog-generation/SKILL.md``) already discourage these
# as negative constraints at generation time; this list is the
# deterministic QA-time enforcement floor that catches drafts the
# local LLMs produce despite the prompt.
#
# Match grammar is case-INSENSITIVE on purpose — "Delve into..." at the
# start of a paragraph is just as much an LLM tell as "...delve into..."
# mid-sentence. Word boundaries (``\b``) prevent false positives
# inside longer compound words (e.g. matching "delve" should not fire
# on "delver" or "manifested" — only the bare offending token).
#
# Some entries are multi-word phrases ("at its core") and use
# ``\s+`` between words so any whitespace count matches. The
# constructed alternation regex compiles once at module load (see
# ``_LLM_TELL_RE`` below).
LLM_TELL_BUZZWORDS: tuple[str, ...] = (
    r"delve",
    r"delves",
    r"delving",
    r"delved",
    r"testament",
    r"tapestry",
    r"multifaceted",
    r"at\s+its\s+core",
    r"at\s+the\s+heart\s+of",
)

# Pre-compile the alternation regex so each scan is cheap. The
# ``(?:...)`` grouping is non-capturing so ``finditer`` returns each
# whole match without spurious group captures.
_LLM_TELL_RE = re.compile(
    r"\b(?:" + "|".join(LLM_TELL_BUZZWORDS) + r")\b",
    re.IGNORECASE,
)


@dataclass
class ValidationIssue:
    """A single quality issue found in the content."""
    severity: str  # "critical", "warning"
    category: str  # "fake_person", "fake_stat", "company_claim", "fake_quote"
    description: str
    matched_text: str
    line_number: int = 0


@dataclass
class ValidationResult:
    """Result of content validation."""
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    score_penalty: int = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")


def _strip_html(text: str) -> str:
    """Remove HTML tags for pattern matching."""
    return re.sub(r"<[^>]+>", "", text)


# Matches triple-backtick/tilde fenced blocks AND inline code spans
# (double-backtick ``...`` before single-backtick `...`).
# Used by the reasoning-token-leak rule to blank out code before scanning so
# that ``<|im_start|>`` shown as an example in a tutorial code block does not
# false-positive. The same logic lives in thinking_models._CODE_SPAN_RE; kept
# here to avoid a cross-module import from a validator that must stay light.
#
# Order matters: triple-backtick fences must be tried before the shorter
# alternatives; double-backtick inline spans (`` ``...`` ``) before single-
# backtick spans (`` `...` ``) so that a double-backtick construct like
# `` ``<|im_start|>`` `` is consumed as one unit rather than being split
# into two single-backtick matches with `<|im_start|>` exposed in between.
_CODE_SPAN_RE_FOR_VALIDATOR = re.compile(
    r"```.*?```"       # triple-backtick fenced block
    r"|~~~.*?~~~"      # triple-tilde fenced block
    r"|``[^`]+``"      # double-backtick inline span (CommonMark ``...``)
    r"|`[^`\n]+`",     # single-backtick inline span
    re.DOTALL,
)


def _strip_code_spans(text: str) -> str:
    """Replace code fences and inline code spans with whitespace of equal length.

    Preserves byte offsets (newlines stay) so line numbers in issues remain
    accurate. The replacement uses a space-per-char approach (not just ``""``)
    so a multi-line fenced block does not collapse adjacent lines together.
    """
    def _blank(m: re.Match) -> str:
        # Preserve newline positions; replace non-newline chars with spaces.
        return re.sub(r"[^\n]", " ", m.group(0))

    return _CODE_SPAN_RE_FOR_VALIDATOR.sub(_blank, text)


# Distinctive prompt-scaffolding / instruction-echo phrases that never occur in
# finished prose. Their presence means an LLM producer — the canonical writer
# (content.generate_draft), the qa.rewrite reviser, or narrate_bundle — leaked
# its instructions / persona / planning outline into the body instead of the
# article. The 2026-06-29 canonical incident: the operator console previews the
# raw pipeline_versions draft, so the scaffolding surfaced in the approval queue
# even though the published site was clean (tasks 06715fb0 writer, ba4d627a
# reviser). Mirrors narrate_bundle._PROMPT_LEAK_MARKERS (the dev_diary path,
# which has no QA rails) and extends it with the canonical writer/reviser shapes.
# Lowercased for case-insensitive substring matching.
_PROMPT_LEAK_MARKERS: tuple[str, ...] = (
    # dev_diary narrate_bundle scaffolding
    "lead with stakes",
    "thread bundle facts",
    "thread the bundle facts",
    "close with reflection",
    "drafting paragraph",
    "first-person plural?",
    "grounding check",
    "operator_notes",
    "voice textures",
    "no surrounding json",
    # canonical writer + qa.rewrite reviser instruction / planning echoes
    "revise a draft article based on",
    "return complete markdown body only",
    "return the complete revised article",
    "no placeholders like [",
    "[internal snippet",
    "use only provided snippets",
)


def detect_prompt_leak(text: str) -> list[str]:
    """Return prompt-scaffolding / instruction-echo markers present in ``text``.

    A non-empty result means an LLM producer leaked its instructions, persona,
    or planning outline into the body instead of emitting finished prose. The
    markers are internal prompt-template vocabulary that does not appear in real
    articles, so false positives are unlikely. Lowercased substring match.

    Pair with ``_strip_code_spans`` at the call site so a prompt-engineering
    article that legitimately *quotes* an instruction inside a code fence is not
    flagged.
    """
    if not text:
        return []
    low = text.lower()
    return [m for m in _PROMPT_LEAK_MARKERS if m in low]


# Instruction SHAPES for paraphrased echo. The exact-marker list above misses
# a producer that restates its instructions in its own words — the 2026-07-01
# regression (tasks e46b449c / 9921678f): gemma opened the article with
# "Expand a draft from ~416 words (actually the provided \"Draft\" is more
# like 250-300 words...) to closer to 651 words. Genuine added substance...",
# no exact marker matched, and the drafts sailed through qa.programmatic to
# the approval queue at 82-85. These regexes match the STRUCTURE of the
# expand/revise instructions rather than their wording. Each alone is weaker
# than an exact marker (an AI-writing article could use one incidentally), so
# the rule requires >=2 DISTINCT shapes before flagging — two prompt
# imperatives co-occurring in finished prose is not a thing.
_PROMPT_ECHO_PARAPHRASE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # {0,3} adjective tolerance: task ecaf0c01 opened "Expand a 323-word
    # draft to approximately 1057 words."
    ("expand-draft", re.compile(
        r"\bexpand (?:a|an|the|this) (?:[\w~-]+ ){0,3}draft\b", re.IGNORECASE,
    )),
    ("word-target", re.compile(
        r"\bto (?:approximately|closer to|about|around) ~?\d[\d,]* words\b",
        re.IGNORECASE,
    )),
    ("no-padding", re.compile(
        r"\b(?:do not|don'?t) pad\b|\bno padding\b", re.IGNORECASE,
    )),
    ("no-preamble", re.compile(r"\bno preamble\b", re.IGNORECASE)),
    ("preserve-facts", re.compile(
        r"\bpreserve (?:all|every|existing) (?:facts|headings|links)\b",
        re.IGNORECASE,
    )),
    ("genuine-substance", re.compile(
        r"\bgenuine (?:added )?substance\b", re.IGNORECASE,
    )),
    ("end-complete-sentence", re.compile(
        r"\bend(?:ing)? on a complete sentence\b", re.IGNORECASE,
    )),
    ("prompt-meta", re.compile(
        r"\bthe prompt (?:asks|says|provided)\b", re.IGNORECASE,
    )),
    ("word-count-check", re.compile(r"\bcheck word count\b", re.IGNORECASE)),
)


def detect_prompt_echo_paraphrase(text: str) -> list[str]:
    """Return the names of paraphrased instruction SHAPES present in ``text``.

    Complements :func:`detect_prompt_leak` (exact phrases) with structural
    matches that survive the model rewording its instructions. The caller
    flags only at >=2 distinct shapes — a single hit can occur incidentally
    in real prose about AI writing, two co-occurring cannot. Pair with
    ``_strip_code_spans`` for the same code-fence exemption.
    """
    if not text:
        return []
    return [name for name, rx in _PROMPT_ECHO_PARAPHRASE_PATTERNS if rx.search(text)]


# Planning-dump preamble detection. The residual gap after the exact markers
# and the paraphrase shapes above: a draft that OPENS with the writer's
# planning/outline dump but contains NO instruction lines at all. The
# 2026-07-01 task e46b449c persisted exactly that — "*   Topic: Ellipses...",
# "*   Source Material provided:", an inventory of source bullets, then the
# article fused mid-line onto the last bullet — with 0 exact markers, 0
# paraphrase shapes, and only 1 leaked_planning_scaffold tell (below its >=2
# bar), so every leak rule stayed silent and it reached awaiting_approval at
# quality 85 (the LLM critic anchored on the title and passed it too).
#
# Detection here is STRUCTURAL, not vocabulary-first: a finished article never
# opens with a wall of outline bullets, so the signature is bullet dominance
# of the pre-heading OPENING plus at least two planning-vocabulary families
# inside that opening. Scoping the vocabulary to the opening bullet block is
# what keeps prose mentions safe — an article ABOUT writing can discuss "word
# count" mid-paragraph without ever matching, because vocabulary alone never
# fires without the structure.
_PLANNING_BULLET_LINE_RE = re.compile(r"^[ \t]*(?:[*+\-]|\d+[.)])[ \t]+\S")

# First Markdown heading, whether line-anchored or glued mid-line onto the
# preceding text ("...Plain language.# Addressing Hallucinations") — the
# writer fuses the article start onto its last planning bullet. Mirror of
# content_normalize_draft._FIRST_HEADING_RE (strip there, detect here — the
# same duplication precedent as _CODE_SPAN_RE_FOR_VALIDATOR, because the
# validator must stay light and not import from atoms).
_FIRST_HEADING_RE_FOR_VALIDATOR = re.compile(
    r"(?:^|(?<=[^\n#]))(#{1,6}[ \t]+\S)", re.MULTILINE,
)

# Vocabulary FAMILIES that mark an opening bullet block as writer planning.
# Each family counts once; the detector needs >=2 distinct families so a
# single incidental phrase can never fire the rule on its own.
_PLANNING_DUMP_VOCAB: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "*   Topic: ..." / "* Outline:" — planning bullet labels.
    ("planning-bullet-label", re.compile(
        r"^[ \t]*[*+\-][ \t]+\*{0,2}(?:topic|outline|audience|tone|voice"
        r"|angle|structure)\b[ \t]*:",
        re.IGNORECASE | re.MULTILINE,
    )),
    # "*   *Introduction:* ..." — section-by-section plan labels (the
    # emphasised-label bullet shape task ecaf0c01 used for its outline).
    ("section-plan-label", re.compile(
        r"^[ \t]*[*+\-][ \t]+\*(?:intro(?:duction)?|conclusion|opening"
        r"|closing|transition[^:*\n]{0,40})\s*:?\*",
        re.IGNORECASE | re.MULTILINE,
    )),
    # Research-material inventory vocabulary.
    ("source-inventory", re.compile(
        r"\bsource\s+material\b|\bsources?\s+provided\b"
        r"|\bprovided\s+sources?\b|\binternal\s+snippets?\b"
        r"|\bexternal\s+sources?\b|\bbackground\s+context\b",
        re.IGNORECASE,
    )),
    # Notes-to-self drafting voice ("I should provide worked examples...").
    ("self-instruction", re.compile(
        r"\bI\s+(?:can|should|need\s+to|must|will)\s+(?:add|provide|explain"
        r"|expand|ensure|mention|discuss|include|elaborate)\b",
        re.IGNORECASE,
    )),
    # Format/meta constraints echoed as checklist items.
    ("format-meta", re.compile(
        r"\bmarkdown\s+format\b|\bword\s+count\b|\bfirst\s+person\b"
        r"|\bthird\s+person\b|\bshort\s+paragraphs\b|\bplain\s+language\b"
        r"|\bcomplete\s+sentence\b|\bno\s+(?:preamble|padding|filler|footnotes)\b",
        re.IGNORECASE,
    )),
    # Citation-mechanics instructions.
    ("citation-meta", re.compile(
        r"\bensure\s+(?:all\s+|inline\s+)?links?\b|\battribute\s+facts\b"
        r"|\binline\s+links?\s+use\b|\bcite\s+if\s+relevant\b"
        r"|\blinks?\s+(?:are\s+)?preserved\b",
        re.IGNORECASE,
    )),
    # Source-triage notes ("(Irrelevant source: ...)", "Discard irrelevant").
    ("source-triage", re.compile(
        r"\bdiscard\s+irrelevant\b|\birrelevant\s+(?:source|data)\b",
        re.IGNORECASE,
    )),
)

_PLANNING_DUMP_MIN_BULLETS = 6
_PLANNING_DUMP_MIN_BULLET_SHARE = 0.6
_PLANNING_DUMP_MAX_OPENING_LINES = 50
_PLANNING_DUMP_MIN_VOCAB = 2


def detect_planning_dump_preamble(text: str) -> list[str]:
    """Return evidence that ``text`` OPENS with a planning/outline dump.

    Empty list means clean. Evidence strings are ``opening_bullets:N/M``
    (bullet lines over non-blank lines in the pre-heading opening) plus one
    ``vocab:<family>`` per matched planning-vocabulary family. Fires only
    when ALL of: >= 6 bullet lines, bullets >= 60% of the opening's non-blank
    lines, and >= 2 distinct vocabulary families — structure without planning
    vocabulary (a legitimate opening list) and vocabulary without structure
    (an article about writing) both stay below the bar.

    Pair with ``_strip_code_spans`` at the call site so a post that QUOTES a
    planning dump inside a code fence is not flagged.
    """
    if not text or not text.strip():
        return []
    body = text
    # Skip a single leading heading line (a generated H1 title) so a dump
    # placed right below the title is still scanned. An article whose intro
    # prose follows the title is untouched — prose fails the bullet bar.
    stripped = body.lstrip()
    if stripped.startswith("#"):
        first_line, _, rest = stripped.partition("\n")
        if re.match(r"#{1,6}[ \t]+\S", first_line):
            body = rest
    heading = _FIRST_HEADING_RE_FOR_VALIDATOR.search(body)
    opening = body[: heading.start(1)] if heading else body
    lines = [ln for ln in opening.split("\n") if ln.strip()]
    if not lines:
        return []
    lines = lines[:_PLANNING_DUMP_MAX_OPENING_LINES]
    bullets = sum(1 for ln in lines if _PLANNING_BULLET_LINE_RE.match(ln))
    if bullets < _PLANNING_DUMP_MIN_BULLETS:
        return []
    if bullets / len(lines) < _PLANNING_DUMP_MIN_BULLET_SHARE:
        return []
    vocab = [name for name, rx in _PLANNING_DUMP_VOCAB if rx.search(opening)]
    if len(vocab) < _PLANNING_DUMP_MIN_VOCAB:
        return []
    return [f"opening_bullets:{bullets}/{len(lines)}"] + [
        f"vocab:{name}" for name in vocab
    ]


def _check_patterns(
    text: str,
    patterns: list,
    severity: str,
    category: str,
    description_template: str,
) -> list[ValidationIssue]:
    """Run regex patterns against text and return issues."""
    issues = []
    clean_text = _strip_html(text)
    lines = clean_text.split("\n")

    for pattern in patterns:
        for i, line in enumerate(lines, 1):
            for match in re.finditer(pattern, line, re.IGNORECASE):
                matched = match.group(0)[:100]
                issues.append(ValidationIssue(
                    severity=severity,
                    category=category,
                    description=description_template.format(matched=matched),
                    matched_text=matched,
                    line_number=i,
                ))
    return issues


# ---------------------------------------------------------------------------
# Hallucinated library / API reference detection (GH-83 part b)
# ---------------------------------------------------------------------------
#
# Real cases caught manually on 2026-04-21:
#   * `schedule_callback(event)` described as a "central asyncio function" —
#     not a real asyncio API (real: loop.call_soon / call_later / call_at).
#   * "explore CadQuery to see how asyncio is used" in an ai-ml post —
#     CadQuery is a 3D CAD library, topically orthogonal to asyncio.
#
# Strategy (low false positives by design):
#   1. Extract only identifiers that LOOK like library/API references:
#      backtick-quoted (`foo.bar`, `baz(args)`) or dotted CamelCase calls.
#   2. Compare against a known-good list: Python 3.12 stdlib + top-500
#      PyPI packages + common Ollama models. All three live as data files
#      under brain/hallucination-check/ — update in one place, no redeploy.
#   3. For library names in the post that ARE recognized, optionally check
#      topic coherence against brain/hallucination-check/library-topics.json.
#
# Files are loaded lazily + cached in-module; the caches are ephemeral
# (no TTL) because the lists are static data, not DB state.
from pathlib import Path as _Path


def _find_hc_dir() -> _Path:
    """Locate ``brain/hallucination-check/`` regardless of host vs container layout.

    On the host the directory lives at ``<repo-root>/brain/hallucination-check``.
    In the worker container ``brain/`` is bind-mounted at
    ``/opt/poindexter/brain`` (see docker-compose.local.yml), not as a
    descendant of this file's path — so ``parents[3]`` from /app/services
    overshoots the filesystem root and raises IndexError.

    Walk every ancestor of ``__file__`` looking for the directory, then
    fall back to the container mount path. If neither exists the lazy
    file loaders below will surface a clearer error at first use.
    """
    here = _Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "brain" / "hallucination-check"
        if candidate.is_dir():
            return candidate
    container_hc = _Path("/opt/poindexter/brain/hallucination-check")
    if container_hc.is_dir():
        return container_hc
    # Best-effort guess for diagnostic output; file reads will fail
    # explicitly if the directory really is missing.
    return here.parent.parent.parent.parent / "brain" / "hallucination-check"


_HC_DIR = _find_hc_dir()

_stdlib_names_cache: set[str] | None = None
_pypi_names_cache: set[str] | None = None
_ollama_names_cache: set[str] | None = None
_library_topics_cache: dict[str, list[str]] | None = None


def _normalize_pkg(name: str) -> str:
    """Normalize PyPI-style names: lowercase, dashes == underscores."""
    return name.strip().lower().replace("_", "-")


def _load_known_list(filename: str) -> set[str]:
    """Load a simple newline-delimited list file from brain/hallucination-check.

    Ignores blank lines and `#` comments. Normalizes each entry.
    Returns an empty set if the file is missing — missing data should
    degrade to "don't flag anything" rather than crash the pipeline.
    """
    path = _HC_DIR / filename
    if not path.is_file():
        logger.warning("[VALIDATOR] hallucination-check list missing: %s", path)
        return set()
    names: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                names.add(_normalize_pkg(line))
    except Exception as exc:  # pragma: no cover — best-effort file read
        logger.warning("[VALIDATOR] failed to load %s: %s", path, exc)
    return names


def _get_stdlib_names() -> set[str]:
    global _stdlib_names_cache
    if _stdlib_names_cache is None:
        _stdlib_names_cache = _load_known_list("stdlib-python-312.txt")
    return _stdlib_names_cache


def _get_pypi_names() -> set[str]:
    global _pypi_names_cache
    if _pypi_names_cache is None:
        _pypi_names_cache = _load_known_list("pypi-top-500.txt")
    return _pypi_names_cache


def _get_ollama_names() -> set[str]:
    global _ollama_names_cache
    if _ollama_names_cache is None:
        _ollama_names_cache = _load_known_list("ollama-models.txt")
    return _ollama_names_cache


def _get_library_topics() -> dict[str, list[str]]:
    """Load library -> expected-topics map. Empty dict on failure."""
    global _library_topics_cache
    if _library_topics_cache is not None:
        return _library_topics_cache
    import json
    path = _HC_DIR / "library-topics.json"
    result: dict[str, list[str]] = {}
    if not path.is_file():
        logger.warning("[VALIDATOR] library-topics.json missing: %s", path)
        _library_topics_cache = result
        return result
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        for k, v in data.items():
            if k.startswith("_") or not isinstance(v, list):
                continue
            result[_normalize_pkg(k)] = [str(t).strip().lower() for t in v if str(t).strip()]
    except Exception as exc:  # pragma: no cover
        logger.warning("[VALIDATOR] failed to parse %s: %s", path, exc)
    _library_topics_cache = result
    return result


# Reference-shaped identifiers. Only flag tokens that LOOK like an API call
# or a library reference; bare prose capitalized words are out of scope.
#   (a) Backtick-quoted identifier with a call or attribute access:
#       `foo()`, `foo.bar`, `Foo.bar()`, `foo.bar.baz(args)`
#   (b) Backtick-quoted module/library-looking bare token (no special chars):
#       `asyncio`, `cadquery` — included because real cases use plain
#       backticks around the lib name.
#   (c) Un-backticked "Package.method(...)" in narrative prose — e.g.
#       "explore CadQuery to see..." (bare capitalized product name
#       followed by one of "module" / "library" / "package" / "framework").
# Group 1 of each regex is the raw identifier (may be dotted).
_HALLUCINATED_REF_PATTERNS = [
    # Backtick-wrapped dotted call or attribute: `foo.bar`, `foo.bar()`
    re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)(?:\(\s*[^`]*\))?`"),
    # Backtick-wrapped bare function call: `schedule_callback(event)`
    re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})\(\s*[^`]*\)`"),
    # Backtick-wrapped bare module name (lowercase, includes - or _): `asyncio`
    re.compile(r"`([a-z][a-z0-9_\-]{2,})`"),
    # Narrative prose: "explore CadQuery to see..." / "the Foo library..."
    # Requires the word to be capitalized AND be followed by an English
    # hint that it's a code artifact. This narrows false positives from
    # prose proper nouns (which rarely carry those hints).
    re.compile(
        r"\b([A-Z][A-Za-z0-9]{2,}(?:\.[A-Za-z][A-Za-z0-9]*)?)\s+"
        r"(?:library|package|module|framework|SDK|API)\b"
    ),
    re.compile(
        r"(?:explor(?:e|ing|ed)|tr(?:y|ying|ied)|us(?:e|ing|ed)"
        r"|consider(?:ing)?|adopt(?:ing|ed)?|install(?:ing|ed)?"
        r"|import(?:ing|ed)?|leverag(?:e|ing|ed)|check(?:ing)?\s+out)"
        r"\s+`?([A-Z][A-Za-z0-9]{2,})`?"
    ),
]

# Identifiers we NEVER flag (pipeline internals, super-common Python builtins,
# common variable names that happen to look dotted, HTTP verbs, test keywords).
# Normalized to lowercase + dashes for matching against _normalize_pkg().
#
# Categorized — the 2026-05-15 audit pulled a sample of ~60 flagged
# terms from ``pipeline_versions.qa_feedback`` and found ~90% were
# legitimate references the Python-centric original whitelist missed.
# Major categories added that day: AI/ML brands, gaming + hardware
# (Matt's brand niches per ``feedback_brand_niches``), security orgs,
# common acronyms, dev tools, common English nouns the writer uses
# narratively. Operator can extend at runtime via
# ``app_settings.hallucination_whitelist_additions`` — see
# ``_load_hallucination_whitelist_additions_sync`` below.
_HALLUCINATION_WHITELIST_BASE = {
    # Ambient project-local / generic names we don't want pattern-matching on
    "glad-labs", "poindexter", "ollama",
    # Python generic builtins and dunders that may show up in backticks
    "true", "false", "none", "self", "cls", "args", "kwargs",
    "print", "len", "range", "list", "dict", "set", "tuple", "str", "int",
    "float", "bool", "bytes", "type", "object", "super", "all", "any",
    "min", "max", "sum", "abs", "open", "map", "filter", "zip", "enumerate",
    "sorted", "reversed", "iter", "next", "input", "hash", "id", "repr",
    # HTTP verbs often shown in backticks in API writeups
    "get", "post", "put", "delete", "patch", "head", "options",
    # Test-framework keywords and common method stubs
    "assert", "yield", "async", "await", "lambda", "return",
    # Extremely common instance/variable names — "loop.call_soon" has root
    # "loop", which is an asyncio event loop instance, not a library. The
    # method name downstream (call_soon) is what matters; flagging "loop"
    # as a library generates high-noise false positives on any asyncio post.
    "loop", "app", "db", "client", "session", "config", "ctx", "context",
    "response", "request", "req", "res", "conn", "connection", "cursor",
    "user", "users", "data", "item", "items", "result", "results",
    "obj", "value", "values", "key", "keys", "event", "events", "error",
    "logger", "log", "logs", "state", "store", "router", "handler",
    "service", "services", "manager", "factory", "builder", "engine",
    "queue", "cache", "pool", "worker", "task", "tasks", "job", "jobs",
    "message", "messages", "payload", "header", "headers", "body", "field",
    "model", "models", "schema", "schemas", "view", "template",
    # Common English / content-writing nouns. A content pipeline writes
    # ABOUT writing, so these appear constantly in backticks (`source`,
    # `audience`, `draft`). The bare-module pattern (`` `[a-z]{3,}` ``)
    # was flagging them as "hallucinated library/API reference" — the
    # writer's citation placeholders (`[source]`) hard-failed the
    # programmatic gate 0/100 on this alone (2026-06-09 false positive).
    "source", "sources", "target", "targets", "content", "contents",
    "audience", "audiences", "topic", "topics", "draft", "drafts",
    "article", "articles", "posts", "claim", "claims",
    "fact", "facts", "citation", "citations", "quote", "quotes",
    "summary", "section", "sections", "paragraph", "heading", "headings",
    "title", "titles", "reader", "readers", "author", "authors",
    "example", "examples", "reference", "references", "snippet", "snippets",
    "link", "links", "note", "notes", "point", "points", "detail",
    "details", "idea", "ideas", "intro", "outro", "word", "words",
    # Python style shorthands often used in examples
    "np", "pd", "plt", "tf", "torch",
    # ---- AI/ML brands and well-known tools (Matt's brand_niches: ai/ml)
    "chatgpt", "claude", "claude-code", "gemini", "copilot", "llama",
    "mistral", "gemma", "qwen", "deepseek", "phi", "phi3", "anthropic",
    "openai", "deepmind", "huggingface", "langchain", "langgraph",
    "langfuse", "langsmith", "litellm", "deepeval", "ragas", "guardrails",
    "guardrails-ai", "llamaindex", "pgvector", "weaviate", "pinecone",
    "chroma", "qdrant", "milvus", "redis", "elasticsearch",
    "lora", "rag", "llm", "llms", "agi", "asi", "rlhf", "embedding",
    "embeddings", "transformer", "transformers", "bge-m3", "bge",
    "sentence-transformers", "hfembedding", "basemodel", "contextgem",
    "localai", "coderabbit", "codestral", "groq", "perplexity",
    # ---- Numeric precision + quantization formats (AI/ML brand niche).
    # Data-type / quantization vocabulary (model weights, KV cache), NOT
    # libraries — but the bare-module (`` `int8` ``) and verb-prefixed
    # (``use FP16``) patterns flag them identically. A 98/100 quantization
    # post hard-rejected on ``FP16`` alone (prod task 2d81e084, 2026-06-21)
    # before these shipped in the base list. Full family so a future post
    # using ``fp8`` / ``exl2`` doesn't re-trip the same rule.
    "fp4", "fp8", "fp16", "fp32", "fp64", "bf16", "tf32",
    "int2", "int4", "int8", "int16", "uint8", "e4m3", "e5m2",
    "gguf", "ggml", "gptq", "awq", "exl2", "qat", "ptq",
    "qlora", "hqq", "kv-cache",
    # ---- Gaming + hardware (brand_niches: gaming + hardware)
    "steam", "vulkan", "opengl", "directx", "opencl", "metal",
    "xbox", "playstation", "ps4", "ps5", "switch", "nintendo", "sega",
    "valve", "epic", "ubisoft", "ea", "activision",
    "nvidia", "amd", "intel", "apple", "microsoft",
    "asus", "msi", "gigabyte", "asrock", "evga", "corsair",
    "lenovo", "dell", "hp", "framework", "razer", "logitech",
    "keychron", "glorious", "ducky", "wooting",
    "ryzen", "threadripper", "epyc", "xeon", "core", "snapdragon",
    "geforce", "rtx", "gtx", "radeon", "rdna", "ada", "blackwell",
    "nvme", "ssd", "hdd", "ddr", "ddr4", "ddr5", "gddr", "gddr6", "hbm",
    "cpu", "gpu", "npu", "tpu", "fpga", "asic", "vram", "ram", "rom",
    "psu", "pcb", "pcie", "usb", "thunderbolt", "hdmi", "displayport",
    # ---- Web/cloud platforms
    "aws", "gcp", "azure", "vercel", "cloudflare", "fastly", "netlify",
    "render", "fly", "railway", "heroku", "digitalocean", "linode",
    "supabase", "neon", "planetscale",
    "github", "gitlab", "gitea", "forgejo", "bitbucket",
    # ---- Browsers + runtimes
    "chrome", "firefox", "safari", "edge", "brave", "arc", "opera",
    "chromium", "webkit", "blink", "v8", "node", "nodejs", "deno", "bun",
    # ---- Languages + frameworks
    "javascript", "typescript", "python", "rust", "go", "golang",
    "java", "kotlin", "swift", "ruby", "php", "elixir", "haskell",
    "scala", "clojure", "dart", "lua", "perl",
    "react", "vue", "svelte", "angular", "nextjs", "next.js", "nuxt",
    "remix", "astro", "solidjs", "qwik",
    "express", "fastify", "fastapi", "django", "flask", "rails",
    "spring", "laravel", "phoenix", "actix", "rocket", "axum",
    "htmx", "tailwind", "tailwindcss", "bootstrap", "shadcn",
    # ---- Databases + infra
    "postgres", "postgresql", "mysql", "sqlite", "mariadb", "mongodb",
    "cassandra", "scylla", "clickhouse", "duckdb", "snowflake", "bigquery",
    "redshift", "kafka", "rabbitmq", "nats",
    "docker", "kubernetes", "k8s", "helm", "podman", "containerd",
    "terraform", "pulumi", "ansible", "puppet", "chef",
    "prometheus", "grafana", "loki", "tempo", "pyroscope",
    "datadog", "honeycomb", "sentry", "glitchtip",
    "nginx", "caddy", "haproxy", "traefik", "envoy",
    # ---- Dev tools / utilities (commonly shown in backticks)
    "git", "npm", "pip", "uv", "poetry", "pipx", "cargo", "rustup",
    "yarn", "pnpm", "bundler", "maven", "gradle",
    "kubectl", "helmfile", "skaffold", "kustomize",
    "make", "cmake", "ninja", "bazel", "buck",
    "vim", "nvim", "emacs", "vscode", "zed", "sublime",
    "tmux", "zsh", "bash", "fish", "powershell",
    "ssh", "scp", "rsync", "curl", "wget", "jq", "yq",
    "ffmpeg", "imagemagick", "graphviz", "pandoc",
    "fail2ban", "ufw", "iptables", "nftables", "wireguard", "tailscale",
    "openssl", "letsencrypt", "certbot",
    "pip-audit", "cargo-audit", "cargo-hack", "kicad-cli", "coreutils",
    # ---- Security / governance / standards orgs
    "owasp", "nist", "cisa", "iso", "ieee", "ietf", "w3c", "rfc",
    "ccpa", "gdpr", "hipaa", "soc2", "pci-dss", "pci",
    "cve", "cvss", "mitre", "att&ck",
    # ---- Common acronyms that get flagged
    "iac", "saas", "paas", "iaas", "faas", "baas",
    "rest", "grpc", "graphql", "ws", "websocket", "wss", "tcp", "udp",
    "tls", "ssl", "https", "http", "mqtt", "smtp", "imap",
    "json", "xml", "yaml", "toml", "csv", "tsv", "html", "css",
    "jwt", "jwts", "oauth", "oauth2", "oidc", "saml", "ldap", "rbac", "abac",
    "ide", "ides", "cli", "tui", "gui", "ssr", "csr", "spa", "pwa",
    "etl", "elt", "olap", "oltp", "dag", "crud",
    "ci", "cd", "ci/cd", "cicd", "vcs", "mvp", "kpi", "okr",
    "ml", "ai", "nlp", "cv", "ar", "vr", "xr",
    # ---- Common English nouns the writer uses narratively (NOT libraries)
    "blog", "photo", "reports", "impact", "level", "library",
    "action", "bridge", "length", "system", "text",
    "main.py", "requirements.txt", "read_file", "content_status",
    # ---- File-extensions + common script names
    "py", "js", "ts", "tsx", "jsx", "rs", "rb", "sh", "ps1",
    # ---- Poindexter-internal pipeline vocabulary (function / graph-node /
    #      table names that recur in dev_diary posts ABOUT the pipeline). NOT
    #      external libraries — none collide with a real PyPI package — so
    #      whitelisting them cannot mask a genuine library fabrication; it only
    #      stops a post discussing the pipeline's own internals from
    #      hard-failing the programmatic gate. Stored dash-normalized because
    #      _normalize_pkg maps "_" -> "-" before the lookup (so `generate_content`
    #      -> "generate-content"). #qa-self-heal §7 — the generate_content
    #      false-positive surfaced 2026-06-22. The dotted atom forms
    #      (`qa.aggregate`, `content.generate_draft`) already resolve to a
    #      short (<3, skipped) or whitelisted ("content") root, so only the
    #      underscore/bare forms need listing here.
    "generate-content", "qa-aggregate", "qa-rewrite", "qa-programmatic",
    "auto-publish-gate", "multi-model-qa", "content-validator",
    "template-runner", "pipeline-architect", "canonical-blog",
    "pipeline-tasks", "pipeline-versions", "graph-def", "atom-runs",
    "qa-gates", "app-settings",
}


# Mutable view of the whitelist — base set + DB-loaded additions. Code
# reads through ``_get_hallucination_whitelist()`` which merges base +
# DB. Direct reads of ``_HALLUCINATION_WHITELIST`` are also supported
# for back-compat (now an alias of the base set).
_HALLUCINATION_WHITELIST = _HALLUCINATION_WHITELIST_BASE


_whitelist_additions_cache: set[str] = set()
_whitelist_additions_ts: float = 0.0
_WHITELIST_ADDITIONS_TTL = 300  # 5-minute cache, mirrors fact_overrides


def _load_hallucination_whitelist_additions_sync() -> set[str]:
    """Load operator-supplied whitelist additions from
    ``app_settings.hallucination_whitelist_additions`` (comma-separated).
    Sync, cached 5 minutes. Mirrors ``_load_fact_overrides_sync`` —
    same async-from-sync bridge, same fallback-to-cache on DB error.

    Operator workflow: insert/update the app_settings row to add new
    terms when the validator flags something that's legitimately a
    real name. No code change, no restart required (TTL <= 5 min)."""
    global _whitelist_additions_cache, _whitelist_additions_ts
    now = _time.time()
    if _whitelist_additions_cache and (now - _whitelist_additions_ts) < _WHITELIST_ADDITIONS_TTL:
        return _whitelist_additions_cache

    try:
        import asyncio
        import sys
        from pathlib import Path
        try:
            _proj = Path(__file__).resolve()
            for _p in _proj.parents:
                if (_p / "brain" / "bootstrap.py").is_file():
                    if str(_p) not in sys.path:
                        sys.path.insert(0, str(_p))
                    break
            from brain.bootstrap import resolve_database_url
            db_url = resolve_database_url() or ""
        except Exception:
            db_url = ""
        if not db_url:
            return _whitelist_additions_cache

        async def _fetch():
            import asyncpg
            conn = await asyncpg.connect(db_url, timeout=5)
            try:
                row = await conn.fetchrow(
                    "SELECT value FROM app_settings "
                    "WHERE key = 'hallucination_whitelist_additions'",
                )
                if row is None or not row["value"]:
                    return set()
                parts = [p.strip().lower() for p in str(row["value"]).split(",")]
                return {p for p in parts if p}
            finally:
                await conn.close()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, _fetch())
                result = future.result(timeout=5)
        else:
            result = asyncio.run(_fetch())

        _whitelist_additions_cache = result
        _whitelist_additions_ts = now
        logger.debug(
            "[VALIDATOR] Loaded %d hallucination whitelist additions from DB",
            len(result),
        )
    except Exception as e:
        logger.warning(
            "[VALIDATOR] hallucination whitelist additions DB load failed "
            "(using cache): %s", e,
        )
    return _whitelist_additions_cache


def _get_hallucination_whitelist() -> set[str]:
    """Effective whitelist = static base + DB-loaded additions. Use this
    at call sites instead of the raw base set."""
    return _HALLUCINATION_WHITELIST_BASE | _load_hallucination_whitelist_additions_sync()


# Source / config file extensions. A backtick token ending in one of these
# (``api_token_auth.py``, ``Component.tsx``, ``vercel.json``) is the author
# referencing a FILE in the repo — never an external library to be checked
# against PyPI/stdlib/Ollama. See ``_looks_like_file_or_path``.
_SOURCE_FILE_EXTENSIONS = frozenset({
    # languages
    "py", "pyi", "ts", "tsx", "js", "jsx", "mjs", "cjs",
    "rs", "go", "rb", "java", "kt", "kts", "swift", "php",
    "c", "h", "cpp", "cc", "hpp", "cs", "scala", "clj", "ex", "exs",
    "lua", "dart", "vue", "svelte",
    # shell / build
    "sh", "bash", "zsh", "ps1", "bat",
    # markup / style / data / config
    "html", "css", "scss", "sass", "sql",
    "json", "toml", "yaml", "yml", "ini", "cfg", "conf", "env",
    "md", "rst", "txt", "lock", "xml", "csv",
})


def _looks_like_file_or_path(raw: str) -> bool:
    """True when a backtick token is a FILE or repo PATH, not a library.

    Internal project files (``api_token_auth.py``, ``Component.tsx``) and repo
    paths (``services/foo.py``) are the author pointing at a file in the
    codebase — never an external library. Flagging them as
    ``hallucinated_reference`` was a false-positive source: prod task ba847e88
    hard-rejected a 92/100 post on a single ``api_token_auth.py`` reference, and
    codebase-discussion (dev_diary) posts that name several ``*.py`` files
    tripped the count-threshold promotion into a multi-critical veto.

    Detected structurally — a path separator, or a final segment in
    ``_SOURCE_FILE_EXTENSIONS`` — so it needs no repo I/O and works for any
    operator's tree. Real dotted library/attribute refs (``asyncio.run``,
    ``np.array``) have no file extension, so detection of genuine fabricated
    libraries is intact (Glad-Labs/poindexter#692).
    """
    if not raw:
        return False
    if "/" in raw or "\\" in raw:
        return True
    if "." not in raw:
        return False
    return raw.rsplit(".", 1)[-1].lower() in _SOURCE_FILE_EXTENSIONS


def _extract_library_candidates(text: str) -> list[tuple[str, str]]:
    """Pull potential library/API references from the text.

    Returns list of (matched_raw, normalized_root) tuples. The "root" is
    the leading segment of a dotted name (e.g. 'asyncio.run' -> 'asyncio')
    because that is what we compare against the stdlib / PyPI lists.

    Deduplicated by the pair to avoid spamming issues for the same token.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    clean = _strip_html(text or "")
    whitelist = _get_hallucination_whitelist()
    for pattern in _HALLUCINATED_REF_PATTERNS:
        for m in pattern.finditer(clean):
            raw = m.group(1)
            if not raw:
                continue
            # Internal project file / repo path is not a library — skip before
            # any list lookup so codebase-discussion posts that name several
            # ``*.py`` files don't false-positive (Glad-Labs/poindexter#692).
            if _looks_like_file_or_path(raw):
                continue
            root = raw.split(".", 1)[0]
            norm = _normalize_pkg(root)
            if not norm or norm in whitelist:
                continue
            # Skip tokens that are just a single short letter (likely noise)
            if len(norm) < 3:
                continue
            key = (raw, norm)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _is_known_reference(norm_name: str) -> bool:
    """True if `norm_name` matches stdlib, top-500 PyPI, or Ollama models."""
    if norm_name in _get_stdlib_names():
        return True
    if norm_name in _get_pypi_names():
        return True
    if norm_name in _get_ollama_names():
        return True
    # Strip common Ollama suffixes (":7b", ":instruct", etc.) before final check
    base = norm_name.split(":", 1)[0]
    if base != norm_name and base in _get_ollama_names():
        return True
    return False


def _detect_hallucinated_references(
    title: str,
    content: str,
    topic: str,
    tags: list[str] | None = None,
) -> list[ValidationIssue]:
    """Flag library/API references that don't match any known source list.

    Also flags topic mismatches for libraries that ARE known (e.g. CadQuery
    in an ai-ml post). Both are emitted as `hallucinated_reference` warnings;
    the downstream severity-promotion step in validate_content() decides
    whether to upgrade any of them to critical.
    """
    issues: list[ValidationIssue] = []
    candidates = _extract_library_candidates(f"{title or ''}\n{content or ''}")
    if not candidates:
        return issues

    lib_topics = _get_library_topics()
    post_context_tokens: set[str] = set()
    for tag in tags or []:
        if tag:
            post_context_tokens.add(str(tag).strip().lower())
    # Fall back to topic + title words when tags aren't provided so we
    # still have *some* signal for topic coherence.
    for word in re.split(r"[\s,\-_/]+", (topic or "").lower()):
        word = word.strip()
        if word and len(word) > 2:
            post_context_tokens.add(word)
    for word in re.split(r"[\s,\-_/]+", (title or "").lower()):
        word = word.strip()
        if word and len(word) > 2:
            post_context_tokens.add(word)

    for raw, norm in candidates:
        if not _is_known_reference(norm):
            # Hallucinated reference — name doesn't match any known source.
            issues.append(ValidationIssue(
                severity="warning",
                category="hallucinated_reference",
                description=(
                    f"Likely hallucinated library/API reference: '{raw}'. "
                    f"Not found in Python stdlib, top-500 PyPI packages, "
                    f"or known Ollama models."
                ),
                matched_text=raw[:100],
            ))
            continue
        # Known reference — check topic coherence when we have a mapping.
        expected = lib_topics.get(norm)
        if not expected or not post_context_tokens:
            continue
        # A mismatch when: the library's expected topics share NO token
        # with the post's context (tags + topic + title words).
        overlap = False
        for topic_tag in expected:
            topic_tokens = {t for t in re.split(r"[\s\-_]+", topic_tag.lower()) if t}
            if topic_tokens & post_context_tokens or topic_tag in post_context_tokens:
                overlap = True
                break
        if overlap:
            continue
        issues.append(ValidationIssue(
            severity="warning",
            category="hallucinated_reference",
            description=(
                f"Library '{raw}' is off-topic for this post "
                f"(expected topics: {', '.join(expected)}). "
                "Possible semantic-embedding drift during research."
            ),
            matched_text=raw[:100],
        ))
    return issues


# ---------------------------------------------------------------------------
# Code-block density check (GH-234)
# ---------------------------------------------------------------------------
#
# Tech blogs without runnable code feel like surface summaries — "talks
# about Docker" rather than "shows Docker working." Pure-prose tech posts
# score worse on EEAT signals because they don't demonstrate first-hand
# expertise.
#
# This rule runs only when the post carries one of the configured tech
# tags (default: technical, ai, programming, ml, python, javascript,
# rust, go). It compares the number of fenced code blocks against the
# post's word count and emits a WARNING — never critical — when either
# threshold is missed:
#
#   (a) at least 1 fenced code block per N words (default N=700), AND
#   (b) at least P% of non-empty content lines live inside a fenced
#       code block (default P=20%, only applied to posts > 300 words).
#
# Intentionally a soft signal: operators may genuinely write a non-code
# tech post (architecture overview, postmortem). The warning surfaces in
# the multi_model_qa critique via the existing programmatic_validator
# review, which lets the human approver decide whether to override.

# Match an opening fenced code block (``` or ~~~), captured as group 1
# so we can find the matching closer with the same fence character.
_FENCE_RE = re.compile(r"^(\s*)(```|~~~)([^\n`~]*)$", re.MULTILINE)


def _count_code_blocks_and_lines(content: str) -> tuple[int, int, int]:
    """Walk ``content`` line-by-line and tally fenced code blocks.

    Returns ``(block_count, code_line_count, total_non_empty_lines)``.

    A "block" is any well-formed ``` ... ``` (or ~~~ ... ~~~) pair. An
    unterminated fence at EOF still counts as one block because the
    writer's intent was clear; we'd rather count it than reject the
    post for malformed markdown alone.

    "Code lines" are non-fence lines that sit between an opening and
    closing fence — used for the line-ratio sub-check.
    """
    if not content:
        return (0, 0, 0)
    block_count = 0
    code_lines = 0
    total_non_empty = 0
    in_block = False
    fence_char: str | None = None
    for raw in content.split("\n"):
        stripped = raw.strip()
        if not stripped:
            # Blank lines are excluded from the denominator so a heavily
            # paragraph-padded post doesn't dilute the ratio artificially.
            continue
        # Fence detection — must start the line (after optional whitespace)
        # and use only ``` or ~~~ for the fence itself.
        is_fence = False
        if stripped.startswith("```") and set(stripped[:3]) == {"`"}:
            is_fence = True
            this_fence = "```"
        elif stripped.startswith("~~~") and set(stripped[:3]) == {"~"}:
            is_fence = True
            this_fence = "~~~"
        if is_fence:
            if not in_block:
                in_block = True
                fence_char = this_fence
                block_count += 1
            elif fence_char == this_fence:
                in_block = False
                fence_char = None
            # Fence lines themselves don't count as code or prose for the
            # ratio; they're structural punctuation.
            continue
        total_non_empty += 1
        if in_block:
            code_lines += 1
    return (block_count, code_lines, total_non_empty)


def _strip_code_blocks_for_word_count(content: str) -> str:
    """Return ``content`` with the inside of every fenced block removed.

    Word count for the density ratio is *prose words only* — counting
    the words inside a code sample would let a single 200-line code
    block satisfy the per-700-words threshold trivially.
    """
    if not content:
        return ""
    out: list[str] = []
    in_block = False
    fence_char: str | None = None
    for raw in content.split("\n"):
        stripped = raw.strip()
        is_fence = False
        if stripped.startswith("```") and set(stripped[:3]) == {"`"}:
            is_fence = True
            this_fence = "```"
        elif stripped.startswith("~~~") and set(stripped[:3]) == {"~"}:
            is_fence = True
            this_fence = "~~~"
        if is_fence:
            if not in_block:
                in_block = True
                fence_char = this_fence
            elif fence_char == this_fence:
                in_block = False
                fence_char = None
            continue
        if not in_block:
            out.append(raw)
    return "\n".join(out)


def _is_tech_post(tags: list[str], topic: str, tech_tags: set[str]) -> bool:
    """Return True if any tag/topic token matches the configured tech-tag set.

    Matching is lowercase + whitespace/dash/underscore-tolerant so the
    operator can list ``"ai"`` and still catch tags like ``"AI/ML"``,
    ``"Artificial-Intelligence"`` (when "artificial intelligence" is on
    the list), etc. Empty inputs return False.
    """
    if not tech_tags:
        return False
    haystack: set[str] = set()
    for source in (*(tags or ()), topic or ""):
        if not source:
            continue
        token = str(source).strip().lower()
        if not token:
            continue
        haystack.add(token)
        for piece in re.split(r"[\s,/\-_]+", token):
            piece = piece.strip()
            if piece:
                haystack.add(piece)
    return any(t in haystack for t in tech_tags)


def _check_code_block_density(
    content: str,
    topic: str,
    tags: list[str],
    site_config: Any,
) -> list[ValidationIssue]:
    """GH-234: warn when tech-tagged posts ship without enough code.

    All thresholds + the tag list are read from ``app_settings`` via
    ``site_config`` so operators can tune per niche without redeploys.
    Returns warnings only — never critical.

    #272 Phase-2d: ``site_config`` is REQUIRED — threaded from
    ``validate_content``.
    """
    _sc = site_config
    if not _sc.get_bool("code_density_check_enabled", True):
        return []
    tech_tags = {
        t.strip().lower()
        for t in _sc.get_list(
            "code_density_tag_filter",
            "technical,ai,programming,ml,python,javascript,rust,go",
        )
        if t and t.strip()
    }
    if not _is_tech_post(tags, topic, tech_tags):
        return []

    min_blocks_per_700w = _sc.get_int("code_density_min_blocks_per_700w", 1)
    min_line_ratio_pct = _sc.get_int("code_density_min_line_ratio_pct", 20)
    long_post_floor_words = _sc.get_int("code_density_long_post_floor_words", 300)

    block_count, code_lines, total_non_empty = _count_code_blocks_and_lines(content)
    prose_text = _strip_code_blocks_for_word_count(content)
    word_count = len(re.findall(r"\b[\w'-]+\b", prose_text))

    issues: list[ValidationIssue] = []

    # Sub-check (a): blocks-per-N-words floor. Only meaningful when the
    # post is long enough that "zero code blocks" is genuinely a signal —
    # a 200-word note doesn't need a snippet.
    if (
        min_blocks_per_700w > 0
        and word_count >= 200
    ):
        # Round up so a 701-word post still requires the floor count.
        expected_blocks = max(
            min_blocks_per_700w,
            -(-word_count * min_blocks_per_700w // 700),
        )
        if block_count < expected_blocks:
            issues.append(ValidationIssue(
                severity="warning",
                category="code_block_density",
                description=(
                    f"Tech post has {block_count} fenced code block(s) for "
                    f"{word_count} prose words; expected at least "
                    f"{expected_blocks} (threshold: "
                    f"{min_blocks_per_700w} per 700 words). Consider adding "
                    "a runnable example — pure-prose tech posts hurt EEAT signals."
                ),
                matched_text=f"blocks={block_count}, prose_words={word_count}",
            ))

    # Sub-check (b): code-line ratio floor for longer posts. Independent
    # of (a) — a post can have one giant block but still flunk this if
    # the code share is buried under prose.
    if (
        min_line_ratio_pct > 0
        and total_non_empty > 0
        and word_count >= long_post_floor_words
    ):
        ratio_pct = (code_lines * 100) // total_non_empty
        if ratio_pct < min_line_ratio_pct:
            issues.append(ValidationIssue(
                severity="warning",
                category="code_block_density",
                description=(
                    f"Tech post code-line ratio is {ratio_pct}% "
                    f"({code_lines}/{total_non_empty} non-empty lines); "
                    f"threshold is {min_line_ratio_pct}%. Add or expand "
                    "code samples so the post demonstrates the technique, "
                    "not just describes it."
                ),
                matched_text=f"code_lines={code_lines}, total_lines={total_non_empty}",
            ))

    return issues


def validate_content(
    title: str,
    content: str,
    topic: str = "",
    tags: list[str] | None = None,
    niche: str | None = None,
    *,
    site_config: Any,
) -> ValidationResult:
    """
    Validate content against hard quality rules.

    Returns ValidationResult with pass/fail and list of issues.
    Content fails if ANY critical issue is found.

    site_config (#272 Phase-2d): REQUIRED (keyword-only) SiteConfig
    instance. Threaded down to internal readers
    (``_check_code_block_density`` + ``is_validator_enabled`` rule
    enable/scope checks). Callers thread the run-bound instance
    (pipeline ``url_validation`` / ``cross_model_qa`` stages →
    ``context.get("site_config")``).

    tags (GH-83 part b): optional list of the post's topic tags. When
    provided, the hallucinated-reference rule uses them to detect
    topic-mismatched library mentions (e.g. recommending CadQuery from
    an ai-ml post). Omitting tags keeps the rule working but with a
    weaker fallback based on the topic/title text.

    niche (Validators CRUD V1, migration 0135): optional niche slug for
    the post. When provided, fine-grained rules in
    ``content_validator_rules`` whose ``applies_to_niches`` excludes
    this niche are skipped. Backwards compatible -- omitting it falls
    through to "no niche scoping" so all enabled rules still run.
    """
    # Per-rule DB-driven enable/scope checks. Imported lazily so module
    # load doesn't pull in asyncpg for callers (tests, scripts) that
    # never reach validate_content().
    from services.validator_config import is_validator_enabled

    _sc = site_config
    # Per-call company facts — populated from the live site_config for this
    # pipeline run (Wave 3f #667: module-level COMPANY_FACTS is {} at import).
    _facts = _get_company_facts(_sc)

    def _enabled(rule_name: str) -> bool:
        return is_validator_enabled(rule_name, niche=niche, site_config=_sc)

    issues: list[ValidationIssue] = []
    title = title or ""
    content = content or ""
    topic = topic or ""
    tags = list(tags) if tags else []
    full_text = f"{title}\n{content}"

    # 1. Check for fabricated people
    if _enabled("fake_person"):
        issues.extend(_check_patterns(
            full_text, FAKE_NAME_PATTERNS, "critical", "fake_person",
            "Fabricated person detected: '{matched}'"
        ))

    # 2. Check for fabricated statistics.
    # Matt 2026-04-11: "A fabrication is a fail, I can't be lying to the
    # audience. That kills brand credibility." Every fabrication
    # category is CRITICAL now -- any match blocks approval. There is no
    # "probably fake" middle ground when the consequence is publishing
    # a lie under your byline.
    if _enabled("fake_stat"):
        issues.extend(_check_patterns(
            full_text, FAKE_STAT_PATTERNS, "critical", "fake_stat",
            "Fabricated statistic: '{matched}'"
        ))

    # 3. Check for impossible company claims. The rule row was renamed
    # glad_labs_claim -> company_claim (migration 20260702) — the rename
    # is deploy-skew-safe because is_validator_enabled fails open on a
    # missing row, so the rule keeps running until the migration lands.
    if _enabled("company_claim"):
        issues.extend(_check_patterns(
            full_text, COMPANY_IMPOSSIBLE, "critical", "company_claim",
            f"Impossible claim about {_COMPANY_NAME}: " + "'{matched}'"
        ))

    # 4. Check for fabricated quotes
    if _enabled("fake_quote"):
        issues.extend(_check_patterns(
            full_text, FAKE_QUOTE_PATTERNS, "critical", "fake_quote",
            "Fabricated quote detected: '{matched}'"
        ))

    # 4b. Check for fabricated personal experiences (AI pretending to be
    # human). Promoted to critical -- a fake anecdote is the same class
    # of lie as a fake stat.
    if _enabled("fabricated_experience"):
        issues.extend(_check_patterns(
            full_text, FABRICATED_EXPERIENCE_PATTERNS, "critical", "fabricated_experience",
            "Fabricated personal experience: '{matched}'"
        ))

    # 5. Check for hallucinated internal links. Promoted to critical --
    # a link that looks valid but leads nowhere is functionally a lie
    # to the reader.
    if _enabled("hallucinated_link"):
        issues.extend(_check_patterns(
            full_text, HALLUCINATED_LINK_PATTERNS, "critical", "hallucinated_link",
            "Hallucinated internal link: '{matched}'"
        ))

    # 5b. Check for unlinked citations (hallucinated paper/study references).
    # Strip Markdown header lines (``# Title``, ``## Section``) before scanning
    # — H1/H2/H3 prose is title-case with colons by design ("The X: Y Z") and
    # is NOT an unlinked citation. Without this strip the title-case-with-colon
    # rule fires on every section heading, which (with the per-warning
    # quality-score penalty from #91) tanks dev_diary posts that are otherwise
    # well-grounded.
    if _enabled("unlinked_citation"):
        _scan_text = re.sub(r"^\s*#{1,6}\s+.*$", "", full_text, flags=re.MULTILINE)
        issues.extend(_check_patterns(
            _scan_text, UNLINKED_CITATION_PATTERNS, "warning", "unlinked_citation",
            "Unlinked citation -- possible hallucinated reference: '{matched}'"
        ))

    # 5c. Hallucinated library/API reference detection (GH-83 part b).
    # Catches `schedule_callback(event)`-style fake asyncio functions and
    # topic-orthogonal library mentions ("explore CadQuery..." in ai-ml).
    # Emitted as warnings; the per-rule threshold promotion below escalates
    # to critical if the same category fires > N times (same plumbing as
    # #91's unlinked_citation path).
    if _enabled("hallucinated_reference"):
        issues.extend(_detect_hallucinated_references(title, content, topic, tags))

    # 5d. Code-block density (GH-234). Soft signal -- tech-tagged posts
    # with too little runnable code get a warning (never critical) so
    # the human approver in multi_model_qa can decide whether the post
    # legitimately doesn't need code (architecture overview, postmortem)
    # or whether the writer surface-summarized a topic that needed
    # demonstration. Tag list + thresholds are DB-tunable.
    if _enabled("code_block_density"):
        issues.extend(_check_code_block_density(content, topic, tags, site_config=_sc))

    # 6. Check for brand contradictions (promoting paid cloud APIs)
    if _enabled("brand_contradiction"):
        issues.extend(_check_patterns(
            full_text, BRAND_CONTRADICTION_PATTERNS, "warning", "brand_contradiction",
            "Brand contradiction -- references paid cloud API: '{matched}'"
        ))

    # 7. Check for leaked image generation prompts
    if _enabled("leaked_image_prompt"):
        issues.extend(_check_patterns(
            full_text, LEAKED_IMAGE_PROMPT_PATTERNS, "warning", "leaked_image_prompt",
            "Leaked image generation prompt in content: '{matched}'"
        ))

    # 7b. Check for LLM image placeholder artifacts ([IMAGE-1: ...], [FIGURE: ...], etc.)
    if _enabled("image_placeholder"):
        issues.extend(_check_patterns(
            full_text, IMAGE_PLACEHOLDER_PATTERNS, "critical", "image_placeholder",
            "LLM image placeholder left in content: '{matched}'"
        ))

    # 7b-bis. Unresolved internal-link placeholders ([posts/<uuid>], [posts/abc-123]).
    # poindexter#489 — the writer emits these when it thinks it's hinting at a
    # related post but the internal_link_coherence resolution step never turned
    # them into real Markdown links. They show up as broken brackets in the
    # reader's view. Critical: a post that ships with literal "[posts/<uuid>]"
    # in the body has the same trust-cost as an [IMAGE: ...] placeholder.
    if _enabled("unresolved_placeholder"):
        issues.extend(_check_patterns(
            full_text, PLACEHOLDER_MARKER_PATTERNS, "critical", "unresolved_placeholder",
            "Unresolved internal-link placeholder leaked to content: '{matched}'"
        ))

    # 7b-bis-2. Placeholder citation artifacts (#766) — bracketed labels echoing
    # the writer prompt's "internal snippet(s)" vocabulary, or markdown links
    # with a placeholder href ("[text](url)", "[text](internal_context_link)").
    # Critical + zero-false-positive (no reader-facing prose contains these), so
    # they hard-reject rather than join the advisory citation_artifact /
    # unlinked_citation warnings below. Scanned with code spans blanked so a
    # markdown tutorial showing "[text](url)" as an example does not fire.
    if _enabled("placeholder_citation"):
        issues.extend(_check_patterns(
            _strip_code_spans(full_text), PLACEHOLDER_CITATION_PATTERNS,
            "critical", "placeholder_citation",
            "Placeholder citation left in content (needs a real link or removal): '{matched}'"
        ))

    # 7b-ter. Citation artifacts (#532) — bracketed numeric / parenthetical
    # academic citations. Warning: usually a hallucinated/dangling reference.
    if _enabled("citation_artifact"):
        issues.extend(_check_patterns(
            full_text, CITATION_ARTIFACT_PATTERNS, "warning", "citation_artifact",
            "Citation artifact (use a Markdown link or remove): '{matched}'"
        ))

    # 7b-quater. Leaked internal path tokens (#532) — poindexter source
    # identifiers in public content. Warning: surfaces a writer/system-context
    # leak for review without hard-blocking.
    if _enabled("leaked_path_token"):
        issues.extend(_check_patterns(
            full_text, LEAKED_PATH_TOKEN_PATTERNS, "warning", "leaked_path_token",
            "Leaked internal path/identifier token in content: '{matched}'"
        ))

    # 7b-quinquies. Orphaned attribution fragments (poindexter#532) — the
    # writer dropped the named source, leaving a sentence that opens with a
    # lowercase "points out that…" verb. Distinct from `citation_artifact`
    # above (numeric / parenthetical academic refs); renamed from the PR's
    # original `citation_artifact` to avoid colliding with the rule main
    # already shipped for #532. Warning-level; the GH-91 per-category
    # threshold promotes to critical once several fire in one post.
    if _enabled("orphaned_attribution"):
        issues.extend(_check_patterns(
            full_text, ORPHANED_ATTRIBUTION_PATTERNS, "warning", "orphaned_attribution",
            "Orphaned attribution fragment (dropped source subject): '{matched}'"
        ))

    # 7b-sexies. Leaked internal store tokens (poindexter#532) — bracketed
    # internal reference tokens like [memory/...] / [brain/...] bleeding into
    # reader-facing prose. Sibling of `unresolved_placeholder` (which owns
    # [posts/...]); distinct from `leaked_path_token` above (bare repo paths).
    if _enabled("internal_path_leak"):
        issues.extend(_check_patterns(
            full_text, INTERNAL_PATH_LEAK_PATTERNS, "warning", "internal_path_leak",
            "Leaked internal path token in content: '{matched}'"
        ))

    # 7b-septies. Leaked reasoning / chat-template control tokens (#1283).
    #
    # Defence-in-depth for the generation-boundary stripper: a draft whose
    # body still contains ``<|channel>``, ``<|im_start|>``, ``<think>``, etc.
    # after the pipeline's strip pass almost certainly had its WHOLE article
    # inside a reasoning channel (e.g. GLM-4 wrapping output in a
    # ``<|channel>thought`` block). Quality scoring can rate such drafts 90+
    # because no earlier rule covers them.
    #
    # Fence-aware: we blank code spans (``` fences + inline `backtick` code)
    # BEFORE scanning so a tutorial that shows ``<|im_start|>`` as an example
    # inside a code block does not fire. We scan the RAW full_text rather than
    # the HTML-stripped version — _strip_html removes the angle-bracket tokens
    # we are hunting for, which is exactly the wrong thing here.
    if _enabled("reasoning_token_leak"):
        _scannable = _strip_code_spans(full_text)
        for _rt_pat in REASONING_TOKEN_LEAK_PATTERNS:
            for _rt_i, _rt_line in enumerate(_scannable.split("\n"), 1):
                for _rt_m in re.finditer(_rt_pat, _rt_line, re.IGNORECASE):
                    _rt_matched = _rt_m.group(0)[:100]
                    issues.append(ValidationIssue(
                        severity="critical",
                        category="reasoning_token_leak",
                        description=(
                            f"Leaked reasoning/control token in content: "
                            f"'{_rt_matched}' — a generation-boundary stripper "
                            f"failed or was bypassed. The whole article may be "
                            f"inside a reasoning channel."
                        ),
                        matched_text=_rt_matched,
                        line_number=_rt_i,
                    ))
                    CONTENT_VALIDATOR_WARNINGS_TOTAL.labels(rule="reasoning_token_leak").inc()

    # 7b-bis. Leaked planning/outline scaffold (#1968). The writer sometimes
    # emits its outline + echoed prompt instructions as a bulleted preamble
    # before the article. normalize_draft strips the heading-anchored case; this
    # is the residual-case gate. Fence-aware (blank code spans first) so a post
    # that shows these rules as a code example does not fire, and requires >= 2
    # tells so a single benign mention ("vary sentence length") never hard-
    # rejects. Scans the body (content), not full_text — the scaffold is body-side.
    if _enabled("leaked_planning_scaffold"):
        _scaffold_hits = LEAKED_PLANNING_SCAFFOLD_RE.findall(_strip_code_spans(content))
        if len(_scaffold_hits) >= 2:
            _scaffold_examples = ", ".join(h.strip()[:50] for h in _scaffold_hits[:3])
            issues.append(ValidationIssue(
                severity="critical",
                category="leaked_planning_scaffold",
                description=(
                    f"Leaked writer planning scaffold in body (tells: "
                    f"{_scaffold_examples}) — the model emitted its outline / "
                    f"echoed prompt instructions instead of finished prose. "
                    f"normalize_draft should strip this; the residual reached QA."
                ),
                matched_text=_scaffold_examples[:100],
            ))
            CONTENT_VALIDATOR_WARNINGS_TOTAL.labels(rule="leaked_planning_scaffold").inc()

    # 7c. Known-wrong facts -- loaded from DB (fact_overrides table).
    # Each row has its own explanation so the rewrite prompt carries the
    # correction, not just "you lied". Manageable via pgAdmin, no redeploy.
    if _enabled("known_wrong_fact"):
        _fact_overrides = _load_fact_overrides_sync()
        clean_full = _strip_html(full_text)
        for _hw_pat, _hw_reason, _hw_sev in _fact_overrides:
            for _hw_line_idx, _hw_line in enumerate(clean_full.split("\n"), 1):
                for _hw_match in re.finditer(_hw_pat, _hw_line, re.IGNORECASE):
                    issues.append(ValidationIssue(
                        severity=_hw_sev,
                        category="known_wrong_fact",
                        description=f"{_hw_reason} Matched: '{_hw_match.group(0)[:80]}'",
                        matched_text=_hw_match.group(0)[:100],
                        line_number=_hw_line_idx,
                    ))

    # 7c-bis. Removed 2026-05-01 -- first-person title gate killed (see
    # FIRST_PERSON_TITLE_PATTERNS removal note at module top for context).

    # 7d. Filler phrases -- "many organizations have found...", "the journey
    # is rewarding", etc. Warning level, score penalty only.
    if _enabled("filler_phrase"):
        issues.extend(_check_patterns(
            full_text, FILLER_PHRASE_PATTERNS, "warning", "filler_phrase",
            "Filler phrase: '{matched}' -- replace with a specific, concrete claim"
        ))

    # 8. Check title for impossible claims (numeric and written-out years)
    if _enabled("title_year_claim"):
        WRITTEN_YEARS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        for word, num in WRITTEN_YEARS.items():
            if re.search(rf"\b{word}\s+years?\b", title, re.IGNORECASE) and num > 1:
                issues.append(ValidationIssue(
                    severity="critical", category="company_claim",
                    description=f"Title claims {word} years -- {_facts.get('company_name', _COMPANY_NAME)} is {_facts.get('age_months', 12)} months old",
                    matched_text=title,
                ))
        if re.search(r"\d+\s*years?", title, re.IGNORECASE):
            match = re.search(r"(\d+)\s*years?", title, re.IGNORECASE)
            years = int(match.group(1)) if match else 0
            if years > 1:
                issues.append(ValidationIssue(
                    severity="critical",
                    category="company_claim",
                    description=f"Title claims {years} years -- {_facts.get('company_name', _COMPANY_NAME)} is {_facts.get('age_months', 12)} months old",
                    matched_text=title,
                ))

    # 8b. Structural banned headers — the prompts already tell the LLM not to
    # use generic section titles like "## Introduction" / "## Conclusion", but
    # some models ignore the rule. This is a warning (not critical): the post
    # is readable, but the score drops so the model learns the pattern over
    # time and we prefer regenerating when it happens.
    if _enabled("banned_header"):
        BANNED_HEADER_WORDS = {
            "introduction",
            "conclusion",
            "summary",
            "background",
            "overview",
            "final thoughts",
            "wrap-up",
            "wrap up",
            "the end",
        }
        for m in re.finditer(r"^#{2,3}\s+(.+?)\s*$", content, re.MULTILINE):
            heading = m.group(1).strip().lower().rstrip(":")
            if heading in BANNED_HEADER_WORDS:
                issues.append(ValidationIssue(
                    severity="warning",
                    category="banned_header",
                    description=f"Generic section title: '{m.group(1).strip()}' — use a creative, benefit-focused heading instead",
                    matched_text=m.group(0)[:80],
                ))

    # 8c. "In this post/article/guide" intros — a common LLM crutch the
    # prompts already ban. Warning-level; penalizes the score without
    # killing the post outright.
    if _enabled("filler_intro"):
        first_500 = content[:500]
        for pat in (
            r"\bIn this (?:post|article|guide|blog post|tutorial)[,\s]",
            r"\bIn today'?s (?:fast-paced|digital|modern|competitive)",
        ):
            m = re.search(pat, first_500, re.IGNORECASE)  # type: ignore[assignment]
            if m:
                issues.append(ValidationIssue(
                    severity="warning",
                    category="filler_intro",
                    description=f"Filler intro phrase: '{m.group(0).strip()}' — start with a concrete hook instead",
                    matched_text=m.group(0)[:80],
                ))
                break

    # 8d. Banned-transition openers (poindexter#232). Counts sentences that
    # start with a stock-LLM transition phrase ("Furthermore", "Moreover",
    # "In conclusion", etc.). The prompt already bans them; this is the
    # deterministic catch when models ignore that rule. Emits one warning
    # per post once the count crosses ``banned_transition_opener_threshold``
    # (default 2 — three or more occurrences fires the warning), so the
    # score penalty applies once even if the writer over-used several
    # different openers. Tunable via app_settings.
    if _enabled("banned_transition_opener"):
        _bto_threshold = _sc.get_int("banned_transition_opener_threshold", 2)
        # Match either at the start of a line (paragraph break / heading
        # break) or right after a sentence terminator + whitespace. Both
        # lookbehinds are fixed length, which Python's re module requires.
        _bto_opener_alt = "|".join(BANNED_TRANSITION_OPENERS)
        _bto_pattern = (
            r"(?:^|(?<=[.!?]\s)|(?<=[.!?]\n))"
            r"(" + _bto_opener_alt + r")\b"
        )
        _bto_matches = list(re.finditer(_bto_pattern, content, re.MULTILINE))
        if len(_bto_matches) > _bto_threshold:
            _bto_used = sorted({m.group(1) for m in _bto_matches})
            issues.append(ValidationIssue(
                severity="warning",
                category="banned_transition_opener",
                description=(
                    f"Stock-LLM sentence openers used {len(_bto_matches)}× "
                    f"(threshold {_bto_threshold}): "
                    f"{', '.join(_bto_used)} — vary openers or restructure "
                    "paragraphs so the writing doesn't read as machine-templated."
                ),
                matched_text=_bto_matches[0].group(0)[:80],
            ))

    # 8b. Check for LLM-tell buzzword density (delve / testament /
    # tapestry / multifaceted / at-its-core / at-the-heart-of). Threshold
    # is the COUNT OF DISTINCT BUZZWORDS used, not total occurrences —
    # a single piece using "delve" four times is one writer falling in
    # love with one word (annoying but localised), whereas a piece
    # using delve + testament + tapestry once each is the unmistakable
    # cadence of an unmodified LLM draft. Default threshold 2 (matches
    # Matt's "≥3 distinct buzzwords = clearly LLM" spec; we trip at
    # >2 = 3 distinct, mirroring the banned_transition_opener pattern
    # of "> threshold").
    if _enabled("buzzword_density"):
        _bd_threshold = _sc.get_int("buzzword_density_threshold", 2)
        _bd_matches = list(_LLM_TELL_RE.finditer(content))
        _bd_distinct = sorted({m.group(0).lower() for m in _bd_matches})
        if len(_bd_distinct) > _bd_threshold:
            issues.append(ValidationIssue(
                severity="warning",
                category="buzzword_density",
                description=(
                    f"LLM-tell buzzwords used: {len(_bd_distinct)} distinct "
                    f"(threshold {_bd_threshold}), {len(_bd_matches)} total occurrences. "
                    f"Found: {', '.join(_bd_distinct)} — replace with more specific verbs/"
                    "nouns. See feedback_writing_anti_llm_tells for the rationale."
                ),
                matched_text=_bd_matches[0].group(0)[:80] if _bd_matches else "",
            ))

    # 9. Check for late acronym expansions — e.g. "CRM (Customer Relationship Management)"
    #    when the acronym was already used earlier without expansion
    if _enabled("late_acronym_expansion"):
        for m in re.finditer(r'\b([A-Z]{2,6})\s*\(([A-Z][a-z][\w\s]{5,50})\)', content):
            acronym = m.group(1)
            # Check if the acronym appears earlier in the content (before this match)
            prior_text = content[:m.start()]
            prior_uses = len(re.findall(rf'\b{re.escape(acronym)}\b', prior_text))
            if prior_uses >= 2:
                issues.append(ValidationIssue(
                    severity="warning", category="late_acronym_expansion",
                    description=f"Acronym '{acronym}' expanded after {prior_uses} prior uses — expand on first use or not at all",
                    matched_text=m.group(0)[:80],
                ))

    # 10. Truncation detection — content that ends mid-sentence indicates
    # the LLM hit its token limit. This is critical because it means the
    # reader gets an incomplete article.
    #
    # 2026-05-16: split the diagnostic so a leaked JSON envelope (the
    # writer returned ``{"content": "..."}`` and nothing unwrapped it
    # before validation) gets its own category. Same severity — still
    # critical — but the operator sees ``json_envelope_leak`` in
    # ``qa_feedback`` and knows the fix is upstream in the writer, not
    # token budget. Captured 2026-05-15: ``pipeline_versions.id=1851``
    # shipped a literal ``}`` as the final line because
    # ``two_pass._revise_node`` was calling ``_ollama_chat_json``.
    if _enabled("truncated_content"):
        stripped_content = content.rstrip()
        if stripped_content and len(stripped_content) > 200:
            # A finished sentence wrapped in markdown emphasis ends with the
            # emphasis marker, not terminal punctuation — e.g. an italic CTA
            # closer "*Read more on X.*" or a bold takeaway "**The point.**".
            # Peel a trailing run of * _ ` before the terminal-punctuation
            # check so a complete-but-emphasized final line isn't misread as a
            # token-limit cutoff (captured live 2026-06-06: task b9aab2fe,
            # quality 97, hard-rejected on "*Read more on AI Health and
            # monitoring.*"). A sentence cut off *inside* emphasis still fails:
            # peeling the markers leaves a non-terminal char, so it still fires.
            _deemphasized_tail = stripped_content.rstrip("*_`")
            last_char = _deemphasized_tail[-1] if _deemphasized_tail else ""
            if last_char not in '.!?"”)’':
                # Check it's not a code block or list that legitimately ends without punctuation
                last_line = stripped_content.split('\n')[-1].strip()
                _in_code = last_line.startswith('```') or last_line.startswith('    ')
                _is_heading = last_line.startswith('#')
                _is_list_item = re.match(r'^[-*\d]+[.)]\s', last_line) or re.match(r'^[-*]\s', last_line)
                # Final line is a lone ``}`` / ``]`` — JSON envelope leak,
                # not truncation. Tag it accordingly so the operator can
                # tell at a glance which producer to fix.
                _is_envelope_terminator = last_line in ("}", "]") or last_line in ('"}', '"]')
                if _is_envelope_terminator:
                    issues.append(ValidationIssue(
                        severity="critical",
                        category="json_envelope_leak",
                        description=(
                            f"Content ends with a JSON envelope terminator "
                            f"({last_line!r}) — a writer/LLM stage emitted "
                            f"``{{\"content\": \"...\"}}`` and nothing un-wrapped "
                            f"it before validation. Fix the producer (most "
                            f"common: a chat helper forcing ``format=json``)."
                        ),
                        matched_text=stripped_content[-160:],
                    ))
                elif not (_in_code or _is_heading or _is_list_item):
                    issues.append(ValidationIssue(
                        severity="critical",
                        category="truncated_content",
                        description=(
                            f"Content appears truncated — ends with '{last_line[-60:]}' "
                            f"which is not a complete sentence. The LLM likely hit its token limit."
                        ),
                        matched_text=stripped_content[-100:],
                    ))

    # 10b. Prompt-scaffolding leak — the writer or qa.rewrite reviser echoed its
    # instructions / persona / planning outline into the body instead of prose
    # (2026-06-29 canonical incident). Sibling to json_envelope_leak: the same
    # "producer emitted non-prose" failure, so it is CRITICAL. Two detectors:
    # exact markers (verbatim template vocabulary; one hit flags) and
    # paraphrased instruction SHAPES (2026-07-01 incident — gemma restated the
    # expand prompt in its own words; >=2 distinct shapes flag). Strip code
    # spans first so a prompt-engineering article that quotes an instruction
    # inside a fence is not a false positive. Runs on the canonical path via
    # qa.programmatic (after both the writer and the qa.rewrite loop), so it
    # gates a leaked draft before it can reach awaiting_approval; dev_diary (no
    # QA rails) is guarded at the source in narrate_bundle.
    if _enabled("prompt_leak"):
        content_for_leak = _strip_code_spans(content)
        leaked_markers = detect_prompt_leak(content_for_leak)
        echo_shapes = detect_prompt_echo_paraphrase(content_for_leak)
        if leaked_markers or len(echo_shapes) >= 2:
            evidence = leaked_markers + [f"shape:{s}" for s in echo_shapes]
            issues.append(ValidationIssue(
                severity="critical",
                category="prompt_leak",
                description=(
                    "Body contains prompt scaffolding / instruction-echo "
                    "(" + ", ".join(evidence[:5]) + ") — an LLM producer "
                    "leaked its instructions, persona, or planning outline "
                    "instead of emitting finished prose (verbatim or "
                    "paraphrased). Fix the producer (canonical writer, "
                    "expand pass, or qa.rewrite reviser)."
                ),
                matched_text=content.strip()[:160],
            ))

    # 10c. Planning-dump preamble — the writer emitted its outline/planning
    # bullets as the OPENING of the draft with NO instruction lines at all,
    # so neither the exact markers nor the >=2 paraphrase shapes above fire
    # (2026-07-01 task e46b449c: "*   Topic: Ellipses...", "*   Source
    # Material provided:" — 0 markers, 0 shapes, 1 scaffold tell; reached
    # awaiting_approval at quality 85). Structural rule: >=6 bullets
    # dominating the pre-heading opening plus >=2 planning-vocabulary
    # families. Same code-span strip as 10b so a post that quotes a dump in
    # a fence is safe.
    if _enabled("planning_dump"):
        dump_evidence = detect_planning_dump_preamble(_strip_code_spans(content))
        if dump_evidence:
            issues.append(ValidationIssue(
                severity="critical",
                category="planning_dump",
                description=(
                    "Draft opens with a planning/outline dump ("
                    + ", ".join(dump_evidence[:5]) + ") — the writer emitted "
                    "its plan instead of (or before) the article. Fix the "
                    "producer (canonical writer or expand pass); finished "
                    "prose never opens with outline bullets."
                ),
                matched_text=content.strip()[:160],
            ))
            CONTENT_VALIDATOR_WARNINGS_TOTAL.labels(rule="planning_dump").inc()

    # 11. Title diversity — detect repetitive opener patterns
    if _enabled("title_diversity"):
        _BANNED_OPENERS = [
            "beyond the", "beyond", "building", "unlocking", "the ultimate",
            "the hidden", "the silent", "the invisible", "the secret",
            "mastering", "revolutionizing", "the complete", "the definitive",
            "how to build", "scale your", "why you need",
        ]
        if title:
            title_lower = title.lower().strip()
            for opener in _BANNED_OPENERS:
                if title_lower.startswith(opener):
                    issues.append(ValidationIssue(
                        severity="warning",
                        category="title_diversity",
                        description=(
                            f"Title starts with overused opener '{opener}'. "
                            "Rotate title structure for better variety."
                        ),
                        matched_text=title[:60],
                    ))
                    break

    # ------------------------------------------------------------------
    # Severity promotion (GH-91)
    # ------------------------------------------------------------------
    # Two-stage promotion wired in 2026-04-20 so validator warnings
    # stop being silent. Before this, a post with 9 `unlinked_citation`
    # warnings reached QA with 0 critical and still passed Q80. Now:
    #
    #   (a) Per-rule threshold: if any single warning category exceeds
    #       `content_validator_warning_reject_threshold` (default 3),
    #       promote every warning in that category to critical. This
    #       catches "writer hallucinated 9 Medium articles" patterns
    #       that were not surfacing as rejects.
    #
    #   (b) Named-source-without-URL: specifically for
    #       `unlinked_citation`, if the matched text names a source
    #       type ("Medium", "article", "blog post", "documentation",
    #       "paper", "study") and has no URL within ~100 chars of the
    #       match, upgrade to critical individually. This is the
    #       hallucinated-attribution pattern — worse than a generic
    #       weasel because the writer literally named a source it
    #       failed to cite.
    #
    # Both promotions preserve the original `category` so downstream
    # rewrite prompts still see *which* rule fired, and Prometheus
    # counts are taken from the original warnings (not post-promotion)
    # so Grafana panels keep showing the raw warning volume.
    warning_counts_by_category: dict[str, int] = {}
    for _i in issues:
        if _i.severity == "warning":
            warning_counts_by_category[_i.category] = (
                warning_counts_by_category.get(_i.category, 0) + 1
            )

    # Emit Prometheus counter BEFORE promotion so Grafana sees the raw
    # warning volume regardless of whether this post was auto-rejected.
    for _cat, _count in warning_counts_by_category.items():
        try:
            CONTENT_VALIDATOR_WARNINGS_TOTAL.labels(rule=_cat).inc(_count)
        except Exception as _exc:  # pragma: no cover — best-effort metric
            logger.debug("[VALIDATOR] prometheus counter emit failed: %s", _exc)

    # (b) Named-source-without-URL promotion. Runs per-issue so we can
    # look at each match in isolation; a post with one fabricated
    # "as noted in this Medium article" should reject even if the
    # category count is only 1.
    #
    # Tunable via ``content_validator_named_source_promote_enabled``
    # (default ``false`` — gated off after task 1738's dev_diary post
    # tripped the rule 7+ times on its own work-in-progress citations,
    # and the per-instance promotion vetoed the whole post). The (a)
    # per-category threshold below still catches genuine
    # hallucination spam — that's enough signal without a single
    # instance being a hard veto. Flip the setting back to ``true``
    # if a future writer regression starts emitting fabricated
    # attributions and the threshold path doesn't catch it.
    _named_source_promote_enabled = _sc.get_bool(
        "content_validator_named_source_promote_enabled", False,
    )
    if _named_source_promote_enabled:
        _clean_full_text = _strip_html(full_text)
        for _i in issues:
            if _i.severity != "warning" or _i.category != "unlinked_citation":
                continue
            _match_lower = _i.matched_text.lower()
            if not any(kw in _match_lower for kw in _NAMED_SOURCE_KEYWORDS):
                continue
            # Look for a URL within ~100 chars after the match; if missing,
            # treat as a hallucinated attribution and promote to critical.
            _idx = _clean_full_text.find(_i.matched_text)
            _context_window = ""
            if _idx != -1:
                _context_window = _clean_full_text[
                    max(0, _idx - 20): _idx + len(_i.matched_text) + 100
                ]
            else:
                _context_window = _i.matched_text
            _has_url = bool(re.search(r"https?://\S+", _context_window))
            if not _has_url:
                _i.severity = "critical"
                _i.description = (
                    "Named source without accompanying URL (hallucinated attribution): "
                    f"{_i.matched_text!r}"
                )

    # (a) Per-rule threshold promotion. Read the threshold from
    # site_config (DB-first) with a hardcoded floor of 3 so the guard
    # still fires on a cold-boot environment with no settings loaded.
    _warning_threshold = _sc.get_int(
        "content_validator_warning_reject_threshold", 3,
    )
    # Per-category override. ``unlinked_citation`` and ``hallucinated_reference``
    # are DEMOTED to non-promotable warnings (default 0 = never promote, via the
    # ``_effective_threshold <= 0`` skip below). These two rules are pattern-based
    # heuristics that cannot distinguish a fabricated EXTERNAL reference from a
    # rhetorical phrase ("According to our analysis"), a real post-cutoff product
    # ("Claude Mythos 5"), a legitimate news citation, or an internal file — so
    # accumulated false positives were promoting to a hard 0/100 veto on
    # high-quality posts (prod tasks ba847e88 / 5979b399 / 29921a2d / b0ee40b9,
    # 2026-06-09). They still fire as WARNINGS (score penalty + rewrite signal +
    # Grafana counter); the hard veto for genuinely fabricated external refs
    # moves to the LLM critic (qa.critic) + the qa.web_factcheck rescue (#661),
    # which CAN make that distinction. Fake people/stats/quotes/company claims are
    # emitted at ``critical`` severity directly and are unaffected. Operators can
    # re-arm either category by setting its threshold > 0 (Glad-Labs/poindexter#692).
    _per_category_overrides = {
        "unlinked_citation": _sc.get_int(
            "content_validator_unlinked_citation_warning_threshold", 0,
        ),
        "hallucinated_reference": _sc.get_int(
            "content_validator_hallucinated_reference_warning_threshold", 0,
        ),
    }
    if _warning_threshold > 0:
        for _i in issues:
            _effective_threshold = _per_category_overrides.get(
                _i.category, _warning_threshold,
            )
            if _effective_threshold <= 0:
                continue
            if (
                _i.severity == "warning"
                and warning_counts_by_category.get(_i.category, 0) > _effective_threshold
            ):
                _i.severity = "critical"
                _i.description = (
                    f"{_i.description} "
                    f"(promoted: {warning_counts_by_category[_i.category]} "
                    f"{_i.category} warnings exceeds reject threshold of "
                    f"{_effective_threshold})"
                )

    # Calculate score penalty
    score_penalty = sum(10 for i in issues if i.severity == "critical")
    score_penalty += sum(3 for i in issues if i.severity == "warning")

    passed = all(i.severity != "critical" for i in issues)

    result = ValidationResult(passed=passed, issues=issues, score_penalty=score_penalty)

    if issues:
        logger.warning(
            "[VALIDATOR] Content '%s': %d critical, %d warnings",
            title[:50], result.critical_count, result.warning_count,
        )
        for issue in issues[:5]:  # Log first 5
            logger.warning(
                "[VALIDATOR]   [%s] %s: %s",
                issue.severity, issue.category, issue.description,
            )

    return result


# ============================================================================
# ASYNC URL VERIFICATION — checks that cited URLs actually resolve (#214)
# Call separately from the async pipeline (validate_content is sync)
# ============================================================================

async def verify_content_urls(
    content: str,
    *,
    site_config: Any,
) -> list[ValidationIssue]:
    """Extract all URLs from content and verify they resolve.

    Returns a list of ValidationIssues for dead/broken links.
    This is async because it makes HTTP requests.

    site_config (#272 Phase-2d): REQUIRED (keyword-only) SiteConfig used
    to read the ``site_domains`` skip-list. Callers thread the run-bound
    instance (the ``url_validation`` pipeline stage →
    ``context.get("site_config")``).
    """
    import httpx

    _sc = site_config

    issues: list[ValidationIssue] = []
    # Extract markdown links: [text](url) and bare https:// URLs
    url_pattern = re.compile(
        r'(?:\[([^\]]*)\]\((https?://[^)]+)\))'  # [text](url)
        r'|(?<![(\[])(https?://[^\s)\]<>"]+)'      # bare url
    )

    urls_found: list[tuple[str, str]] = []  # (display_text, url)
    for match in url_pattern.finditer(content):
        if match.group(2):  # markdown link
            urls_found.append((match.group(1) or "", match.group(2)))
        elif match.group(3):  # bare url
            urls_found.append(("", match.group(3)))

    if not urls_found:
        return issues

    # Skip internal links (our own site) and known-good domains.
    # Domain list comes from site_config (site_domains = comma-separated),
    # not hardcoded — lets operators bring their own brand (#198).
    _raw = _sc.get("site_domains", "")
    skip_domains = {d.strip().lower() for d in _raw.split(",") if d.strip()}
    skip_domains.add("localhost")

    # Per-request headers (the lifespan client doesn't carry these as
    # defaults — every caller passes its own UA/Accept). The User-Agent
    # routes through the shared crawler-UA helper, which folds in
    # ``crawler_contact_url`` with the OSS contact-URL leak guard (#1969).
    link_check_headers = {
        "User-Agent": build_crawler_ua(_sc, product="PoindexterLinkChecker"),
    }

    from urllib.parse import urlparse

    async def _verify_loop(client: httpx.AsyncClient) -> None:
        for display_text, url in urls_found:
            try:
                domain = urlparse(url).netloc.lower()
                if domain in skip_domains or domain.endswith(".localhost"):
                    continue

                resp = await client.head(
                    url, timeout=10, headers=link_check_headers,
                    follow_redirects=True,
                )
                # Accept 2xx and 3xx as valid
                if resp.status_code >= 400:
                    issues.append(ValidationIssue(
                        severity="critical",
                        category="dead_link",
                        description=f"Dead link (HTTP {resp.status_code}): {url[:80]}",
                        matched_text=f"[{display_text}]({url})" if display_text else url,
                    ))
            except httpx.TimeoutException:
                issues.append(ValidationIssue(
                    severity="warning",
                    category="slow_link",
                    description=f"URL timed out (10s): {url[:80]}",
                    matched_text=url[:100],
                ))
            except Exception as e:
                issues.append(ValidationIssue(
                    severity="warning",
                    category="unresolvable_link",
                    description=f"Cannot resolve URL: {url[:60]} ({type(e).__name__})",
                    matched_text=url[:100],
                ))

    # Prefer the lifespan-bound shared client (warm pool); fall back to
    # a per-call client only when nothing has been wired (tests, CLI).
    if http_client is not None:
        await _verify_loop(http_client)
    else:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=True,
            headers=link_check_headers,
        ) as client:
            await _verify_loop(client)

    # Citation count check
    external_citations = [
        (t, u) for t, u in urls_found
        if urlparse(u).netloc.lower() not in skip_domains
    ]
    if len(external_citations) == 0:
        issues.append(ValidationIssue(
            severity="warning",
            category="no_citations",
            description="No external citations found. Content should reference real sources.",
            matched_text="",
        ))

    return issues
