"""Parse operator / API caller model selections into ``(model, provider)``.

Lifted from content_router_service.py during Phase E2. The generate_content
Stage uses it to decide which Ollama model to target for drafting.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_model_preferences(
    models_by_phase: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Extract the draft model + provider from a ``{phase: model}`` selection.

    Returns ``(preferred_model, preferred_provider)``. Either or both may
    be ``None`` when no explicit selection was made — the downstream
    router's config-driven default kicks in.

    ``models_by_phase`` typically comes from the API caller or the UI's
    model picker. Legacy key variants (``draft`` / ``generate`` / ``content``)
    are all accepted for the main writing phase.
    """
    preferred_model: str | None = None
    preferred_provider: str | None = None

    logger.info("STEP 2A: Processing model selections from UI")
    logger.info("   models_by_phase = %s", models_by_phase)

    if not models_by_phase:
        return preferred_model, preferred_provider

    draft_model = (
        models_by_phase.get("draft")
        or models_by_phase.get("generate")
        or models_by_phase.get("content")
    )
    logger.info("   draft_model = %s", draft_model)

    if not draft_model or draft_model == "auto":
        return preferred_model, preferred_provider

    draft_model = draft_model.strip()

    if "/" in draft_model:
        preferred_provider, preferred_model = draft_model.split("/", 1)
    else:
        # Infer provider from model name — Ollama-only policy post-2026-03.
        preferred_provider = "ollama"
        preferred_model = draft_model

    logger.info(
        "   FINAL: preferred_model='%s', preferred_provider='%s'",
        preferred_model, preferred_provider,
    )
    logger.info(
        "User selected model: %s (provider: %s)",
        preferred_model or "auto", preferred_provider or "auto",
    )
    return preferred_model, preferred_provider
