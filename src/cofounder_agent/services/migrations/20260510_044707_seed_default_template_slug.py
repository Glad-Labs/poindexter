"""Seed the Lane C cutover switch: default_template_slug.

Lane C of ``Glad-Labs/poindexter#450`` (the OSS migration sweep
umbrella) lifts content pipeline orchestration from the legacy
chunked StageRunner flow inside ``content_router_service`` onto the
LangGraph ``canonical_blog`` template that already ships in
``services/pipeline_templates/__init__.py``.

The cutover seam lives in ``tasks_db.add_task``: when a new task
doesn't carry an explicit ``template_slug``, the helper consults
``app_settings.default_template_slug`` and writes that into the row.
Empty / missing → NULL ``template_slug`` → legacy path runs (today's
behavior). Setting it to ``'canonical_blog'`` routes every new task
through TemplateRunner.

The setting ships **empty** so this migration is purely additive.
The cutover itself is an operator decision: flip the value when the
canonical_blog template has enough advisory parity coverage. Until
then, ``dev_diary`` remains the only template_slug consumer in
production traffic.

Idempotent — ``ON CONFLICT DO NOTHING``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_DESCRIPTION = (
    "Lane C cutover switch: when set, every new pipeline_tasks row "
    "without an explicit caller-supplied template_slug gets this "
    "value. Empty (default) preserves the legacy chunked StageRunner "
    "flow inside content_router_service. Set to 'canonical_blog' to "
    "route every new task through services/template_runner.py + the "
    "LangGraph canonical_blog template (Glad-Labs/poindexter#355). "
    "Per-task overrides (e.g. dev_diary cron passing template_slug="
    "'dev_diary' explicitly) always win."
)


async def run_migration(conn) -> None:
    await conn.execute(
        """
        INSERT INTO app_settings
            (key, value, category, description, is_secret, is_active)
        VALUES ('default_template_slug', '', 'pipeline', $1, false, true)
        ON CONFLICT (key) DO NOTHING
        """,
        _DESCRIPTION,
    )
    logger.info(
        "Migration 20260510_044707: default_template_slug seeded empty "
        "(operator opts in by setting to 'canonical_blog')."
    )
