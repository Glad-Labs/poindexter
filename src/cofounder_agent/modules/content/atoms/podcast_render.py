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

import os
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from utils.findings import emit_finding

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
    # podcast_script required (poindexter#983): this atom read the key with
    # an empty-string fallback, so a graph with no script producer upstream
    # validated clean and rendered silence. Producers: stage.generate_media_scripts
    # (forward pipeline) or podcast.load_script (re-render lane).
    requires=("task_id", "podcast_script"),
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

    # Intro/outro sting mix (poindexter#690, finished 2026-08): when
    # generate_media_scripts synthesized a sting, wrap the narration in
    # it. Read undeclared from state on purpose — the sting is optional
    # ambience, not wiring: declaring it on the contract would force a
    # sting producer into every podcast graph (and re-stamp every stored
    # graph_def) for a feature that degrades gracefully to a dry cut.
    sting = str(state.get("podcast_intro_audio_path") or "")
    site_config = state.get("site_config")
    enabled = (
        str(
            site_config.get("podcast_sting_mix_enabled", "true")
            if site_config is not None else "true"
        ).lower() == "true"
    )
    if path and sting and enabled and os.path.exists(sting):
        from services.podcast_sting_mixer import mix_intro_outro

        mixed = await mix_intro_outro(
            path, sting, site_config=site_config, task_id=str(task_id),
        )
        if mixed:
            return {"podcast_audio_path": mixed}
        # Sting existed but the mix failed — the episode ships dry, which
        # is a quality downgrade the operator should hear about
        # (feedback_flag_quality_downgrades), not a silent fallback.
        emit_finding(
            source="podcast.render",
            kind="podcast_sting_mix_failed",
            title="podcast: sting mix failed — episode shipped without music",
            body=(
                f"A generated intro sting existed for task {task_id} but the "
                f"ffmpeg mix failed (see worker log '[sting_mixer]'); the "
                "episode was persisted as dry narration. Re-render after "
                "fixing to get the produced cut."
            ),
            severity="warn",
            dedup_key=f"podcast_sting_mix_failed:{task_id}",
            extra={"task_id": str(task_id or ""), "sting": sting},
        )
    return {"podcast_audio_path": path}


__all__ = ["ATOM_META", "run"]
