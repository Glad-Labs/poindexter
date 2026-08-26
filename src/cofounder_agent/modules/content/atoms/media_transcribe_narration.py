"""media.transcribe_narration — Stage-2 per-lane ASR atom (#676 / #689).

Runs one ASR pass over EACH video lane's narration audio (long + short),
BEFORE the render nodes. The provider is selected by
``services.caption_providers.get_caption_provider`` — the ``video_caption_engine``
app-setting, default ``speaches`` (the already-running faster-whisper sidecar).
Per lane it does two things from one transcription (redesign §6 "one ASR pass"):

1. **Captions (#676):** writes the SRT document to a temp file and surfaces it
   on the lane's caption channel (``long_caption_srt_path`` /
   ``short_caption_srt_path``) so each render burns in the captions for the
   narration it actually plays. (Pre-#689 a single pass over a shared
   ``podcast_audio_path`` produced one caption track for both renders; now that
   each lane narrates its OWN script, captions are per-lane.)

2. **Fidelity QA (#676 part b):** compares each lane's ASR transcript against
   the **voiced** narration text — the lane's script with stage-direction labels
   stripped and its CTA outro appended (``compose_narration_text``, the exact
   text ``render_narration`` synthesized) — with a normalized
   ``difflib.SequenceMatcher`` ratio. Diffing against the voiced text rather than
   the raw script keeps the CTA outro (and the dropped labels) from spuriously
   tanking the ratio — worst on the short lane, where the CTA is a big fraction
   of a brief summary, the cause of the ``caption_fidelity`` 0.00 false positives.
   A low ratio (below the DB-configurable ``media.caption.fidelity_min_ratio``,
   default 0.80) emits an advisory ``caption_fidelity`` finding — catches TTS
   dropouts / truncation.

Captions are **best-effort**: a caption failure (provider disabled / unreachable,
audio missing, provider exception) must NEVER halt the graph — the video still
renders, just without burned-in captions. So every failure mode returns an empty
path (and, where useful, emits a per-lane finding) rather than raising.

NOTE (#674 trap): ``long_caption_srt_path`` / ``short_caption_srt_path`` MUST be
declared ``PipelineState`` channels or LangGraph silently drops them, and the
render atoms would never see the captions.
"""

from __future__ import annotations

import difflib
import logging
import re
import tempfile
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from services.caption_providers import get_caption_provider
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# DB-configurable minimum ASR-vs-script similarity ratio. Below this, the
# transcript diverged enough from the source script to flag (TTS dropout /
# truncation). Tunable via app_settings ``media.caption.fidelity_min_ratio``.
_DEFAULT_FIDELITY_MIN_RATIO = 0.80

# Display-cue word budgets per lane (2026-08-24): whisper segments are
# sentence-sized (10-15 words is routine), and burned whole they wrap into a
# frame-filling text wall — worst on the 9:16 short, where the portrait frame
# fits ~2-4 words per line at mobile-readable size. Segments over the budget
# are re-cut into short cues with interpolated timings
# (``caption_align.split_segments_for_display``). Short-form convention is
# 3-8 words on screen; the 16:9 long lane wears conventional two-line
# subtitles, hence the larger budget. Tunable per lane via app_settings
# ``media.caption.{short,long}_max_cue_words``; 0 disables splitting.
_DEFAULT_MAX_CUE_WORDS = {"short": 5, "long": 14}
_DEFAULT_MIN_CUE_SECONDS = 0.6


def _resolve_max_cue_words(site_config: Any, label: str) -> int:
    """Per-lane display-cue word budget (``media.caption.<lane>_max_cue_words``)."""
    default = _DEFAULT_MAX_CUE_WORDS.get(label, 0)
    if site_config is None:
        return default
    try:
        return int(site_config.get(f"media.caption.{label}_max_cue_words", default))
    except (TypeError, ValueError):
        return default


def _resolve_min_cue_seconds(site_config: Any) -> float:
    """Floor a display cue's window (``media.caption.min_cue_seconds``)."""
    if site_config is None:
        return _DEFAULT_MIN_CUE_SECONDS
    try:
        return float(
            site_config.get("media.caption.min_cue_seconds", _DEFAULT_MIN_CUE_SECONDS)
        )
    except (TypeError, ValueError):
        return _DEFAULT_MIN_CUE_SECONDS


ATOM_META = AtomMeta(
    name="media.transcribe_narration",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-2: one ASR pass per video lane over its narration audio — "
        "produces a per-lane SRT caption track for the render to burn in "
        "(#676/#689) and a fidelity check of each transcript vs its source script."
    ),
    inputs=(
        FieldSpec(name="long_narration_audio_path", type="str", description="long narration audio path", required=False),
        FieldSpec(name="short_narration_audio_path", type="str", description="short narration audio path", required=False),
        FieldSpec(name="video_long_script", type="str", description="long source script (fidelity)", required=False),
        FieldSpec(name="short_summary_script", type="str", description="short source script (fidelity)", required=False),
        FieldSpec(name="site_config", type="object", description="DI seam (caption provider config)", required=False),
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
    ),
    outputs=(
        FieldSpec(name="long_caption_srt_path", type="str", description="long burned-in SRT path ('' when unavailable)"),
        FieldSpec(name="short_caption_srt_path", type="str", description="short burned-in SRT path ('' when unavailable)"),
    ),
    requires=("task_id",),
    produces=("long_caption_srt_path", "short_caption_srt_path"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("filesystem",),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=False,
)


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Used so the fidelity ratio compares the WORDS the ASR heard vs the WORDS
    the writer scripted, not capitalization / punctuation / spacing noise.
    """
    lowered = (text or "").lower()
    stripped = re.sub(r"[^\w\s]", " ", lowered)
    return re.sub(r"\s+", " ", stripped).strip()


def _fidelity_ratio(asr_transcript: str, script: str) -> float:
    """Normalized similarity ratio (0.0–1.0) between ASR + source script."""
    return difflib.SequenceMatcher(
        None, _normalize(asr_transcript), _normalize(script)
    ).ratio()


def _resolve_threshold(site_config: Any) -> float:
    """Read the DB-configurable fidelity threshold (default 0.80)."""
    if site_config is None:
        return _DEFAULT_FIDELITY_MIN_RATIO
    try:
        raw = site_config.get(
            "media.caption.fidelity_min_ratio", _DEFAULT_FIDELITY_MIN_RATIO
        )
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_FIDELITY_MIN_RATIO


async def _transcribe_one(
    *,
    audio_path: str,
    script: str,
    caption_text: str = "",
    task_id: Any,
    label: str,
    site_config: Any,
) -> str:
    """One ASR pass over a single lane's narration → its SRT caption path.

    ``script`` is the fidelity reference the ASR transcript is diffed against —
    in production the fully **voiced** text (labels stripped + CTA outro +
    the TTS speech-normalization pass), composed by ``run`` so it matches
    what TTS actually said. ``caption_text`` is the CLEAN written sibling
    (same composition, no speech pass): when script-alignment is enabled the
    burned captions are rebuilt from it on the ASR timings, so viewers read
    "Phi-4" / "CI/CD" / "gate" instead of Whisper's phonetic guesses
    ("PHY 4", "gait" — the 2026-08-03 operator finding).

    Returns the SRT path, or ``""`` on any no-op/failure (no audio, whisper
    unavailable, write error). Best-effort — never raises. Emits per-lane
    findings (dedup keyed by task + ``label``) on failure / low fidelity.
    """
    if not audio_path:
        logger.info(
            "[media.transcribe_narration] task=%s lane=%s no narration audio — "
            "skipping ASR (captions unavailable, video still renders)",
            task_id, label,
        )
        return ""

    try:
        provider = get_caption_provider(site_config)
        result = await provider.transcribe(audio_path=audio_path, task_id=task_id)
    except Exception as exc:  # noqa: BLE001 — a caption failure must not halt the graph
        logger.exception(
            "[media.transcribe_narration] task=%s lane=%s transcribe raised: %s",
            task_id, label, exc,
        )
        emit_finding(
            source="media.transcribe_narration",
            kind="caption_failed",
            title=f"ASR transcription raised an exception ({label})",
            body=f"provider.transcribe raised for task {task_id} lane {label}: {describe_exception(exc)}",
            severity="warn",
            dedup_key=f"caption_failed:{task_id}:{label}",
            extra={"task_id": str(task_id or ""), "lane": label, "error": str(exc)},
        )
        return ""

    asr_transcript = " ".join(
        seg.text for seg in (result.segments or []) if seg.text
    ).strip()

    if not result.success or not result.srt_text:
        logger.info(
            "[media.transcribe_narration] task=%s lane=%s captions unavailable "
            "(success=%s, srt=%s) — rendering without burned-in captions",
            task_id, label, result.success, bool(result.srt_text),
        )
        emit_finding(
            source="media.transcribe_narration",
            kind="caption_unavailable",
            title=f"ASR produced no usable caption track ({label})",
            body=(
                f"transcribe for task {task_id} lane {label} returned success="
                f"{result.success}, srt_text empty={not result.srt_text}: "
                f"{result.error or 'no error detail'}"
            ),
            severity="info",
            dedup_key=f"caption_unavailable:{task_id}:{label}",
            extra={"task_id": str(task_id or ""), "lane": label, "error": result.error},
        )
        return ""

    # Script-alignment (2026-08-03): the captions' TEXT comes from the known
    # script; ASR contributes only the timings. Whisper transcribing the TTS
    # audio wrote homophones ("gait") and phonetic product names ("PHY 4")
    # into burned captions — but we synthesized that audio from exact text, so
    # map the clean written script onto the ASR segments instead. Gated on
    # alignment quality: a poor match (heavy TTS dropout, wrong audio) keeps
    # the ASR captions rather than smearing script text across wrong timings.
    srt_text = result.srt_text
    display_segments = list(result.segments or [])
    align_enabled = True
    min_ratio = 0.5
    if site_config is not None:
        try:
            align_enabled = site_config.get_bool(
                "media.caption.script_alignment_enabled", True,
            )
        except Exception:  # noqa: BLE001 — settings read must not kill captions
            align_enabled = True
        try:
            min_ratio = float(site_config.get(
                "media.caption.alignment_min_ratio", 0.5,
            ))
        except (TypeError, ValueError):
            min_ratio = 0.5
    if align_enabled and caption_text and result.segments:
        from services.caption_align import align_script_to_segments, segments_to_srt

        aligned, fraction = align_script_to_segments(
            list(result.segments), caption_text,
        )
        if fraction >= min_ratio:
            rebuilt = segments_to_srt(aligned)
            if rebuilt:
                srt_text = rebuilt
                display_segments = aligned
                logger.info(
                    "[media.transcribe_narration] task=%s lane=%s captions "
                    "aligned to script (match %.2f, %d segments)",
                    task_id, label, fraction, len(aligned),
                )
        else:
            logger.info(
                "[media.transcribe_narration] task=%s lane=%s alignment match "
                "%.2f < %.2f — keeping ASR captions",
                task_id, label, fraction, min_ratio,
            )

    # Display chunking (2026-08-24): re-cut sentence-sized segments into short
    # cues so the burn never renders a frame-filling text wall. Applies to the
    # aligned segments when alignment ran (their text is the clean script) and
    # to the raw ASR segments otherwise. Runs AFTER fidelity references are
    # captured — ``asr_transcript`` above is built from the provider's original
    # segments, so QA compares speech, not display formatting.
    max_cue_words = _resolve_max_cue_words(site_config, label)
    if max_cue_words > 0 and display_segments:
        from services.caption_align import (
            segments_to_srt,
            split_segments_for_display,
        )

        chunked = split_segments_for_display(
            display_segments,
            max_words=max_cue_words,
            min_cue_seconds=_resolve_min_cue_seconds(site_config),
        )
        if len(chunked) != len(display_segments):
            rebuilt = segments_to_srt(chunked)
            if rebuilt:
                srt_text = rebuilt
                logger.info(
                    "[media.transcribe_narration] task=%s lane=%s split %d "
                    "segment(s) into %d display cue(s) (max %d words/cue)",
                    task_id, label, len(display_segments), len(chunked),
                    max_cue_words,
                )

    srt_path = f"{tempfile.gettempdir()}/captions_{task_id}_{label}.srt"
    try:
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_text)
    except OSError as exc:
        logger.warning(
            "[media.transcribe_narration] task=%s lane=%s failed to write SRT %s: %s",
            task_id, label, srt_path, exc,
        )
        emit_finding(
            source="media.transcribe_narration",
            kind="caption_failed",
            title=f"Failed to write caption SRT to disk ({label})",
            body=f"writing {srt_path} for task {task_id} lane {label} raised: {describe_exception(exc)}",
            severity="warn",
            dedup_key=f"caption_failed:{task_id}:{label}",
            extra={"task_id": str(task_id or ""), "lane": label, "error": str(exc)},
        )
        return ""

    # Fidelity QA (#676 part b): compare the ASR transcript to the source script.
    # Only when both are non-empty — nothing to compare otherwise.
    if asr_transcript and script:
        threshold = _resolve_threshold(site_config)
        ratio = _fidelity_ratio(asr_transcript, script)
        if ratio < threshold:
            emit_finding(
                source="media.transcribe_narration",
                kind="caption_fidelity",
                title=f"ASR fidelity {ratio:.2f} < {threshold} ({label})",
                body=(
                    f"The {label} narration ASR transcript for task {task_id} "
                    f"diverged from the voiced narration text — its script (labels "
                    f"stripped) plus the CTA outro (normalized SequenceMatcher "
                    f"ratio {ratio:.3f} < {threshold}). Likely a TTS dropout or "
                    "truncation. Captions still burned in; advisory only."
                ),
                severity="warn",
                dedup_key=f"caption_fidelity:{task_id}:{label}",
                extra={
                    "task_id": str(task_id or ""),
                    "lane": label,
                    "ratio": ratio,
                    "threshold": threshold,
                    "asr_len": len(asr_transcript),
                    "script_len": len(script),
                },
            )

    logger.info(
        "[media.transcribe_narration] task=%s lane=%s wrote captions to %s "
        "(transcript=%dc)",
        task_id, label, srt_path, len(asr_transcript),
    )
    return srt_path


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """One ASR pass per video lane → per-lane SRT caption tracks (#689).

    Best-effort — never raises. Returns ``{"long_caption_srt_path": <path-or-"">,
    "short_caption_srt_path": <path-or-"">}``.
    """
    from modules.content.atoms._narration_render import compose_narration_text

    task_id = state.get("task_id")
    site_config = state.get("site_config")

    # Diff the ASR against the VOICED text — labels stripped + the per-lane CTA
    # outro appended — reproduced from the SAME composer render_narration used,
    # so the reference matches what TTS actually said. Diffing against the raw
    # script instead lacks the CTA (and keeps stage-direction labels), which
    # tanks fidelity — worst on the short lane where the CTA dominates — and was
    # the false positive behind the caption_fidelity 0.00 findings.
    long_script = (state.get("video_long_script") or "").strip() or (
        state.get("podcast_script") or ""
    )
    # 2026-08-01 normalizer split: scripts are stored CLEAN and the TTS
    # boundary applies the full speech pass (pronunciations, acronyms, model
    # names). The fidelity reference must be the text TTS actually received,
    # so the same pass runs here — otherwise a clean-script "CI/CD" diffs
    # against ASR of the spoken "See Eye See Dee" and tanks fidelity.
    from services.podcast_service import _normalize_for_speech

    def _tts_input(text: str) -> str:
        # site_config=None (tests / degraded runs) would fail-loud inside the
        # normalizer's DI seam (#272); the un-normalized composed text is the
        # correct reference there because the TTS boundary can't normalize
        # without a site_config either.
        if site_config is None or not text:
            return text
        return _normalize_for_speech(text, site_config=site_config)

    long_caption_text = compose_narration_text(
        script=long_script, cta_key="media.cta.video", site_config=site_config,
    )
    short_caption_text = compose_narration_text(
        script=state.get("short_summary_script") or "",
        cta_key="media.cta.video_short", site_config=site_config,
    )
    long_srt = await _transcribe_one(
        audio_path=state.get("long_narration_audio_path") or "",
        script=_tts_input(long_caption_text),
        caption_text=long_caption_text,
        task_id=task_id, label="long", site_config=site_config,
    )
    short_srt = await _transcribe_one(
        audio_path=state.get("short_narration_audio_path") or "",
        script=_tts_input(short_caption_text),
        caption_text=short_caption_text,
        task_id=task_id, label="short", site_config=site_config,
    )
    return {
        "long_caption_srt_path": long_srt,
        "short_caption_srt_path": short_srt,
    }


__all__ = ["ATOM_META", "run"]
