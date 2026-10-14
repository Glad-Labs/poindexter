"""media.director_notes — a finished cartoon in, the director's notes on it out.

Chat drafting, phase 8. Thin adapter over ``services/director_notes.py``: the
vision judge looks at every drawn shot of the video ``media.persist`` just
saved, and the writer turns what it saw into ranked notes, stored on the
video's asset row for the render card and the chat. Redo notes feed a render
revision from the chat.

Best-effort and last: it runs after the video is in the approval queue, and
a review that fails is recorded as failed, never as a failed render.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="media.director_notes",
    type="atom",
    version="1.0.0",
    description=(
        "Screen a finished cartoon: the vision judge checks every drawn shot "
        "(hands, faces, mouths, strangers, writing, motion, does it show what "
        "it should) and the writer gives ranked notes, with redo notes the "
        "chat can turn into a render revision. INPUT task_id (the video "
        "media.persist saved). OUTPUT director_notes (status and note count; "
        "the notes are stored on the video). USUALLY FOLLOWS media.persist, "
        "last in a cartoon chain; best-effort."
    ),
    inputs=(FieldSpec(name="task_id", type="str", description="the task whose video to review"),),
    outputs=(FieldSpec(name="director_notes", type="dict", description="status and note count"),),
    requires=("task_id",),
    produces=("director_notes",),
    capability_tier="standard",
    cost_class="compute",
    idempotent=True,
    side_effects=("calls the vision judge and the writer LLM", "stores notes on the video's asset row"),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services import director_notes as dn

    site_config = state.get("site_config")
    if not dn._on(site_config, "director_notes_enabled"):
        return {"director_notes": {"status": "off"}}
    database_service = state.get("database_service")
    pool = getattr(database_service, "pool", None) if database_service else state.get("pool")
    task_id = str(state.get("task_id") or "")
    if pool is None or not task_id:
        return {"director_notes": {"status": "skipped", "reason": "no pool or task"}}
    try:
        review = await dn.run_review(pool, task_id=task_id, site_config=site_config)
    except Exception as exc:  # noqa: BLE001 — notes are never worth failing a finished render for
        logger.warning("[media.director_notes] no notes for %s: %s", task_id, exc)
        return {"director_notes": {"status": "failed", "error": str(exc)[:300]}}
    return {"director_notes": {"status": review.get("status"), "notes": len(review.get("notes") or [])}}
