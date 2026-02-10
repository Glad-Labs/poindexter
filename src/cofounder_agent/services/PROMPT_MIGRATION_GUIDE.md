"""
PROMPT CONSOLIDATION MIGRATION GUIDE

How to migrate from scattered prompts to unified prompt_manager.py

Current State: Prompts scattered across 6+ files
Target State: All prompts from prompt_manager.py

Time to migrate full system: ~4-6 hours
Risk: LOW (prompts are self-contained, backward compatible)
"""

# ============================================================================

# MIGRATION OVERVIEW

# ============================================================================

OLD ARCHITECTURE (Current):
├── agents/content_agent/prompts.json
│ ├── "initial_draft_generation"
│ ├── "seo_and_social_media"
│ ├── "iterative_refinement"
│ ├── "qa_review"
│ └── ... (8 more prompts)
├── services/prompt_templates.py
│ ├── blog_generation_prompt()
│ ├── content_critique_prompt()
│ └── ... (utility methods)
├── services/ai_content_generator.py
│ └── system_prompt (hardcoded)
├── services/unified_metadata_service.py
│ └── \_llm_generate_title() (hardcoded)
├── services/content_router_service.py
│ └──_generate_catchy_title() (hardcoded)
└── tasks/content_tasks.py
└── Research/Creative/QA task prompts

NEW ARCHITECTURE (Target):
└── services/prompt_manager.py
├── UnifiedPromptManager class
├── All prompts centralized (30+ prompts)
├── Metadata for each prompt
├── Version control built-in
└── JSON export for documentation

# ============================================================================

# STEP-BY-STEP MIGRATION GUIDE

# ============================================================================

STEP 1: CONTENT AGENT (agents/content_agent/agents/)
═════════════════════════════════════════════════════

Location: agents/content_agent/agents/creative_agent.py
Current: Uses prompts.json via load_prompts_from_file()

OLD CODE (line ~30):
────────────────────
from ..utils.helpers import load_prompts_from_file
from ..config import config

    class CreativeAgent:
        def __init__(self, llm_client: LLMClient):
            self.prompts = load_prompts_from_file(config.PROMPTS_PATH)  # ← loads agents/content_agent/prompts.json

NEW CODE:
─────────
from ...services.prompt_manager import get_prompt_manager

    class CreativeAgent:
        def __init__(self, llm_client: LLMClient):
            self.pm = get_prompt_manager()

OLD USAGE (line ~60+):
──────────────────────
draft_prompt = self.prompts["initial_draft_generation"].format(
topic=post.topic,
target_audience=post.target_audience,
...
)
raw_draft = await self.llm_client.generate_text(draft_prompt)

NEW USAGE:
──────────
draft_prompt = self.pm.get_prompt(
"blog_generation.initial_draft",
topic=post.topic,
target_audience=post.target_audience,
word_count=word_count,
research_context=post.research_data,
internal_link_titles=list(post.published_posts_map.keys())
)
raw_draft = await self.llm_client.generate_text(draft_prompt)

Benefits:
✅ Single source of truth
✅ Built-in versioning
✅ Add examples/documentation easily
✅ Temperature params visible

STEP 2: QA AGENT (agents/content_agent/agents/qa_agent.py)
═══════════════════════════════════════════════════════════

Location: agents/content_agent/agents/qa_agent.py
Current: Uses prompts["qa_review"]

OLD CODE:
─────────
self.prompts = load_prompts_from_file(config.PROMPTS_PATH)

    prompt = self.prompts["qa_review"].format(
        primary_keyword=post.primary_keyword,
        target_audience=post.target_audience,
        draft=previous_content,
    )

NEW CODE:
─────────
from ...services.prompt_manager import get_prompt_manager

    self.pm = get_prompt_manager()

    prompt = self.pm.get_prompt(
        "qa.content_review",
        primary_keyword=post.primary_keyword,
        target_audience=post.target_audience,
        draft=previous_content
    )

    # Get metadata for logging/debugging
    metadata = self.pm.get_metadata("qa.content_review")
    logger.info(f"Using prompt version {metadata.version}")

STEP 3: CONTENT ROUTER SERVICE
═══════════════════════════════

Location: services/content_router_service.py
Current: \_generate_catchy_title() hardcoded prompt

OLD CODE (line ~295-355):
─────────────────────────
async def \_generate_catchy_title(topic: str, content_excerpt: str) -> Optional[str]:
prompt = f"""You are a creative content strategist...
Generate a single, catchy, engaging blog title...
Topic: {topic}
Content excerpt: {content_excerpt}
..."""

        response = await ollama.generate(prompt, model="neural-chat:latest")
        title = response.get("response", "")
        return title.strip()

NEW CODE:
─────────
from .prompt_manager import get_prompt_manager
from .model_router import model_router

    async def _generate_canonical_title(topic: str, keyword: str, content_excerpt: str) -> Optional[str]:
        pm = get_prompt_manager()
        prompt = pm.get_prompt(
            "seo.generate_canonical_title",
            topic=topic,
            primary_keyword=keyword,
            content=content_excerpt
        )

        # Use model router instead of hardcoded Ollama
        response = await model_router.query_with_fallback(
            prompt=prompt,
            temperature=0.5,
            max_tokens=100
        )
        return response.strip()

Benefits:
✅ Removes hardcoded Ollama reference
✅ Uses intelligent fallback chain
✅ Canonical title generator (single source)
✅ Temperature & parameters explicit

Usage update (line ~564):
──────────────────────── # OLD
title = await \_generate_catchy_title(topic, content_text[:500])

    # NEW
    title = await _generate_canonical_title(
        topic=topic,
        keyword=primary_keyword,
        content_excerpt=content_text[:500]
    )

STEP 4: UNIFIED METADATA SERVICE
═════════════════════════════════

Location: services/unified_metadata_service.py
Current: \_llm_generate_title(),\_llm_generate_seo_description(), etc.

OLD CODE (line ~313-350):
─────────────────────────
async def \_llm_generate_title(self, content: str) -> Optional[str]:
prompt = f"""Given the following content, generate a short, engaging,
professional title (max 100 characters)...
Content: {content[:500]}
..."""

        if ANTHROPIC_AVAILABLE:
            response = anthropic_client.messages.create(
                model="claude-3-haiku-20240307",
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()[:100]

NEW CODE:
─────────
from .prompt_manager import get_prompt_manager

    async def _generate_title(self, content: str, primary_keyword: str = "") -> Optional[str]:
        pm = get_prompt_manager()
        prompt = pm.get_prompt(
            "seo.generate_title",
            content=content[:400],
            primary_keyword=primary_keyword
        )

        # Use model router fallback chain
        response = await model_router.query_with_fallback(
            prompt=prompt,
            temperature=0.6,
            max_tokens=100
        )
        return response.strip()[:100] if response else None

    async def _generate_meta_description(self, title: str, content: str) -> Optional[str]:
        pm = get_prompt_manager()
        prompt = pm.get_prompt(
            "seo.generate_meta_description",
            title=title,
            content=content[:400]
        )
        response = await model_router.query_with_fallback(
            prompt=prompt,
            temperature=0.5,
            max_tokens=160
        )
        return response.strip()[:155] if response else None

    async def _extract_keywords(self, title: str, content: str) -> Optional[List[str]]:
        pm = get_prompt_manager()
        prompt = pm.get_prompt(
            "seo.extract_keywords",
            title=title,
            content=content[:500]
        )
        response = await model_router.query_with_fallback(
            prompt=prompt,
            temperature=0.3,
            max_tokens=100
        )
        if response:
            return [k.strip() for k in response.split(",")]
        return None

Benefits:
✅ Removes hardcoded API client references
✅ Explicit fallback chain (Claude→OpenAI→Ollama)
✅ All SEO generation consolidated
✅ Easy to update/version all SEO prompts

STEP 5: CONTENT TASKS
═════════════════════

Location: tasks/content_tasks.py
Current: Hardcoded prompts in ResearchTask, CreativeTask, QATask

For each task, follow same pattern:

OLD CODE (tasks/content_tasks.py:64):
──────────────────────────────────────
prompt = f"""Analyze the following search results for the topic: "{topic}"

    Search Results:
    {search_context}

    Depth: {depth}

    Based ONLY on the search results..."""

NEW CODE:
─────────
from services.prompt_manager import get_prompt_manager

    pm = get_prompt_manager()
    prompt = pm.get_prompt(
        "research.analyze_search_results",
        topic=topic,
        depth=depth,
        search_context=search_context
    )

Apply to:
• ResearchTask (line 64)
• CreativeTask (line 169)  
 • QATask (line 253)
• SocialResearchTask (line 44)
• SocialCreativeTask (line 133)
• FinancialAnalysisTask (line 65)
• MarketAnalysisTask (line 158)

STEP 6: UTILITIES & HELPERS
═════════════════════════════

Location: services/prompt_templates.py
Current: Python methods that build prompts

Decision: DEPRECATE this file gradually

- Keep for backward compatibility for 1 sprint
- New code uses prompt_manager directly
- Remove in next sprint after migration complete

Deprecation notice to add (line 1):
───────────────────────────────────
"""
DEPRECATED: Use prompt_manager.py instead

    This module will be removed on 2026-03-01.
    All new code should use:
        from services.prompt_manager import get_prompt_manager
        pm = get_prompt_manager()
        prompt = pm.get_prompt("key.name", **kwargs)

    See MIGRATION_GUIDE.md for details.
    """

# ============================================================================

# TESTING MIGRATION

# ============================================================================

Test each service after migration:

1. UNIT TESTS
   ─────────────
   [ ] Test prompt_manager.py directly - Test get_prompt() with valid keys - Test get_prompt() with missing variables - Test list_prompts() - Test export_prompts_as_json()

   [ ] Test each agent/service - Run existing unit tests for that service - Verify prompts are fetched correctly - Check output format unchanged

2. INTEGRATION TESTS
   ──────────────────
   [ ] End-to-end blog generation - Creative agent uses prompts correctly - QA agent evaluates with new prompts - SEO metadata generated properly

   [ ] Social media generation - Research task uses new prompts - Creative task generates correct format

   [ ] Financial/Market analysis - Prompts execute without errors

3. REGRESSION TESTS
   ─────────────────
   [ ] Quality stays same/improves
   [ ] Output format unchanged
   [ ] No extra LLM calls
   [ ] Error handling works

# ============================================================================

# ROLLOUT PLAN

# ============================================================================

PHASE 1: Pilot (1 day)
✓ Deploy prompt_manager.py
✓ Migrate 1 service (e.g., content_router_service)
✓ Run full test suite
✓ Monitor logs for errors

PHASE 2: Content Agent (1 day)
✓ Migrate creative_agent.py
✓ Migrate qa_agent.py
✓ Update prompts.json dependency
✓ Run e2e tests

PHASE 3: Services (1 day)
✓ Migrate unified_metadata_service.py
✓ Migrate ai_content_generator.py
✓ Update task prompts
✓ Run integration tests

PHASE 4: Cleanup (1 day)
✓ Remove old prompts from agents/content_agent/prompts.json
✓ Deprecate prompt_templates.py
✓ Update documentation
✓ Commit & deploy

Total time: 4 days

If issues found:
→ Revert individual service
→ Fix and re-test
→ Continue with next service

# ============================================================================

# BACKWARD COMPATIBILITY

# ============================================================================

For gradual migration, keep both systems running:

1. Existing code continues using old prompts.json
2. New code uses prompt_manager.py
3. Adapter layer (if needed):

   def get_prompt_legacy_compat(key, **kwargs):
   """Fallback to prompts.json if key not in prompt_manager"""
   try:
   return get_prompt_manager().get_prompt(key,**kwargs)
   except KeyError: # Fall back to old system
   return load_prompts_from_file[PROMPTS_PATH](key).format(\*\*kwargs)

# ============================================================================

# CHECKLIST FOR COMPLETION

# ============================================================================

Migration Completion Checklist:
[ ] prompt_manager.py deployed
[ ] All services migrated
[ ] All tests passing
[ ] No performance regression
[ ] No quality regression
[ ] Documentation updated
[ ] prompts.json removed (or deprecation warning added)
[ ] prompt_templates.py deprecated
[ ] Feature merged to main
[ ] Monitoring in place

Staff notifications:
[ ] Tell engineering team migration is complete
[ ] Update onboarding docs
[ ] Update internal wiki
[ ] Archive old documentation
"""
