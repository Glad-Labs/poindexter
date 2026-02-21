"""
Prompt Templates for Co-founder Agent

Centralized storage for all system prompts and templates.
Makes it easier to version, edit, and manage prompts.
"""

from typing import Dict, Optional


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

    @staticmethod
    def system_aware_chat_prompt(
        system_context: Optional[str] = None,
        user_query: Optional[str] = None,
        conversation_history: Optional[str] = None,
    ) -> str:
        """
        Generate a prompt for system-aware chat responses.

        When system_context is provided (from knowledge base), use it to ground responses.
        This prevents hallucination about system capabilities and features.
        """
        system_instruction = """You are an assistant for Glad Labs, a production-ready AI orchestration platform.

IMPORTANT: You are an expert on the Glad Labs system. Use ONLY the provided system knowledge below to answer questions about the system.

If asked about system features, architecture, capabilities, or technologies:
1. Use ONLY the system knowledge base provided
2. Be specific and accurate
3. If information is not in the knowledge base, clearly state: "I don't have information about that feature yet"
4. Do NOT make up features or technologies
5. Do NOT confuse Glad Labs with other products or industries
"""

        context_section = ""
        if system_context:
            context_section = f"""
SYSTEM KNOWLEDGE BASE:
{system_context}

"""

        history_section = ""
        if conversation_history:
            history_section = f"""
CONVERSATION HISTORY:
{conversation_history}

"""

        return f"""{system_instruction}{context_section}{history_section}
Now respond to the user's message. Be helpful, accurate, and concise."""

    @staticmethod
    def detect_system_question(query: str) -> bool:
        """
        Detect if a query is asking about the system itself.

        Returns True if the question seems to be about system architecture,
        capabilities, features, or technologies.
        """
        system_keywords = {
            # Architecture
            "architecture",
            "tech stack",
            "technology",
            "language",
            "framework",
            "built",
            "platform",
            # Capabilities
            "capability",
            "feature",
            "agent",
            "service",
            "api",
            "route",
            "endpoint",
            "provider",
            "model",
            "llm",
            # Implementation
            "port",
            "database",
            "postgres",
            "postgresql",
            "deploy",
            "deployment",
            "backend",
            "frontend",
            "authentication",
            "auth",
            # Structured questions
            "how many",
            "what are",
            "which",
            "where is",
            "does glad labs",
            "glad labs support",
            "glad labs use",
            "glad labs have",
        }

        query_lower = query.lower()
        return any(keyword in query_lower for keyword in system_keywords)
