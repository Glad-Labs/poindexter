"""Prefect flows for the content pipeline (Glad-Labs/poindexter#410).

Wraps the content-generation pipeline in Prefect primitives so the
operator gets:

- Cron-driven scheduling
- Native retries with backoff
- Run-state machine (heartbeat + stale-task sweep handled natively)
- Operator UI at ``http://localhost:4200``

The flow itself is a thin wrapper over
``services.content_router_service.process_content_generation_task`` —
the actual pipeline didn't change. Lane C already moved orchestration
to LangGraph + the ``canonical_blog`` template; this lift moves the
WHEN/WHO part of dispatch (polling, retry, sweep) to Prefect without
touching the WHAT (the pipeline body).

Cutover history: ``app_settings.use_prefect_orchestration`` was the
staged-rollout seam. The legacy ``services/task_executor.py`` polling
daemon was deleted in Stage 4 (2026-05-16); Prefect is now the sole
dispatcher. See ``docs/architecture/prefect-cutover.md`` for the
full rollout runbook.
"""

from __future__ import annotations
