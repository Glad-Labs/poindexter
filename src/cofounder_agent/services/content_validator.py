"""
Content Validator — programmatic quality gate for AI-generated content.

Runs hard rules against generated content BEFORE it can be published.
No LLM judgment — deterministic pattern matching that catches:
- Fabricated people, quotes, and statistics
- False claims about the company
- Unverifiable citations
- Impossible timeframes and metrics

Usage:
    from services.content_validator import validate_content
    issues = validate_content(title, content, topic)
    if issues:
        # Reject — content has quality issues
"""

import re
import time as _time
from dataclasses import dataclass, field

from services.logger_config import get_logger
from services.site_config import site_config as _sc

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


def _get_company_facts() -> dict:
    """Load company facts from DB (site_config) with env fallback."""
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


# Loaded at module import time — uses DB values from site_config cache. Not refreshed on config changes without reimport.
GLAD_LABS_FACTS = _get_company_facts()
_COMPANY_NAME = GLAD_LABS_FACTS["company_name"]

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
GLAD_LABS_IMPOSSIBLE = [
    rf"(?:{_CN}|our|we)\s+(?:has|have)\s+(?:been|spent)\s+(?:\w+\s+)*(?:years?|decade)",
    rf"(?:{_CN}|our)\s+(?:team|staff|employees|engineers|developers)\s+of\s+\d{{2,}}",
    rf"(?:{_CN}|our)\s+(?:clients?|customers?|users?)\s+(?:include|such as|like)\s+[A-Z]",
    rf"(?:{_CN}|we)\s+(?:processed|handled|served|generated)\s+(?:\d+\s*(?:million|billion|thousand))",
    rf"(?:{_CN}|our)\s+(?:revenue|profit|valuation|funding)",
]

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
    # Bare paper-style titles with colon: "Word Word: Subtitle With Title Case"
    r"(?<!\[)(?:[A-Z][A-Za-z0-9\-]*(?:\s+[A-Z][a-z]+){1,}:\s+[A-Z][a-z]+(?:\s+[A-Za-z]+){2,})(?!\])",
    # "et al." references — almost certainly fabricated
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

# First-person pronouns in titles. Matt 2026-04-11: "Another issue I found
# is one of the posts uses we in the title." The pipeline is a solo + AI
# operation — titles like "How We Built X" are corporate/team-speak that
# implies a team the author doesn't have. It's the same class of problem
# as FABRICATED_EXPERIENCE_PATTERNS (which handles the post body), just
# applied specifically to the title line.
#
# Matched bare — an issue here is CRITICAL because titles are the first
# thing readers see and sharing a title like "How We Built..." to an
# audience that knows you're solo is an immediate credibility hit.
FIRST_PERSON_TITLE_PATTERNS = [
    r"\b(?:we|our|us|my|mine)\b",
    # Standalone "I" needs a negative lookahead for "/" so "I/O" doesn't match.
    r"\bi(?!/)(?:'ve|'m|'ll|'d)?\b",
    r"\bwe(?:'ve|'re|'ll|'d)\b",
]

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
        import os
        import sys
        from pathlib import Path

        db_url = os.getenv("DATABASE_URL", "")
        if not db_url:
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
                pass
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


@dataclass
class ValidationIssue:
    """A single quality issue found in the content."""
    severity: str  # "critical", "warning"
    category: str  # "fake_person", "fake_stat", "glad_labs_claim", "fake_quote"
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


def validate_content(title: str, content: str, topic: str = "") -> ValidationResult:
    """
    Validate content against hard quality rules.

    Returns ValidationResult with pass/fail and list of issues.
    Content fails if ANY critical issue is found.
    """
    issues: list[ValidationIssue] = []
    title = title or ""
    content = content or ""
    topic = topic or ""
    full_text = f"{title}\n{content}"

    # 1. Check for fabricated people
    issues.extend(_check_patterns(
        full_text, FAKE_NAME_PATTERNS, "critical", "fake_person",
        "Fabricated person detected: '{matched}'"
    ))

    # 2. Check for fabricated statistics.
    # Matt 2026-04-11: "A fabrication is a fail, I can't be lying to the
    # audience. That kills brand credibility." Every fabrication
    # category is CRITICAL now — any match blocks approval. There is no
    # "probably fake" middle ground when the consequence is publishing
    # a lie under your byline.
    issues.extend(_check_patterns(
        full_text, FAKE_STAT_PATTERNS, "critical", "fake_stat",
        "Fabricated statistic: '{matched}'"
    ))

    # 3. Check for impossible company claims
    issues.extend(_check_patterns(
        full_text, GLAD_LABS_IMPOSSIBLE, "critical", "glad_labs_claim",
        f"Impossible claim about {_COMPANY_NAME}: " + "'{matched}'"
    ))

    # 4. Check for fabricated quotes
    issues.extend(_check_patterns(
        full_text, FAKE_QUOTE_PATTERNS, "critical", "fake_quote",
        "Fabricated quote detected: '{matched}'"
    ))

    # 4b. Check for fabricated personal experiences (AI pretending to be
    # human). Promoted to critical — a fake anecdote is the same class
    # of lie as a fake stat.
    issues.extend(_check_patterns(
        full_text, FABRICATED_EXPERIENCE_PATTERNS, "critical", "fabricated_experience",
        "Fabricated personal experience: '{matched}'"
    ))

    # 5. Check for hallucinated internal links. Promoted to critical —
    # a link that looks valid but leads nowhere is functionally a lie
    # to the reader.
    issues.extend(_check_patterns(
        full_text, HALLUCINATED_LINK_PATTERNS, "critical", "hallucinated_link",
        "Hallucinated internal link: '{matched}'"
    ))

    # 5b. Check for unlinked citations (hallucinated paper/study references)
    issues.extend(_check_patterns(
        full_text, UNLINKED_CITATION_PATTERNS, "warning", "unlinked_citation",
        "Unlinked citation — possible hallucinated reference: '{matched}'"
    ))

    # 6. Check for brand contradictions (promoting paid cloud APIs)
    issues.extend(_check_patterns(
        full_text, BRAND_CONTRADICTION_PATTERNS, "warning", "brand_contradiction",
        "Brand contradiction — references paid cloud API: '{matched}'"
    ))

    # 7. Check for leaked image generation prompts
    issues.extend(_check_patterns(
        full_text, LEAKED_IMAGE_PROMPT_PATTERNS, "warning", "leaked_image_prompt",
        "Leaked image generation prompt in content: '{matched}'"
    ))

    # 7b. Check for LLM image placeholder artifacts ([IMAGE-1: ...], [FIGURE: ...], etc.)
    issues.extend(_check_patterns(
        full_text, IMAGE_PLACEHOLDER_PATTERNS, "critical", "image_placeholder",
        "LLM image placeholder left in content: '{matched}'"
    ))

    # 7c. Known-wrong facts — loaded from DB (fact_overrides table).
    # Each row has its own explanation so the rewrite prompt carries the
    # correction, not just "you lied". Manageable via pgAdmin, no redeploy.
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

    # 7c-bis. First-person pronouns in TITLE only. The body has its own
    # fabricated_experience check; this one specifically guards the headline.
    #
    # Strip quoted substrings first so idioms like "It Works on My Machine"
    # don't false-positive. Both curly and straight quotes (single and
    # double) are handled. If the pronoun survives quote-stripping, it's
    # a real first-person claim.
    _title_str = title or ""
    _quote_stripped = re.sub(
        r"""(?x)
        (?: \"[^\"]*\"      # "double quoted"
          | '[^']*'         # 'single quoted'
          | \u201c[^\u201d]*\u201d   # curly-double quoted
          | \u2018[^\u2019]*\u2019   # curly-single quoted
        )
        """,
        " ",
        _title_str,
    )
    for _fp_pat in FIRST_PERSON_TITLE_PATTERNS:
        _fp_match = re.search(_fp_pat, _quote_stripped, re.IGNORECASE)
        if _fp_match:
            issues.append(ValidationIssue(
                severity="critical",
                category="first_person_title",
                description=(
                    f"Title contains first-person pronoun '{_fp_match.group(0)}'. "
                    f"Titles must be third-person — Glad Labs is a solo+AI operation, "
                    f"'How We Built X' implies a team that doesn't exist."
                ),
                matched_text=_title_str[:100],
            ))
            break  # one hit is enough; don't spam multiple issues for the same title

    # 7d. Filler phrases — "many organizations have found...", "the journey
    # is rewarding", etc. Warning level, score penalty only.
    issues.extend(_check_patterns(
        full_text, FILLER_PHRASE_PATTERNS, "warning", "filler_phrase",
        "Filler phrase: '{matched}' — replace with a specific, concrete claim"
    ))

    # 8. Check title for impossible claims (numeric and written-out years)
    WRITTEN_YEARS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    for word, num in WRITTEN_YEARS.items():
        if re.search(rf"\b{word}\s+years?\b", title, re.IGNORECASE) and num > 1:
            issues.append(ValidationIssue(
                severity="critical", category="glad_labs_claim",
                description=f"Title claims {word} years — {_COMPANY_NAME} is {GLAD_LABS_FACTS['age_months']} months old",
                matched_text=title,
            ))
    if re.search(r"\d+\s*years?", title, re.IGNORECASE):
        match = re.search(r"(\d+)\s*years?", title, re.IGNORECASE)
        years = int(match.group(1)) if match else 0
        if years > 1:
            issues.append(ValidationIssue(
                severity="critical",
                category="glad_labs_claim",
                description=f"Title claims {years} years — {_COMPANY_NAME} is {GLAD_LABS_FACTS['age_months']} months old",
                matched_text=title,
            ))

    # 8b. Structural banned headers — the prompts already tell the LLM not to
    # use generic section titles like "## Introduction" / "## Conclusion", but
    # some models ignore the rule. This is a warning (not critical): the post
    # is readable, but the score drops so the model learns the pattern over
    # time and we prefer regenerating when it happens.
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
    first_500 = content[:500]
    for pat in (
        r"\bIn this (?:post|article|guide|blog post|tutorial)[,\s]",
        r"\bIn today'?s (?:fast-paced|digital|modern|competitive)",
    ):
        m = re.search(pat, first_500, re.IGNORECASE)
        if m:
            issues.append(ValidationIssue(
                severity="warning",
                category="filler_intro",
                description=f"Filler intro phrase: '{m.group(0).strip()}' — start with a concrete hook instead",
                matched_text=m.group(0)[:80],
            ))
            break

    # 9. Check for late acronym expansions — e.g. "CRM (Customer Relationship Management)"
    #    when the acronym was already used earlier without expansion
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
    stripped_content = content.rstrip()
    if stripped_content and len(stripped_content) > 200:
        # Check if content ends with a sentence-ending character
        last_char = stripped_content[-1]
        if last_char not in '.!?"\u201d)\u2019':
            # Check it's not a code block or list that legitimately ends without punctuation
            last_line = stripped_content.split('\n')[-1].strip()
            _in_code = last_line.startswith('```') or last_line.startswith('    ')
            _is_heading = last_line.startswith('#')
            _is_list_item = re.match(r'^[-*\d]+[.)]\s', last_line) or re.match(r'^[-*]\s', last_line)
            if not (_in_code or _is_heading or _is_list_item):
                issues.append(ValidationIssue(
                    severity="critical",
                    category="truncated_content",
                    description=(
                        f"Content appears truncated — ends with '{last_line[-60:]}' "
                        f"which is not a complete sentence. The LLM likely hit its token limit."
                    ),
                    matched_text=stripped_content[-100:],
                ))

    # 11. Title diversity — detect repetitive opener patterns
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
    if _warning_threshold > 0:
        for _i in issues:
            if (
                _i.severity == "warning"
                and warning_counts_by_category.get(_i.category, 0) > _warning_threshold
            ):
                _i.severity = "critical"
                _i.description = (
                    f"{_i.description} "
                    f"(promoted: {warning_counts_by_category[_i.category]} "
                    f"{_i.category} warnings exceeds reject threshold of "
                    f"{_warning_threshold})"
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

async def verify_content_urls(content: str) -> list[ValidationIssue]:
    """Extract all URLs from content and verify they resolve.

    Returns a list of ValidationIssues for dead/broken links.
    This is async because it makes HTTP requests.
    """
    import httpx

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

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Poindexter-LinkChecker/1.0)"},
    ) as client:
        for display_text, url in urls_found:
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.lower()
                if domain in skip_domains or domain.endswith(".localhost"):
                    continue

                resp = await client.head(url, timeout=10)
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
