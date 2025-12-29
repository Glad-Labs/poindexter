"""
Constraint Management Integration Example

Demonstrates using Tier 1, 2, and 3 word count and writing style constraints
in a real content generation scenario.

This example shows:
1. Frontend to Backend flow with constraint parameters
2. Tier 1: Basic constraint enforcement
3. Tier 2: Compliance tracking and phase-specific targets
4. Tier 3: Auto-correction and cost optimization
"""

import asyncio
import json
from typing import Dict, Any, Optional

# Import constraint utilities (all Tiers)
from utils.constraint_utils import (
    ContentConstraints,
    extract_constraints_from_request,
    inject_constraints_into_prompt,
    count_words_in_content,
    validate_constraints,
    calculate_phase_targets,
    apply_strict_mode,
    merge_compliance_reports,
    auto_trim_content,
    analyze_style_consistency,
    calculate_cost_impact,
    format_compliance_report
)


# ============================================================================
# EXAMPLE 1: FRONTEND → BACKEND REQUEST FLOW
# ============================================================================

def example_frontend_request_with_constraints() -> Dict[str, Any]:
    """
    Example: How frontend sends constraint parameters to backend.
    
    The ModelSelectionPanel in oversight-hub collects these parameters
    and sends them in the content_constraints field of TaskCreateRequest.
    """
    
    # This is what the frontend form sends
    frontend_request = {
        "topic": "The Future of AI in Healthcare",
        "primary_keyword": "AI healthcare",
        "target_audience": "Healthcare professionals",
        "category": "healthcare",
        "metadata": {
            "source": "oversight-hub",
            "created_by": "user@example.com"
        },
        # NEW: Constraint parameters from ModelSelectionPanel
        "content_constraints": {
            "word_count": 2000,  # Target 2000 words
            "writing_style": "educational",  # Style: technical, narrative, listicle, educational, thought-leadership
            "word_count_tolerance": 10,  # Allow ±10%
            "per_phase_overrides": {
                "research": 400,
                "creative": 800,
                "qa": 400,
                "format": 300,
                "finalize": 100
            },
            "strict_mode": False  # If True, fail task if constraints violated
        }
    }
    
    print("📤 Frontend Request with Constraints:")
    print(json.dumps(frontend_request, indent=2))
    print()
    
    return frontend_request


# ============================================================================
# EXAMPLE 2: TIER 1 - BASIC CONSTRAINT ENFORCEMENT
# ============================================================================

async def example_tier_1_basic_enforcement():
    """
    Tier 1: Basic word count and writing style enforcement
    
    Shows:
    - Extracting constraints from request
    - Injecting into prompts
    - Validating output
    """
    
    print("=" * 70)
    print("TIER 1: BASIC CONSTRAINT ENFORCEMENT")
    print("=" * 70)
    
    # 1. Extract constraints from request
    request = example_frontend_request_with_constraints()
    constraints = extract_constraints_from_request(request)
    
    print(f"✅ Constraints extracted:")
    print(f"   Word Count: {constraints.word_count}")
    print(f"   Style: {constraints.writing_style}")
    print(f"   Tolerance: ±{constraints.word_count_tolerance}%")
    print()
    
    # 2. Inject constraints into research prompt
    research_prompt = "Research the latest developments in AI healthcare applications"
    constrained_prompt = inject_constraints_into_prompt(
        research_prompt,
        constraints,
        phase_name="research",
        word_count_target=400
    )
    
    print(f"📋 Research Prompt with Constraints Injected:")
    print(constrained_prompt[:300] + "...")
    print()
    
    # 3. Simulate research output and validate
    research_output = " ".join(["word"] * 385)  # 385 words (within 400±10%)
    
    research_compliance = validate_constraints(
        research_output,
        constraints,
        phase_name="research",
        word_count_target=400
    )
    
    print(f"📊 Research Output Validation:")
    print(f"   Target: {research_compliance.word_count_target} words")
    print(f"   Actual: {research_compliance.word_count_actual} words")
    print(f"   Within Tolerance: {'✅ YES' if research_compliance.word_count_within_tolerance else '❌ NO'}")
    print(f"   Difference: {research_compliance.word_count_percentage:+.1f}%")
    print()


# ============================================================================
# EXAMPLE 3: TIER 2 - COMPLIANCE TRACKING & PHASE TARGETS
# ============================================================================

async def example_tier_2_compliance_tracking():
    """
    Tier 2: Track compliance across all phases with per-phase targets
    
    Shows:
    - Calculating phase-specific word count targets
    - Tracking compliance across multiple phases
    - Aggregating compliance reports
    - Checking strict mode
    """
    
    print("=" * 70)
    print("TIER 2: COMPLIANCE TRACKING & PHASE TARGETS")
    print("=" * 70)
    
    # Extract constraints
    request = example_frontend_request_with_constraints()
    constraints = extract_constraints_from_request(request)
    
    # Calculate phase targets
    phase_targets = calculate_phase_targets(
        constraints.word_count,
        constraints,
        num_phases=5
    )
    
    print(f"📊 Phase Word Count Targets:")
    for phase, target in phase_targets.items():
        print(f"   {phase:12} → {target:4} words")
    print()
    
    # Simulate output from each phase
    phase_outputs = {
        "research": " ".join(["word"] * 400),      # 400 words (target: 400)
        "creative": " ".join(["word"] * 850),      # 850 words (target: 800)
        "qa": " ".join(["word"] * 380),            # 380 words (target: 400)
        "format": " ".join(["word"] * 350),        # 350 words (target: 300)
        "finalize": " ".join(["word"] * 90),       # 90 words (target: 100)
    }
    
    # Validate each phase
    compliance_reports = []
    print(f"📈 Phase Compliance:")
    for phase, output in phase_outputs.items():
        compliance = validate_constraints(
            output,
            constraints,
            phase_name=phase,
            word_count_target=phase_targets.get(phase)
        )
        compliance_reports.append(compliance)
        
        status = "✅" if compliance.word_count_within_tolerance else "⚠️"
        print(f"   {status} {phase:12} {compliance.word_count_actual:4} words "
              f"(target: {compliance.word_count_target:4}) {compliance.word_count_percentage:+6.1f}%")
    
    print()
    
    # Merge compliance reports
    overall_compliance = merge_compliance_reports(compliance_reports)
    
    print(f"📋 Overall Task Compliance:")
    print(f"   Total Words: {overall_compliance.word_count_actual} (target: {overall_compliance.word_count_target})")
    print(f"   Within Tolerance: {'✅ YES' if overall_compliance.word_count_within_tolerance else '❌ NO'}")
    print(f"   Percentage: {overall_compliance.word_count_percentage:+.1f}%")
    print()
    
    # Check strict mode
    if constraints.strict_mode:
        is_valid, error = apply_strict_mode(overall_compliance)
        print(f"🔒 Strict Mode Check: {'✅ PASS' if is_valid else '❌ FAIL'}")
        if not is_valid:
            print(f"   Error: {error}")
    else:
        print(f"🔓 Strict Mode: Disabled (constraints are advisory)")
    
    print()


# ============================================================================
# EXAMPLE 4: TIER 3 - AUTO-CORRECTION & OPTIMIZATION
# ============================================================================

async def example_tier_3_auto_correction():
    """
    Tier 3: Automatic correction and cost optimization
    
    Shows:
    - Auto-trimming content that's over word count
    - Style consistency analysis
    - Cost impact calculation
    """
    
    print("=" * 70)
    print("TIER 3: AUTO-CORRECTION & OPTIMIZATION")
    print("=" * 70)
    
    # Extract constraints
    request = example_frontend_request_with_constraints()
    constraints = extract_constraints_from_request(request)
    
    # Simulate content that's too long
    original_content = " ".join(["This is a sample content word."] * 100)  # ~300 words
    
    print(f"📝 Original Content: {count_words_in_content(original_content)} words")
    print()
    
    # 1. Auto-trim to fit constraints
    trimmed = auto_trim_content(
        original_content,
        target_words=constraints.word_count,
        tolerance_percent=constraints.word_count_tolerance
    )
    
    print(f"✂️ Auto-Trimmed Content: {count_words_in_content(trimmed)} words")
    print(f"   Reduction: {count_words_in_content(original_content) - count_words_in_content(trimmed)} words")
    print()
    
    # 2. Analyze style consistency
    style_score, style_feedback = analyze_style_consistency(
        trimmed,
        constraints.writing_style,
        min_score=0.7
    )
    
    print(f"🎨 Style Consistency Analysis:")
    print(f"   Target Style: {constraints.writing_style}")
    print(f"   Score: {style_score:.1%}")
    print(f"   Feedback: {style_feedback}")
    print()
    
    # 3. Calculate cost impact
    cost_metrics = calculate_cost_impact(
        trimmed,
        original_word_count=count_words_in_content(original_content),
        constraint_word_count=constraints.word_count,
        cost_per_1k_tokens=0.01
    )
    
    print(f"💰 Cost Impact:")
    print(f"   Tokens Estimated: {cost_metrics['tokens_estimated']}")
    print(f"   Cost: ${cost_metrics['cost_estimated']:.4f}")
    print(f"   Cost Savings: ${cost_metrics['cost_savings_from_reduction']:.4f}")
    print(f"   Efficiency: {cost_metrics['efficiency_ratio']:.1%}")
    print()


# ============================================================================
# EXAMPLE 5: COMPLETE END-TO-END FLOW
# ============================================================================

async def example_complete_flow():
    """
    Complete end-to-end example: Frontend → Constraints → Generation → Validation → Report
    
    This is what happens in the actual content_orchestrator.py when constraints are provided.
    """
    
    print("=" * 70)
    print("COMPLETE END-TO-END FLOW")
    print("=" * 70)
    print()
    
    # Step 1: Frontend sends request with constraints
    print("1️⃣  FRONTEND REQUEST")
    request = example_frontend_request_with_constraints()
    constraints = extract_constraints_from_request(request)
    print()
    
    # Step 2: Backend calculates phase targets
    print("2️⃣  PHASE TARGET CALCULATION")
    phase_targets = calculate_phase_targets(constraints.word_count, constraints, num_phases=5)
    print(f"   Phase targets calculated: {sum(phase_targets.values())} total words")
    print()
    
    # Step 3: Generate content for each phase (with constraints injected)
    print("3️⃣  CONTENT GENERATION (with constraints)")
    phases_content = {
        "research": "Research about AI in healthcare findings and data " * 50,     # ~400 words
        "creative": "Healthcare professionals are seeing transformative impacts " * 100,  # ~800 words
        "qa": "Quality assessment shows strong adherence to guidelines " * 40,     # ~400 words
        "format": "Formatted with proper markdown and SEO optimization " * 30,     # ~300 words
        "finalize": "Final review complete ready for publication " * 10,            # ~100 words
    }
    print(f"   Generated content for all phases")
    print()
    
    # Step 4: Validate all phases
    print("4️⃣  CONSTRAINT VALIDATION")
    all_compliance = []
    total_words = 0
    
    for phase, content in phases_content.items():
        word_count = count_words_in_content(content)
        total_words += word_count
        
        compliance = validate_constraints(
            content,
            constraints,
            phase_name=phase,
            word_count_target=phase_targets.get(phase)
        )
        all_compliance.append(compliance)
        
        status = "✅" if compliance.word_count_within_tolerance else "⚠️"
        print(f"   {status} {phase}: {word_count} words (tolerance: {phase_targets[phase]}±{constraints.word_count_tolerance}%)")
    
    print()
    
    # Step 5: Aggregate compliance
    print("5️⃣  COMPLIANCE AGGREGATION")
    overall = merge_compliance_reports(all_compliance)
    print(f"   Total: {overall.word_count_actual} words (target: {overall.word_count_target})")
    print(f"   Status: {'✅ COMPLIANT' if overall.word_count_within_tolerance else '⚠️  NEEDS ADJUSTMENT'}")
    print()
    
    # Step 6: Check strict mode
    print("6️⃣  STRICT MODE CHECK")
    if constraints.strict_mode:
        is_valid, error = apply_strict_mode(overall)
        print(f"   Strict Mode: {'✅ PASS' if is_valid else '❌ FAIL'}")
        if not is_valid:
            print(f"   Reason: {error}")
    else:
        print(f"   Strict Mode: DISABLED (advisory only)")
    
    print()
    
    # Step 7: Format compliance report
    print("7️⃣  COMPLIANCE REPORT")
    report = format_compliance_report(overall)
    print(report)
    
    # Step 8: Return to frontend with constraint metrics
    print("8️⃣  RESPONSE TO FRONTEND")
    response = {
        "task_id": "task_12345",
        "status": "awaiting_approval",
        "constraint_compliance": {
            "word_count_actual": overall.word_count_actual,
            "word_count_target": overall.word_count_target,
            "word_count_within_tolerance": overall.word_count_within_tolerance,
            "word_count_percentage": overall.word_count_percentage,
            "writing_style": overall.writing_style_applied,
            "strict_mode_enforced": overall.strict_mode_enforced,
            "violation_message": overall.violation_message
        },
        "content": "...formatted content here...",
        "quality_score": 85
    }
    
    print(json.dumps(response, indent=2))
    print()


# ============================================================================
# MAIN: RUN ALL EXAMPLES
# ============================================================================

async def main():
    """Run all examples"""
    
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 15 + "CONSTRAINT MANAGEMENT IMPLEMENTATION GUIDE" + " " * 11 + "║")
    print("╚" + "=" * 68 + "╝")
    print()
    
    # Run examples
    await example_tier_1_basic_enforcement()
    await example_tier_2_compliance_tracking()
    await example_tier_3_auto_correction()
    await example_complete_flow()
    
    print("=" * 70)
    print("✅ ALL EXAMPLES COMPLETE")
    print("=" * 70)
    print()


if __name__ == "__main__":
    asyncio.run(main())
