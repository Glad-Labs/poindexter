"""Shared narration-TTS helper for the Stage-2 media render atoms (#689).

Underscore-prefixed so the atom-registry filesystem scan SKIPS it
(``services/atom_registry.py``: files starting with ``_`` are not discovered as
atoms). This is plumbing, not an atom — mirrors ``_media_render.py``.

Synthesizes a narration script into an MP3 via the existing ``PodcastService``
TTS, after appending the per-medium CTA outro. Used by ``media.render_narration``
(long + short video narration) AND ``podcast.render`` (podcast narration), so the
CTA-append + synth + fail-soft contract lives in ONE place.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from services import live_activity
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


def _emit_narration_failure_finding(
    *, key: str, task_id: Any, cta_key: str, reason: str,
) -> None:
    """Emit the ``narration_synthesis_failed`` finding — TTS produced no audio,
    so this post's video will render **silent and caption-less**.

    Mirrors ``shot_list_renderer``'s ``_emit_hero_fallback_finding`` /
    ``_emit_fallback_finding``: every other degrade-gracefully path in this
    render lane surfaces a finding, and this one didn't, so a dead TTS sidecar
    shipped at least 4 silent videos to the public site over five weeks before
    anyone noticed (Glad-Labs/poindexter#910).

    Advisory ``warn`` — the graph still must not halt on a narration failure.
    This buys the Findings dashboard + the existing findings-routing → Discord
    policy for free; no new alerting plumbing.

    ``reason`` carries WHY synthesis missed, because the TTS sidecar that
    produced the miss may already be gone (recreated / recycled) by the time
    anyone reads it.
    """
    emit_finding(
        source="_narration_render",
        kind="narration_synthesis_failed",
        title=f"narration synthesis failed ({key}) — video will be silent",
        body=(
            f"TTS produced no audio for `{key}` (cta_key=`{cta_key}`, "
            f"task=`{task_id}`), so the media graph falls through to a video "
            f"with no voiceover AND no captions (ASR has nothing to "
            f"transcribe).\n\nreason: {reason}\n\n"
            "The render is fail-soft by design — a narration failure must not "
            "halt the graph — but the resulting post ships degraded and should "
            "be re-rendered once TTS is healthy."
        ),
        severity="warn",
        dedup_key=f"narration_synthesis_failed:{task_id}:{key}",
        extra={
            "key": key,
            "task_id": str(task_id) if task_id else None,
            "cta_key": cta_key,
            "reason": reason,
        },
    )


# Structural section labels the script generator emits as spoken-looking
# text. They are stage directions, not narration — TTS read "Hook" aloud at
# the top of a video (#media-render-fixes). The real writer output wraps them
# in square brackets on their own line ("[Opening Hook]") and often prefixes a
# qualifier word ("Opening Hook", "Final CTA"), so the matchers cover four
# shapes: a whole-line ``[...]`` annotation, a leading ``[...]`` prefix on a
# real line, a bare/marked-up label-only line, and a ``Label:`` prefix — while
# leaving prose that merely STARTS with a label word ("Body cameras changed…")
# untouched.
_SECTION_LABELS = (
    r"hook|teaser|intro(?:duction)?|body|segment|section|scene|part|"
    r"outro|conclusion|wrap[\s-]?up|cta|call[\s-]?to[\s-]?action|"
    r"narrator|voice[\s-]?over|vo"
)
# Qualifier words that commonly precede a section label in a stage direction
# ("Opening Hook", "Closing Outro", "Main Body", "Final CTA").
_LABEL_QUALIFIERS = r"opening|closing|main|final"

# A line that is wholly a square-bracket annotation ("[Opening Hook]",
# "[pause]") — always a stage direction in a narration script, drop outright.
_BRACKET_ONLY_RE = re.compile(r"^\s*\[[^\]]*\]\s*$")
# A leading "[...]" annotation on an otherwise-real line
# ("[Opening Hook] In today's world…") — drop the bracket, keep the prose.
_BRACKET_PREFIX_RE = re.compile(r"^\s*\[[^\]]*\]\s*")

_LABEL_LINE_RE = re.compile(
    rf"^[\s>#*_\(\-]*(?:(?:{_LABEL_QUALIFIERS})\s+)?(?:{_SECTION_LABELS})"
    rf"\s*\d*\s*[:.\)\]\-–—]+\s*",
    re.IGNORECASE,
)
_LABEL_ONLY_RE = re.compile(
    rf"^[\s>#*_\(\-]*(?:(?:{_LABEL_QUALIFIERS})\s+)?(?:{_SECTION_LABELS})"
    rf"\s*\d*\s*[\]\)\*_>.]*\s*$",
    re.IGNORECASE,
)


def _strip_script_labels(text: str) -> str:
    """Drop structural section labels so TTS doesn't read them aloud."""
    out: list[str] = []
    for line in text.splitlines():
        if _BRACKET_ONLY_RE.match(line.strip()):
            # Whole line is a bracketed stage direction ("[Opening Hook]").
            continue
        # A leading "[...]" annotation on a real line — drop the bracket,
        # keep the sentence.
        line = _BRACKET_PREFIX_RE.sub("", line, count=1)
        if _LABEL_ONLY_RE.match(line):
            # The (de-marked) line is just a label ("Hook", "Opening Hook",
            # "**Outro**") — drop it.
            continue
        # A label prefix on an otherwise-real line ("Hook: VRAM is…") — strip
        # the prefix, keep the sentence.
        out.append(_LABEL_LINE_RE.sub("", line, count=1))
    return "\n".join(out).strip()


def compose_narration_text(*, script: str, cta_key: str, site_config: Any) -> str:
    """The exact text the narration TTS voices: structural labels stripped, then
    the per-medium CTA outro appended.

    Shared by ``render_narration`` (what gets synthesized) and
    ``media.transcribe_narration``'s fidelity check (what the ASR transcript is
    diffed against), so the reference can never drift from the audio — the whole
    point of the ``caption_fidelity`` CTA fix. Returns ``""`` when the script is
    empty after label-stripping. ``site_config`` may be ``None`` (no CTA
    resolvable) — the bare stripped script is returned.
    """
    text = _strip_script_labels((script or "").strip())
    if not text:
        return ""
    cta = ""
    if site_config is not None:
        cta = (site_config.get(cta_key, "") or "").strip()
    if cta:
        text = f"{text}\n\n{cta}"
    return text


async def render_narration(
    *,
    script: str,
    cta_key: str,
    site_config: Any,
    task_id: Any,
    key: str,
) -> str:
    """Synthesize ``script`` (+ the CTA at ``cta_key``) to an MP3.

    Returns the temp render path, or ``""`` on any fail-soft condition (empty
    script, no ``site_config``, or a TTS exception). NEVER raises — a narration
    failure must not halt the media graph.

    Every ``""`` return that represents a *failure* also emits a
    ``narration_synthesis_failed`` finding (#910). An empty return is not
    harmless: downstream, ``media.transcribe_narration`` skips ASR and the video
    renders silent AND caption-less, so without a finding the degradation is
    invisible until someone watches the published video. The one genuinely
    harmless case — an empty script, i.e. nothing to narrate — stays quiet.
    """
    text = compose_narration_text(
        script=script, cta_key=cta_key, site_config=site_config,
    )
    if not text:
        # Nothing to narrate — a real no-op, not a degradation. Stay quiet.
        return ""
    if site_config is None:
        # Misconfiguration, not "nothing to say": there IS a script and we are
        # dropping it. Same silent-video outcome as a TTS failure, so it gets
        # the same finding.
        _emit_narration_failure_finding(
            key=key, task_id=task_id, cta_key=cta_key,
            reason="site_config missing — cannot resolve TTS provider",
        )
        return ""

    from services.podcast_service import PodcastService

    # Best-effort live-activity: surface the TTS synth as a kind='media' row in
    # the console pulse (podcast + long/short video narration all reach here).
    # The ledger never affects the render — a None id (begin swallowed) makes
    # the whole bracket a silent no-op, and a synth failure still fail-softs.
    pool = getattr(site_config, "_pool", None)
    try:
        async with live_activity.track(
            pool,
            kind="media",
            ref_id=str(task_id) if task_id else None,
            title=f"Narration · {key}",
            detail={"medium": "audio", "cta_key": cta_key},
            heartbeat_seconds=live_activity.resolve_heartbeat_seconds(site_config),
        ) as act:
            await act.update(step="synthesizing narration")
            path, _duration = await PodcastService(site_config=site_config).synthesize(
                text, key=key,
            )
    except Exception as exc:  # noqa: BLE001 — TTS failure must not halt the graph
        logger.warning(
            "[_narration_render] synthesis failed (key=%s, task=%s): %s",
            key, task_id, exc,
        )
        _emit_narration_failure_finding(
            key=key, task_id=task_id, cta_key=cta_key, reason=str(exc),
        )
        return ""
    if not path:
        # synthesize() returned empty WITHOUT raising — e.g. every configured
        # voice failed and the provider swallowed it. Same silent-video
        # outcome as the exception path, so it must be equally visible; this
        # branch is why the finding can't just live in the `except`.
        logger.warning(
            "[_narration_render] synthesis returned no audio path "
            "(key=%s, task=%s)", key, task_id,
        )
        _emit_narration_failure_finding(
            key=key, task_id=task_id, cta_key=cta_key,
            reason="TTS returned no audio path (no exception raised)",
        )
        return ""
    return path


__all__ = ["compose_narration_text", "render_narration"]
