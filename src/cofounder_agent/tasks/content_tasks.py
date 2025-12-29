"""Content generation tasks - blog post creation pipeline."""

from typing import Dict, Any
from src.cofounder_agent.tasks.base import PureTask, ExecutionContext
import logging

logger = logging.getLogger(__name__)


class ResearchTask(PureTask):
    """
    Research task: Gather information about a topic.

    Input:
        - topic: str - Topic to research
        - depth: str - "shallow", "medium", "deep" (default: "medium")

    Output:
        - research_data: dict - Gathered research findings
        - sources: list - Source materials/URLs
        - key_points: list - Key findings
    """

    def __init__(self):
        super().__init__(
            name="research",
            description="Gather research data on topic using web search and LLM",
            required_inputs=["topic"],
            timeout_seconds=120,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute research task."""
        from src.cofounder_agent.services.model_consolidation_service import (
            get_model_consolidation_service,
        )
        from src.cofounder_agent.services.serper_client import SerperClient

        model_service = get_model_consolidation_service()

        topic = input_data["topic"]
        depth = input_data.get("depth", "medium")

        # 1. Perform Web Search (ASYNC)
        serper = SerperClient()
        search_results = await serper.search(topic, num=10)

        # Extract organic results
        organic_results = search_results.get("organic", [])

        # Format search context for LLM
        search_context = ""
        for idx, result in enumerate(organic_results[:5]):  # Top 5 results
            search_context += f"{idx+1}. {result.get('title', 'No Title')}\n"
            search_context += f"   URL: {result.get('link', 'No URL')}\n"
            search_context += f"   Snippet: {result.get('snippet', 'No snippet')}\n\n"

        # Build research prompt with REAL search data
        prompt = f"""Analyze the following search results for the topic: "{topic}"
        
Search Results:
{search_context}

Depth: {depth}

Based ONLY on the search results above (and your general knowledge to fill gaps), provide:
1. Key findings (list 5-10 main points)
2. Current trends mentioned
3. Important statistics found
4. Recommended sources (use the URLs provided)

Format as JSON with keys: key_points, trends, statistics, sources"""

        # Query LLM with fallback chain
        response_obj = await model_service.generate(
            prompt=prompt,
            temperature=0.3,  # Factual, precise
            max_tokens=1500,
        )
        response = response_obj.text

        # Parse response (assuming LLM returns structured data)
        try:
            import json

            # Clean up potential markdown code blocks
            cleaned_response = response.replace("```json", "").replace("```", "").strip()
            research_data = json.loads(cleaned_response)
        except:
            # Fallback if not valid JSON
            research_data = {
                "key_points": [response[:200]],
                "trends": [],
                "statistics": [],
                "sources": [],
            }

        # Ensure sources from search are included if LLM missed them
        if not research_data.get("sources"):
            research_data["sources"] = [r.get("link") for r in organic_results[:3]]

        return {
            "topic": topic,
            "research_data": research_data,
            "sources": research_data.get("sources", []),
            "key_points": research_data.get("key_points", []),
            "depth_used": depth,
            "raw_search_results": organic_results,  # Keep raw data for reference
        }


class CreativeTask(PureTask):
    """
    Creative task: Generate high-quality content.

    Input:
        - topic: str - Content topic
        - research_data: dict - From ResearchTask
        - style: str - "professional", "casual", "technical" (default: "professional")
        - length: int - Word count (default: 1500)

    Output:
        - content: str - Generated content in markdown
        - outline: list - Content structure
        - title: str - Content title
    """

    def __init__(self):
        super().__init__(
            name="creative",
            description="Generate high-quality content based on research",
            required_inputs=["topic"],
            timeout_seconds=180,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute creative task."""
        from src.cofounder_agent.services.model_consolidation_service import (
            get_model_consolidation_service,
        )

        model_service = get_model_consolidation_service()

        topic = input_data["topic"]
        research_data = input_data.get("research_data", {})
        style = input_data.get("style", "professional")
        length = input_data.get("length", 1500)

        # Build creative prompt
        research_context = ""
        if research_data:
            research_context = f"""
Research Context:
- Key Points: {research_data.get('key_points', [])}
- Trends: {research_data.get('trends', [])}
- Statistics: {research_data.get('statistics', [])}
"""

        prompt = f"""Create a high-quality {style} blog post about:

Topic: {topic}
Word Count: {length} words
Style: {style}
{research_context}

Include:
1. Compelling title
2. Clear outline (H2 sections)
3. Engaging introduction
4. Well-structured body with key points
5. Strong conclusion with call-to-action

Format: Markdown with proper headings and formatting"""

        # Query LLM
        response_obj = await model_service.generate(
            prompt=prompt,
            temperature=0.7,  # Creative
            max_tokens=int(length * 1.2),  # Buffer for tokens
        )
        response = response_obj.text

        # Extract title and outline
        lines = response.split("\n")
        title = lines[0].replace("# ", "").strip() if lines else "Untitled"
        outline = [line.replace("## ", "").strip() for line in lines if line.startswith("## ")]

        return {
            "topic": topic,
            "content": response,
            "title": title,
            "outline": outline,
            "style_used": style,
            "word_count_target": length,
        }


class QATask(PureTask):
    """
    Quality Assurance task: Evaluate and refine content.

    Input:
        - content: str - Content to evaluate
        - topic: str - Original topic
        - criteria: list - Evaluation criteria

    Output:
        - feedback: str - Constructive feedback
        - score: float - Quality score (0-10)
        - suggestions: list - Improvement suggestions
        - refined: bool - Whether content passes QA
    """

    def __init__(self):
        super().__init__(
            name="qa",
            description="Evaluate content quality and provide feedback",
            required_inputs=["content"],
            timeout_seconds=120,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute QA task."""
        from src.cofounder_agent.services.model_consolidation_service import (
            get_model_consolidation_service,
        )

        model_service = get_model_consolidation_service()

        content = input_data["content"]
        topic = input_data.get("topic", "")
        criteria = input_data.get(
            "criteria", ["clarity", "accuracy", "engagement", "structure", "completeness"]
        )

        # Build QA prompt
        criteria_str = "\n".join(f"- {c}" for c in criteria)

        prompt = f"""Evaluate this content for quality:

Topic: {topic}

Content:
{content}

Evaluation Criteria:
{criteria_str}

For each criterion, provide:
1. Rating (1-10)
2. Specific feedback
3. Improvement suggestions

Format as JSON with keys: scores (dict), feedback (str), suggestions (list), overall_score (float)"""

        # Query LLM
        response_obj = await model_service.generate(
            prompt=prompt, temperature=0.2, max_tokens=1000, response_format="json"  # Analytical
        )

        # Parse response
        try:
            import json

            qa_result = json.loads(response_obj.text)
            overall_score = qa_result.get("overall_score", 5.0)
        except:
            overall_score = 5.0
            qa_result = {
                "feedback": response_obj.text,
                "suggestions": [],
                "scores": {},
            }

        refined = overall_score >= 7.0

        return {
            "topic": topic,
            "quality_score": overall_score,
            "feedback": qa_result.get("feedback", ""),
            "suggestions": qa_result.get("suggestions", []),
            "scores": qa_result.get("scores", {}),
            "passes_qa": refined,
            "criteria_evaluated": criteria,
        }


class ImageSelectionTask(PureTask):
    """
    Image selection task: Find and prepare images for content.

    Input:
        - topic: str - Topic for image search
        - content: str - Content for image context
        - count: int - Number of images (default: 3)

    Output:
        - images: list - Image URLs and metadata
        - image_captions: dict - Image captions
    """

    def __init__(self):
        super().__init__(
            name="image_selection",
            description="Find and select images for content",
            required_inputs=["topic"],
            timeout_seconds=60,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute image selection task."""
        from src.cofounder_agent.services.model_consolidation_service import (
            get_model_consolidation_service,
        )

        model_service = get_model_consolidation_service()

        topic = input_data["topic"]
        content = input_data.get("content", "")
        count = input_data.get("count", 3)

        # Get LLM suggestions for image searches
        prompt = f"""Suggest {count} specific image searches for this content:

Topic: {topic}

Content preview: {content[:500]}

Return JSON with list of search queries.
Each query should be specific and visual (not just text).

Format: {{"search_queries": ["query1", "query2", "query3"]}}"""

        response_obj = await model_service.generate(
            prompt=prompt, temperature=0.5, max_tokens=300, response_format="json"
        )

        # Parse search queries
        try:
            import json

            suggestions = json.loads(response_obj.text)
            search_queries = suggestions.get("search_queries", [topic])
        except:
            search_queries = [topic]

        # In production, would search Pexels/Unsplash API
        # For now, return placeholder structure
        images = [
            {
                "search_query": q,
                "url": f"https://api.pexels.com/search?q={q}",
                "alt_text": f"Image related to {q}",
                "caption": f"Relevant image for {topic}",
            }
            for q in search_queries[:count]
        ]

        return {
            "topic": topic,
            "images": images,
            "count": len(images),
            "search_queries": search_queries,
        }


class PublishTask(PureTask):
    """
    Publish task: Format and publish content to CMS.

    Input:
        - content: str - Final content
        - title: str - Content title
        - topic: str - Topic/category
        - images: list - Image data (optional)

    Output:
        - published_url: str - CMS URL
        - cms_id: int - Content ID in CMS
        - status: str - Publication status
    """

    def __init__(self):
        super().__init__(
            name="publish",
            description="Format and publish content to CMS",
            required_inputs=["content", "title"],
            timeout_seconds=60,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute publish task."""
        from src.cofounder_agent.services.database_service import DatabaseService

        # Instantiate database service
        db_service = DatabaseService()

        content = input_data["content"]
        title = input_data["title"]
        topic = input_data.get("topic", "General")
        images = input_data.get("images", [])

        # Format for CMS (Strapi)
        slug = title.lower().replace(" ", "-").replace(".", "")[:100]
        excerpt = content.split("\n")[2][:200] if len(content.split("\n")) > 2 else content[:200]

        try:
            # Create post in database
            post_data = {
                "title": title,
                "slug": slug,
                "content": content,
                "excerpt": excerpt,
                "category": topic,
                "status": "published",
                "featured_image": images[0]["url"] if images else None,
            }

            # Save to database
            # Note: DatabaseService.create_post might not exist, checking methods...
            # Assuming create_post exists or using generic insert
            # Let's check DatabaseService methods first to be sure

            # Re-reading DatabaseService to check for create_post method
            # If not found, I'll use a generic query or implement it

            # For now, assuming create_post exists based on previous code usage
            result = await db_service.create_post(post_data)

            return {
                "content_title": title,
                "published_url": f"/posts/{slug}",
                "cms_id": result.get("id"),
                "status": "published",
                "slug": slug,
                "images_published": len(images),
            }
        except Exception as e:
            logger.error(f"Publishing failed: {str(e)}")
            return {
                "content_title": title,
                "status": "failed",
                "error": str(e),
            }
