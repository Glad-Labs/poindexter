"""
Content Structure Validator Service

Provides validation for:
- Heading hierarchy (H1 → H2 → H3)
- Paragraph length and organization
- Section word counts
- Forbidden/generic heading titles
- Content flow and structure
"""

import re
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ParagraphValidation:
    """Validation for a paragraph"""
    index: int
    sentence_count: int
    word_count: int
    text_preview: str  # First 50 chars
    is_orphan: bool  # Single sentence (< 2 sentences)
    is_bloated: bool  # Too many sentences (> 10)
    issues: List[str]


@dataclass
class SectionValidation:
    """Validation for a section (heading + content)"""
    heading_level: int
    heading_text: str
    heading_index: int
    word_count: int
    paragraph_count: int
    min_length_met: bool  # >= 100 words
    paragraphs: List[ParagraphValidation]
    issues: List[str]


@dataclass
class ContentStructureResult:
    """Complete structure validation result"""
    is_valid: bool
    heading_hierarchy_valid: bool
    no_forbidden_titles: bool
    all_sections_adequate: bool
    no_orphan_paragraphs: bool

    sections: List[SectionValidation]
    total_sections: int
    orphan_paragraph_count: int
    bloated_paragraph_count: int

    errors: List[str]
    warnings: List[str]
    suggestions: List[str]


class ContentStructureValidator:
    """
    Validates content structure and organization
    """

    # Constraints
    MIN_SECTION_WORDS = 100  # Minimum words per section
    IDEAL_PARAGRAPH_SENTENCES_MIN = 2  # Avoid orphans (single sentence)
    IDEAL_PARAGRAPH_SENTENCES_MAX = 10  # Avoid walls-of-text
    IDEAL_SENTENCE_LENGTH_MIN = 8  # Words per sentence
    IDEAL_SENTENCE_LENGTH_MAX = 25  # Words per sentence

    # Forbidden heading titles (generic, templated)
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

    def validate(self, content: str) -> ContentStructureResult:
        """
        Validate content structure

        Args:
            content: Full blog post content (markdown)

        Returns:
            ContentStructureResult with validation status and details
        """
        errors = []
        warnings = []
        suggestions = []

        # Extract headings and sections
        sections = self._extract_sections(content)

        if not sections:
            errors.append("No heading structure found in content")
            return ContentStructureResult(
                is_valid=False,
                heading_hierarchy_valid=False,
                no_forbidden_titles=False,
                all_sections_adequate=False,
                no_orphan_paragraphs=False,
                sections=[],
                total_sections=0,
                orphan_paragraph_count=0,
                bloated_paragraph_count=0,
                errors=errors,
                warnings=warnings,
                suggestions=suggestions,
            )

        # Validate heading hierarchy
        hierarchy_valid, hierarchy_errors = self._validate_heading_hierarchy(
            sections
        )
        if not hierarchy_valid:
            errors.extend(hierarchy_errors)

        # Check for forbidden headings
        forbidden_found = []
        for section in sections:
            if section.heading_text.lower() in self.FORBIDDEN_HEADINGS:
                forbidden_found.append(section.heading_text)
                errors.append(
                    f"Generic heading title detected: '{section.heading_text}'. "
                    f"Use specific, benefit-focused headings instead."
                )

        # Validate section lengths
        sections_adequate = True
        for section in sections:
            if section.word_count < self.MIN_SECTION_WORDS and section.heading_level > 0:
                # Skip H0 (intro), but check others
                if section.heading_level >= 2:
                    sections_adequate = False
                    errors.append(
                        f"Section '{section.heading_text}' too short: "
                        f"{section.word_count} words (min {self.MIN_SECTION_WORDS})"
                    )

        # Count orphan paragraphs
        orphan_count = sum(
            sum(1 for p in s.paragraphs if p.is_orphan) for s in sections
        )
        bloated_count = sum(
            sum(1 for p in s.paragraphs if p.is_bloated) for s in sections
        )

        if orphan_count > 0:
            warnings.append(
                f"Found {orphan_count} orphan paragraphs (single sentence). "
                f"Merge these with adjacent paragraphs or expand content."
            )

        if bloated_count > 0:
            warnings.append(
                f"Found {bloated_count} bloated paragraphs (>10 sentences). "
                f"Consider breaking these into smaller paragraphs."
            )

        # Validate paragraph lengths within sections
        for section in sections:
            for para in section.paragraphs:
                if para.is_orphan:
                    section.issues.append(
                        f"Orphan paragraph (single sentence): {para.text_preview}..."
                    )
                if para.is_bloated:
                    section.issues.append(
                        f"Bloated paragraph ({para.sentence_count} sentences): "
                        f"{para.text_preview}..."
                    )

        # Determine overall validity
        is_valid = len(errors) == 0

        # Generate suggestions
        if not is_valid:
            suggestions = self._generate_suggestions(
                hierarchy_valid,
                len(forbidden_found) > 0,
                sections_adequate,
                orphan_count,
                bloated_count,
                sections,
            )

        return ContentStructureResult(
            is_valid=is_valid,
            heading_hierarchy_valid=hierarchy_valid,
            no_forbidden_titles=len(forbidden_found) == 0,
            all_sections_adequate=sections_adequate,
            no_orphan_paragraphs=orphan_count == 0,
            sections=sections,
            total_sections=len(sections),
            orphan_paragraph_count=orphan_count,
            bloated_paragraph_count=bloated_count,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
        )

    def _extract_sections(self, content: str) -> List[SectionValidation]:
        """
        Extract sections from content based on headings

        Returns:
            List of sections with their content
        """
        # Find all headings
        heading_pattern = r'^(#+)\s+(.+?)$'
        headings = []
        for match in re.finditer(heading_pattern, content, re.MULTILINE):
            level = len(match.group(1))
            text = match.group(2).strip()
            start_pos = match.start()
            headings.append((level, text, start_pos))

        if not headings:
            return []

        # Extract content for each section
        sections = []
        for i, (level, text, start_pos) in enumerate(headings):
            # Find end position (start of next heading or end of content)
            if i + 1 < len(headings):
                end_pos = headings[i + 1][2]
            else:
                end_pos = len(content)

            # Extract section content
            section_content = content[start_pos:end_pos]

            # Remove the heading itself
            section_lines = section_content.split('\n')[1:]
            section_text = '\n'.join(section_lines).strip()

            # Validate paragraphs in section
            paragraphs = self._validate_paragraphs(section_text)

            word_count = len(section_text.split())

            section = SectionValidation(
                heading_level=level,
                heading_text=text,
                heading_index=i,
                word_count=word_count,
                paragraph_count=len(paragraphs),
                min_length_met=word_count >= self.MIN_SECTION_WORDS,
                paragraphs=paragraphs,
                issues=[],
            )

            sections.append(section)

        return sections

    def _validate_paragraphs(self, text: str) -> List[ParagraphValidation]:
        """
        Split text into paragraphs and validate each

        Args:
            text: Section text (without heading)

        Returns:
            List of paragraph validations
        """
        # Split by double newlines (paragraph breaks)
        raw_paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

        paragraphs = []
        for i, para_text in enumerate(raw_paragraphs):
            # Skip list items and other non-prose
            if para_text.startswith(('-', '*', '1.', '2.', '3.')):
                continue

            # Count sentences
            sentences = re.split(r'[.!?]+', para_text)
            sentences = [s.strip() for s in sentences if s.strip()]
            sentence_count = len(sentences)

            word_count = len(para_text.split())

            is_orphan = sentence_count < self.IDEAL_PARAGRAPH_SENTENCES_MIN
            is_bloated = sentence_count > self.IDEAL_PARAGRAPH_SENTENCES_MAX

            issues = []
            if is_orphan:
                issues.append(f"Orphan paragraph (single sentence)")
            if is_bloated:
                issues.append(f"Bloated paragraph ({sentence_count} sentences)")

            para = ParagraphValidation(
                index=i,
                sentence_count=sentence_count,
                word_count=word_count,
                text_preview=para_text[:50],
                is_orphan=is_orphan,
                is_bloated=is_bloated,
                issues=issues,
            )

            paragraphs.append(para)

        return paragraphs

    def _validate_heading_hierarchy(
        self, sections: List[SectionValidation]
    ) -> Tuple[bool, List[str]]:
        """
        Validate heading hierarchy (no skips, proper nesting)

        Returns:
            (is_valid, error_list)
        """
        errors = []

        if not sections:
            return False, ["No headings found"]

        # Check first heading is H1
        if sections[0].heading_level != 1:
            errors.append("First heading must be H1 (#)")

        # Check for single H1
        h1_count = sum(1 for s in sections if s.heading_level == 1)
        if h1_count > 1:
            errors.append(f"Multiple H1 headings found ({h1_count}), should be unique")

        # Check hierarchy progression
        for i in range(len(sections) - 1):
            curr_level = sections[i].heading_level
            next_level = sections[i + 1].heading_level

            # Can't skip levels (H1 → H3 not OK, H1 → H2 OK)
            if next_level > curr_level and (next_level - curr_level) > 1:
                errors.append(
                    f"Heading hierarchy skip: H{curr_level} '{sections[i].heading_text}' "
                    f"→ H{next_level} '{sections[i + 1].heading_text}'. "
                    f"Use H{curr_level + 1} instead."
                )

        is_valid = len(errors) == 0
        return is_valid, errors

    def _generate_suggestions(
        self,
        hierarchy_valid: bool,
        has_forbidden: bool,
        sections_adequate: bool,
        orphan_count: int,
        bloated_count: int,
        sections: List[SectionValidation],
    ) -> List[str]:
        """Generate specific improvement suggestions"""
        suggestions = []

        if not hierarchy_valid:
            suggestions.append(
                "Fix heading hierarchy: ensure H1 is first and unique, "
                "and don't skip levels (H1 → H2, not H1 → H3)"
            )

        if has_forbidden:
            suggestions.append(
                "Replace generic headings with specific, benefit-focused titles. "
                "Examples: 'Why This Matters' instead of 'Introduction'; "
                "'Key Takeaways' instead of 'Conclusion'"
            )

        if not sections_adequate:
            suggestions.append(
                "Expand sections with <100 words. Add examples, context, or break "
                "content into more detailed subsections."
            )

        if orphan_count > 0:
            suggestions.append(
                f"Merge {orphan_count} single-sentence paragraphs with adjacent "
                f"content or expand them into full paragraphs."
            )

        if bloated_count > 0:
            suggestions.append(
                f"Break {bloated_count} long paragraphs (>10 sentences) into "
                f"smaller, more readable sections."
            )

        return suggestions
