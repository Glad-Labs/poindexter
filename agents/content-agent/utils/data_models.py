from pydantic import BaseModel, Field
from typing import Optional, List, Dict

class ImageDetails(BaseModel):
    """Holds the details for a single image, from generation to final URL."""
    query: Optional[str] = None
    source: str = "pexels"  # Default to Pexels, can be 'gcs' or 'local'
    path: Optional[str] = None  # Local path or GCS blob name
    public_url: Optional[str] = None
    alt_text: Optional[str] = None
    caption: Optional[str] = None
    description: Optional[str] = None
    strapi_image_id: Optional[int] = None # To link featured image in Strapi

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
    refinement_loops: int = Field(default=1)
    sheet_row_index: int

    # --- Generated Content ---
    generated_title: Optional[str] = None
    raw_content: Optional[str] = None  # The main Markdown body from the creative agent
    meta_description: Optional[str] = None
    keywords: List[str] = Field(default_factory=list) # SEO keywords from CreativeAgent
    research_data: Optional[str] = None # Raw research data from ResearchAgent
    
    # --- Refinement & State Tracking ---
    qa_feedback: List[str] = Field(default_factory=list)
    status: str = "New" # Tracks the current state (e.g., "In Progress", "Published", "Failed")
    
    # --- Publishing & Finalization ---
    images: List[ImageDetails] = Field(default_factory=list)
    strapi_post_id: Optional[int] = None
    strapi_url: Optional[str] = None
    rejection_reason: Optional[str] = None # Reason for failing QA or publishing

    # --- Internal State ---
    # Holds a map of {post_title: post_url} for internal linking, excluded from serialization
    published_posts_map: Dict[str, str] = Field(default_factory=dict, exclude=True)


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

    model_config = {
        "populate_by_name": True
    }
