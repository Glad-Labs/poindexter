"""media.render_narration — Stage-2 narration TTS atom (#689).

Renders the long-form AND short-form video narration audio from their OWN
scripts + CTAs, BEFORE the transcribe / QA / render nodes that consume them:

  - long:  ``video_long_script`` (fallback ``podcast_script``) + ``media.cta.video``
           → ``long_narration_audio_path``
  - short: ``short_summary_script`` + ``media.cta.video_short``
           → ``short_narration_audio_path``

Fail-soft per channel (empty path on TTS failure / empty script) — a narration
failure must NOT halt the graph; the downstream render no-ops audio gracefully.
Delegates CTA-append + synth to ``_narration_render.render_narration`` so this
atom and ``podcast.render`` share one TTS code path.

NOTE (#674 trap): the two output channels MUST be declared ``PipelineState``
channels (they are — added alongside this atom) or LangGraph silently drops them.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="media.render_narration",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-2: synthesize the long-form + short-form video narration audio "
        "from their own scripts + CTAs (media.cta.video / media.cta.video_short)."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="video_long_script", type="str", description="long-form narration script", required=False),
        FieldSpec(name="podcast_script", type="str", description="fallback long narration script", required=False),
        FieldSpec(name="short_summary_script", type="str", description="short-form narration script", required=False),
        FieldSpec(name="site_config", type="object", description="DI seam (TTS + CTA config)", required=False),
    ),
    outputs=(
        FieldSpec(name="long_narration_audio_path", type="str", description="long narration MP3 ('' on no-op/failure)"),
        FieldSpec(name="short_narration_audio_path", type="str", description="short narration MP3 ('' on no-op/failure)"),
    ),
    requires=("task_id",),
    produces=("long_narration_audio_path", "short_narration_audio_path"),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("filesystem",),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Render long + short narration audio. Best-effort — never raises."""
    from modules.content.atoms._narration_render import render_narration

    task_id = state.get("task_id")
    site_config = state.get("site_config")

    # Long: prefer the purpose-built long script, fall back to the podcast
    # script so a missing long script degrades to "has audio", not silence.
    long_script = (state.get("video_long_script") or "").strip() or (
        state.get("podcast_script") or ""
    )
    long_path = await render_narration(
        script=long_script,
        cta_key="media.cta.video",
        site_config=site_config,
        task_id=task_id,
        key=f"{task_id}_long",
    )

    # Short: its own script only (a "short" narrated by the full article would
    # be wrong) — empty short script → no short narration.
    short_path = await render_narration(
        script=state.get("short_summary_script") or "",
        cta_key="media.cta.video_short",
        site_config=site_config,
        task_id=task_id,
        key=f"{task_id}_short",
    )

    return {
        "long_narration_audio_path": long_path,
        "short_narration_audio_path": short_path,
    }


__all__ = ["ATOM_META", "run"]
