"""
TITLE GENERATION CONSOLIDATION ANALYSIS

Current System: 3 separate title generators creating redundancy

Problem: Multiple title generators in different services create inconsistency:

1. Title in prompts.json (seo_and_social_media) - generates SEO title
2. \_generate_catchy_title() in content_router_service.py - generates catchy title
3. \_llm_generate_title() in unified_metadata_service.py - generates professional title

Result: Same content gets 3 different titles, potential conflicts
"""

# ============================================================================

# EXISTING TITLE GENERATORS

# ============================================================================

# GENERATOR 1: SEO Title (prompts.json)

# Location: agents/content_agent/prompts.json

# Function: Part of "seo_and_social_media" prompt

# Input: Blog post draft

# Output: SEO-optimized title (<60 chars)

# Model: Content agent (Ollama/Claude/GPT)

# Status: USED for: Final SEO title in blog post response

#

# Prompt excerpt

# "Generate: A concise, SEO-optimized 'title' (under 60 characters)"

#

# Usage in code

# - agents/content_agent/agents/creative_agent.py (run method)

# - Extracts title via regex, slugifies it

#

# Output: Markdown heading extracted from generated content

# GENERATOR 2: Catchy Title (\_generate_catchy_title)

# Location: services/content_router_service.py, line 295

# Function: async def \_generate_catchy_title(topic, content_excerpt)

# Input: Topic + content excerpt (500 chars)

# Output: Single catchy, engaging title

# Model: Ollama (hardcoded to neural-chat:latest)

# Status: USED for: Blog post title during content routing

#

# Prompt

# "You are a creative content strategist specializing in blog titles

# Generate a single, catchy, engaging blog title

# Requirements: Concise (max 100 chars), Contains keyword, Compelling

# Generate ONLY the title, nothing else."

#

# Usage in code

# - content_router_service.py:564

# - Stored in content_task["title"]

# - Output: Raw text string

# GENERATOR 3: Professional Title (\_llm_generate_title)

# Location: services/unified_metadata_service.py, line 313

# Function: async def \_llm_generate_title(self, content)

# Input: Content text (first 500 chars)

# Output: Professional title (<100 chars)

# Model: Claude or OpenAI (fallback chain)

# Status: POTENTIALLY UNUSED (in metadata service pathway)

#

# Prompt

# "Given the following content, generate a short, engaging, professional

# title (max 100 characters)... Generate ONLY the title, nothing else."

#

# Usage in code

# - unified_metadata_service.py (metadata generation)

# - May override previous titles if called

# - Output: Raw text string

# ============================================================================

# COMPARISON MATRIX

# ============================================================================

"""
┌─────────────────────┬──────────────────┬──────────────────┬──────────────────┐
│ Aspect │ SEO Title │ Catchy Title │ Professional │
├─────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Max length │ 60 chars │ 100 chars │ 100 chars │
│ Model │ Content agent │ Ollama hardcoded │ Claude/OpenAI │
│ Primary goal │ SEO optimization │ Engagement │ Professional │
│ Keyword focused? │ YES (required) │ OPTIONAL │ NO │
│ Tone │ Professional │ Catchy/engaging │ Professional │
│ Use case │ Blog publication │ Blog routing │ Metadata gen │
│ Execution point │ Late (after QA) │ During routing │ Post-generation │
│ Override risk │ LOW │ MEDIUM │ HIGH │
│ Consistency │ MEDIUM │ LOW │ LOW │
└─────────────────────┴──────────────────┴──────────────────┴──────────────────┘

ISSUE: Each generator produces different output for same content

- User sees: Up to 3 different titles for the same post
- Confusing: Which is the "real" title?
- Inefficient: 3 LLM calls for same task
  """

# ============================================================================

# RECOMMENDATION: CONSOLIDATE TO SINGLE GENERATOR

# ============================================================================

# PROPOSAL: Single canonical title generator with variants

#

# Location: Use prompt_manager.py

# Key: "seo.generate_canonical_title"

#

# Design

# 1. Single LLM call per blog post

# 2. Optimized for ALL requirements

# - SEO-friendly (<=60 chars, keyword included)

# - Engaging (hooks attention)

# - Professional (appropriate tone)

# 3. Input: topic, keyword, content_excerpt

# 4. Output: Single title used everywhere

#

# Prompt (unified)

# ─────────────────

# """Generate a SINGLE title that satisfies ALL requirements

#

# TOPIC: {topic}

# PRIMARY KEYWORD: {keyword}

# CONTENT EXCERPT: {content_excerpt}

#

# REQUIREMENTS

# 1. Maximum 60 characters (Google SERP limit)

# 2. Include primary keyword naturally (front-load if possible)

# 3. Compelling and click-worthy (use power words where appropriate)

# 4. Professional tone (avoid clickbait)

# 5. Front-load the main benefit/concept

#

# EXAMPLES OF STRONG TITLES

# - "AI in Healthcare: Why Doctors Love It Now"

# - "2025 Market Trends: What Changed This Year"

# - "Cost Savings Blueprint: Reduce Overhead 30%"

#

# Generate ONLY the title, nothing else. No quotes, no explanation."""

#

# Implementation

# - Remove \_generate_catchy_title from content_router_service.py

# - Remove \_llm_generate_title from unified_metadata_service.py

# - Use pm.get_prompt("seo.generate_canonical_title") everywhere

# - Store result in single field: task["seo_title"]

# ============================================================================

# IMPLEMENTATION STEPS

# ============================================================================

# Step 1: Add to prompt_manager.py

# ✅ Already included in seo.generate_canonical_title in prompt_manager.py

# Step 2: Update content_router_service.py

# OLD

# title = await \_generate_catchy_title(topic, content_text[:500])

# NEW

# pm = get_prompt_manager()

# prompt = pm.get_prompt(

# "seo.generate_canonical_title"

# topic=topic

# keyword=primary_keyword

# content_excerpt=content_text[:500]

# )

# title = await model_router.query_with_fallback(prompt)

# Step 3: Update unified_metadata_service.py

# Remove \_llm_generate_title method entirely

# Use prompt_manager for any title generation

# Step 4: Update creative_agent.py

# If extracting title from generated content, apply as fallback only

# Primary title: Use canonical title generator

# Step 5: Testing

# Verify single title generator produces consistent output

# A/B test: old system (3 titles) vs new (1 title)

# Measure: User satisfaction, consistency

# ============================================================================

# MIGRATION CHECKLIST

# ============================================================================

MIGRATION_TODO = """
Priority 1 Consolidation - Title Generator Consolidation
=========================================================

PHASE 1: Preparation (1 hour)
[ ] Review this analysis document
[ ] Verify prompt_manager.py is deployed
[ ] Run tests on existing system to establish baseline

PHASE 2: Update content_router_service.py (30 min)
[ ] Locate \_generate_catchy_title function (line ~295)
[ ] Replace with prompt_manager call
[ ] Test: Title generation during content routing
[ ] Verify: Title stored correctly in task object

PHASE 3: Update unified_metadata_service.py (30 min)
[ ] Locate \_llm_generate_title method (line ~313)
[ ] Remove method entirely
[ ] Remove hardcoded Ollama model reference
[ ] Use prompt_manager for any title needs
[ ] Update metadata_service to use canonical title

PHASE 4: Verify creative_agent.py (15 min)
[ ] Check how title extraction works
[ ] If falls back to generated content, keep as secondary
[ ] Primary source: canonical title generator
[ ] Test content generation with new title flow

PHASE 5: Integration Testing (1 hour)
[ ] End-to-end blog post generation
[ ] Verify single title appears in response
[ ] Check frontend displays title correctly
[ ] Monitor for any lost fields or functionality

PHASE 6: Documentation (30 min)
[ ] Update developer docs
[ ] Add to prompt_manager usage examples
[ ] Document removed title generators

PHASE 7: Deployment (30 min)
[ ] Create feature branch: feature/consolidate-title-generators
[ ] All changes committed
[ ] Code review
[ ] Merge to dev branch
[ ] Test in staging
[ ] Deploy to production

TOTAL TIME: ~4 hours
RISK: LOW (title generators are independent, no side effects)
ROLLBACK: Simple (revert code, restore old functions)
"""

print(MIGRATION_TODO)
