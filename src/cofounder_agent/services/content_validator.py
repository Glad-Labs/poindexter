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
from dataclasses import dataclass, field
from typing import List

from services.logger_config import get_logger

logger = get_logger(__name__)

# ============================================================================
# COMPANY FACTS — ground truth for fact-checking (configurable)
# Override via environment variables for your own brand
# ============================================================================

from services.site_config import site_config as _sc


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


# Lazy-loaded — uses DB values once site_config.load() has been called
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
import time as _time

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
        from services.db_service import get_db_service
        db = get_db_service()
        pool = getattr(db, "pool", None)
        if not pool:
            return _fact_overrides_cache

        async def _fetch():
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT pattern, correct_fact, severity FROM fact_overrides WHERE active = true"
                )
            return [(r["pattern"], r["correct_fact"], r["severity"]) for r in rows]

        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context — create a task won't work here,
            # but validate_content is called synchronously from async code.
            # Use the cached value and schedule a background refresh.
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
        logger.debug("[VALIDATOR] fact_overrides DB load failed (using cache): %s", e)

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
