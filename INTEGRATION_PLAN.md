"""
Integration Plan: Add Blog Post Phases to Existing Workflow System

This plan integrates the blog post generation pipeline into the existing
CustomWorkflowsService / PhaseRegistry system without duplication.

APPROACH:
Rather than maintain separate phase definitions, extend the existing
phase_registry.py with blog-specific phases that use your existing services.

TASKS:
1. ✅ Review existing phase_registry.py structure
2. ⏳ Add 7 blog post phases to phase_registry.py:
   - generate_content
   - quality_evaluation
   - search_image
   - generate_seo
   - capture_training_data
   - create_post
   - publish_post

3. ⏳ Create blog post agents if needed (or use existing)
4. ⏳ Test workflow: [generate] → [quality] → [image] → [seo] → [create] → [publish]
5. ⏳ Create examples via the existing custom_workflows_routes.py API

EXISTING SYSTEM (Use As-Is):
- CustomWorkflowsService (handles CRUD, execution, validation)
- PhaseRegistry (will extend with blog phases)
- WorkflowExecutor (runs phases + agents)
- custom_workflows_routes.py (REST API)

NEW: Only add blog-specific PhaseDefinitions to phase_registry.py

FILES TO MODIFY:
- services/phase_registry.py - Add blog post phase definitions

FILES TO KEEP (from what I created):
- services/phases/base_phase.py - Optional: reference pattern
- services/phases/content_phases.py - Reference for implementations
- services/phases/publishing_phases.py - Reference for implementations
- services/phases/example_workflows.py - Example workflows
- services/phases/PHASES_DOCUMENTATION.md - Documentation

FILES TO DELETE:
- Anything that duplicates existing services

WORKFLOW EXAMPLE:
POST /api/workflows/custom
{
  "name": "Blog Post Generation",
  "phases": [
    {"type": "generate_content", "config": {"style": "technical"}},
    {"type": "quality_evaluation", "config": {"threshold": 75}},
    {"type": "search_image", "config": {}},
    {"type": "generate_seo", "config": {}},
    {"type": "create_post", "config": {}},
    {"type": "publish_post", "config": {}}
  ],
  "topic": "AI in Healthcare"
}
"""
