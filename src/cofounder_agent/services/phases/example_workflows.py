"""
Example Workflows - Pre-configured workflow patterns users can use

These are template workflows that can be customized via the Oversight Hub UI.
Users can also create completely custom workflows by selecting phases.
"""

# ============================================================================
# WORKFLOW 1: Simple Blog Post Generation
# ============================================================================
BLOG_GENERATION_ONLY = {
    "name": "Generate & Evaluate Blog",
    "description": "Generates blog content and evaluates quality (no publishing)",
    "phases": [
        {
            "type": "generate_content",
            "config": {"style": "balanced", "tone": "professional", "target_length": 1500},
        },
        {"type": "quality_evaluation", "config": {"threshold": 70}},
    ],
}

# ============================================================================
# WORKFLOW 2: Blog with Everything (Generate → Evaluate → Image → SEO → Publish)
# ============================================================================
BLOG_COMPLETE_WORKFLOW = {
    "name": "Complete Blog Post",
    "description": "Full workflow: generate, evaluate, add image, SEO, and publish",
    "phases": [
        {
            "type": "generate_content",
            "config": {"style": "balanced", "tone": "professional", "target_length": 2000},
        },
        {"type": "quality_evaluation", "config": {"threshold": 75}},
        {"type": "search_image", "config": {"enabled": True}},
        {"type": "generate_seo", "config": {}},
        {"type": "create_post", "config": {"status": "draft"}},
    ],
}

# ============================================================================
# WORKFLOW 3: Reuse Published Blog for Social Media
# ============================================================================
# This workflow would take a published blog and create social media posts
# NOTE: Requires social_media_post phase (not yet implemented)
BLOG_TO_SOCIAL_WORKFLOW = {
    "name": "Blog to Social Media",
    "description": "Create social media posts from existing blog content",
    "phases": [
        # {
        #     "type": "social_media_post",
        #     "config": {
        #         "platforms": ["twitter", "linkedin", "facebook"],
        #         "use_original_images": True
        #     }
        # }
    ],
    "requires_existing_content": True,
}

# ============================================================================
# WORKFLOW 4: Research → Content → Evaluation (Multi-phase workflow)
# ============================================================================
# NOTE: Requires research_phase (not yet implemented)
RESEARCH_AND_CONTENT_WORKFLOW = {
    "name": "Research-Based Content",
    "description": "Research topic, generate content based on research, evaluate quality",
    "phases": [
        # {
        #     "type": "research",
        #     "config": {"search_depth": "comprehensive"}
        # },
        {
            "type": "generate_content",
            "config": {"style": "balanced", "tone": "professional", "target_length": 2500},
        },
        {"type": "quality_evaluation", "config": {"threshold": 80}},
    ],
}

# ============================================================================
# EXAMPLE USAGE
# ============================================================================
"""
To use these workflows via the REST API:

1. Get available phases:
   GET /api/workflows/phases

   Response shows phase names, inputs, outputs, configurable params

2. Create a custom workflow:
   POST /api/workflows/custom
   {
       "name": "My Blog Workflow",
       "description": "Custom workflow for my use case",
       "phases": [
           {"type": "generate_content", "config": {...}},
           {"type": "quality_evaluation", "config": {...}},
           ...
       ],
       "topic": "AI in Healthcare",
       "style": "technical",
       "tone": "professional",
       "tags": ["AI", "healthcare", "ML"]
   }

   Response (202 Accepted):
   {
       "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
       "status": "pending",
       "phases_count": 4,
       "created_at": "2026-02-25T12:34:56Z"
   }

3. Check workflow status:
   GET /api/workflows/550e8400-e29b-41d4-a716-446655440000

   Response shows phase results and final status

DATA THREADING EXAMPLE:

User specifies:
{
    "topic": "AI in Healthcare",
    "style": "technical",
    "tone": "professional"
}

Phase 1: generate_content
  Inputs:  topic (user), style (config), tone (config)
  Outputs: content, model_used, metrics

Phase 2: quality_evaluation
  Inputs:  content (Phase 1 output), topic (user), tags (user)
  Outputs: overall_score, scores, passing, feedback

Phase 3: search_image
  Inputs:  topic (user), tags (user)
  Outputs: image_url, photographer, source

Phase 4: generate_seo
  Inputs:  content (Phase 1 output), topic (user)
  Outputs: seo_title, seo_description, seo_keywords

Phase 5: create_post
  Inputs:  content (Phase 1), topic (user), seo_title (Phase 4),
           seo_description (Phase 4), seo_keywords (Phase 4),
           image_url (Phase 3)
  Outputs: post_id, slug, status

Final Result:
{
    "workflow_id": "...",
    "status": "completed",
    "phase_results": {
        "generate_content": {
            "content": "...",
            "model_used": "gpt-4",
            "metrics": {...}
        },
        "quality_evaluation": {
            "overall_score": 82.5,
            "scores": {...},
            "passing": true,
            "feedback": "..."
        },
        "search_image": {
            "image_url": "https://images.pexels.com/...",
            "photographer": "John Doe",
            "source": "Pexels"
        },
        "generate_seo": {
            "seo_title": "AI in Healthcare: Revolutionizing Medicine",
            "seo_description": "Explore how AI is transforming healthcare...",
            "seo_keywords": ["AI", "healthcare", "ML", ...]
        },
        "create_post": {
            "post_id": "uuid-...",
            "slug": "ai-in-healthcare-revolutionizing-medicine",
            "status": "draft"
        }
    },
    "execution_time": 45.2
}

APPROVAL GATES:

Workflows can have approval gates after specific phases.
For example, require human approval after Quality Evaluation before proceeding to publish.

POST /api/workflows/{workflow_id}/approve
{
    "feedback": "Looks good, please proceed with publishing"
}

This would continue execution from the approval point.
"""
