import logging
import json
import re
from typing import Dict, Any, List, Optional
from pydantic import ValidationError
import config # Add this import
from services.llm_client import LLMClient
from services.local_llm_client import LocalLLMClient
from utils.data_models import BlogPost, ImageDetails, ContentGenerationError, QAReview, ImagePathDetails
from utils.helpers import load_prompts_from_file # Import the new helper
from config import MAX_IMAGE_GEN_ATTEMPTS, MAX_IMAGE_METADATA_ATTEMPTS, MAX_CONTENT_GEN_ATTEMPTS # Updated import
import utils.logging_config as log_utils # FIX: Import logging_config
from crewai import Agent

logger = logging.getLogger(__name__)
prompts_logger = log_utils.prompts_logger # FIX: Use imported logger

class CreativeAgent:
    def __init__(self, llm_client: LLMClient, local_llm_client: LocalLLMClient, persona: str): # Remove refinement_loops
        self.llm_client = llm_client
        self.local_llm_client = local_llm_client
        self.persona = persona
        self.prompts = load_prompts_from_file(config.PROMPTS_PATH) # Use helper
        logger.info(f"CreativeAgent initialized for {self.persona} persona.")

    def run(self, post: BlogPost) -> BlogPost:
        """
        Orchestrates the content generation process, including initial draft,
        iterative refinement, title/tag generation, image metadata generation,
        and SEO/social media asset generation.
        """
        logging.info(f"CreativeAgent: Starting iterative content generation for '{post.topic}'.")

        initial_draft = None
        for attempt in range(MAX_CONTENT_GEN_ATTEMPTS):
            logging.info(f"CreativeAgent: Generating initial draft (Attempt {attempt + 1}/{MAX_CONTENT_GEN_ATTEMPTS})...")
            initial_draft_prompt = self.prompts['initial_draft_generation'].format(
                topic=post.topic,
                primary_keyword=post.primary_keyword,
                secondary_keywords=", ".join(post.secondary_keywords),
                target_audience=post.target_audience,
                internal_link_titles=", ".join(post.internal_links) if post.internal_links else "None available" # FIX: Pass titles
            )
            initial_draft = self.llm_client.generate_text_content(initial_draft_prompt)
            if initial_draft:
                logging.info("Initial draft generated.")
                post.raw_content = initial_draft.strip()
                
                # FIX: Replace internal link titles with actual Markdown links after LLM generation
                if post.internal_links and post.published_posts_map: # Ensure map is available
                    for link_title in post.internal_links:
                        if link_title in post.published_posts_map:
                            url = post.published_posts_map[link_title]
                            # Use regex to replace only the title, not parts of other words
                            post.raw_content = re.sub(r'\b' + re.escape(link_title) + r'\b', f'[{link_title}]({url})', post.raw_content, flags=re.IGNORECASE)
                            logging.info(f"Replaced internal link title '{link_title}' with Markdown link.")
                        else:
                            logging.warning(f"Could not find published URL for internal link title: '{link_title}'. Not replacing.")
                break
            logging.warning(f"CreativeAgent: Initial draft generation failed on attempt {attempt + 1}. Retrying...")
            if attempt == MAX_CONTENT_GEN_ATTEMPTS - 1:
                logging.error("CreativeAgent: LLM failed to generate valid initial draft after multiple attempts. This is a critical failure.")
                raise ContentGenerationError("CreativeAgent: Failed to generate initial draft from LLM.")

        # Iterative refinement loops
        for i in range(post.refinement_loops): # Use post.refinement_loops
            logging.info(f"Starting refinement loop {i+1}/{post.refinement_loops}...")
            critique_prompt = self.prompts['self_critique_and_analysis'].format(
                draft=post.raw_content,
                persona=self.persona,
            )
            critique = self.llm_client.generate_text_content(critique_prompt)
            if not critique:
                logging.warning(f"CreativeAgent: Failed to generate self-critique in loop {i+1}. Skipping refinement.")
                continue
            logging.info("Self-critique generated.")

            refinement_prompt = self.prompts['iterative_refinement'].format( # Corrected key
                draft=post.raw_content,
                critique=critique,
                persona=self.persona,
            )
            refined_draft_text = self.llm_client.generate_text_content(refinement_prompt)
            if not refined_draft_text:
                logging.warning(f"CreativeAgent: Failed to refine draft in loop {i+1}. Keeping previous draft.")
                continue

            try:
                # Attempt to parse as JSON, if it's a structured response
                refined_draft_json = json.loads(refined_draft_text)
                post.raw_content = refined_draft_json.get("blog_post", refined_draft_json.get("body", refined_draft_text))
            except json.JSONDecodeError:
                post.raw_content = refined_draft_text

            logging.info(f"Draft refined based on critique in loop {i+1}.")

        if not post.raw_content:
            raise ContentGenerationError("CreativeAgent: LLM failed to generate valid blog post content after refinement loops.")

        # Programmatically insert image placeholders if none were generated by the LLM
        if '[IMAGE]' not in post.raw_content:
            num_images_to_insert = config.DEFAULT_IMAGE_PLACEHOLDERS
            paragraphs = [p.strip() for p in post.raw_content.split('\n\n') if p.strip()]
            if len(paragraphs) > num_images_to_insert:
                # Insert images after the first few paragraphs
                insert_indices = [len(paragraphs) // (num_images_to_insert + 1) * (j + 1) for j in range(num_images_to_insert)]
                for idx in sorted(insert_indices, reverse=True):
                    paragraphs.insert(idx, '[IMAGE]')
                post.raw_content = '\n\n'.join(paragraphs)
                logging.info(f"Programmatically inserted {num_images_to_insert} [IMAGE] placeholders into the final draft.")
            else:
                logging.warning("CreativeAgent: Not enough paragraphs to programmatically insert image placeholders.")


        # Generate title and tags
        logging.info("CreativeAgent: Generating title and tags...")
        title_tags_prompt = self.prompts['generate_title_and_tags'].format(
            final_content=post.raw_content
        )
        title_tags_response = self.local_llm_client.generate_structured_content(title_tags_prompt)
        if title_tags_response and "title" in title_tags_response and "tags" in title_tags_response:
            post.generated_title = title_tags_response['title']
            post.tags = title_tags_response['tags']
            logging.info(f"Successfully generated title: '{post.generated_title}'")
        else:
            logging.error("CreativeAgent: Failed to generate final structured assets from LLM.")
            raise ContentGenerationError("CreativeAgent: Failed to generate final structured assets from LLM.")

        # Generate image metadata
        for attempt in range(MAX_IMAGE_METADATA_ATTEMPTS):
            logging.info(f"CreativeAgent: Generating image metadata (Attempt {attempt + 1}/{MAX_IMAGE_METADATA_ATTEMPTS})...")
            image_metadata_prompt = self.prompts['image_metadata_generation'].format(
                raw_content=post.raw_content
            )
            image_response = self.local_llm_client.generate_structured_content(image_metadata_prompt)

            if image_response and "images" in image_response:
                validated_images = self._validate_and_log_images(image_response)
                if validated_images:
                    post.images = validated_images
                    logging.info(f"Successfully generated and validated metadata for {len(post.images)} images.")
                    break
            
            logging.warning(f"CreativeAgent: Image metadata generation failed on attempt {attempt + 1}. Retrying...")
            if attempt == MAX_IMAGE_METADATA_ATTEMPTS - 1:
                logging.error("CreativeAgent: Failed to generate any valid image metadata after multiple attempts. This is a critical failure.")
                raise ContentGenerationError("CreativeAgent: Failed to generate any valid image metadata.")

        # Generate SEO and social media assets
        logging.info("CreativeAgent: Generating SEO and social media assets...")
        seo_social_prompt = self.prompts['generate_seo_and_social'].format(
            final_title=post.generated_title,
            final_content=post.raw_content,
            tags=", ".join(post.tags)
        )
        seo_social_response = self.local_llm_client.generate_structured_content(seo_social_prompt)
        if seo_social_response:
            post.meta_description = seo_social_response.get('meta_description')
            post.related_keywords = seo_social_response.get('related_keywords', [])
            post.social_media_posts = seo_social_response.get('social_media_posts', {})
            logging.info("Successfully generated SEO and social media assets.")
        else:
            logging.warning("CreativeAgent: Failed to generate SEO and social media assets.")

        logging.info(f"Successfully generated all final assets for '{post.generated_title}'.")
        return post

    def _validate_and_log_images(self, image_response: Dict[str, Any]) -> List[ImageDetails]:
        """Validates image metadata and logs any issues."""
        validated_images = []
        if "images" in image_response and isinstance(image_response["images"], list):
            for img_data in image_response["images"]:
                try:
                    validated_images.append(ImageDetails(**img_data))
                except ValidationError as e:
                    logging.warning(f"Skipping invalid image metadata: {img_data}. Error: {e}")
        
        if not validated_images:
            logging.warning("No valid image metadata was generated or all were invalid.")
        return validated_images

    def refine_image_assets(self, post: BlogPost) -> Optional[BlogPost]:
        """
        Uses QA feedback to refine the generated image assets.
        Returns the updated post on success, or None on failure.
        """
        logging.info(f"CreativeAgent: Refining image assets based on QA feedback for '{post.generated_title}'.")
        
        previous_images_str = json.dumps([img.model_dump() for img in post.images], indent=2)
        
        correction_prompt = self.prompts['image_asset_correction'].format(
            final_content=post.raw_content,
            previous_images=previous_images_str,
            rejection_reason=post.qa_review.rejection_reason if post.qa_review else ""
        )
        
        structured_response = self.local_llm_client.generate_structured_content(correction_prompt)

        if structured_response and "images" in structured_response:
            validated_images = self._validate_and_log_images(structured_response)

            if not validated_images:
                 logging.error("Self-correction failed to produce any valid image metadata.")
                 return None

            post.images = validated_images
            logging.info("Successfully refined image assets based on QA feedback.")
            return post
        else:
            logging.error("Failed to refine image assets. The local LLM did not return a valid correction.")
            return None

def create_creative_agent():
    """
    Creates the Creative Agent.
    This agent is responsible for generating initial ideas and content drafts.
    """
    return Agent(
        role='Senior Content Strategist',
        goal='Generate a compelling and original topic for a blog post about the future of AI in business.',
        backstory=(
            "You are a visionary content strategist with a deep understanding of technology trends. "
            "Your expertise lies in identifying breakthrough topics that captivate audiences and "
            "establish thought leadership. You are known for your creative flair and ability to "
            "translate complex concepts into engaging narratives."
        ),
        verbose=True,
        allow_delegation=False
    )
