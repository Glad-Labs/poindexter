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

from services.logger_config import get_logger
import re
from dataclasses import dataclass, field
from typing import List

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

# Leaked image generation prompts — italic descriptions after images
LEAKED_IMAGE_PROMPT_PATTERNS = [
    r"(?:^|\n)\s*:\s*\*[A-Z][^*]{30,}\*",  # `: *A split-screen comparison...*`
    r"(?:^|\n)\s*\*(?:A |An |Imagine |Visual |Split|Close)[^*]{40,}\*",  # standalone `*A description...*`
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
    issues: List[ValidationIssue] = field(default_factory=list)
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
) -> List[ValidationIssue]:
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
    issues: List[ValidationIssue] = []
    title = title or ""
    content = content or ""
    topic = topic or ""
    full_text = f"{title}\n{content}"

    # 1. Check for fabricated people
    issues.extend(_check_patterns(
        full_text, FAKE_NAME_PATTERNS, "critical", "fake_person",
        "Fabricated person detected: '{matched}'"
    ))

    # 2. Check for fabricated statistics
    issues.extend(_check_patterns(
        full_text, FAKE_STAT_PATTERNS, "warning", "fake_stat",
        "Potentially fabricated statistic: '{matched}'"
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

    # 5. Check for hallucinated internal links
    issues.extend(_check_patterns(
        full_text, HALLUCINATED_LINK_PATTERNS, "warning", "hallucinated_link",
        "Possible hallucinated internal link: '{matched}'"
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
