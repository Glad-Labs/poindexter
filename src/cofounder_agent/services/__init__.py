"""Services module for AI content generation and management.

Intentionally empty — no top-level re-exports. Each caller imports
the specific submodule it needs, e.g. ``from services.content_task_store
import ContentTaskStore``. This keeps ``import services.taps.runner``
cheap for contexts (like the auto-embed container) that don't want to
drag in heavy deps like yaml, structlog, pydantic via transitive
imports from content_router_service.
"""
