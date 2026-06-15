"""podcast.render — Stage-3 render atom (#689 deviation, ``podcast_pipeline``).

Synthesizes the loaded ``podcast_script`` into an MP3 via Kokoro/Speaches TTS
(``PodcastService.synthesize``), after appending the DB-configurable per-medium
CTA outro (``media.cta.podcast`` — "rate & review", distinct from the video
lane's "like & subscribe"). Surfaces the temp render path on
``podcast_audio_path`` for ``qa.audio`` and ``podcast.persist`` downstream.

Fail-soft per the pipeline contract: an empty script, a missing ``site_config``,
or a TTS failure returns an empty ``podcast_audio_path`` rather than raising —
the graph finishes and the ``media_reconciliation`` watchdog re-dispatches.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="podcast.render",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-3: synthesize the podcast narration MP3 from podcast_script via "
        "Kokoro/Speaches TTS with the per-medium CTA outro appended."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="podcast_script", type="str", description="podcast VO script", required=False),
        FieldSpec(name="site_config", type="object", description="SiteConfig (TTS + CTA config)", required=False),
    ),
    outputs=(
        FieldSpec(name="podcast_audio_path", type="str", description="temp path of the rendered narration MP3"),
    ),
    requires=("task_id",),
    produces=("podcast_audio_path",),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("file_write",),
    retry=RetryPolicy(),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Render the podcast narration MP3, returning its temp path (or '').

    Delegates the CTA-append + synth + fail-soft contract to the shared
    ``_narration_render`` helper (the same one the video lanes use), so there is
    a single TTS code path across podcast + video narration (#689).
    """
    from modules.content.atoms._narration_render import render_narration

    task_id = state.get("task_id")
    if not task_id:
        raise ValueError("podcast.render requires task_id")

    path = await render_narration(
        script=state.get("podcast_script") or "",
        cta_key="media.cta.podcast",
        site_config=state.get("site_config"),
        task_id=task_id,
        key=str(task_id),
    )
    return {"podcast_audio_path": path}


__all__ = ["ATOM_META", "run"]
