"""Prefect flows for the content pipeline (Glad-Labs/poindexter#410).

Wraps the content-generation pipeline in Prefect primitives so the
operator gets:

- Cron-driven scheduling (replaces ``services.task_executor._process_loop``)
- Native retries with backoff (replaces ``_auto_retry_failed_tasks``)
- Run-state machine (replaces heartbeat tracking + stale-task sweep)
- Operator UI at ``http://localhost:4200`` (replaces custom metrics endpoint)

The flow itself is a thin wrapper over
``services.content_router_service.process_content_generation_task`` —
the actual pipeline didn't change. Lane C already moved orchestration
to LangGraph + the ``canonical_blog`` template; this lift moves the
WHEN/WHO part of dispatch (polling, retry, sweep) to Prefect without
touching the WHAT (the pipeline body).

Cutover seam: ``app_settings.use_prefect_orchestration`` (default
``'false'`` in the seed migration). When ``'true'``, ``TaskExecutor``
turns into a no-op poller and Prefect's deployment owns dispatch
entirely. See ``docs/architecture/prefect-cutover.md`` for the
staged-rollout runbook (modeled on Lane C's cutover pattern).
"""

from __future__ import annotations
