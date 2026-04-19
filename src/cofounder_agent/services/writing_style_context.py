"""Load active writing-style samples for voice matching.

Lifted from content_router_service.py during Phase E2. Returns a
newline-joined block of recent writing samples the writer LLM consults
to match the operator's voice — or ``None`` when no samples are
configured / the DB lookup fails (non-fatal path).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def build_writing_style_context(
    database_service: Any | None,
    max_samples: int = 3,
    max_words_per_sample: int = 500,
) -> str | None:
    """Fetch active writing style samples for voice matching.

    Returns ``None`` when:

    - ``database_service`` is None or lacks a ``writing_style`` attribute
    - no samples are configured for the ``"default"`` user slot
    - every configured sample is empty / the DB query raises

    Sample bodies are truncated to ``max_words_per_sample`` to stay
    within the writer model's context window.
    """
    if not database_service:
        return None

    try:
        writing_style_db = getattr(database_service, "writing_style", None)
        if not writing_style_db:
            return None

        samples = await writing_style_db.get_user_writing_samples(
            user_id="default", limit=max_samples,
        )
        if not samples:
            return None

        excerpts: list[str] = []
        for sample in samples[:max_samples]:
            content = sample.get("content", "")
            title = sample.get("title", "Untitled")
            if not content:
                continue
            words = content.split()
            if len(words) > max_words_per_sample:
                excerpt = " ".join(words[:max_words_per_sample]) + "..."
            else:
                excerpt = content
            excerpts.append(f"### Sample: {title}\n{excerpt}")

        if not excerpts:
            return None

        logger.info(
            "Loaded %d writing style sample(s) for voice matching", len(excerpts),
        )
        return "\n\n".join(excerpts)

    except Exception as e:  # noqa: BLE001 — legacy non-fatal path
        logger.warning(
            "Failed to load writing style samples (non-fatal, proceeding without): %s",
            e, exc_info=True,
        )
        return None
