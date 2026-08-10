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
    from services.podcast_service import dedupe_episode_title

    task_id = state.get("task_id")
    if not task_id:
        raise ValueError("podcast.render requires task_id")

    site_config = state.get("site_config")

    # The Stage-1 script is a persisted artifact rendered days later, so the
    # duplicate-title guard has to run HERE too — a generator-only fix would
    # leave every already-written script stuttering its own episode name on
    # top of the canonical "Today's episode:" intro. No-op when clean.
    script = dedupe_episode_title(
        state.get("podcast_script") or "", site_config=site_config,
    )

    path = await render_narration(
        script=script,
        cta_key="media.cta.podcast",
        site_config=site_config,
        task_id=task_id,
        key=str(task_id),
    )

    # Intro/outro sting mix (poindexter#690, finished 2026-08): wrap the
    # narration in the show's sting. Read undeclared from state on purpose —
    # the sting is optional ambience, not wiring: declaring it on the contract
    # would force a sting producer into every podcast graph (and re-stamp every
    # stored graph_def) for a feature that degrades gracefully to a dry cut.
    # The state value is only the FIRST choice; ``resolve_sting_path`` falls
    # back to the curated show theme so a stale or never-populated snapshot
    # doesn't cost the episode its music.
    enabled = (
        str(
            site_config.get("podcast_sting_mix_enabled", "true")
            if site_config is not None else "true"
        ).lower() == "true"
    )
    if path and enabled:
        from services.podcast_sting_mixer import mix_intro_outro, resolve_sting_path

        sting = resolve_sting_path(state.get("podcast_intro_audio_path"), site_config)
        if sting.path:
            mixed = await mix_intro_outro(
                path, sting.path, site_config=site_config, task_id=str(task_id),
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
                    f"An intro sting ({sting.source}) was available for task "
                    f"{task_id} but the ffmpeg mix failed (see worker log "
                    "'[sting_mixer]'); the episode was persisted as dry "
                    "narration. Re-render after fixing to get the produced cut."
                ),
                severity="warn",
                dedup_key=f"podcast_sting_mix_failed:{task_id}",
                extra={"task_id": str(task_id or ""), "sting": sting.path},
            )
        elif sting.expected:
            # A sting was carried or configured but nothing usable resolved.
            # This shipped SILENTLY before: the old guard skipped the mix
            # without a word whenever the path was empty, so episodes lost
            # their music with no signal anywhere.
            emit_finding(
                source="podcast.render",
                kind="podcast_sting_missing",
                title="podcast: no usable sting — episode shipped without music",
                body=(
                    f"Task {task_id} rendered without an intro sting: "
                    f"{sting.detail}. The episode was persisted as dry "
                    "narration; fix the path and re-render for the produced cut."
                ),
                severity="warn",
                dedup_key=f"podcast_sting_missing:{task_id}",
                extra={
                    "task_id": str(task_id or ""),
                    "state_sting": str(state.get("podcast_intro_audio_path") or ""),
                },
            )
    return {"podcast_audio_path": path}


__all__ = ["ATOM_META", "run"]
