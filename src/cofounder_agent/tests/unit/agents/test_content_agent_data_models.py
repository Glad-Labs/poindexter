"""
Unit tests for agents/content_agent/utils/data_models.py

Tests for BlogPost, ImageDetails, and StrapiPost Pydantic models.
"""

from agents.content_agent.utils.data_models import BlogPost, ImageDetails, StrapiPost

# ---------------------------------------------------------------------------
# ImageDetails
# ---------------------------------------------------------------------------


class TestImageDetails:
    def test_defaults(self):
        img = ImageDetails()
        assert img.source == "pexels"
        assert img.query is None
        assert img.path is None
        assert img.public_url is None
        assert img.alt_text is None
        assert img.caption is None
        assert img.description is None
        assert img.strapi_image_id is None

    def test_explicit_fields(self):
        img = ImageDetails(
            query="sunset beach",
            source="gcs",
            path="/tmp/beach.jpg",
            public_url="https://cdn.example.com/beach.jpg",
            alt_text="Beautiful sunset over the beach",
            caption="Photo caption",
            description="Wide angle sunset shot",
            strapi_image_id=42,
        )
        assert img.query == "sunset beach"
        assert img.source == "gcs"
        assert img.strapi_image_id == 42

    def test_source_validation_accepts_local(self):
        img = ImageDetails(source="local")
        assert img.source == "local"

    def test_model_serialization(self):
        img = ImageDetails(query="cat", public_url="https://example.com/cat.jpg")
        data = img.model_dump()
        assert data["query"] == "cat"
        assert data["public_url"] == "https://example.com/cat.jpg"
        assert data["source"] == "pexels"


# ---------------------------------------------------------------------------
# BlogPost
# ---------------------------------------------------------------------------


DEFAULTS = {  # type: ignore[arg-type]
    "topic": "Test topic",
    "primary_keyword": "test keyword",
    "target_audience": "developers",
    "category": "tech",
}


class TestBlogPost:
    def test_required_fields(self):
        post = BlogPost(**DEFAULTS)  # type: ignore[arg-type]
        assert post.topic == "Test topic"
        assert post.primary_keyword == "test keyword"
        assert post.target_audience == "developers"
        assert post.category == "tech"

    def test_defaults(self):
        post = BlogPost(**DEFAULTS)  # type: ignore[arg-type]
        assert post.status == "New"
        assert post.task_id is None
        assert post.run_id is None
        assert post.refinement_loops == 3
        assert post.writing_style is None
        assert post.title is None
        assert post.meta_description is None
        assert post.slug is None
        assert post.research_data is None
        assert post.raw_content is None
        assert post.body_content_blocks is None
        assert post.qa_feedback == []
        assert post.images == []
        assert post.strapi_id is None
        assert post.strapi_url is None
        assert post.quality_scores == []
        assert post.metadata == {}
        assert post.strapi_post_id is None
        assert post.rejection_reason is None

    def test_published_posts_map_excluded_from_serialization(self):
        post = BlogPost(**DEFAULTS)  # type: ignore[arg-type]
        post.published_posts_map = {"My Post": "https://example.com/my-post"}
        data = post.model_dump()
        # published_posts_map is excluded=True so it won't appear
        assert "published_posts_map" not in data

    def test_qa_feedback_accepts_list(self):
        post = BlogPost(**DEFAULTS, qa_feedback=["Fix intro", "Add examples"])  # type: ignore[arg-type]
        assert len(post.qa_feedback) == 2
        assert "Fix intro" in post.qa_feedback

    def test_quality_scores_track_history(self):
        post = BlogPost(**DEFAULTS)  # type: ignore[arg-type]
        post.quality_scores.append(72.5)
        post.quality_scores.append(85.0)
        assert len(post.quality_scores) == 2
        assert post.quality_scores[0] == 72.5

    def test_images_list_mutable(self):
        post = BlogPost(**DEFAULTS)  # type: ignore[arg-type]
        img = ImageDetails(query="sky", source="pexels")
        if post.images is None:
            post.images = []
        post.images.append(img)
        assert len(post.images) == 1
        assert post.images[0].query == "sky"

    def test_metadata_dict_mutable(self):
        post = BlogPost(**DEFAULTS)  # type: ignore[arg-type]
        post.metadata["writing_sample_guidance"] = "use active voice"  # type: ignore[index]
        assert post.metadata["writing_sample_guidance"] == "use active voice"  # type: ignore[index]

    def test_full_populated_post(self):
        post = BlogPost(  # type: ignore[arg-type]
            topic="AI trends",
            primary_keyword="artificial intelligence",
            target_audience="CTOs",
            category="Technology",
            title="Top AI Trends 2026",
            slug="top-ai-trends-2026",
            meta_description="Explore the biggest AI trends shaping 2026.",
            raw_content="# AI Trends\n\nContent here...",
            status="Published",
            task_id="task-123",
            run_id="run-456",
            refinement_loops=5,
        )
        assert post.title == "Top AI Trends 2026"
        assert post.slug == "top-ai-trends-2026"
        assert post.status == "Published"
        assert post.task_id == "task-123"
        assert post.refinement_loops == 5

    def test_body_content_blocks(self):
        blocks = [{"type": "paragraph", "children": [{"type": "text", "text": "Hello"}]}]
        post = BlogPost(**DEFAULTS, body_content_blocks=blocks)  # type: ignore[arg-type]
        assert post.body_content_blocks is not None
        assert len(post.body_content_blocks) == 1
        assert post.body_content_blocks[0]["type"] == "paragraph"

    def test_strapi_ids(self):
        post = BlogPost(**DEFAULTS, strapi_id=99, strapi_post_id=100)  # type: ignore[arg-type]
        assert post.strapi_id == 99
        assert post.strapi_post_id == 100


# ---------------------------------------------------------------------------
# StrapiPost
# ---------------------------------------------------------------------------


class TestStrapiPost:
    def test_required_fields(self):
        post = StrapiPost(
            Title="My Post",
            Slug="my-post",
            BodyContent=[{"type": "paragraph", "children": []}],
        )
        assert post.Title == "My Post"
        assert post.Slug == "my-post"
        assert post.PostStatus == "Draft"

    def test_defaults(self):
        post = StrapiPost(
            Title="Test",
            Slug="test",
            BodyContent=[],
        )
        assert post.PostStatus == "Draft"
        assert post.Keywords is None
        assert post.MetaDescription is None
        assert post.FeaturedImage is None
        assert post.ReadingTime is None
        assert post.Excerpt is None
        assert post.author is None
        assert post.category is None
        assert post.tags is None

    def test_full_fields(self):
        post = StrapiPost(
            Title="Full Post",
            Slug="full-post",
            BodyContent=[{"type": "heading", "level": 1, "children": []}],
            PostStatus="Published",
            Keywords="ai, machine learning",
            MetaDescription="A complete post",
            FeaturedImage=12,
            ReadingTime=5,
            Excerpt="Brief overview",
            author=3,
            category=7,
            tags=[1, 2, 3],
        )
        assert post.PostStatus == "Published"
        assert post.FeaturedImage == 12
        assert post.ReadingTime == 5
        assert post.tags == [1, 2, 3]

    def test_model_serialization(self):
        post = StrapiPost(
            Title="Serialization Test",
            Slug="serialization-test",
            BodyContent=[],
        )
        data = post.model_dump()
        assert data["Title"] == "Serialization Test"
        assert data["Slug"] == "serialization-test"
        assert data["PostStatus"] == "Draft"
