"""
Publishing Phases

Handle post creation, publication, and metadata updates.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from services.logger_config import get_logger

from .base_phase import BasePhase, PhaseConfig, PhaseInputSpec, PhaseInputType, PhaseOutputSpec


async def _resolve_database_service(config: dict[str, Any]) -> tuple[Any, bool]:
    """Return database service and whether this phase created it."""
    db_service = config.get("database_service")
    if db_service is not None:
        return db_service, False

    from ..database_service import DatabaseService

    db_service = DatabaseService()
    await db_service.initialize()
    return db_service, True


async def _close_database_service(db_service: Any, owns_service: bool) -> None:
    """Close owned database service instances without masking execution errors."""
    if not owns_service:
        return

    try:
        await db_service.close()
    except Exception as close_error:
        logger.warning(
            "[_close_database_service] Failed to close database service: %s", close_error,
            exc_info=True,
        )


def _extract_field(payload: Any, field: str, default: Any = None) -> Any:
    """Read fields from dict-like or model-like response payloads."""
    if payload is None:
        return default

    if isinstance(payload, dict):
        return payload.get(field, default)

    if hasattr(payload, "model_dump"):
        dumped = payload.model_dump()
        if isinstance(dumped, dict):
            return dumped.get(field, default)

    return getattr(payload, field, default)


logger = get_logger(__name__)


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

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Create a post record"""

        is_valid, error = await self.validate_inputs(inputs)
        if not is_valid:
            self.status = "failed"
            self.error = error
            raise ValueError(error)

        db_service = None
        owns_service = False

        try:
            from slugify import slugify

            content = inputs.get("content")
            topic = inputs.get("topic")
            seo_title = inputs.get("seo_title", topic)
            seo_description = inputs.get("seo_description", "")
            seo_keywords = inputs.get("seo_keywords", [])
            image_url = inputs.get("image_url")

            # Generate slug from title
            slug = slugify(seo_title or "")  # type: ignore[arg-type]

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

            logger.info("[CreatePostPhase] Creating post: %s (slug: %s)", seo_title, slug)

            db_service, owns_service = await _resolve_database_service(config)
            created_post = await db_service.create_post(post_data)

            post_id = str(_extract_field(created_post, "id", ""))
            persisted_slug = _extract_field(created_post, "slug", slug)
            persisted_status = _extract_field(created_post, "status", config.get("status", "draft"))

            self.status = "completed"
            self.result = {
                "post_id": post_id,
                "slug": persisted_slug,
                "status": persisted_status,
                "post_data": post_data,
            }

            logger.info("[CreatePostPhase] Created post %s", post_id)

            return self.result

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logger.error("[CreatePostPhase] Error: %s", str(e), exc_info=True)
            raise

        finally:
            if db_service is not None:
                await _close_database_service(db_service, owns_service)


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

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Publish a post"""

        is_valid, error = await self.validate_inputs(inputs)
        if not is_valid:
            self.status = "failed"
            self.error = error
            raise ValueError(error)

        db_service = None
        owns_service = False

        try:
            post_id = inputs.get("post_id")
            slug = inputs.get("slug")
            base_url = config.get("base_url", "https://example.com")

            published_at = datetime.now(timezone.utc).isoformat()
            public_url = f"{base_url}/posts/{slug}"

            logger.info("[PublishPostPhase] Publishing post %s", post_id)

            db_service, owns_service = await _resolve_database_service(config)
            updated = await db_service.update_post(
                post_id,
                {
                    "status": "published",
                },
            )
            if not updated:
                raise ValueError(f"Failed to publish post {post_id}: post not found")

            self.status = "completed"
            self.result = {
                "post_id": post_id,
                "published_at": published_at,
                "public_url": public_url,
            }

            logger.info("[PublishPostPhase] Published at %s", public_url)

            return self.result

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logger.error("[PublishPostPhase] Error: %s", str(e), exc_info=True)
            raise

        finally:
            if db_service is not None:
                await _close_database_service(db_service, owns_service)
