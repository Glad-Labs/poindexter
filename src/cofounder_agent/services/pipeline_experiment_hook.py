"""pipeline_experiment_hook — wire ExperimentService into the content pipeline.

Glad-Labs/poindexter#27 follow-up: ``ExperimentService`` was fully built
in :mod:`services.experiment_service` (migration 0097, full unit tests)
but never instantiated outside its own test file. This module is the
narrow integration seam — call :func:`assign_pipeline_variant` at the
top of ``content_router_service.process_content_generation_task`` and
:func:`record_pipeline_outcome` after the pipeline finalizes.

Design notes:

- **Active experiment is operator-declared via app_settings.**
  ``app_settings.active_pipeline_experiment_key`` (default empty/disabled)
  controls which experiment, if any, the pipeline routes through. This
  matches the project's "DB-first config" rule — no code changes to flip
  experiments on/off.
- **Variant config is opaque to the router.** The hook understands one
  config key — ``writer_model`` — which it merges into ``models_by_phase``
  so the writer stage picks up the variant's preferred model. Future
  per-arm knobs (prompt template, scoring weights, etc.) can be added
  one app_settings key at a time without touching this module.
- **Best-effort.** A misconfigured experiment must NEVER halt the pipeline
  — every helper logs and returns sentinel values rather than raising.
  Real production runs are too valuable to lose to an A/B harness bug.
- **Sticky assignment + idempotent outcome write.** The service handles
  this via UNIQUE(experiment_id, subject_id) and JSONB ``||`` merge.
  Re-running a task after a crash records the same variant + accumulates
  metrics on the same row, exactly what we want.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


_ACTIVE_EXPERIMENT_SETTING = "active_pipeline_experiment_key"


async def _resolve_active_experiment_key(database_service: Any) -> str | None:
    """Read ``active_pipeline_experiment_key`` from app_settings.

    Returns the trimmed string when set + non-empty, otherwise ``None``.
    Never raises — DB hiccup means "no experiment", not "halt pipeline".
    """
    if database_service is None or getattr(database_service, "pool", None) is None:
        return None
    try:
        async with database_service.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT value FROM app_settings "
                "WHERE key = $1 AND is_active = TRUE",
                _ACTIVE_EXPERIMENT_SETTING,
            )
    except Exception as e:
        logger.debug(
            "[experiment_hook] could not read %s from app_settings: %s",
            _ACTIVE_EXPERIMENT_SETTING, e,
        )
        return None
    if value is None:
        return None
    trimmed = str(value).strip()
    return trimmed or None


def _get_variant_config(
    *,
    experiment_key: str,
    variant_key: str,
    site_config: Any,
) -> Any:
    """Coroutine returning the variant's ``config`` dict, or ``{}``.

    Reads the Langfuse Dataset for ``experiment_key`` and returns the
    matching variant's config from the dataset metadata. Best-effort —
    Langfuse outage / missing dataset / unknown variant_key all return
    ``{}`` so the pipeline falls back to default config rather than
    crashing.
    """

    async def _read() -> dict[str, Any]:
        try:
            from services.langfuse_experiments import LangfuseExperimentService
            svc = LangfuseExperimentService(site_config=site_config, pool=None)
            client = svc._get_client()
            ds = client.get_dataset(experiment_key)
        except Exception as e:
            logger.debug(
                "[experiment_hook] could not load variants for %r: %s",
                experiment_key, e,
            )
            return {}

        md = getattr(ds, "metadata", {}) or {}
        variants = md.get("variants") or []
        if not isinstance(variants, list):
            return {}
        for v in variants:
            if not isinstance(v, dict):
                continue
            if v.get("key") == variant_key:
                cfg = v.get("config", {})
                return cfg if isinstance(cfg, dict) else {}
        return {}

    return _read()


async def assign_pipeline_variant(
    *,
    task_id: str,
    database_service: Any,
    site_config: Any,
    models_by_phase: dict[str, str],
) -> dict[str, Any]:
    """Assign ``task_id`` to a variant of the active pipeline experiment.

    Mutates ``models_by_phase`` in place when the variant declares a
    ``writer_model`` override (additive — explicit per-task overrides
    take precedence so callers can still pin a model for debugging).

    Returns a small dict the caller stashes for record_outcome::

        {
            "experiment_key": str | None,
            "variant_key":    str | None,
        }

    When no experiment is active (most of the time) both fields are
    ``None`` and the caller just no-ops the record_outcome.

    Best-effort — any error returns the no-op shape so the pipeline
    continues with default behavior.
    """
    no_op = {"experiment_key": None, "variant_key": None}

    if not task_id or database_service is None:
        return no_op

    experiment_key = await _resolve_active_experiment_key(database_service)
    if not experiment_key:
        return no_op

    pool = getattr(database_service, "pool", None)
    if pool is None:
        return no_op

    try:
        from services.langfuse_experiments import (
            LangfuseExperimentService as ExperimentService,
        )
    except Exception as e:
        logger.debug("[experiment_hook] ExperimentService import failed: %s", e)
        return no_op

    try:
        svc = ExperimentService(site_config=site_config, pool=pool)
        variant_key = await svc.assign(
            experiment_key=experiment_key,
            subject_id=str(task_id),
        )
    except Exception as e:
        logger.warning(
            "[experiment_hook] assign() failed for experiment=%r task=%s: %s",
            experiment_key, task_id, e,
        )
        return no_op

    if not variant_key:
        # Experiment is draft / paused / complete / unknown — pipeline
        # falls back to default config.
        return no_op

    # Apply variant.config["writer_model"] — the only supported per-arm
    # knob today. Adding more knobs is a one-line addition each.
    try:
        cfg = await _get_variant_config(
            experiment_key=experiment_key,
            variant_key=variant_key,
            site_config=site_config,
        )
        writer_override = cfg.get("writer_model") if isinstance(cfg, dict) else None
        if writer_override and "writer" not in models_by_phase:
            # Don't trample explicit per-task overrides from the API caller.
            models_by_phase["writer"] = str(writer_override)
            logger.info(
                "[experiment_hook] %s/%s assigned task %s — writer_model=%s",
                experiment_key, variant_key, task_id[:8], writer_override,
            )
        else:
            logger.info(
                "[experiment_hook] %s/%s assigned task %s",
                experiment_key, variant_key, task_id[:8],
            )
    except Exception as e:
        logger.warning(
            "[experiment_hook] variant config apply failed for %s/%s: %s",
            experiment_key, variant_key, e,
        )

    return {"experiment_key": experiment_key, "variant_key": variant_key}


async def record_pipeline_outcome(
    *,
    assignment: dict[str, Any],
    task_id: str,
    database_service: Any,
    site_config: Any,
    metrics: dict[str, Any],
) -> None:
    """Attribute pipeline outcome metrics to the assignment row.

    Called from finalize once we have a quality_score / status. ``metrics``
    is merged into the assignment's JSONB column so multiple downstream
    signals (initial QA, post-publish CTR, revenue) accumulate on the
    same row over time.

    No-op when assignment carries no experiment_key (the pipeline ran
    with default config, nothing to attribute). Best-effort — never
    raises.
    """
    experiment_key = (assignment or {}).get("experiment_key")
    if not experiment_key or database_service is None:
        return

    pool = getattr(database_service, "pool", None)
    if pool is None:
        return

    try:
        from services.langfuse_experiments import (
            LangfuseExperimentService as ExperimentService,
        )
    except Exception as e:
        logger.debug("[experiment_hook] ExperimentService import failed: %s", e)
        return

    try:
        svc = ExperimentService(site_config=site_config, pool=pool)
        await svc.record_outcome(
            experiment_key=experiment_key,
            subject_id=str(task_id),
            metrics=metrics,
        )
    except Exception as e:
        logger.warning(
            "[experiment_hook] record_outcome() failed for experiment=%r task=%s: %s",
            experiment_key, task_id, e,
        )


__all__ = ["assign_pipeline_variant", "record_pipeline_outcome"]
