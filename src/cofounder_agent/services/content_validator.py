"""
Content Validator — programmatic quality gate for AI-generated content.

Runs hard rules against generated content BEFORE it can be published.
No LLM judgment — deterministic pattern matching that catches:
- Fabricated people, quotes, and statistics
- False claims about Glad Labs
- Unverifiable citations
- Impossible timeframes and metrics

Usage:
    from services.content_validator import validate_content
    issues = validate_content(title, content, topic)
    if issues:
        # Reject — content has quality issues
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

# ============================================================================
# GLAD LABS FACTS — ground truth for fact-checking
# ============================================================================

GLAD_LABS_FACTS = {
    "founded_year": 2025,
    "age_months": 6,
    "team_size": 1,  # Solo founder
    "founder_name": "Matt",
    "known_employees": set(),  # No employees — solo operation
    "real_products": {"gladlabs.io", "content pipeline", "openclaw"},
    "real_tech": {"fastapi", "next.js", "postgresql", "ollama", "railway", "vercel", "grafana"},
}

# People names that should NEVER appear (fabricated by LLMs)
FAKE_NAME_PATTERNS = [
    r"\b(?:Sarah|John|Emily|David|Michael|Jennifer|James|Jessica|Robert|Lisa)\s+[A-Z][a-z]+(?:,\s*(?:CEO|CTO|VP|Director|Lead|Head|Chief|Manager|Founder|Co-founder))",
    r"\b(?:Dr\.|Prof\.)\s+[A-Z][a-z]+\s+[A-Z][a-z]+",
    r"(?:CEO|CTO|VP|Director|Lead Architect|Head of|Chief)\s+(?:at|of)\s+(?:Glad Labs|GladLabs)",
]

# Fake statistic patterns
FAKE_STAT_PATTERNS = [
    r"\b\d{1,3}%\s+(?:reduction|increase|improvement|decrease|growth|decline|boost|drop|surge|rise)",
    r"(?:according to|a\s+\d{4}\s+(?:study|report|survey))\s+(?:by|from|conducted)",
    r"\b(?:McKinsey|Gartner|Forrester|Deloitte|BCG|Bain|Accenture)\s+(?:report|study|survey|research|found|estimates)",
    r"(?:research|data|studies)\s+(?:shows?|suggests?|indicates?|reveals?|confirms?)\s+that\s+\d",
]

# Impossible claims about Glad Labs
GLAD_LABS_IMPOSSIBLE = [
    r"(?:Glad Labs|our|we)\s+(?:has|have)\s+(?:been|spent)\s+(?:\w+\s+)*(?:years?|decade)",
    r"(?:Glad Labs|our)\s+(?:team|staff|employees|engineers|developers)\s+of\s+\d{2,}",
    r"(?:Glad Labs|our)\s+(?:clients?|customers?|users?)\s+(?:include|such as|like)\s+[A-Z]",
    r"(?:Glad Labs|we)\s+(?:processed|handled|served|generated)\s+(?:\d+\s*(?:million|billion|thousand))",
    r"(?:Glad Labs|our)\s+(?:revenue|profit|valuation|funding)",
]

# Fabricated quote patterns
FAKE_QUOTE_PATTERNS = [
    r'["\u201c][^"\u201d]{10,200}["\u201d]\s*(?:says?|said|explains?|explained|recalls?|recalled|notes?|noted|adds?|added)\s+[A-Z][a-z]+',
    r'(?:says?|said|explains?|recalled)\s+[A-Z][a-z]+\s+[A-Z][a-z]+(?:,\s*(?:CEO|CTO|VP|founder|director))',
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

    # 3. Check for impossible Glad Labs claims
    issues.extend(_check_patterns(
        full_text, GLAD_LABS_IMPOSSIBLE, "critical", "glad_labs_claim",
        "Impossible claim about Glad Labs: '{matched}'"
    ))

    # 4. Check for fabricated quotes
    issues.extend(_check_patterns(
        full_text, FAKE_QUOTE_PATTERNS, "critical", "fake_quote",
        "Fabricated quote detected: '{matched}'"
    ))

    # 5. Check title for impossible claims (numeric and written-out years)
    WRITTEN_YEARS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    for word, num in WRITTEN_YEARS.items():
        if re.search(rf"\b{word}\s+years?\b", title, re.IGNORECASE) and num > 1:
            issues.append(ValidationIssue(
                severity="critical", category="glad_labs_claim",
                description=f"Title claims {word} years — Glad Labs is {GLAD_LABS_FACTS['age_months']} months old",
                matched_text=title,
            ))
    if re.search(r"\d+\s*years?", title, re.IGNORECASE):
        match = re.search(r"(\d+)\s*years?", title, re.IGNORECASE)
        years = int(match.group(1)) if match else 0
        if years > 1:
            issues.append(ValidationIssue(
                severity="critical",
                category="glad_labs_claim",
                description=f"Title claims {years} years — Glad Labs is {GLAD_LABS_FACTS['age_months']} months old",
                matched_text=title,
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
