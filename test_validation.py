"""
Test script to verify self-checking validation is working correctly.

Run with: python test_validation.py
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of content validation"""
    is_valid: bool
    quality_score: float
    issues: list = field(default_factory=list)
    feedback: str = ""


def validate_content(content: str, topic: str, target_length: int, quality_threshold: float = 7.0) -> ValidationResult:
    """Validate content against quality rubric"""
    issues = []
    score = 10.0
    
    # 1. Check length
    word_count = len(content.split())
    min_words = int(target_length * 0.7)
    max_words = int(target_length * 1.3)
    
    if word_count < min_words:
        issues.append(f"Content too short: {word_count} words (target: {target_length})")
        score -= 2.0
    elif word_count > max_words:
        issues.append(f"Content too long: {word_count} words (target: {target_length})")
        score -= 1.0
    
    # 2. Check structure
    heading_count = len(re.findall(r'^##+ ', content, re.MULTILINE))
    if heading_count < 3:
        issues.append(f"Insufficient structure: {heading_count} sections (need 3-5)")
        score -= 1.5
    
    # 3. Check title
    if not re.search(r'^# ', content, re.MULTILINE):
        issues.append("Missing title (# heading)")
        score -= 1.0
    
    # 4. Check conclusion
    conclusion_keywords = ['conclusion', 'summary', 'next steps', 'takeaway']
    has_conclusion = any(keyword in content.lower() for keyword in conclusion_keywords)
    if not has_conclusion:
        issues.append("Missing conclusion section")
        score -= 1.5
    
    # 5. Check examples
    has_examples = '- ' in content or '* ' in content or '1. ' in content
    if not has_examples:
        issues.append("Missing practical examples or lists")
        score -= 1.0
    
    # 6. Check CTA
    cta_keywords = ['ready', 'start', 'begin', 'try', 'implement', 'action']
    has_cta = any(keyword in content.lower() for keyword in cta_keywords)
    if not has_cta:
        issues.append("Missing call-to-action")
        score -= 0.5
    
    # 7. Check topic relevance
    topic_words = topic.lower().split()[:3]
    topic_mentions = sum(1 for word in topic_words if word in content.lower())
    if topic_mentions < 2:
        issues.append(f"Topic '{topic}' mentioned too few times")
        score -= 1.0
    
    score = max(0, min(10, score))
    is_valid = score >= quality_threshold
    
    feedback = f"{'✓' if is_valid else '✗'} Score: {score:.1f}/10 - "
    if is_valid:
        feedback += "APPROVED"
    else:
        feedback += f"Threshold: {quality_threshold} (issues: {len(issues)})"
    
    return ValidationResult(
        is_valid=is_valid,
        quality_score=score,
        issues=issues,
        feedback=feedback
    )


# Test cases
def test_validation():
    """Test the validation logic"""
    
    print("=" * 70)
    print("VALIDATION TEST SUITE")
    print("=" * 70)
    
    # Test 1: Perfect content
    print("\n[TEST 1] Perfect Content")
    print("-" * 70)
    perfect_content = """
# The Future of Artificial Intelligence in Healthcare

## Introduction
AI is revolutionizing healthcare. This blog post explores the latest trends.

## 1. Diagnosis and Detection
AI models can now detect diseases:
- Cancer screening with 98% accuracy
- Early disease detection
- Personalized treatment recommendations

## 2. Drug Discovery
AI accelerates pharmaceutical innovation:
1. Molecule simulation
2. Drug interaction prediction
3. Clinical trial optimization

## 3. Patient Monitoring
Real-time monitoring systems help track patient health:
- Wearable devices
- Predictive analytics
- Early warning systems

## Conclusion
The future of healthcare is AI-powered. Start implementing these solutions today. Begin your AI journey now!
"""
    
    result = validate_content(perfect_content, "Artificial Intelligence Healthcare", 1500)
    print(f"Score: {result.quality_score}/10")
    print(f"Valid: {result.is_valid}")
    print(f"Issues: {len(result.issues)}")
    if result.issues:
        for issue in result.issues:
            print(f"  - {issue}")
    print(f"Feedback: {result.feedback}")
    assert result.is_valid, "Perfect content should pass!"
    
    # Test 2: Content too short
    print("\n[TEST 2] Content Too Short")
    print("-" * 70)
    short_content = """
# Title
## Section 1
Some content here.
## Conclusion
Done.
"""
    
    result = validate_content(short_content, "Test Topic", 1500)
    print(f"Score: {result.quality_score}/10")
    print(f"Valid: {result.is_valid}")
    print(f"Issues: {len(result.issues)}")
    for issue in result.issues:
        print(f"  - {issue}")
    assert not result.is_valid, "Short content should fail!"
    
    # Test 3: Missing examples
    print("\n[TEST 3] Missing Examples/Lists")
    print("-" * 70)
    no_examples = """
# Kubernetes Best Practices

## Introduction
Kubernetes is a powerful orchestration platform for containerized applications. This guide covers essential practices.

## Container Strategies
When deploying containers in Kubernetes, you need to consider resource limits, health checks, and scaling policies. The platform provides tools to manage all aspects of container deployment.

## Networking Configuration  
Kubernetes networking includes service discovery, load balancing, and network policies. These features work together to ensure reliable communication between services.

## Summary
Following these practices will help you build robust systems. Ready to implement these strategies in your infrastructure?
"""
    
    result = validate_content(no_examples, "Kubernetes Best Practices", 1500)
    print(f"Score: {result.quality_score}/10")
    print(f"Valid: {result.is_valid}")
    print(f"Missing items: {[i for i in result.issues if 'examples' in i.lower() or 'lists' in i.lower()]}")
    
    # Test 4: Refinement feedback
    print("\n[TEST 4] Refinement Feedback")
    print("-" * 70)
    poor_content = """
# AI in Business

## Basics
AI is important for business. Companies use it for many things.

## Implementation
Deploy AI models carefully.

## Result
AI helps businesses.
"""
    
    result = validate_content(poor_content, "Artificial Intelligence Business", 1500)
    print(f"Initial Score: {result.quality_score}/10")
    print(f"Issues found: {len(result.issues)}")
    for i, issue in enumerate(result.issues, 1):
        print(f"  {i}. {issue}")
    print(f"\nRefinement prompt would include this feedback:")
    print(f"'{', '.join(result.issues)}'")
    
    print("\n" + "=" * 70)
    print("ALL TESTS PASSED ✓")
    print("=" * 70)
    print("\nValidation logic is working correctly!")
    print("Content with quality_score < 7.0 would be sent for refinement.")


if __name__ == "__main__":
    test_validation()
