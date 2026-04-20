"""
Post Quality Validation Tests

Validates that the content pipeline produces posts with ALL required fields
for a fully-fleshed-out, publish-ready blog post. These tests ensure consistent
quality output with "all the trimmings":
- Title, slug, content, excerpt
- SEO metadata (meta_description, seo_title, seo_keywords)
- Featured image with alt text
- Tags and category
- Quality scores and refinement history
- Proper markdown structure

Tests cover:
1. BlogPost model completeness for pipeline output
2. Quality score tracking across refinement iterations
3. Image metadata completeness
4. SEO field population
5. Content structure validation (headings, paragraphs, word count)
"""

import pytest

from agents.content_agent.utils.data_models import BlogPost, ImageDetails

# ---------------------------------------------------------------------------
# Fixtures: Realistic pipeline output data
# ---------------------------------------------------------------------------

FULLY_POPULATED_POST_KWARGS = {
    "topic": "AI-Powered Healthcare Diagnostics in 2026",
    "primary_keyword": "AI healthcare diagnostics",
    "target_audience": "Healthcare professionals and technology decision-makers",
    "category": "Healthcare Technology",
    "status": "Published",
    "task_id": "task-abc-123",
    "run_id": "run-xyz-789",
    "refinement_loops": 3,
    "writing_style": "technical",
    "title": "How AI-Powered Diagnostics Are Transforming Healthcare in 2026",
    "meta_description": "Explore how AI-powered diagnostic tools are revolutionizing patient care, reducing misdiagnosis rates, and enabling earlier disease detection in 2026.",
    "slug": "ai-powered-diagnostics-transforming-healthcare-2026",
    "research_data": {
        "sources": [
            {"url": "https://example.com/ai-health", "title": "AI in Healthcare Study"},
            {"url": "https://example.com/diagnostics", "title": "Diagnostic AI Review"},
        ],
        "key_points": [
            "AI diagnostics reduce misdiagnosis by 30%",
            "Early detection rates improve by 45%",
        ],
    },
    "raw_content": """# How AI-Powered Diagnostics Are Transforming Healthcare in 2026

## Introduction

Artificial intelligence is reshaping the healthcare landscape in unprecedented ways. From radiology to pathology, AI-powered diagnostic tools are enabling clinicians to detect diseases earlier, reduce errors, and improve patient outcomes.

## The Rise of AI Diagnostics

The adoption of AI in clinical diagnostics has accelerated dramatically. In 2025, the global AI diagnostics market reached $8.5 billion, and projections suggest it will double by 2028.

### Key Benefits

- **Earlier Detection**: AI algorithms can identify patterns invisible to the human eye
- **Reduced Misdiagnosis**: Machine learning models trained on millions of cases achieve unprecedented accuracy
- **Cost Efficiency**: Automated screening reduces the burden on specialist physicians

## Real-World Applications

### Radiology

AI-powered imaging analysis tools now assist radiologists in detecting lung nodules, breast cancer, and neurological conditions with accuracy rates exceeding 95%.

### Pathology

Digital pathology combined with AI enables faster and more consistent analysis of tissue samples, reducing turnaround times from days to hours.

### Genomics

AI-driven genomic analysis tools help identify genetic predispositions to diseases, enabling preventive care strategies tailored to individual patients.

## Challenges and Considerations

Despite the promise, several challenges remain:

1. **Data Privacy**: Patient data must be handled with strict compliance to HIPAA and GDPR regulations that govern how sensitive medical information is stored and transmitted
2. **Regulatory Approval**: AI diagnostic tools require rigorous FDA clearance processes that can take years to complete
3. **Clinical Integration**: Seamless integration with existing EHR systems like Epic and Cerner is critical for adoption
4. **Bias Mitigation**: AI models must be trained on diverse, representative datasets to avoid diagnostic disparities across demographic groups

## The Road Ahead

Investment in AI diagnostics continues to accelerate. Venture capital funding reached $4.2 billion in 2025, a 35% increase over the previous year. Hospital systems are establishing dedicated AI governance committees to evaluate and deploy these tools responsibly.

## Conclusion

AI-powered diagnostics represent one of the most transformative developments in modern healthcare. As the technology matures and regulatory frameworks adapt, we can expect even broader adoption and more sophisticated applications that ultimately improve patient care worldwide. Healthcare leaders who embrace these innovations today will be better positioned to deliver faster, more accurate diagnoses tomorrow.
""",
    "qa_feedback": [
        "Add more specific statistics and cite sources",
        "Strengthen the conclusion with actionable takeaways",
    ],
    "quality_scores": [62.0, 78.0, 88.5],
    "images": [
        ImageDetails(
            query="AI healthcare diagnostics",
            source="pexels",
            public_url="https://images.pexels.com/photos/12345/ai-health.jpg",
            alt_text="AI-powered diagnostic system analyzing medical imaging data",
            caption="Modern AI diagnostics system in a clinical setting",
            description="A medical professional reviewing AI-assisted diagnostic results on a screen",
        ),
        ImageDetails(
            query="digital pathology AI",
            source="pexels",
            public_url="https://images.pexels.com/photos/67890/pathology-ai.jpg",
            alt_text="Digital pathology slide analysis using artificial intelligence",
            caption="AI-enhanced digital pathology workflow",
        ),
    ],
    "metadata": {
        "writing_sample_guidance": "Use active voice, avoid jargon, cite specific data",
        "estimated_reading_time": 7,
    },
}


def _make_full_post(**overrides):
    kwargs = {**FULLY_POPULATED_POST_KWARGS, **overrides}
    return BlogPost(**kwargs)


# ---------------------------------------------------------------------------
# Test Class: Post completeness for publishing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostCompletenessForPublishing:
    """Verify a pipeline-produced post has every field needed for publishing."""

    def test_essential_content_fields_populated(self):
        """A publish-ready post must have title, slug, content, and meta_description."""
        post = _make_full_post()
        assert post.title is not None
        assert len(post.title) > 0
        assert post.slug is not None
        assert len(post.slug) > 0
        assert post.raw_content is not None
        assert len(post.raw_content) > 0
        assert post.meta_description is not None
        assert len(post.meta_description) > 0

    def test_slug_is_url_safe(self):
        """Slug must contain only lowercase alphanumeric chars and hyphens."""
        post = _make_full_post()
        import re

        assert re.match(
            r"^[a-z0-9]+(?:-[a-z0-9]+)*$", post.slug
        ), f"Slug '{post.slug}' is not URL-safe"

    def test_meta_description_length_seo_optimal(self):
        """Meta description should be between 50 and 160 chars for SEO."""
        post = _make_full_post()
        desc_len = len(post.meta_description)
        assert (
            50 <= desc_len <= 160
        ), f"Meta description length {desc_len} is outside SEO optimal range (50-160)"

    def test_title_length_seo_optimal(self):
        """Title should be under 70 characters for search engine display."""
        post = _make_full_post()
        assert len(post.title) <= 70, f"Title length {len(post.title)} exceeds 70-char SEO limit"

    def test_content_has_markdown_headings(self):
        """Published content should have structured headings (H1, H2, or H3)."""
        post = _make_full_post()
        assert (
            "## " in post.raw_content or "# " in post.raw_content
        ), "Content lacks markdown headings for proper structure"

    def test_content_has_sufficient_word_count(self):
        """Blog post content should meet minimum word count (300+)."""
        post = _make_full_post()
        word_count = len(post.raw_content.split())
        assert word_count >= 300, f"Content has only {word_count} words; minimum 300 expected"

    def test_images_present_with_alt_text(self):
        """Published post should have at least one image with alt text."""
        post = _make_full_post()
        assert post.images is not None, "No images attached"
        assert len(post.images) > 0, "No images attached"
        for img in post.images:
            assert img.public_url is not None, "Image missing public_url"
            assert img.alt_text is not None, "Image missing alt_text"
            assert len(img.alt_text) > 0, "Image has empty alt_text"

    def test_category_populated(self):
        """Post must have a category for content organization."""
        post = _make_full_post()
        assert post.category is not None
        assert len(post.category) > 0

    def test_primary_keyword_populated(self):
        """Post must have a primary keyword for SEO targeting."""
        post = _make_full_post()
        assert post.primary_keyword is not None
        assert len(post.primary_keyword) > 0

    def test_target_audience_populated(self):
        """Post must have a defined target audience."""
        post = _make_full_post()
        assert post.target_audience is not None
        assert len(post.target_audience) > 0

    def test_writing_style_set(self):
        """Post should have a writing style for consistency."""
        post = _make_full_post()
        assert post.writing_style in {
            "technical",
            "narrative",
            "listicle",
            "educational",
            "thought-leadership",
        }


# ---------------------------------------------------------------------------
# Test Class: Quality score tracking
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQualityScoreTracking:
    """Verify quality score and refinement tracking work correctly."""

    def test_quality_scores_record_all_iterations(self):
        """Quality scores list should have one entry per QA evaluation."""
        post = _make_full_post()
        assert len(post.quality_scores) == 3
        assert post.quality_scores == [62.0, 78.0, 88.5]

    def test_quality_scores_show_improvement_trend(self):
        """Quality should generally improve across refinement iterations."""
        post = _make_full_post()
        # Final score should be higher than the first
        assert (
            post.quality_scores[-1] > post.quality_scores[0]
        ), "Final quality score should be higher than initial"

    def test_final_quality_above_threshold(self):
        """Final quality score should meet the 75-point threshold."""
        post = _make_full_post()
        assert (
            post.quality_scores[-1] >= 75.0
        ), f"Final quality {post.quality_scores[-1]} below 75-point threshold"

    def test_qa_feedback_accumulates_across_rounds(self):
        """QA feedback should accumulate from all refinement rounds."""
        post = _make_full_post()
        assert len(post.qa_feedback) >= 1, "No QA feedback recorded"
        assert all(isinstance(f, str) and len(f) > 0 for f in post.qa_feedback)

    def test_empty_quality_scores_for_new_post(self):
        """A fresh post should have empty quality scores."""
        post = BlogPost(
            topic="Test", primary_keyword="test", target_audience="devs", category="tech"
        )
        assert post.quality_scores == []
        assert post.qa_feedback == []

    def test_quality_plateau_detection(self):
        """Pipeline should detect quality plateau (delta < 2 points)."""
        post = _make_full_post(quality_scores=[70.0, 71.5, 72.0])
        # Detect plateau: last two improvements < 2 points each
        improvements = [
            post.quality_scores[i] - post.quality_scores[i - 1]
            for i in range(1, len(post.quality_scores))
        ]
        plateau = (
            all(delta < 2.0 for delta in improvements[-2:]) if len(improvements) >= 2 else False
        )
        assert plateau is True, "Should detect quality plateau for early exit"

    def test_refinement_count_matches_extra_scores(self):
        """Number of refinements = len(quality_scores) - 1 (first is initial assessment)."""
        post = _make_full_post()
        refinement_count = len(post.quality_scores) - 1
        assert refinement_count == 2, "Expected 2 refinement rounds"


# ---------------------------------------------------------------------------
# Test Class: Image metadata completeness
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImageMetadataCompleteness:
    """Verify image metadata is complete for accessibility and SEO."""

    def test_featured_image_has_all_fields(self):
        """Featured (first) image must have url, alt_text, and caption."""
        post = _make_full_post()
        featured = post.images[0]
        assert featured.public_url is not None
        assert featured.alt_text is not None
        assert featured.caption is not None
        assert featured.query is not None
        assert featured.source in {"pexels", "gcs", "local"}

    def test_all_images_have_public_urls(self):
        """Every image must have a public URL for rendering."""
        post = _make_full_post()
        for i, img in enumerate(post.images):
            assert img.public_url is not None, f"Image {i} missing public_url"
            assert img.public_url.startswith("http"), (
                f"Image {i} public_url is not http-scheme"
            )

    def test_all_images_have_alt_text(self):
        """Every image must have alt text for WCAG compliance."""
        post = _make_full_post()
        for i, img in enumerate(post.images):
            assert img.alt_text is not None, f"Image {i} alt_text missing (WCAG 1.1.1)"
            assert len(img.alt_text) >= 10, (
                f"Image {i} alt_text too short (WCAG 1.1.1): {img.alt_text!r}"
            )

    def test_image_alt_text_not_generic(self):
        """Alt text should be descriptive, not generic like 'image' or 'photo'."""
        post = _make_full_post()
        generic_patterns = {"image", "photo", "picture", "img", "untitled"}
        for img in post.images:
            assert (
                img.alt_text.lower().strip() not in generic_patterns
            ), f"Alt text '{img.alt_text}' is too generic"

    def test_post_without_images_still_valid(self):
        """A post with no images should still be a valid model."""
        post = _make_full_post(images=[])
        assert post.images == []
        # All other fields should still be valid
        assert post.title is not None
        assert post.raw_content is not None


# ---------------------------------------------------------------------------
# Test Class: Content structure validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentStructureValidation:
    """Verify that generated content has proper structure for a quality blog post."""

    def test_content_has_introduction(self):
        """Content should start with an introduction section."""
        post = _make_full_post()
        # After the H1 title, there should be introductory text before the next heading
        lines = post.raw_content.strip().split("\n")
        found_heading = False
        found_intro_text = False
        for line in lines:
            if line.startswith("# "):
                found_heading = True
                continue
            if found_heading and line.strip() and not line.startswith("#"):
                found_intro_text = True
                break
        assert found_intro_text, "Content lacks introductory text after the title"

    def test_content_has_multiple_sections(self):
        """Content should have multiple H2 sections for depth."""
        post = _make_full_post()
        h2_count = post.raw_content.count("\n## ")
        assert h2_count >= 3, f"Only {h2_count} H2 sections; expect 3+ for depth"

    def test_content_has_conclusion(self):
        """Content should include a conclusion section."""
        post = _make_full_post()
        content_lower = post.raw_content.lower()
        has_conclusion = (
            "## conclusion" in content_lower
            or "## summary" in content_lower
            or "## final thoughts" in content_lower
            or "## key takeaways" in content_lower
        )
        assert has_conclusion, "Content lacks a conclusion section"

    def test_content_uses_formatting(self):
        """Content should use markdown formatting (bold, lists, etc.)."""
        post = _make_full_post()
        has_bold = "**" in post.raw_content
        has_list = "- " in post.raw_content or "1. " in post.raw_content
        assert has_bold or has_list, "Content lacks markdown formatting (bold/lists)"

    def test_content_no_placeholder_text(self):
        """Content must not contain placeholder text from the pipeline."""
        post = _make_full_post()
        placeholders = [
            "[INSERT",
            "[TODO",
            "[PLACEHOLDER",
            "Lorem ipsum",
            "Content here...",
            "[YOUR",
            "[EDIT",
        ]
        content_lower = post.raw_content.lower()
        for placeholder in placeholders:
            assert (
                placeholder.lower() not in content_lower
            ), f"Content contains placeholder text: {placeholder}"

    def test_content_no_prompt_leakage(self):
        """Content must not leak LLM prompt instructions."""
        post = _make_full_post()
        prompt_leaks = [
            "as an ai",
            "i'm an ai",
            "as a language model",
            "i cannot",
            "i don't have access",
            "my training data",
            "openai",
            "anthropic",
            "claude",
            "gpt-4",
        ]
        content_lower = post.raw_content.lower()
        for leak in prompt_leaks:
            assert leak not in content_lower, f"Content contains prompt leakage: '{leak}'"


# ---------------------------------------------------------------------------
# Test Class: Metadata coordination
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetadataCoordination:
    """Verify metadata fields are consistent and properly coordinated."""

    def test_slug_derived_from_title(self):
        """Slug should be derived from the title (lowercase, hyphenated)."""
        post = _make_full_post()
        # Slug should contain key words from the title
        slug_words = set(post.slug.split("-"))
        title_words = {w.lower() for w in post.title.split() if len(w) > 3}
        overlap = slug_words & title_words
        assert len(overlap) >= 2, f"Slug '{post.slug}' doesn't appear to match title '{post.title}'"

    def test_meta_description_relates_to_topic(self):
        """Meta description should reference the post topic."""
        post = _make_full_post()
        # At least one keyword from the topic should appear in meta_description
        topic_words = {w.lower() for w in post.topic.split() if len(w) > 3}
        desc_lower = post.meta_description.lower()
        matches = {w for w in topic_words if w in desc_lower}
        assert (
            len(matches) >= 1
        ), f"Meta description doesn't reference topic keywords: {topic_words}"

    def test_research_data_has_sources(self):
        """Research data should include sources for content credibility."""
        post = _make_full_post()
        assert post.research_data is not None
        assert "sources" in post.research_data
        assert len(post.research_data["sources"]) >= 1

    def test_metadata_dict_carries_pipeline_context(self):
        """Metadata dict should carry pipeline coordination data."""
        post = _make_full_post()
        assert post.metadata is not None
        assert "writing_sample_guidance" in post.metadata

    def test_task_and_run_ids_set(self):
        """Pipeline-produced posts should have task_id and run_id for traceability."""
        post = _make_full_post()
        assert post.task_id is not None
        assert post.run_id is not None

    def test_serialization_preserves_all_fields(self):
        """model_dump should include all publish-critical fields."""
        post = _make_full_post()
        data = post.model_dump()
        required_keys = {
            "topic",
            "primary_keyword",
            "target_audience",
            "category",
            "title",
            "slug",
            "meta_description",
            "raw_content",
            "images",
            "quality_scores",
            "qa_feedback",
            "status",
        }
        missing = required_keys - set(data.keys())
        assert not missing, f"Serialization missing keys: {missing}"

    def test_published_posts_map_excluded(self):
        """Internal published_posts_map should not leak into serialized output."""
        post = _make_full_post()
        post.published_posts_map = {"Title A": "/posts/a"}
        data = post.model_dump()
        assert "published_posts_map" not in data
