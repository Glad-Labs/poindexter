#!/usr/bin/env python3
"""
Blog Post Quality Validator and Improver

Validates blog posts for quality issues and suggests improvements.
Integrated with Glad Labs content generation pipeline.
"""

import re
import sys
from typing import Dict, List, Tuple, Any

class BlogQualityValidator:
    """Validates blog post content for quality issues"""
    
    def __init__(self, min_word_count: int = 500):
        self.min_word_count = min_word_count
        self.issues = []
        self.warnings = []
        self.quality_score = 100
        
    def validate(self, content: str, title: str = "") -> Tuple[int, Dict[str, Any]]:
        """
        Validate blog post content
        
        Args:
            content: Full blog post markdown content
            title: Blog post title
            
        Returns:
            Tuple of (quality_score, detailed_report)
        """
        self.issues = []
        self.warnings = []
        self.quality_score = 100
        
        # Run all validation checks
        self._check_word_count(content)
        self._check_template_variables(content)
        self._check_sentence_completion(content)
        self._check_orphaned_text(content)
        self._check_citations(content)
        self._check_section_structure(content)
        self._check_topic_coherence(content, title)
        self._check_formatting(content)
        
        return self.quality_score, self._generate_report()
    
    def _check_word_count(self, content: str):
        """Check if content meets minimum word count"""
        word_count = len(content.split())
        
        if word_count < self.min_word_count:
            deduction = (self.min_word_count - word_count) * 0.1
            deduction = min(deduction, 15)  # Max 15 point deduction
            self.quality_score -= deduction
            self.warnings.append(
                f"Word count is {word_count} (target: {self.min_word_count}). "
                f"Consider adding {self.min_word_count - word_count} more words."
            )
        elif word_count > 5000:
            self.warnings.append(
                f"Content is quite long ({word_count} words). "
                f"Consider breaking into multiple articles for better readability."
            )
    
    def _check_template_variables(self, content: str):
        """Check for unresolved template variables"""
        # Check for common template patterns
        patterns = [
            (r'\{\{.*?\}\}', 'Double brace template variable'),
            (r'\{.*?\}', 'Single brace template variable'),
            (r'\[BLANK\]', 'Blank placeholder text'),
            (r'\[\w+\]', 'Bracket placeholder'),
        ]
        
        for pattern, desc in patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                self.quality_score -= 20
                self.issues.append(
                    f"Found {len(matches)} unresolved {desc}: {matches[0]}"
                )
    
    def _check_sentence_completion(self, content: str):
        """Check for incomplete sentences"""
        # Check for sentences ending with incomplete words (e.g., "effectively.")
        incomplete_patterns = [
            r'\s+[a-z]+ly\.$',  # Incomplete adverbs
            r'\s+designed to\.$',  # Incomplete purpose
            r'to\s+\.$',  # Incomplete infinitive
            r'is\s+\.$',  # Incomplete predicate
        ]
        
        lines = content.split('\n')
        incomplete_count = 0
        
        for line in lines:
            for pattern in incomplete_patterns:
                if re.search(pattern, line):
                    incomplete_count += 1
                    self.issues.append(f"Incomplete sentence: '{line.strip()}'")
        
        if incomplete_count > 0:
            self.quality_score -= (incomplete_count * 5)
    
    def _check_orphaned_text(self, content: str):
        """Check for orphaned text (broken references)"""
        patterns = [
            (r'and its relevance to \.$', 'Orphaned reference with no context'),
            (r'\s+\.$', 'Sentence ending with just period'),
            (r'^\s*For\s*$', 'Incomplete section header'),
            (r'and\s+\.$', 'Incomplete conjunction'),
        ]
        
        orphaned_count = 0
        lines = content.split('\n')
        
        for i, line in enumerate(lines):
            for pattern, desc in patterns:
                if re.search(pattern, line):
                    orphaned_count += 1
                    self.issues.append(
                        f"Line {i+1}: Orphaned text - {desc}: '{line.strip()}'"
                    )
        
        if orphaned_count > 0:
            self.quality_score -= (orphaned_count * 5)
    
    def _check_citations(self, content: str):
        """Check for incomplete citations"""
        # Look for empty citations ()
        empty_citations = re.findall(r'\(\s*\)', content)
        
        if empty_citations:
            self.quality_score -= 10
            self.issues.append(
                f"Found {len(empty_citations)} empty citations: () with no source"
            )
        
        # Check for references without parentheses
        reference_pattern = r'([A-Z][A-Za-z\s]+)(?:\n|$)'
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if line.startswith('- ') and len(line) > 100:
                if not re.search(r'\([^)]*\)', line):
                    self.warnings.append(
                        f"Line {i+1}: Reference may be missing citation: {line[:80]}"
                    )
    
    def _check_section_structure(self, content: str):
        """Check for proper section structure"""
        # Count headings
        headings = re.findall(r'^#+\s+', content, re.MULTILINE)
        
        if len(headings) < 3:
            self.warnings.append(
                f"Article has only {len(headings)} sections. "
                f"Consider adding more headings for better structure."
            )
        
        # Check for empty sections (heading followed by another heading)
        if re.search(r'^#+\s+.*?\n#+\s+', content, re.MULTILINE):
            self.quality_score -= 5
            self.warnings.append(
                "Found consecutive headings with no content between them"
            )
        
        # Check for sections with only one line
        sections = re.split(r'^#+\s+', content, flags=re.MULTILINE)
        for section in sections[1:]:  # Skip first element (before first heading)
            lines = [l for l in section.split('\n') if l.strip()]
            if len(lines) < 2:
                self.quality_score -= 3
    
    def _check_topic_coherence(self, content: str, title: str):
        """Check if content matches the title topic"""
        if not title:
            return
        
        # Extract key words from title (ignore common words)
        title_words = set(
            w.lower() for w in title.split() 
            if len(w) > 4 and w not in ['about', 'this', 'that', 'with', 'from']
        )
        
        # Check if title words appear in content
        content_lower = content.lower()
        coverage = sum(1 for word in title_words if word in content_lower) / max(len(title_words), 1)
        
        if coverage < 0.5 and len(title_words) > 2:
            self.quality_score -= 15
            self.issues.append(
                f"Topic mismatch: Title words appear in only {coverage*100:.0f}% of content. "
                f"Content may not match the title."
            )
        elif coverage < 0.8:
            self.warnings.append(
                f"Topic coherence could be improved. "
                f"Title concepts appear in {coverage*100:.0f}% of content."
            )
    
    def _check_formatting(self, content: str):
        """Check for formatting issues"""
        # Check for proper markdown
        if not re.search(r'^#+\s+', content, re.MULTILINE):
            self.warnings.append("No markdown headings found. Consider adding structure with #, ##, etc.")
        
        # Check for proper list formatting
        if re.search(r'\n\s{2,}[-\*]', content):
            self.warnings.append("Indented lists found. Consider using consistent formatting.")
        
        # Check for code blocks
        if '```' in content:
            code_blocks = re.findall(r'```[\s\S]*?```', content)
            for block in code_blocks:
                if not re.match(r'```\w+\n', block):
                    self.warnings.append(
                        "Code block found without language specification. "
                        "Add language after opening ```: ```python"
                    )
    
    def _generate_report(self) -> Dict[str, Any]:
        """Generate detailed quality report"""
        return {
            'quality_score': max(0, self.quality_score),
            'grade': self._calculate_grade(),
            'issues': self.issues,
            'warnings': self.warnings,
            'recommendation': self._get_recommendation(),
        }
    
    def _calculate_grade(self) -> str:
        """Calculate letter grade based on score"""
        score = max(0, self.quality_score)
        if score >= 90:
            return 'A'
        elif score >= 80:
            return 'B'
        elif score >= 70:
            return 'C'
        elif score >= 60:
            return 'D'
        else:
            return 'F'
    
    def _get_recommendation(self) -> str:
        """Get publication recommendation"""
        if self.quality_score >= 80:
            return "✅ READY TO PUBLISH"
        elif self.quality_score >= 60:
            return "⚠️  NEEDS REVIEW - Fix issues before publishing"
        else:
            return "❌ NOT READY - Requires major revision or rewrite"


def print_quality_report(title: str, content: str, verbose: bool = True):
    """Print formatted quality report"""
    validator = BlogQualityValidator()
    score, report = validator.validate(content, title)
    
    print(f"\n{'='*70}")
    print(f"📊 BLOG POST QUALITY ASSESSMENT")
    print(f"{'='*70}")
    print(f"Title: {title}")
    print(f"Score: {score:.1f}/100 | Grade: {report['grade']}")
    print(f"Status: {report['recommendation']}")
    print(f"{'='*70}\n")
    
    if report['issues']:
        print("🔴 CRITICAL ISSUES (Must Fix):")
        for i, issue in enumerate(report['issues'], 1):
            print(f"  {i}. {issue}")
        print()
    
    if report['warnings']:
        print("🟡 WARNINGS (Consider Fixing):")
        for i, warning in enumerate(report['warnings'], 1):
            print(f"  {i}. {warning}")
        print()
    
    if not report['issues']:
        print("✅ No critical issues found!\n")
    
    return score, report


def validate_article_file(filepath: str):
    """Validate an article file"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract title from first heading or filename
        match = re.search(r'^#+\s+(.+)$', content, re.MULTILINE)
        title = match.group(1) if match else filepath.split('/')[-1]
        
        score, report = print_quality_report(title, content)
        return score, report
        
    except FileNotFoundError:
        print(f"❌ Error: File not found: {filepath}")
        return 0, {}
    except Exception as e:
        print(f"❌ Error reading file: {e}")
        return 0, {}


if __name__ == "__main__":
    # Example usage
    print("\n🧪 Blog Post Quality Validator\n")
    
    # Test with problematic content
    bad_content = """
# Making delicious muffins

## Introduction

This article explores the key aspects of Making delicious muffins and its relevance to .
We'll cover the essential information you need to know about this important subject.

## Understanding Making delicious muffins

Making delicious muffins is a crucial area that impacts many aspects of modern business
and personal development. By understanding , professionals and enthusiasts can make more
informed decisions and stay ahead of industry trends.

## Best Practices

When implementing strategies related to Making delicious muffins, you should:

- Research thoroughly before making decisions related to
- Stay updated with the latest developments in Making delicious muffins
"""
    
    print("Testing with problematic content...")
    score, report = print_quality_report(
        "Making delicious muffins",
        bad_content,
        verbose=True
    )
    
    # Test with good content
    good_content = """
# PC Cooling and Its Importance to Performance

## Introduction

When it comes to building or upgrading your computer, proper cooling is essential for
maintaining optimal performance and preventing hardware failure. This comprehensive guide
explores the importance of PC cooling and available options.

## Why is PC Cooling Important?

PC cooling is essential for several reasons:

- **Performance**: Temperature affects component speed and can lead to throttling
- **Longevity**: Proper cooling extends the lifespan of your components  
- **Efficiency**: Optimal temperatures ensure better efficiency and performance
- **Reliability**: Prevents hardware failure and system instability

## Types of PC Cooling Systems

There are several cooling options available:

- **Air Cooling**: Most common type, uses fans for heat dissipation
- **Liquid Cooling**: More efficient, uses liquid to carry heat
- **AIO Cooling**: Combines both types into a single unit

## Choosing the Right System

Consider these factors when selecting a cooling system:

1. Your performance needs and budget
2. Available space in your case
3. Cooling capacity required for your components
4. Long-term maintenance requirements

## Conclusion

Proper PC cooling is essential for optimal computer performance and longevity.
By understanding your cooling options and selecting the right system for your needs,
you can ensure your computer operates at its best.
"""
    
    print("\n\nTesting with good content...")
    score, report = print_quality_report(
        "PC Cooling and Its Importance to Performance",
        good_content,
        verbose=True
    )
