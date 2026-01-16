"""
Prompt Templates for Co-founder Agent

Centralized storage for all system prompts and templates.
Makes it easier to version, edit, and manage prompts.
"""

from typing import Optional, Dict


class PromptTemplates:
    """Collection of prompt templates"""

    @staticmethod
    def blog_generation_prompt(
        topic: str,
        primary_keyword: Optional[str] = None,
        target_audience: Optional[str] = None,
        category: Optional[str] = None,
        style: Optional[str] = None,
        tone: Optional[str] = None,
        target_length: Optional[int] = None,
    ) -> str:
        """Generate a prompt for blog post creation"""
        prompt = f"Generate a blog post about '{topic}'."

        if primary_keyword:
            prompt += f" Focus on keywords: {primary_keyword}."

        if target_audience:
            prompt += f" Target audience is {target_audience}."

        if category:
            prompt += f" Category: {category}."

        if style:
            prompt += f" Writing style: {style}."

        if tone:
            prompt += f" Tone: {tone}."

        if target_length:
            prompt += f" Target length is approximately {target_length} words."
        else:
            prompt += " Ensure the content is professional and approximately 1500-2000 words."

        return prompt

    @staticmethod
    def content_critique_prompt(content: str, context: Optional[Dict] = None) -> str:
        """Generate a prompt for content critique"""
        context_str = ""
        style_guidance = ""

        if context:
            if context.get("topic"):
                context_str += f"Topic: {context.get('topic')}\n"
            if context.get("target_audience"):
                context_str += f"Target Audience: {context.get('target_audience')}\n"
            if context.get("primary_keyword"):
                context_str += f"Keywords: {context.get('primary_keyword')}\n"
            if context.get("style"):
                context_str += f"Style: {context.get('style')}\n"
            if context.get("tone"):
                context_str += f"Tone: {context.get('tone')}\n"
            if context.get("target_length"):
                context_str += f"Target Length: {context.get('target_length')} words\n"
            if context.get("writing_style_guidance"):
                style_guidance = (
                    f"\n\nWRITING STYLE REFERENCE:\n{context.get('writing_style_guidance')}\n"
                )

        return f"""
You are an expert content editor and QA specialist. Your task is to critique the following blog post content.

CONTEXT:
{context_str}{style_guidance}
CONTENT TO EVALUATE:
{content[:10000]}  # Truncate if too long

INSTRUCTIONS:
Evaluate the content based on the following criteria:
1. Tone and Voice: Is it professional and appropriate for the audience?
2. Structure: Does it have a clear introduction, body, and conclusion? Are headings used effectively?
3. SEO: Are keywords used naturally?
4. Engagement: Is the content engaging and valuable?
5. Accuracy: Does it seem factually plausible (within general knowledge)?
{"6. Writing Style Consistency: If a writing style reference is provided above, does the content match that style? Pay attention to vocabulary, sentence structure, tone, and overall voice." if style_guidance else ""}
- quality_score: (0-100)
- approved: (boolean, true if score >= 75)
- feedback: (string, summary of feedback)
- suggestions: (list of strings, specific improvements)
- needs_refinement: (boolean, true if score < 85)

Do not include any markdown formatting or explanations outside the JSON.
"""
