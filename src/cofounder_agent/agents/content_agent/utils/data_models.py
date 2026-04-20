from typing import Any

from pydantic import BaseModel, Field


class ImageDetails(BaseModel):
    """Holds the details for a single image, from generation to final URL."""

    query: str | None = None
    source: str = "pexels"  # Default to Pexels, can be 'gcs' or 'local'
    path: str | None = None  # Local path or GCS blob name
    public_url: str | None = None
    alt_text: str | None = None
    caption: str | None = None
    description: str | None = None
    strapi_image_id: int | None = None  # To link featured image in Strapi


class BlogPost(BaseModel):
    """
    The single source of truth for a blog post throughout the creation pipeline.
    This model tracks the state of the content from the initial idea to the final published URL.
    """

    # --- Input Fields from Google Sheet ---
    topic: str
    primary_keyword: str
    target_audience: str
    category: str
    status: str | None = "New"
    task_id: str | None = None  # Firestore document ID for the task
    run_id: str | None = None
    refinement_loops: int = 3
    writing_style: str | None = (
        None  # Writing style: technical, narrative, listicle, educational, thought-leadership
    )
    # SEO & Metadata
    title: str | None = None
    meta_description: str | None = None
    slug: str | None = None
    # Content Stages
    research_data: Any | None = None
    raw_content: str | None = None
    body_content_blocks: list[dict[str, Any]] | None = None
    qa_feedback: list[str] = []
    # Image data
    images: list[ImageDetails] | None = []
    # Publishing data
    strapi_id: int | None = None
    strapi_url: str | None = None
    # --- Internal State ---
    # Holds a map of {post_title: post_url} for internal linking, excluded from serialization
    published_posts_map: dict[str, str] = Field(default_factory=dict, exclude=True)

    # --- Refinement & State Tracking ---
    qa_feedback: list[str] = Field(default_factory=list)

    # --- NEW: Quality Score Tracking ---
    quality_scores: list[float] = Field(
        default_factory=list,
        description="Quality scores from each QA evaluation (0-100 scale). "
        "Allows tracking improvement trend across refinement iterations.",
    )

    # --- Metadata for Agent Coordination ---
    metadata: dict[str, Any] | None = Field(
        default_factory=dict,
        description="Metadata for agent coordination (e.g., writing_sample_guidance)",
    )

    # --- Publishing & Finalization ---
    strapi_post_id: int | None = None
    rejection_reason: str | None = None  # Reason for failing QA or publishing


class StrapiPost(BaseModel):
    """
    Pydantic model representing the final structure for the Strapi API call.
    Includes all the new content type relationships and fields.
    """

    Title: str
    Slug: str
    BodyContent: list[dict]
    PostStatus: str = "Draft"  # Corrected to capitalized "Draft"
    Keywords: str | None = None
    MetaDescription: str | None = None
    FeaturedImage: int | None = None
    ReadingTime: int | None = None
    Excerpt: str | None = None
    author: int | None = None  # Author relationship ID
    category: int | None = None  # Category relationship ID
    tags: list[int] | None = None  # Tag relationship IDs

    model_config = {"populate_by_name": True}
