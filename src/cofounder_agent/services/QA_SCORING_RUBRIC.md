"""
QA SCORING RUBRIC & QUALITY THRESHOLDS

This document consolidates the quality evaluation framework used by the system.
The scoring infrastructure already exists in quality_service.py and is passed
to the frontend via unified_task_response.py.

Format: 0-100 scale (0-10 scale converted to percentage)
"""

# ============================================================================

# QUALITY DIMENSIONS (7-POINT RUBRIC)

# ============================================================================

#

# The system evaluates content across 7 independent dimensions

#

# 1. CLARITY (0-100)

# - Is the content clear and easy to understand?

# - Are sentences well-structured?

# - Is technical jargon explained?

# Scoring

# 80-100: Crystal clear, easy to follow, well-explained

# 70-79:  Clear, minor complexity

# 60-69:  Generally clear but some confusing sections

# 50-59:  Confusing, needs significant simplification

# <50:    Very difficult to understand

#

# 2. ACCURACY (0-100)

# - Is information correct and fact-checked?

# - Are claims supported by evidence?

# - Any factually incorrect statements?

# Scoring

# 80-100: Accurate, well-supported, no errors

# 70-79:  Mostly accurate, minor gaps

# 60-69:  Some inaccuracies or unsupported claims

# 50-59:  Multiple factual errors

# <50:    Significantly inaccurate

#

# 3. COMPLETENESS (0-100)

# - Does content cover the topic thoroughly?

# - Are major subtopics addressed?

# - Any critical gaps?

# Scoring

# 80-100: Comprehensive, covers all major areas

# 70-79:  Good coverage, minor gaps

# 60-69:  Covers main points but some gaps

# 50-59:  Incomplete, missing key areas

# <50:    Severely incomplete

#

# 4. RELEVANCE (0-100)

# - Is all content relevant to the topic?

# - Any tangential or off-topic sections?

# - Does everything contribute to the main point?

# Scoring

# 80-100: Highly relevant, everything on point

# 70-79:  Mostly relevant, minor tangents

# 60-69:  Mostly relevant but some tangents

# 50-59:  Mixed relevance, significant tangents

# <50:    Mostly off-topic

#

# 5. SEO QUALITY (0-100)

# - Are keywords incorporated naturally?

# - Good heading structure for SEO?

# - Meta potential (would rank well)?

# - Link opportunities?

# Scoring

# 80-100: Excellent SEO potential, keywords natural

# 70-79:  Good SEO, keywords well-placed

# 60-69:  Decent SEO potential, some keyword gaps

# 50-59:  Weak SEO, poor keyword integration

# <50:    No SEO optimization

#

# 6. READABILITY (0-100)

# - Grammar and spelling correct?

# - Good flow and transitions?

# - Proper formatting?

# - Sentence and paragraph lengths appropriate?

# Scoring

# 80-100: Excellent grammar, great flow, well-formatted

# 70-79:  Good readability, minor issues

# 60-69:  Readable but some grammar/flow issues

# 50-59:  Difficult to read, multiple issues

# <50:    Poor grammar/formatting, hard to read

#

# 7. ENGAGEMENT (0-100)

# - Is content interesting and compelling?

# - Does it hold reader attention?

# - Any hooks, examples, or stories?

# - Would readers find it valuable?

# Scoring

# 80-100: Highly engaging, compelling, valuable

# 70-79:  Engaging, interesting examples

# 60-69:  Somewhat engaging, basic examples

# 50-59:  Dry or boring

# <50:    Very boring, no engagement value

#

# ============================================================================

# OVERALL SCORE CALCULATION

# ============================================================================

#

# Overall Score = Average of 7 dimension scores

# Formula: (clarity + accuracy + completeness + relevance + seo + readability + engagement) / 7

#

# Example

# Clarity: 85      (well-written)

# Accuracy: 90     (well-researched)

# Completeness: 75 (decent coverage)

# Relevance: 80    (mostly on-topic)

# SEO: 72          (keyword gaps)

# Readability: 88  (excellent)

# Engagement: 78   (interesting)

# ─────────────────

# OVERALL: 81      ← Average

# ============================================================================

# PUBLICATION THRESHOLDS  

# ============================================================================

#

# These thresholds determine content readiness for publication

# Implementation: quality_service.py, line 529-531

#

# ⭐ OVERALL SCORE >= 85 (Excellent)

# Status: APPROVED FOR IMMEDIATE PUBLICATION

# Refinement: None needed

# Frontend Signal: "Publication Ready ✅"

# Next Action: Can publish immediately

#

# - Feedback: "Excellent content quality - publication ready"

# - Typical use case: High-quality content from experienced writers

#

# ⭐ OVERALL SCORE 75-84 (Good)

# Status: APPROVED FOR PUBLICATION

# Refinement: Optional polish recommended

# Frontend Signal: "Ready to Publish (Minor Updates Suggested)"

# Next Action: Can publish, suggest optional refinement

#

# - Feedback: "Good quality - minor improvements recommended"

# - Typical use case: Solid AI-generated content that needs light editing

#

# ⭐ OVERALL SCORE 70-74 (Acceptable)

# Status: APPROVED FOR PUBLICATION

# Refinement: Recommended

# Frontend Signal: "Ready to Publish (Improvements Suggested)"

# Next Action: Can publish OR refine for better quality

#

# - Feedback: "Acceptable quality - some improvements suggested"

# - Typical use case: Content generation with moderate issues

#

# ⭐ OVERALL SCORE 60-69 (Fair)

# Status: NEEDS IMPROVEMENT

# Refinement: Required before publication

# Frontend Signal: "Needs Improvement Before Publishing"

# Next Action: Must refine, then re-evaluate

#

# - Feedback: "Fair quality - significant improvements needed"

# - Typical use case: Initial drafts needing substantial work

# - This is where iterative refinement loop kicks in

#

# ⭐ OVERALL SCORE < 60 (Poor)

# Status: REJECTED

# Refinement: Extensive revision required

# Frontend Signal: "Rejected - Major Revisions Required"

# Next Action: Regenerate or significantly rewrite

#

# - Feedback: "Poor quality - major revisions required"

# - Typical use case: Failed generations, off-topic content, hallucinations

# - May warrant starting from scratch

# ============================================================================

# DIMENSION-LEVEL THRESHOLDS

# ============================================================================

#

# Individual dimensions are evaluated against a 70% threshold

# If any single dimension falls below 70%, a specific improvement suggestion

# is generated

#

# Dimension Score < 70 → Include in suggestions list

#

# Implementation: quality_service.py, line 542-556

#

# Example suggestions mapping

# - clarity < 70   → "Simplify sentence structure and use shorter sentences"

# - accuracy < 70  → "Fact-check claims and add citations where appropriate"

# - completeness < 70 → "Add more detail and cover the topic more thoroughly"

# - relevance < 70 → "Keep content focused on the main topic"

# - seo < 70       → "Improve SEO with better headers, keywords, and structure"

# - readability < 70 → "Improve grammar and readability"

# - engagement < 70 → "Add engaging elements like questions, lists, or examples"

#

# ============================================================================

# BACKEND TO FRONTEND FLOW

# ============================================================================

#

# 1. Content generated → quality_service.evaluate_content()

# 2. Each dimension scored (0-100)

# 3. Overall score calculated (average)

# 4. Feedback generated based on thresholds

# 5. Suggestions generated (dimension-specific)

# 6. QualityScore object created → Database

# 7. UnifiedTaskResponse built with quality_score field

# 8. Frontend receives

# {

# "quality_score": 82.1

# "status": "completed"

# "content": "..."

# "qa_feedback": "Good quality - minor improvements..."

# }

#

# Frontend displays

# - Quality score badge (0-100)

# - Pass/fail indicator

# - Feedback message

# - Specific suggestions if score < 85

# ============================================================================

# ITERATIVE REFINEMENT LOGIC

# ============================================================================

#

# When overall_score < 75

#

# Content Generation

# ↓

# Quality Assessment (score < 75)

# ↓

# Generate Suggestions

# ↓

# QA Feedback sent to LLM

# ↓

# Refinement Loop (back to generation)

# ↓

# Re-evaluate

# ↓

# If score >= 75: STOP, publish

# If score < 75: Loop again (max 3 iterations)

#

# Max iterations: 3 (prevents infinite loops)

# Timeout: Content must complete within 5 minutes total

# ============================================================================

# PRAGMATIC QA PRINCIPLE

# ============================================================================

#

# From prompt: "Content doesn't need to be perfect, just good enough for publication."

#

# This means

# - We're not looking for 95+ scores

# - 75+ is acceptable for publication

# - Minor imperfections are OK

# - Only reject if there are SERIOUS issues

# * Unclear writing

# * Off-topic content

# * Missing structure

# * Factual hallucinations

#

# This pragmatic approach balances

# - Quality (75+)

# - Speed (3 iterations max)

# - Cost (fewer LLM calls)

# - User expectations (good, not perfect)

# ============================================================================

# EXAMPLES

# ============================================================================

#

# EXAMPLE 1: High-Quality First Draft

# ─────────────────────────────────────

# Clarity: 85, Accuracy: 90, Completeness: 85, Relevance: 88

# SEO: 82, Readability: 85, Engagement: 88

# Overall: 86.14

# Action: ✅ APPROVED - Publish immediately

#

# EXAMPLE 2: Good Draft, Needs Polish  

# ─────────────────────────────────────

# Clarity: 78, Accuracy: 82, Completeness: 75, Relevance: 80

# SEO: 72, Readability: 80, Engagement: 75

# Overall: 77.71

# Action: ✅ APPROVED - Can publish, suggest refinement for SEO

#

# EXAMPLE 3: Acceptable, Some Issues

# ────────────────────────────────────

# Clarity: 75, Accuracy: 72, Completeness: 68, Relevance: 75

# SEO: 70, Readability: 75, Engagement: 70

# Overall: 72.14

# Action: ✅ APPROVED - Ready to publish, but suggest refinement in

# - Completeness (add more detail)

# - SEO (improve headers/keywords)

# - Engagement (add examples)

#

# EXAMPLE 4: Needs Improvement

# ──────────────────────────────

# Clarity: 65, Accuracy: 72, Completeness: 60, Relevance: 68

# SEO: 62, Readability: 70, Engagement: 58

# Overall: 65.00

# Action: ❌ NEEDS REFINEMENT - Suggest

# - Simplify sentences (clarity)

# - Add more detail (completeness)

# - Add engaging elements (engagement)

# - Improve SEO optimization

# → Trigger refinement loop

#

# EXAMPLE 5: Poor Quality

# ────────────────────────

# Clarity: 45, Accuracy: 55, Completeness: 35, Relevance: 40

# SEO: 50, Readability: 50, Engagement: 40

# Overall: 45.00  

# Action: ❌ REJECTED - Major revisions required

# → Might warrant regenerating from scratch

# ============================================================================

# MIGRATION & IMPLEMENTATION NOTES

# ============================================================================

#

# Current State

# - Scoring infrastructure: ✅ Implemented in quality_service.py

# - Frontend integration: ✅ Already in unified_task_response.py

# - QA prompts: ✅ Already use these thresholds

#

# What was missing

# - Documentation ❌ (this file)

# - Prompt consolidation ❌ (created prompt_manager.py)

# - Title generator consolidation ❌ (see title_generation_consolidation.md)

#

# Next steps

# 1. Use prompt_manager.py instead of scattered prompts

# 2. Consolidate duplicate title generators

# 3. Update QA agent to reference prompt_manager.py

# 4. Test end-to-end quality flow with new managers
