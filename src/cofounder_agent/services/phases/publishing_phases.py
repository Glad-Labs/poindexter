"""
Publishing Phases

Handle post creation, publication, and metadata updates.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .base_phase import (
    BasePhase,
    PhaseConfig,
    PhaseInputSpec,
    PhaseInputType,
    PhaseOutputSpec,
)

logger = logging.getLogger(__name__)


class CreatePostPhase(BasePhase):
    """
    Create a post record in the database (draft status).

    Can be inserted into workflows to save content to the database.
    """

    @classmethod
    def get_phase_type(cls) -> str:
        return "create_post"

    @classmethod
    def get_phase_config(cls) -> PhaseConfig:
        return PhaseConfig(
            name="Create Post",
            description="Create a post record in the database (draft status)",
            inputs=[
                PhaseInputSpec(
                    name="content",
                    type="str",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="Post content (markdown)",
                    required=True,
                    accepts_from_phases=["generate_content"],
                ),
                PhaseInputSpec(
                    name="topic",
                    type="str",
                    source=PhaseInputType.USER_PROVIDED,
                    description="Post title/topic",
                    required=True,
                ),
                PhaseInputSpec(
                    name="seo_title",
                    type="str",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="SEO title for metadata",
                    required=False,
                    accepts_from_phases=["generate_seo"],
                ),
                PhaseInputSpec(
                    name="seo_description",
                    type="str",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="SEO description for metadata",
                    required=False,
                    accepts_from_phases=["generate_seo"],
                ),
                PhaseInputSpec(
                    name="seo_keywords",
                    type="list[str]",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="SEO keywords",
                    required=False,
                    accepts_from_phases=["generate_seo"],
                ),
                PhaseInputSpec(
                    name="image_url",
                    type="str",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="Featured image URL",
                    required=False,
                    accepts_from_phases=["search_image"],
                ),
            ],
            outputs=[
                PhaseOutputSpec(
                    name="post_id",
                    type="str",
                    description="Created post ID (UUID)",
                ),
                PhaseOutputSpec(
                    name="slug",
                    type="str",
                    description="Post slug (URL-safe identifier)",
                ),
                PhaseOutputSpec(
                    name="status",
                    type="str",
                    description="Post status (draft)",
                ),
            ],
            configurable_params={
                "status": "draft",  # draft or published
                "category_id": None,  # Optional category
            },
        )

    async def execute(self, inputs: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Create a post record"""

        is_valid, error = await self.validate_inputs(inputs)
        if not is_valid:
            self.status = "failed"
            self.error = error
            raise ValueError(error)

        try:
            from slugify import slugify

            from .database_service import DatabaseService

            content = inputs.get("content")
            topic = inputs.get("topic")
            seo_title = inputs.get("seo_title", topic)
            seo_description = inputs.get("seo_description", "")
            seo_keywords = inputs.get("seo_keywords", [])
            image_url = inputs.get("image_url")

            # Generate slug from title
            slug = slugify(seo_title)

            post_data = {
                "title": seo_title,
                "slug": slug,
                "content": content,
                "featured_image_url": image_url,
                "seo_title": seo_title,
                "seo_description": seo_description,
                "seo_keywords": seo_keywords,
                "status": config.get("status", "draft"),
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }

            if config.get("category_id"):
                post_data["category_id"] = config.get("category_id")

            logger.info(f"[CreatePostPhase] Creating post: {seo_title} (slug: {slug})")

            # TODO: Use database_service to create post
            # For now, just simulate
            post_id = f"post_{id(post_data)}"

            self.status = "completed"
            self.result = {
                "post_id": post_id,
                "slug": slug,
                "status": config.get("status", "draft"),
                "post_data": post_data,  # For reference
            }

            logger.info(f"[CreatePostPhase] Created post {post_id}")

            return self.result

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logger.error(f"[CreatePostPhase] Error: {str(e)}", exc_info=True)
            raise


class PublishPostPhase(BasePhase):
    """
    Publish a post (update status and set published_at).

    Typically used after CreatePostPhase with approval.
    """

    @classmethod
    def get_phase_type(cls) -> str:
        return "publish_post"

    @classmethod
    def get_phase_config(cls) -> PhaseConfig:
        return PhaseConfig(
            name="Publish Post",
            description="Publish a post (set status=published and published_at timestamp)",
            inputs=[
                PhaseInputSpec(
                    name="post_id",
                    type="str",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="Post ID to publish",
                    required=True,
                    accepts_from_phases=["create_post"],
                ),
                PhaseInputSpec(
                    name="slug",
                    type="str",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="Post slug",
                    required=True,
                    accepts_from_phases=["create_post"],
                ),
            ],
            outputs=[
                PhaseOutputSpec(
                    name="post_id",
                    type="str",
                    description="Published post ID",
                ),
                PhaseOutputSpec(
                    name="published_at",
                    type="str",
                    description="Publication timestamp (ISO 8601)",
                ),
                PhaseOutputSpec(
                    name="public_url",
                    type="str",
                    description="Public URL for the post",
                ),
            ],
            configurable_params={
                "base_url": "https://example.com",  # Base URL for public posts
            },
        )

    async def execute(self, inputs: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Publish a post"""

        is_valid, error = await self.validate_inputs(inputs)
        if not is_valid:
            self.status = "failed"
            self.error = error
            raise ValueError(error)

        try:
            post_id = inputs.get("post_id")
            slug = inputs.get("slug")
            base_url = config.get("base_url", "https://example.com")

            published_at = datetime.now(timezone.utc).isoformat()
            public_url = f"{base_url}/posts/{slug}"

            logger.info(f"[PublishPostPhase] Publishing post {post_id}")

            # TODO: Use database_service to update post status to "published"
            # For now, just simulate

            self.status = "completed"
            self.result = {
                "post_id": post_id,
                "published_at": published_at,
                "public_url": public_url,
            }

            logger.info(f"[PublishPostPhase] Published at {public_url}")

            return self.result

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logger.error(f"[PublishPostPhase] Error: {str(e)}", exc_info=True)
            raise
