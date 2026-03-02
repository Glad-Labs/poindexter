"""
SEO Validation Service

Provides hard validation constraints for:
- Keyword density and placement
- SEO title and meta description length
- URL slug format and length
- Heading structure validation
"""

import re
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class KeywordDensityStatus(Enum):
    """Status of keyword density validation"""
    TOO_LOW = "too_low"        # < 0.5%
    OPTIMAL = "optimal"         # 0.5% - 3%
    TOO_HIGH = "too_high"       # > 3%


@dataclass
class KeywordValidation:
    """Validation result for a single keyword"""
    keyword: str
    appears_in_content: bool
    density: float  # percentage
    status: KeywordDensityStatus
    appearances: int
    reason: str = ""


@dataclass
class SEOValidationResult:
    """Complete SEO validation result"""
    is_valid: bool
    title_valid: bool
    meta_valid: bool
    slug_valid: bool
    h1_valid: bool
    keywords_valid: bool
    primary_keyword_placed: bool

    keyword_validations: List[KeywordValidation]
    title_char_count: int
    meta_char_count: int
    h1_text: Optional[str]

    errors: List[str]
    warnings: List[str]
    suggestions: List[str]


class SEOValidator:
    """
    Validates SEO metadata against hard constraints
    """

    # Constraints
    TITLE_MAX_CHARS = 60
    META_MAX_CHARS = 155
    SLUG_MAX_CHARS = 75
    KEYWORD_DENSITY_MIN = 0.5  # percent
    KEYWORD_DENSITY_MAX = 3.0  # percent
    PRIMARY_KEYWORD_WORD_LIMIT = 100  # Check in first N words

    # Forbidden heading titles
    FORBIDDEN_HEADINGS = {
        "introduction",
        "background",
        "overview",
        "summary",
        "conclusion",
        "the end",
        "wrap-up",
        "closing",
        "final thoughts",
        "ending",
        "epilogue",
        "what's next",
    }

    def __init__(self):
        self.logger = logger

    def validate(
        self,
        content: str,
        title: str,
        meta_description: str,
        keywords: List[str],
        primary_keyword: Optional[str] = None,
        slug: Optional[str] = None,
    ) -> SEOValidationResult:
        """
        Validate all SEO elements

        Args:
            content: Full blog post content (markdown)
            title: SEO title
            meta_description: Meta description for search results
            keywords: List of SEO keywords
            primary_keyword: Primary keyword to check placement
            slug: URL slug to validate

        Returns:
            SEOValidationResult with validation status and details
        """
        errors = []
        warnings = []
        suggestions = []

        # Validate title
        title_valid = len(title) <= self.TITLE_MAX_CHARS
        if not title_valid:
            errors.append(
                f"Title too long: {len(title)} chars (max {self.TITLE_MAX_CHARS})"
            )

        # Validate meta description
        meta_valid = len(meta_description) <= self.META_MAX_CHARS
        if not meta_valid:
            errors.append(
                f"Meta description too long: {len(meta_description)} chars "
                f"(max {self.META_MAX_CHARS})"
            )

        # Validate slug
        slug_valid = True
        if slug:
            slug_valid = self._validate_slug(slug)
            if not slug_valid:
                errors.append(f"Slug format invalid: {slug}")

        # Extract H1 and validate
        h1_text = self._extract_h1(content)
        h1_valid = h1_text is not None
        if not h1_valid:
            errors.append("No H1 heading found in content")

        # Validate keywords
        content_lower = content.lower()
        word_count = len(content.split())

        keyword_validations = []
        keywords_all_valid = True

        for keyword in keywords:
            validation = self._validate_keyword(
                keyword, content_lower, word_count
            )
            keyword_validations.append(validation)

            if not validation.appears_in_content:
                keywords_all_valid = False
                errors.append(
                    f"Keyword not found in content: '{keyword}'"
                )
            elif validation.status == KeywordDensityStatus.TOO_LOW:
                warnings.append(
                    f"Keyword '{keyword}' density too low: "
                    f"{validation.density:.2f}% (recommended 0.5-3%)"
                )
            elif validation.status == KeywordDensityStatus.TOO_HIGH:
                errors.append(
                    f"Keyword '{keyword}' density too high: "
                    f"{validation.density:.2f}% (recommended 0.5-3%)"
                )

        # Check primary keyword placement if provided
        primary_keyword_placed = True
        if primary_keyword:
            primary_keyword_placed = self._validate_primary_keyword_placement(
                primary_keyword, content, title, h1_text
            )
            if not primary_keyword_placed:
                warnings.append(
                    f"Primary keyword '{primary_keyword}' not found in "
                    f"title or first {self.PRIMARY_KEYWORD_WORD_LIMIT} words"
                )

        # Validate heading hierarchy
        hierarchy_valid, hierarchy_errors = self._validate_heading_hierarchy(
            content
        )
        if not hierarchy_valid:
            errors.extend(hierarchy_errors)

        # Check for forbidden headings
        forbidden_found = self._check_forbidden_headings(content)
        if forbidden_found:
            for heading in forbidden_found:
                errors.append(
                    f"Generic heading title detected: '{heading}'. "
                    f"Use specific, benefit-focused headings instead."
                )

        # Determine overall validity
        is_valid = (
            title_valid
            and meta_valid
            and slug_valid
            and h1_valid
            and keywords_all_valid
            and len(errors) == 0
        )

        # Generate suggestions
        if not is_valid:
            suggestions = self._generate_suggestions(
                title_valid, meta_valid, slug_valid, h1_valid,
                keyword_validations, forbidden_found
            )

        return SEOValidationResult(
            is_valid=is_valid,
            title_valid=title_valid,
            meta_valid=meta_valid,
            slug_valid=slug_valid,
            h1_valid=h1_valid,
            keywords_valid=keywords_all_valid,
            primary_keyword_placed=primary_keyword_placed,
            keyword_validations=keyword_validations,
            title_char_count=len(title),
            meta_char_count=len(meta_description),
            h1_text=h1_text,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
        )

    def _validate_keyword(
        self, keyword: str, content_lower: str, word_count: int
    ) -> KeywordValidation:
        """
        Validate a single keyword's presence and density
        """
        keyword_lower = keyword.lower()

        # Count appearances (phrase matching)
        pattern = r'\b' + re.escape(keyword_lower) + r'\b'
        appearances = len(re.findall(pattern, content_lower))

        if appearances == 0:
            return KeywordValidation(
                keyword=keyword,
                appears_in_content=False,
                density=0.0,
                status=KeywordDensityStatus.TOO_LOW,
                appearances=0,
                reason="Keyword not found in content"
            )

        # Calculate density (keyword frequency / total words * 100)
        density = (appearances / word_count * 100) if word_count > 0 else 0

        if density < self.KEYWORD_DENSITY_MIN:
            status = KeywordDensityStatus.TOO_LOW
        elif density > self.KEYWORD_DENSITY_MAX:
            status = KeywordDensityStatus.TOO_HIGH
        else:
            status = KeywordDensityStatus.OPTIMAL

        return KeywordValidation(
            keyword=keyword,
            appears_in_content=True,
            density=density,
            status=status,
            appearances=appearances,
            reason=f"Density: {density:.2f}%"
        )

    def _validate_slug(self, slug: str) -> bool:
        """
        Validate slug format:
        - Max 75 chars
        - Lowercase alphanumeric and hyphens only
        - No consecutive hyphens
        - Can't start or end with hyphen
        """
        if len(slug) > self.SLUG_MAX_CHARS:
            return False

        # Pattern: starts/ends with alphanumeric, contains alphanumeric and hyphens
        pattern = r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'
        return bool(re.match(pattern, slug))

    def _extract_h1(self, content: str) -> Optional[str]:
        """Extract H1 heading text, if it exists"""
        match = re.search(r'^#\s+(.+?)$', content, re.MULTILINE)
        if match:
            return match.group(1).strip()
        return None

    def _validate_primary_keyword_placement(
        self,
        primary_keyword: str,
        content: str,
        title: str,
        h1_text: Optional[str],
    ) -> bool:
        """
        Check if primary keyword appears in:
        1. Title, OR
        2. H1, OR
        3. First 100 words of content
        """
        pk_lower = primary_keyword.lower()

        # Check title
        if pk_lower in title.lower():
            return True

        # Check H1
        if h1_text and pk_lower in h1_text.lower():
            return True

        # Check first N words
        first_words = " ".join(content.split()[:self.PRIMARY_KEYWORD_WORD_LIMIT])
        if pk_lower in first_words.lower():
            return True

        return False

    def _validate_heading_hierarchy(
        self, content: str
    ) -> Tuple[bool, List[str]]:
        """
        Validate heading hierarchy (H1 → H2 → H3, no skips)

        Returns:
            (is_valid, error_list)
        """
        errors = []

        # Find all headings with their levels
        heading_pattern = r'^(#+)\s+(.+?)$'
        headings = [
            (len(m.group(1)), m.group(2).strip())
            for m in re.finditer(heading_pattern, content, re.MULTILINE)
        ]

        if not headings:
            return True, []  # No headings to validate

        # Check H1 exists and is first
        if headings[0][0] != 1:
            errors.append("First heading must be H1 (#)")

        # Check for single H1
        h1_count = sum(1 for level, _ in headings if level == 1)
        if h1_count > 1:
            errors.append(f"Multiple H1 headings found ({h1_count}), should be unique")

        # Check hierarchy progression (no skips)
        for i in range(len(headings) - 1):
            curr_level, curr_text = headings[i]
            next_level, next_text = headings[i + 1]

            # Can't skip levels when increasing (H1 → H2 OK, H1 → H3 not OK)
            if next_level > curr_level and (next_level - curr_level) > 1:
                errors.append(
                    f"Heading hierarchy skip: H{curr_level} → H{next_level}. "
                    f"Use H{curr_level + 1} instead."
                )

        is_valid = len(errors) == 0
        return is_valid, errors

    def _check_forbidden_headings(self, content: str) -> List[str]:
        """
        Detect forbidden/generic heading titles

        Returns:
            List of forbidden headings found
        """
        heading_pattern = r'^#+\s+(.+?)$'
        found = []

        for match in re.finditer(heading_pattern, content, re.MULTILINE):
            heading_text = match.group(1).strip().lower()

            if heading_text in self.FORBIDDEN_HEADINGS:
                found.append(match.group(1).strip())

        return found

    def _generate_suggestions(
        self,
        title_valid: bool,
        meta_valid: bool,
        slug_valid: bool,
        h1_valid: bool,
        keyword_validations: List[KeywordValidation],
        forbidden_headings: List[str],
    ) -> List[str]:
        """Generate specific suggestions for fixing validation errors"""
        suggestions = []

        if not title_valid:
            suggestions.append(
                f"Shorten SEO title to max {self.TITLE_MAX_CHARS} characters"
            )

        if not meta_valid:
            suggestions.append(
                f"Shorten meta description to max {self.META_MAX_CHARS} characters"
            )

        if not slug_valid:
            suggestions.append(
                "Use lowercase alphanumeric characters and hyphens only in slug. "
                "Max 75 characters, no leading/trailing hyphens."
            )

        if not h1_valid:
            suggestions.append("Add an H1 heading (# Title) at the start of content")

        for kv in keyword_validations:
            if kv.status == KeywordDensityStatus.TOO_LOW:
                suggestions.append(
                    f"Increase mentions of '{kv.keyword}' for better keyword density "
                    f"(current: {kv.density:.2f}%, target: 0.5-3%)"
                )
            elif kv.status == KeywordDensityStatus.TOO_HIGH:
                suggestions.append(
                    f"Reduce mentions of '{kv.keyword}' - keyword stuffing detected "
                    f"(current: {kv.density:.2f}%, target: 0.5-3%)"
                )

        if forbidden_headings:
            suggestions.append(
                f"Replace generic headings {forbidden_headings} with specific, "
                f"benefit-focused titles (e.g., 'Why This Matters', 'Key Takeaways')"
            )

        return suggestions
