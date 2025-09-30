import logging
import re
import os
from markdown import markdown
from utils.data_models import BlogPost
from crewai import Agent

IMAGE_PLACEHOLDER_COMMENT = "<!-- image_placeholder_{i} -->"

class EditingAgent:
    """
    Handles the final processing of the text content before publishing.
    Its primary role is to sanitize the Markdown, convert it to clean HTML,
    and replace image placeholders with uniquely identifiable HTML comments.
    """
    def run(self, post: BlogPost) -> BlogPost:
        logging.info(f"EditingAgent: Performing final edits for '{post.generated_title}'.")
        
        # Replace image placeholders with final GCS URLs
        content_with_images = post.raw_content
        for i, image_detail in enumerate(post.images):
            if image_detail.public_url:
                placeholder = f"[IMAGE-{i+1}]"
                markdown_img_tag = f"![{image_detail.alt_text}]({image_detail.public_url})"
                content_with_images = content_with_images.replace(placeholder, markdown_img_tag)
        
        # Perform other editing tasks like grammar checks if needed
        # For now, we just assign the content with images
        post.edited_content = content_with_images
        
        logging.info("Final image URLs have been embedded into the content.")
        return post

    def _sanitize_markdown(self, content: str) -> str:
        """Sanitizes the Markdown content."""
        return re.sub(r'\n{3,}', r'\n\n', content).strip()

    def _replace_image_placeholders(self, content: str) -> str:
        """Replaces image placeholders with HTML comments."""
        placeholders = re.findall(r'\[IMAGE.*\]', content)
        image_count = len(placeholders)
        
        content_with_placeholders = content
        if image_count > 0:
            for i in range(image_count):
                content_with_placeholders = re.sub(r'\[IMAGE.*\]', IMAGE_PLACEHOLDER_COMMENT.format(i=i), content_with_placeholders, count=1)
            logging.info(f"Replaced {image_count} image placeholders with HTML comments.")
        else:
            logging.warning("No [IMAGE] placeholders were found in the content to be replaced.")
            
        return content_with_placeholders

def create_editing_agent():
    """
    Creates the Editing Agent.
    This agent refines content for clarity, style, and accuracy.
    """
    return Agent(
        role='Expert Technical Editor',
        goal='Review the generated blog post topic, ensuring it is clear, concise, and grammatically perfect. Add a compelling headline.',
        backstory=(
            "You are a meticulous editor with a sharp eye for detail. You specialize in technology content, "
            "ensuring that every piece is not only well-written but also technically accurate. "
            "Your mission is to elevate content from good to great, polishing it to a professional standard."
        ),
        verbose=True,
        allow_delegation=False
    )
