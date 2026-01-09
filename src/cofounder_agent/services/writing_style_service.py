"""
Writing Style Service

Service layer for managing and retrieving writing samples for LLM prompt integration.
Handles retrieval of active writing sample and formatting for inclusion in system prompts.
"""

import logging
from typing import Optional, Dict, Any

from services.database_service import DatabaseService

logger = logging.getLogger(__name__)


class WritingStyleService:
    """Service for managing writing samples and style guidance"""

    def __init__(self, database_service: DatabaseService):
        """
        Initialize writing style service.
        
        Args:
            database_service: Database service instance for sample operations
        """
        self.db = database_service

    async def get_active_style_prompt(self, user_id: str) -> str:
        """
        Get the active writing sample formatted for inclusion in LLM prompts.
        
        Args:
            user_id: User ID
            
        Returns:
            Formatted prompt guidance string, or empty string if no active sample
        """
        try:
            sample = await self.db.writing_style.get_active_writing_sample(user_id)
            
            if not sample or not sample.get("content"):
                return ""
            
            return self._format_sample_for_prompt(sample)
            
        except Exception as e:
            logger.error(f"Error retrieving active writing style: {e}")
            return ""

    async def get_style_prompt_for_generation(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get writing sample data structured for use during content generation.
        
        Returns dict with:
        - sample_text: The full writing sample
        - writing_style_guidance: Formatted guidance for inclusion in prompts
        
        Args:
            user_id: User ID
            
        Returns:
            Dict with sample info, or None if no active sample
        """
        try:
            sample = await self.db.writing_style.get_active_writing_sample(user_id)
            
            if not sample:
                return None
            
            return {
                "sample_id": sample.get("id"),
                "sample_title": sample.get("title"),
                "sample_text": sample.get("content"),
                "writing_style_guidance": self._format_sample_for_prompt(sample),
                "word_count": sample.get("word_count"),
                "description": sample.get("description")
            }
            
        except Exception as e:
            logger.error(f"Error preparing writing sample for generation: {e}")
            return None

    async def get_style_prompt_for_specific_sample(self, writing_style_id: str) -> Optional[Dict[str, Any]]:
        """
        Get writing sample data for a specific writing sample ID.
        
        Args:
            writing_style_id: UUID of the writing sample
            
        Returns:
            Dict with sample info, or None if sample not found
        """
        try:
            sample = await self.db.writing_style.get_writing_sample(writing_style_id)
            
            if not sample:
                logger.warning(f"Writing sample not found: {writing_style_id}")
                return None
            
            return {
                "sample_id": sample.get("id"),
                "sample_title": sample.get("title"),
                "sample_text": sample.get("content"),
                "writing_style_guidance": self._format_sample_for_prompt(sample),
                "word_count": sample.get("word_count"),
                "description": sample.get("description")
            }
            
        except Exception as e:
            logger.error(f"Error retrieving specific writing sample {writing_style_id}: {e}")
            return None

    @staticmethod
    def _format_sample_for_prompt(sample: Dict[str, Any]) -> str:
        """
        Format a writing sample for inclusion in LLM prompts.
        
        Creates guidance text that the LLM can use to match the writing style.
        
        Args:
            sample: Writing sample dict from database
            
        Returns:
            Formatted prompt guidance string
        """
        title = sample.get("title", "User Writing Sample")
        description = sample.get("description", "")
        content = sample.get("content", "")
        
        # Create the guidance prompt
        guidance = f"""
## Writing Style Reference

**Sample Title:** {title}
"""
        
        if description:
            guidance += f"**Description:** {description}\n\n"
        
        guidance += f"""**Sample Text:**
```
{content}
```

**Instructions:** 
Analyze the above writing sample and match its style, tone, vocabulary, sentence structure, and overall voice in your generation. Pay particular attention to:
- The writer's preferred sentence length and structure
- Vocabulary complexity and word choice preferences
- Tone (formal, casual, professional, etc.)
- Use of examples, transitions, and organizational patterns
- Paragraph structure and pacing
- Any distinctive stylistic choices or preferences

Generate content that a reader would believe came from the same author.
"""
        
        return guidance.strip()
