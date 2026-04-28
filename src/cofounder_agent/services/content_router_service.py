"""Unified Content Router Service — centralized blog post generation pipeline.

History: this module used to hand-orchestrate the 6-stage StageRunner
pipeline directly. As of #206 the orchestration moved into
``services/pipeline_flow.py:content_generation_flow`` (a Prefect 3
@flow). This module is now a thin shim that preserves the public
``process_content_generation_task(...)`` entry point signature for
existing callers while delegating execution to the flow.

Why keep the shim: callers (task_executor, tests, MCP server) pass the
exact kwargs the flow already accepts. Re-pointing them all in one PR
is churn for no benefit; the shim is 5 lines and lives forever.
"""

from typing import TYPE_CHECKING, Any

from services.logger_config import get_logger

from .database_service import DatabaseService

if TYPE_CHECKING:
    from .site_config import SiteConfig

logger = get_logger(__name__)


async def process_content_generation_task(
    topic: str,
    style: str,
    tone: str,
    target_length: int,
    tags: list[str] | None = None,
    generate_featured_image: bool = True,
    database_service: DatabaseService | None = None,
    task_id: str | None = None,
    models_by_phase: dict[str, str] | None = None,
    quality_preference: str | None = None,
    category: str | None = None,
    target_audience: str | None = None,
    *,
    site_config: "SiteConfig",
) -> dict[str, Any]:
    """Run the full content generation pipeline.

    Thin shim over ``services.pipeline_flow.content_generation_flow`` —
    see that module's docstring for the full Prefect-flow design (#206).
    Existing callers (task_executor, MCP, tests) keep the same kwargs
    and the same return-shape; the migration is invisible to them.

    Phase H step 5 (GH#95): ``site_config`` is required keyword arg.
    """
    from services.pipeline_flow import content_generation_flow

    return await content_generation_flow(
        topic=topic,
        style=style,
        tone=tone,
        target_length=target_length,
        tags=tags,
        generate_featured_image=generate_featured_image,
        database_service=database_service,
        task_id=task_id,
        models_by_phase=models_by_phase,
        quality_preference=quality_preference,
        category=category,
        target_audience=target_audience,
        site_config=site_config,
    )
