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
    raw_content: Optional[str] = None  # The main Markdown body
    meta_description: Optional[str] = None
    related_keywords: List[str] = Field(default_factory=list)
    internal_links: List[str] = Field(default_factory=list) # Titles of posts to link to
    external_links: Dict[str, str] = Field(default_factory=dict) # e.g., {"Text": "URL"}
    
    # --- Image & Asset Management ---
    images: List[ImageDetails] = Field(default_factory=list)
    
    # --- State & Output ---
    status: str = "New"
    rejection_reason: Optional[str] = None
    strapi_post_id: Optional[int] = None
    strapi_url: Optional[str] = None
    
    # --- QA & Refinement Tracking ---
    qa_feedback: List[str] = Field(default_factory=list)
    
    # --- Internal State ---
    # Holds a map of {post_title: post_url} for internal linking
    published_posts_map: Dict[str, str] = Field(default_factory=dict, exclude=True)

class StrapiPost(BaseModel):
    """
    Pydantic model representing the final structure for the Strapi API call.
    """
    Title: str
    Slug: str
    BodyContent: List[Dict]
    PostStatus: str = "draft"
    Keywords: Optional[str] = None
    MetaDescription: Optional[str] = None
    FeaturedImage: Optional[int] = None # Will be the ID of the uploaded image
    ImageAltText: Optional[str] = None

    model_config = {
        "populate_by_name": True
    }
