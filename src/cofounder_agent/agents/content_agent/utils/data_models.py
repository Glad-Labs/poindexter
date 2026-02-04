from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ImageDetails(BaseModel):
    """Holds the details for a single image, from generation to final URL."""

    query: Optional[str] = None
    source: str = "pexels"  # Default to Pexels, can be 'gcs' or 'local'
    path: Optional[str] = None  # Local path or GCS blob name
    public_url: Optional[str] = None
    alt_text: Optional[str] = None
    caption: Optional[str] = None
    description: Optional[str] = None
    strapi_image_id: Optional[int] = None  # To link featured image in Strapi


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
    status: Optional[str] = "New"
    task_id: Optional[str] = None  # Firestore document ID for the task
    run_id: Optional[str] = None
    refinement_loops: int = 3
    writing_style: Optional[str] = (
        None  # Writing style: technical, narrative, listicle, educational, thought-leadership
    )
    # SEO & Metadata
    title: Optional[str] = None
    meta_description: Optional[str] = None
    slug: Optional[str] = None
    # Content Stages
    research_data: Optional[Any] = None
    raw_content: Optional[str] = None
    body_content_blocks: Optional[List[Dict[str, Any]]] = None
    qa_feedback: List[str] = []
    # Image data
    images: Optional[List[ImageDetails]] = []
    # Publishing data
    strapi_id: Optional[int] = None
    strapi_url: Optional[str] = None
    published_posts_map: Optional[Dict[str, str]] = {}

    # --- Internal State ---
    # Holds a map of {post_title: post_url} for internal linking, excluded from serialization
    published_posts_map: Dict[str, str] = Field(default_factory=dict, exclude=True)

    # --- Refinement & State Tracking ---
    qa_feedback: List[str] = Field(default_factory=list)

    # --- Metadata for Agent Coordination ---
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Metadata for agent coordination (e.g., writing_sample_guidance)",
    )

    # --- Publishing & Finalization ---
    strapi_post_id: Optional[int] = None
    rejection_reason: Optional[str] = None  # Reason for failing QA or publishing


class StrapiPost(BaseModel):
    """
    Pydantic model representing the final structure for the Strapi API call.
    Includes all the new content type relationships and fields.
    """

    Title: str
    Slug: str
    BodyContent: List[Dict]
    PostStatus: str = "Draft"  # Corrected to capitalized "Draft"
    Keywords: Optional[str] = None
    MetaDescription: Optional[str] = None
    FeaturedImage: Optional[int] = None
    ReadingTime: Optional[int] = None
    Excerpt: Optional[str] = None
    author: Optional[int] = None  # Author relationship ID
    category: Optional[int] = None  # Category relationship ID
    tags: Optional[List[int]] = None  # Tag relationship IDs

    model_config = {"populate_by_name": True}
