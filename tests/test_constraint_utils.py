"""
Unit tests for constraint_utils - Word Count and Writing Style Management

Tests Tier 1, 2, and 3 constraint handling utilities.
"""

import pytest
from typing import Optional
from utils.constraint_utils import (
    ContentConstraints,
    ConstraintCompliance,
    extract_constraints_from_request,
    count_words_in_content,
    inject_constraints_into_prompt,
    validate_constraints,
    calculate_phase_targets,
    check_tolerance,
    apply_strict_mode,
    merge_compliance_reports,
    auto_trim_content,
    auto_expand_content,
    analyze_style_consistency,
    calculate_cost_impact,
    format_compliance_report
)


# ============================================================================
# TIER 1 TESTS
# ============================================================================

class TestConstraintExtraction:
    """Test extraction of constraints from request data"""
    
    def test_extract_constraints_from_dict(self):
        """Test extracting constraints from dict"""
        request_data = {
            "content_constraints": {
                "word_count": 2000,
                "writing_style": "technical",
                "word_count_tolerance": 15,
                "strict_mode": True
            }
        }
        
        constraints = extract_constraints_from_request(request_data)
        
        assert constraints.word_count == 2000
        assert constraints.writing_style == "technical"
        assert constraints.word_count_tolerance == 15
        assert constraints.strict_mode is True
    
    def test_extract_constraints_with_defaults(self):
        """Test that defaults are applied when constraints not provided"""
        request_data = {}
        
        constraints = extract_constraints_from_request(request_data)
        
        assert constraints.word_count == 1500
        assert constraints.writing_style == "educational"
        assert constraints.word_count_tolerance == 10
        assert constraints.strict_mode is False
    
    def test_extract_constraints_partial(self):
        """Test extracting partial constraints with defaults for missing fields"""
        request_data = {
            "content_constraints": {
                "word_count": 2500
            }
        }
        
        constraints = extract_constraints_from_request(request_data)
        
        assert constraints.word_count == 2500
        assert constraints.writing_style == "educational"  # Default


class TestWordCounting:
    """Test word counting functionality"""
    
    def test_count_empty_string(self):
        """Test counting words in empty string"""
        assert count_words_in_content("") == 0
    
    def test_count_simple_text(self):
        """Test counting words in simple text"""
        text = "Hello world this is a test"
        assert count_words_in_content(text) == 6
    
    def test_count_with_punctuation(self):
        """Test word counting with punctuation"""
        text = "Hello, world! This is a test."
        assert count_words_in_content(text) == 6
    
    def test_count_with_extra_whitespace(self):
        """Test word counting with extra whitespace"""
        text = "Hello    world   this    is    a    test"
        assert count_words_in_content(text) == 6
    
    def test_count_multiline_text(self):
        """Test word counting in multiline text"""
        text = """First line
        Second line
        Third line"""
        assert count_words_in_content(text) == 6


class TestConstraintInjection:
    """Test constraint injection into prompts"""
    
    def test_inject_basic_constraints(self):
        """Test basic constraint injection"""
        prompt = "Write about AI"
        constraints = ContentConstraints(word_count=1500, writing_style="technical")
        
        result = inject_constraints_into_prompt(prompt, constraints)
        
        assert "1500" in result
        assert "technical" in result
        assert "Write about AI" in result
    
    def test_inject_with_phase_target(self):
        """Test constraint injection with phase-specific target"""
        prompt = "Write about AI"
        constraints = ContentConstraints(word_count=1500)
        
        result = inject_constraints_into_prompt(
            prompt, constraints, phase_name="research", word_count_target=300
        )
        
        assert "300" in result
        assert "research" in result
    
    def test_inject_no_constraints(self):
        """Test that None constraints returns original prompt"""
        prompt = "Write about AI"
        constraints_none: Optional[ContentConstraints] = None
        result = inject_constraints_into_prompt(prompt, constraints_none)
        
        assert result == prompt


class TestConstraintValidation:
    """Test constraint validation"""
    
    def test_validate_within_tolerance(self):
        """Test validation when content is within tolerance"""
        content = " ".join(["word"] * 1500)  # 1500 words
        constraints = ContentConstraints(
            word_count=1500,
            word_count_tolerance=10
        )
        
        compliance = validate_constraints(content, constraints)
        
        assert compliance.word_count_actual == 1500
        assert compliance.word_count_target == 1500
        assert compliance.word_count_within_tolerance is True
        assert compliance.violation_message is None
    
    def test_validate_too_short(self):
        """Test validation when content is too short"""
        content = " ".join(["word"] * 1200)  # 1200 words
        constraints = ContentConstraints(
            word_count=1500,
            word_count_tolerance=10
        )
        
        compliance = validate_constraints(content, constraints)
        
        assert compliance.word_count_actual == 1200
        assert compliance.word_count_within_tolerance is False
        assert compliance.violation_message is not None
        assert "too short" in compliance.violation_message.lower()
    
    def test_validate_too_long(self):
        """Test validation when content is too long"""
        content = " ".join(["word"] * 1800)  # 1800 words
        constraints = ContentConstraints(
            word_count=1500,
            word_count_tolerance=10
        )
        
        compliance = validate_constraints(content, constraints)
        
        assert compliance.word_count_actual == 1800
        assert compliance.word_count_within_tolerance is False
        assert compliance.violation_message is not None
        assert "too long" in compliance.violation_message.lower()
    
    def test_validate_with_phase_target(self):
        """Test validation with phase-specific target"""
        content = " ".join(["word"] * 300)  # 300 words
        constraints = ContentConstraints(
            word_count=1500,
            word_count_tolerance=10
        )
        
        compliance = validate_constraints(
            content,
            constraints,
            phase_name="research",
            word_count_target=300
        )
        
        assert compliance.word_count_target == 300
        assert compliance.word_count_within_tolerance is True


# ============================================================================
# TIER 2 TESTS
# ============================================================================

class TestPhaseTargetCalculation:
    """Test calculation of per-phase word count targets"""
    
    def test_calculate_equal_distribution(self):
        """Test that phase targets are distributed equally"""
        constraints = ContentConstraints(word_count=1500)
        
        targets = calculate_phase_targets(1500, constraints, num_phases=5)
        
        assert targets["research"] == 300
        assert targets["creative"] == 300
        assert targets["qa"] == 300
        assert targets["format"] == 300
        assert targets["finalize"] == 300
    
    def test_calculate_with_overrides(self):
        """Test phase targets with per-phase overrides"""
        constraints = ContentConstraints(
            word_count=1500,
            per_phase_overrides={
                "research": 400,
                "creative": 600
            }
        )
        
        targets = calculate_phase_targets(1500, constraints, num_phases=5)
        
        assert targets["research"] == 400
        assert targets["creative"] == 600
        assert targets["qa"] == 300  # Default
    
    def test_calculate_different_total(self):
        """Test with different total word count"""
        constraints = ContentConstraints(word_count=3000)
        
        targets = calculate_phase_targets(3000, constraints, num_phases=5)
        
        assert targets["research"] == 600
        assert targets["creative"] == 600


class TestToleranceCheck:
    """Test tolerance checking"""
    
    def test_within_tolerance(self):
        """Test value within tolerance"""
        is_within, percentage = check_tolerance(1500, 1500, 10)
        
        assert is_within is True
        assert percentage == 0.0
    
    def test_just_within_tolerance(self):
        """Test value just within tolerance bounds"""
        is_within, percentage = check_tolerance(1650, 1500, 10)  # +10% = 1650
        
        assert is_within is True
        assert percentage == 10.0
    
    def test_outside_tolerance_high(self):
        """Test value outside tolerance (too high)"""
        is_within, percentage = check_tolerance(1700, 1500, 10)  # +13%
        
        assert is_within is False
        assert percentage == 13.3
    
    def test_outside_tolerance_low(self):
        """Test value outside tolerance (too low)"""
        is_within, percentage = check_tolerance(1300, 1500, 10)  # -13%
        
        assert is_within is False


class TestStrictMode:
    """Test strict mode enforcement"""
    
    def test_strict_mode_with_violation(self):
        """Test strict mode rejects constraint violations"""
        compliance = ConstraintCompliance(
            word_count_actual=1300,
            word_count_target=1500,
            word_count_within_tolerance=False,
            word_count_percentage=-13.3,
            writing_style_applied="technical",
            strict_mode_enforced=True,
            violation_message="Content too short"
        )
        
        is_valid, error_msg = apply_strict_mode(compliance)
        
        assert is_valid is False
        assert "short" in error_msg
    
    def test_strict_mode_without_violation(self):
        """Test strict mode passes when constraints met"""
        compliance = ConstraintCompliance(
            word_count_actual=1500,
            word_count_target=1500,
            word_count_within_tolerance=True,
            word_count_percentage=0.0,
            writing_style_applied="technical",
            strict_mode_enforced=True,
            violation_message=None
        )
        
        is_valid, error_msg = apply_strict_mode(compliance)
        
        assert is_valid is True
        assert error_msg == ""
    
    def test_non_strict_mode_always_valid(self):
        """Test non-strict mode doesn't enforce constraints"""
        compliance = ConstraintCompliance(
            word_count_actual=1200,
            word_count_target=1500,
            word_count_within_tolerance=False,
            word_count_percentage=-20.0,
            writing_style_applied="technical",
            strict_mode_enforced=False,
            violation_message="Way too short"
        )
        
        is_valid, error_msg = apply_strict_mode(compliance)
        
        assert is_valid is True


class TestComplianceMerging:
    """Test merging multiple compliance reports"""
    
    def test_merge_all_compliant(self):
        """Test merging when all phases are compliant"""
        reports = [
            ConstraintCompliance(
                word_count_actual=300, word_count_target=300,
                word_count_within_tolerance=True, word_count_percentage=0.0,
                writing_style_applied="technical", strict_mode_enforced=False
            ),
            ConstraintCompliance(
                word_count_actual=600, word_count_target=600,
                word_count_within_tolerance=True, word_count_percentage=0.0,
                writing_style_applied="technical", strict_mode_enforced=False
            ),
        ]
        
        merged = merge_compliance_reports(reports)
        
        assert merged.word_count_actual == 900
        assert merged.word_count_target == 900
        assert merged.word_count_within_tolerance is True
    
    def test_merge_with_violation(self):
        """Test merging when one phase violates"""
        reports = [
            ConstraintCompliance(
                word_count_actual=300, word_count_target=300,
                word_count_within_tolerance=True, word_count_percentage=0.0,
                writing_style_applied="technical", strict_mode_enforced=False
            ),
            ConstraintCompliance(
                word_count_actual=550, word_count_target=600,
                word_count_within_tolerance=False, word_count_percentage=-8.3,
                writing_style_applied="technical", strict_mode_enforced=False,
                violation_message="Too short"
            ),
        ]
        
        merged = merge_compliance_reports(reports)
        
        assert merged.word_count_within_tolerance is False
        assert merged.violation_message == "Too short"


# ============================================================================
# TIER 3 TESTS
# ============================================================================

class TestAutoTrimming:
    """Test automatic content trimming"""
    
    def test_trim_over_limit(self):
        """Test trimming content that's over word limit"""
        words = ["word"] * 2000
        content = " ".join(words)
        
        trimmed = auto_trim_content(content, target_words=1500, tolerance_percent=10)
        
        trimmed_count = count_words_in_content(trimmed)
        assert trimmed_count <= 1650  # 1500 + 10% tolerance
        assert trimmed_count < 2000
    
    def test_trim_within_tolerance(self):
        """Test that content within tolerance is not trimmed"""
        words = ["word"] * 1500
        content = " ".join(words)
        
        trimmed = auto_trim_content(content, target_words=1500, tolerance_percent=10)
        
        assert count_words_in_content(trimmed) == 1500


class TestStyleAnalysis:
    """Test writing style analysis"""
    
    def test_analyze_technical_style(self):
        """Test technical style detection"""
        content = "The algorithm implements a framework architecture with components"
        
        score, feedback = analyze_style_consistency(content, "technical")
        
        assert score > 0.5
        assert "technical" in feedback.lower()
    
    def test_analyze_listicle_style(self):
        """Test listicle style detection"""
        content = "- First point\n- Second point\n- Third point"
        
        score, feedback = analyze_style_consistency(content, "listicle")
        
        assert score > 0.5


class TestCostCalculation:
    """Test cost impact calculation"""
    
    def test_calculate_cost_impact(self):
        """Test cost impact calculation"""
        content = " ".join(["word"] * 1500)
        
        impact = calculate_cost_impact(
            content,
            original_word_count=2000,
            constraint_word_count=1500,
            cost_per_1k_tokens=0.01
        )
        
        assert "tokens_estimated" in impact
        assert "cost_estimated" in impact
        assert "cost_savings_from_reduction" in impact
        assert impact["word_reduction"] == 500


# ============================================================================
# FORMATTING TESTS
# ============================================================================

class TestComplianceFormatting:
    """Test compliance report formatting"""
    
    def test_format_passing_report(self):
        """Test formatting a passing compliance report"""
        compliance = ConstraintCompliance(
            word_count_actual=1500,
            word_count_target=1500,
            word_count_within_tolerance=True,
            word_count_percentage=0.0,
            writing_style_applied="technical",
            strict_mode_enforced=False
        )
        
        report = format_compliance_report(compliance)
        
        assert "✅ PASS" in report
        assert "1500" in report
        assert "technical" in report
    
    def test_format_failing_report(self):
        """Test formatting a failing compliance report"""
        compliance = ConstraintCompliance(
            word_count_actual=1200,
            word_count_target=1500,
            word_count_within_tolerance=False,
            word_count_percentage=-20.0,
            writing_style_applied="technical",
            strict_mode_enforced=False,
            violation_message="Content too short"
        )
        
        report = format_compliance_report(compliance)
        
        assert "❌ FAIL" in report
        assert "too short" in report


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
