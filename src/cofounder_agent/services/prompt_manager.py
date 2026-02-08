"""
Unified Prompt Manager

Consolidates ALL LLM prompts from across the codebase into a single,
versioned, and documented system.

Replaces:
- agents/content_agent/prompts.json (blog generation prompts)
- services/prompt_templates.py (utility prompt builders)
- Hardcoded prompts in ai_content_generator.py, unified_metadata_service.py, etc.

Benefits:
- Single source of truth for all prompts
- Easy version control and A/B testing
- Consistent output format specifications
- Built-in documentation and examples
- Centralized temperature/parameter management
- Easy to add new prompts without scattered changes

Version History:
- v1.0 (2026-02-07): Initial consolidation from scattered prompts across codebase
"""

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PromptVersion(str, Enum):
    """Prompt versions for A/B testing and rollouts"""
    V1_0 = "v1.0"  # Initial consolidated version
    V1_1 = "v1.1"  # Current production


class PromptCategory(str, Enum):
    """Prompt categories for organization"""
    BLOG_GENERATION = "blog_generation"
    CONTENT_QA = "content_qa"
    SEO_METADATA = "seo_metadata"
    SOCIAL_MEDIA = "social_media"
    RESEARCH = "research"
    FINANCIAL = "financial"
    MARKET_ANALYSIS = "market_analysis"
    IMAGE_GENERATION = "image_generation"
    UTILITY = "utility"


@dataclass
class PromptMetadata:
    """Metadata about a prompt for versioning and tracking"""
    category: PromptCategory
    version: PromptVersion
    created_date: str  # ISO format: YYYY-MM-DD
    last_modified: str  # ISO format: YYYY-MM-DD
    deprecated: bool = False
    replacement_prompt_key: Optional[str] = None
    description: str = ""
    output_format: str = ""  # "json", "text", "markdown", etc.
    example_output: Optional[str] = None
    notes: str = ""  # A/B test results, performance notes, etc.


class UnifiedPromptManager:
    """
    Central manager for all LLM prompts.
    
    Usage:
        pm = UnifiedPromptManager()
        prompt = pm.get_prompt("blog_generation.initial_draft", topic="AI Trends")
        metadata = pm.get_metadata("blog_generation.initial_draft")
    """

    def __init__(self):
        self.prompts: Dict[str, Dict[str, Any]] = {}
        self.metadata: Dict[str, PromptMetadata] = {}
        self._initialize_prompts()

    def _initialize_prompts(self):
        """Populate all prompts from consolidated sources"""
        
        # ======================================================================
        # BLOG GENERATION PROMPTS
        # ======================================================================
        
        self._register_prompt(
            key="blog_generation.initial_draft",
            category=PromptCategory.BLOG_GENERATION,
            template="""Generate a comprehensive, well-structured blog post draft about '{topic}'. 
The target audience is {target_audience}. 
The primary keyword to focus on is '{primary_keyword}'.

⭐ CRITICAL REQUIREMENTS:
1. Start your response with a Markdown heading (# Your Title Here) on the first line
2. Include 3-5 main sections with ## subheadings
3. Incorporate research context provided below
4. Add [IMAGE-1], [IMAGE-2], etc. where visuals would enhance the content
5. Target word count: {word_count} words (±10% tolerance acceptable)
6. Format: Valid Markdown with proper structure

⭐ RESEARCH CONTEXT TO INCORPORATE:
{research_context}

⭐ INTERNAL LINKS (if relevant, suggest these topics):
{internal_link_titles}

Ensure the tone is professional and engaging. 
Include placeholders like '[IMAGE-1]', '[IMAGE-2]', etc., where you think images would be appropriate. 
Also, suggest a list of external URLs to credible sources that would support the content.""",
            description="Generate initial blog post draft with research context",
            output_format="markdown",
            example_output="""# The Future of AI in Healthcare: 2025 Trends

## Introduction
Healthcare is undergoing a transformation...

## Key Trend 1: Diagnostic AI Systems
[IMAGE-1]
...

## Conclusion
The integration of AI...

**Related: [[Internal Link 1]], [[Internal Link 2]]**""",
            notes="v1.0: Added word count tolerance guidance, research context structure"
        )

        self._register_prompt(
            key="blog_generation.seo_and_social",
            category=PromptCategory.BLOG_GENERATION,
            template="""Analyze the following blog post draft and generate SEO assets:

---DRAFT---
{draft}
---END DRAFT---

Generate EXACTLY the following assets in VALID JSON format (no markdown, no extra text):
{{
  "title": "SEO-optimized title (max 60 characters, must include primary keyword)",
  "meta_description": "Compelling meta description (max 160 characters, actionable)",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"]
}}

⭐ REQUIREMENTS:
1. Title must be under 60 characters
2. Meta description must be under 160 characters
3. All keywords must be relevant to content
4. Respond with ONLY valid JSON, no other text
5. Include primary keyword in title if possible

Example output:
{{
  "title": "AI in Healthcare: 2025 Trends & Opportunities",
  "meta_description": "Discover how AI is transforming healthcare in 2025. Expert insights on diagnostics, treatment, and patient outcomes.",
  "keywords": ["AI healthcare", "medical AI 2025", "health tech trends", "diagnostic AI", "healthcare innovation"]
}}""",
            description="Generate SEO-optimized title, description, and keywords",
            output_format="json",
            example_output='{"title":"AI in Healthcare: 2025 Trends","meta_description":"Discover how AI transforms healthcare in 2025","keywords":["AI healthcare","medical AI","health tech","diagnostic AI","patient care"]}',
            notes="v1.0: Enforces character limits, provides JSON example"
        )

        self._register_prompt(
            key="blog_generation.iterative_refinement",
            category=PromptCategory.BLOG_GENERATION,
            template="""Revise the following blog post draft based on the provided critique. 
Incorporate all feedback to improve quality while maintaining professional tone and target audience focus.

⭐ WORD COUNT CONSTRAINT:
{word_count_constraint}

---DRAFT---
{draft}
---END DRAFT---

---CRITIQUE---
{critique}
---END CRITIQUE---

⭐ REQUIREMENTS:
1. Start with Markdown heading (# Title) on first line
2. Preserve all original insights and data points
3. Improve clarity, structure, and engagement
4. Address ALL critique points
5. Maintain professional tone for {target_audience}
6. Ensure word count falls within specified range

Improved version:""",
            description="Refine blog post based on QA feedback",
            output_format="markdown",
            notes="v1.0: Added constraint about preserving insights, explicit audience focus"
        )

        # ======================================================================
        # CONTENT QA / CRITIQUE PROMPTS
        # ======================================================================
        
        self._register_prompt(
            key="qa.content_review",
            category=PromptCategory.CONTENT_QA,
            template="""Review this blog post for publication readiness.

TARGET AUDIENCE: {target_audience}
PRIMARY KEYWORD: '{primary_keyword}'

---DRAFT---
{draft}
---END DRAFT---

Evaluate against these 5 criteria:
1. Clarity: Is writing clear and easy to understand?
2. Tone: Is tone appropriate and engaging for target audience?
3. Keyword Natural Incorporation: Is primary keyword used naturally (not forced)?
4. Structure: Clear headings, logical flow, proper intro/conclusion?
5. Value: Does content provide genuine value and useful information?

⭐ GRADING SCALE (0-100):
- 85+: Excellent - publication ready, no refinement needed
- 75-84: Good - publication ready with minor polish
- 70-74: Acceptable - can publish, some improvements suggested
- 60-69: Fair - needs significant improvements before publishing
- <60: Poor - requires major revisions

⭐ PRAGMATISM PRINCIPLE:
Content doesn't need to be perfect, just good enough for publication.
Only reject if there are serious issues: unclear writing, off-topic, missing structure.

Respond with ONLY valid JSON (no markdown, no explanation):
{{
  "approved": true or false,
  "quality_score": NUMBER from 0-100,
  "feedback": "specific feedback here (2-3 sentences max)"
}}

Example:
{{
  "approved": true,
  "quality_score": 82,
  "feedback": "Well-structured with good keyword integration. Consider adding more specific examples in section 2."
}}""",
            description="QA review with pragmatic publication criteria",
            output_format="json",
            example_output='{"approved":true,"quality_score":82,"feedback":"Well-structured. Add more examples to section 2."}',
            notes="v1.0: Emphasizes pragmatism, includes scoring scale in prompt, provides JSON example"
        )

        self._register_prompt(
            key="qa.self_critique",
            category=PromptCategory.CONTENT_QA,
            template="""You are an expert content editor. Critique the following blog post draft.

CONTEXT:
- Topic: {topic}
- Target Audience: {target_audience}
- Primary Keywords: {primary_keyword}
- Writing Style: {style}
- Tone: {tone}
- Target Length: {target_length}

{writing_style_reference}

---CONTENT TO EVALUATE---
{content}
---END CONTENT---

Evaluate across 6 dimensions:
1. Tone and Voice: Professional and appropriate for audience?
2. Structure: Clear intro, body sections, conclusion? Effective headings?
3. SEO Quality: Keywords used naturally? Good meta potential?
4. Engagement: Compelling? Valuable? Would readers care?
5. Accuracy: Factually sound within general knowledge?
6. Writing Style Consistency: Matches reference style if provided?

Return ONLY valid JSON:
{{
  "quality_score": FLOAT 0-100,
  "approved": BOOLEAN (true if score >= 75),
  "feedback": "summary feedback",
  "suggestions": ["suggestion 1", "suggestion 2"],
  "needs_refinement": BOOLEAN (true if score < 85)
}}""",
            description="Self-critique with style matching support",
            output_format="json",
            notes="v1.0: Supports optional writing style reference for RAG-based style matching"
        )

        # ======================================================================
        # SEO & METADATA PROMPTS
        # ======================================================================
        
        self._register_prompt(
            key="seo.generate_title",
            category=PromptCategory.SEO_METADATA,
            template="""Generate a professional, SEO-optimized blog title.

Content: {content}
Primary Keyword: {primary_keyword}

⭐ REQUIREMENTS:
- Maximum 60 characters
- Include primary keyword if naturally possible
- Front-load the main benefit/topic
- Be specific and descriptive
- Avoid clickbait while remaining compelling
- Professional tone

Generate ONLY the title, nothing else. No quotes, no explanation.""",
            description="Generate SEO-optimized title (60 char max)",
            output_format="text",
            example_output="AI in Healthcare: 2025 Trends & Opportunities",
            notes="v1.0: Strict output constraint reduces hallucination"
        )

        self._register_prompt(
            key="seo.generate_meta_description",
            category=PromptCategory.SEO_METADATA,
            template="""Generate a compelling SEO meta description for search results.

Title: {title}
Content (first 400 characters): {content}

⭐ REQUIREMENTS:
- Maximum 155 characters (Google's exact limit)
- Include primary keyword naturally
- Include call-to-action (Learn more, Discover, Explore, etc.)
- Compelling and click-worthy
- Accurate reflection of content

Generate ONLY the description, nothing else. No quotes, no explanation.""",
            description="Generate SEO meta description (155 char max)",
            output_format="text",
            example_output="Discover how AI is transforming healthcare in 2025. Expert insights on diagnostics, treatment, and patient outcomes.",
            notes="v1.0: Enforces Google's 155-char standard, emphasizes CTA"
        )

        self._register_prompt(
            key="seo.extract_keywords",
            category=PromptCategory.SEO_METADATA,
            template="""Extract 5-7 SEO keywords from this content.

Title: {title}
Content (first 500 characters): {content}

⭐ REQUIREMENTS:
- 5-7 keywords total
- Comma-separated, no quotes
- Most important keywords first
- Should include primary keyword
- Mix of short-tail (2-3 words) and long-tail (4+ words)
- Relevant to content

Example: "AI healthcare, healthcare AI trends, medical diagnostics, AI patient care, 2025 health tech"

Generate ONLY the comma-separated list, nothing else.""",
            description="Extract SEO keywords from content",
            output_format="text",
            example_output="AI healthcare, healthcare AI trends, medical diagnostics, AI patient care, 2025 health tech",
            notes="v1.0: Specifies keyword composition (short vs long-tail)"
        )

        # ======================================================================
        # RESEARCH PROMPTS
        # ======================================================================
        
        self._register_prompt(
            key="research.analyze_search_results",
            category=PromptCategory.RESEARCH,
            template="""Analyze the following search results for: "{topic}"

Depth Level: {depth}

---SEARCH RESULTS---
{search_context}
---END RESULTS---

⭐ CRITICAL INSTRUCTION:
Base your analysis ONLY on the search results provided.
Use general knowledge ONLY to fill minor gaps.
If data is unavailable, state "DATA NOT FOUND" explicitly.

Provide a structured analysis with:
1. Key Findings (5-10 main points from search results)
2. Current Trends (from sources)
3. Important Statistics (with sources if available)
4. Recommended Sources (use provided URLs)

⭐ OUTPUT FORMAT (Valid JSON only, no markdown):
{{
  "key_points": ["point 1", "point 2", ...],
  "trends": ["trend 1", "trend 2", ...],
  "statistics": ["stat 1", "stat 2", ...],
  "sources": ["url1", "url2", ...]
}}

Example:
{{
  "key_points": ["AI adoption in healthcare increased 45% YoY", "Diagnostic accuracy improved with ML models"],
  "trends": ["Move toward explainable AI", "Integration with EHR systems"],
  "statistics": ["$15.2B market size by 2025", "78% hospitals planning AI implementation"],
  "sources": ["https://example1.com", "https://example2.com"]
}}""",
            description="Analyze search results for content research",
            output_format="json",
            example_output='{"key_points":["AI adoption increased 45% YoY","ML diagnostic accuracy improved"],"trends":["Explainable AI","EHR integration"],"statistics":["$15.2B market by 2025"],"sources":["https://example.com"]}',
            notes="v1.0: Emphasizes source-based analysis, explicit DATA NOT FOUND guidance"
        )

        # ======================================================================
        # SOCIAL MEDIA PROMPTS
        # ======================================================================
        
        self._register_prompt(
            key="social.research_trends",
            category=PromptCategory.SOCIAL_MEDIA,
            template="""Analyze social media trends for: {topic}

Target Platforms: {platforms}

Provide platform-specific insights:
1. Trending formats and content types
2. 10-15 relevant hashtags
3. Current audience sentiment
4. Best posting times per platform
5. Recommended content formats

⭐ OUTPUT FORMAT (Valid JSON):
{{
  "trends_by_platform": {{"twitter": "...", "linkedin": "...", ...}},
  "hashtags": ["#tag1", "#tag2", ...],
  "sentiment": "positive|neutral|negative",
  "posting_times": {{"twitter": "9am-12pm", "linkedin": "Tuesday-Thursday", ...}},
  "content_formats": {{"twitter": "threads, polls", "linkedin": "articles, thought-leadership", ...}}
}}""",
            description="Research platform-specific social media trends",
            output_format="json",
            notes="v1.0: Platform-aware hashtag and format guidance"
        )

        self._register_prompt(
            key="social.create_post",
            category=PromptCategory.SOCIAL_MEDIA,
            template="""Create a {style} social media {content_type} for {platform}.

Topic: {topic}
Character Limit: {char_limit}
Platform Formatting: {format_guide}

⭐ REQUIREMENTS:
1. Hook in first line (grab attention immediately)
2. Platform-native language and tone
3. {hashtag_count} relevant hashtags
4. Strong call-to-action (Learn more, Link in bio, Explore, etc.)
5. Maintain {style} tone throughout
6. Stay within {char_limit} character limit

⭐ OUTPUT FORMAT (Valid JSON):
{{
  "post_text": "your text here",
  "hashtags": ["#tag1", "#tag2"],
  "cta": "call-to-action text"
}}

Example for Twitter:
{{
  "post_text": "AI is transforming healthcare 🔬 Diagnostic accuracy up 45% YoY. Hospitals investing $15B+ in ML. The future of medicine is here. [LINK]",
  "hashtags": ["#HealthTech", "#AI"],
  "cta": "Learn more"
}}""",
            description="Generate platform-optimized social media post",
            output_format="json",
            notes="v1.0: Emphasizes hook/CTA, provides platform-specific example"
        )

        # ======================================================================
        # IMAGE GENERATION PROMPTS
        # ======================================================================
        
        self._register_prompt(
            key="image.featured_image",
            category=PromptCategory.IMAGE_GENERATION,
            template="""Generate a professional, modern featured image prompt for this blog post.

Title: {title}
Category: {category}
Content Context: {content_context}

⭐ CRITICAL REQUIREMENTS:
1. Aspect ratio: 1200x630px (16:9)
2. Professional and visually appealing
3. Relevant to topic
4. Modern design aesthetic
5. High quality, suitable for blog thumbnail + social sharing

⚠️ MANDATORY: NO PEOPLE IN IMAGE
- Do NOT include any human figures, faces, portraits, people of any kind
- Focus ONLY on: objects, nature, technology, abstract concepts, landscapes, icons
- If scale needed, use non-human elements (buildings, vehicles, props, tools)

Generate ONLY the image description prompt (2-3 sentences), nothing else.""",
            description="Generate featured image prompt (no people)",
            output_format="text",
            example_output="A modern, minimalist medical technology visualization showing interconnected healthcare nodes and AI algorithms. Abstract blue and green data streams flowing through digital healthcare infrastructure. 1200x630px, professional medical tech aesthetic.",
            notes="v1.0: Strong safety guardrail against people/faces, aspect ratio specified"
        )

        self._register_prompt(
            key="image.search_queries",
            category=PromptCategory.IMAGE_GENERATION,
            template="""Generate image search queries for this blog post.

Title: {title}
Number of Images: {num_images}
Content: {content}

⭐ REQUIREMENTS:
1. Generate EXACTLY {num_images} image search queries
2. Each query: 5-10 words, specific and descriptive
3. Each query should describe what the image shows (not "AI" - more like "AI technology in hospital diagnostic center")
4. Provide accompanying alt-text for accessibility
5. Alt-text: 10-20 words, describes image for blind users

⭐ OUTPUT FORMAT (Valid JSON array):
[
  {{
    "query": "descriptive search term 1 (5-10 words)",
    "alt_text": "SEO-friendly description for accessibility (10-20 words)"
  }},
  {{
    "query": "descriptive search term 2",
    "alt_text": "Describes image for accessibility"
  }}
]

⭐ No other text, ONLY valid JSON array.""",
            description="Generate image search queries with alt-text",
            output_format="json",
            example_output='[{"query":"hospital diagnostic AI technology healthcare center","alt_text":"A healthcare professional using AI diagnostic system in a modern hospital"}]',
            notes="v1.0: Accessibility-first, word count guardrails, provides structure example"
        )

        # ======================================================================
        # CONTENT GENERATION (SYSTEM PROMPT)
        # ======================================================================
        
        self._register_prompt(
            key="system.content_writer",
            category=PromptCategory.UTILITY,
            template="""You are an expert technical writer and professional blogger specializing in {domain}.

⭐ YOUR ROLE & STYLE:
- Writing Style: {style}
- Tone: {tone}
- Target Audience: {target_audience} (education level: {education_level})
- Target Word Count: ~{target_length} words

⭐ CONTENT REQUIREMENTS:
1. Format as Markdown with properly structured headings
   - # for main title (first line ONLY)
   - ## for major sections
   - ### for subsections
2. Include compelling introduction that hooks the reader
3. Organize 3-5 main sections with practical insights
4. Include real-world examples or concrete use cases
5. Use bullet points or numbered lists for clarity
6. End with strong conclusion and call-to-action
7. Keep sentences short and paragraphs focused
8. Define technical jargon for general audience

⭐ QUALITY STANDARDS:
- Accuracy: Verify claims with available knowledge
- Clarity: Use simple language, avoid unnecessary jargon
- Engagement: Include questions, examples, or storytelling
- Value: Provide actionable insights

Tags/Topics: {tags}

Begin writing now:""",
            description="System prompt for blog content generation",
            output_format="markdown",
            notes="v1.0: Includes role, style, quality standards, and formatting guidance"
        )

    def _register_prompt(
        self,
        key: str,
        category: PromptCategory,
        template: str,
        description: str = "",
        output_format: str = "text",
        example_output: Optional[str] = None,
        notes: str = "",
        version: PromptVersion = PromptVersion.V1_1,
        created_date: str = "2026-02-07",
        last_modified: str = "2026-02-07",
    ):
        """Register a prompt with metadata"""
        self.prompts[key] = {
            "template": template,
            "version": version.value,
        }
        self.metadata[key] = PromptMetadata(
            category=category,
            version=version,
            created_date=created_date,
            last_modified=last_modified,
            description=description,
            output_format=output_format,
            example_output=example_output,
            notes=notes,
        )
        logger.debug(f"Registered prompt: {key} ({category.value})")

    def get_prompt(self, key: str, **kwargs) -> str:
        """
        Get a prompt by key and format with provided kwargs.
        
        Args:
            key: Prompt key (e.g., "blog_generation.initial_draft")
            **kwargs: Values to format into prompt template
            
        Returns:
            Formatted prompt ready for LLM
            
        Raises:
            KeyError: If prompt key not found
        """
        if key not in self.prompts:
            available = ", ".join(self.prompts.keys())
            raise KeyError(f"Prompt '{key}' not found. Available: {available}")
        
        template = self.prompts[key]["template"]
        try:
            return template.format(**kwargs)
        except KeyError as e:
            missing_var = e.args[0]
            raise KeyError(
                f"Prompt '{key}' missing required variable: {missing_var}. "
                f"Please provide: {missing_var}=..."
            )

    def get_metadata(self, key: str) -> PromptMetadata:
        """Get metadata for a prompt"""
        if key not in self.metadata:
            raise KeyError(f"Prompt '{key}' not found")
        return self.metadata[key]

    def list_prompts(self, category: Optional[PromptCategory] = None) -> Dict[str, Dict[str, Any]]:
        """List all prompts, optionally filtered by category"""
        result = {}
        for key, metadata in self.metadata.items():
            if category is None or metadata.category == category:
                result[key] = {
                    "category": metadata.category.value,
                    "description": metadata.description,
                    "output_format": metadata.output_format,
                    "version": metadata.version.value,
                    "example": metadata.example_output,
                }
        return result

    def export_prompts_as_json(self) -> str:
        """Export all prompts as JSON for documentation/migration"""
        export_data = {}
        for key, prompt_data in self.prompts.items():
            meta = self.metadata[key]
            export_data[key] = {
                "template": prompt_data["template"],
                "category": meta.category.value,
                "description": meta.description,
                "output_format": meta.output_format,
                "example_output": meta.example_output,
                "version": meta.version.value,
                "created": meta.created_date,
                "modified": meta.last_modified,
                "notes": meta.notes,
            }
        return json.dumps(export_data, indent=2)


# Global singleton instance
_prompt_manager: Optional[UnifiedPromptManager] = None


def get_prompt_manager() -> UnifiedPromptManager:
    """Get or create the global prompt manager instance"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = UnifiedPromptManager()
    return _prompt_manager
