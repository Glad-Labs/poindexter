"""media.transcribe_narration — Stage-2 one-ASR-pass atom (Plan 5, #676).

Runs a single ASR (whisper.cpp) pass over the podcast narration BEFORE the
render nodes. It does two things from one transcription (redesign §6 "one ASR
pass"):

1. **Captions (#676 part a):** writes the SRT document to a temp file and
   surfaces it on the ``caption_srt_path`` channel so BOTH render atoms
   (``media.render_long_video`` / ``media.render_short_video``) can burn the
   same captions into their videos — they narrate the same
   ``podcast_audio_path``, so one ASR pass covers both.

2. **Fidelity QA (#676 part b):** compares the ASR transcript against the
   source narration script (``podcast_script``) with a normalized
   ``difflib.SequenceMatcher`` ratio. A low ratio (below the DB-configurable
   ``media.caption.fidelity_min_ratio``, default 0.80) emits an advisory
   ``caption_fidelity`` finding — this catches TTS dropouts / truncation where
   the spoken audio diverged from what the writer scripted.

Captions are **best-effort**: a caption failure (whisper not installed, audio
missing, provider exception) must NEVER halt the graph — the video still
renders, just without burned-in captions. So every failure mode returns empty
keys (and, where useful, emits an informational/warning finding) rather than
raising.

NOTE (#674 trap): ``caption_srt_path`` / ``asr_transcript`` MUST be declared
``PipelineState`` channels (added in Plan 5) or LangGraph silently drops them,
and the render atoms would never see the captions.
"""

from __future__ import annotations

import difflib
import logging
import re
import tempfile
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from services.caption_providers.whisper_local import WhisperLocalCaptionProvider
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# DB-configurable minimum ASR-vs-script similarity ratio. Below this, the
# transcript diverged enough from the source script to flag (TTS dropout /
# truncation). Tunable via app_settings ``media.caption.fidelity_min_ratio``.
_DEFAULT_FIDELITY_MIN_RATIO = 0.80


ATOM_META = AtomMeta(
    name="media.transcribe_narration",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-2: one ASR pass over the podcast narration — produces an SRT "
        "caption track for the renders to burn in (#676) and a fidelity check "
        "of the transcript vs the source script."
    ),
    inputs=(
        FieldSpec(name="podcast_audio_path", type="str", description="narration audio path"),
        FieldSpec(name="podcast_script", type="str", description="source narration script", required=False),
        FieldSpec(name="site_config", type="object", description="DI seam (caption provider config)", required=False),
        FieldSpec(name="database_service", type="object", description="DB service (pool source)", required=False),
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
    ),
    outputs=(
        FieldSpec(name="caption_srt_path", type="str", description="burned-in SRT path ('' when unavailable)"),
        FieldSpec(name="asr_transcript", type="str", description="ASR transcript text ('' when unavailable)"),
    ),
    requires=("task_id",),
    produces=("caption_srt_path", "asr_transcript"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("filesystem",),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=False,
)

_EMPTY = {"caption_srt_path": "", "asr_transcript": ""}


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Used so the fidelity ratio compares the WORDS the ASR heard vs the WORDS
    the writer scripted, not capitalization / punctuation / spacing noise
    (which differs between TTS-input prose and ASR output).
    """
    lowered = (text or "").lower()
    # Drop everything that isn't a word char or whitespace, then collapse runs.
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


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """One ASR pass: produce ``caption_srt_path`` + ``asr_transcript``.

    Best-effort — never raises. Returns ``{"caption_srt_path": <path-or-"">,
    "asr_transcript": <text-or-"">}``.
    """
    task_id = state.get("task_id")
    narration = state.get("podcast_audio_path") or ""
    if not narration:
        # Nothing to transcribe (e.g. podcast TTS disabled) — graceful no-op.
        logger.info(
            "[media.transcribe_narration] task=%s no podcast_audio_path — "
            "skipping ASR (captions unavailable, video still renders)",
            task_id,
        )
        return dict(_EMPTY)

    site_config = state.get("site_config")

    # Get the caption provider. caption_providers/__init__ exposes no
    # resolver/factory (it's a docstring-only package), so instantiate the
    # default whisper_local provider directly behind the CaptionProvider seam.
    # The provider returns success=False (never raises) when disabled / binary
    # missing / model missing, so it's safe to call even without whisper.
    try:
        provider = WhisperLocalCaptionProvider(site_config=site_config)
        result = await provider.transcribe(
            audio_path=narration, task_id=task_id
        )
    except Exception as exc:  # noqa: BLE001 — a caption failure must not halt the graph
        logger.exception(
            "[media.transcribe_narration] task=%s transcribe raised: %s",
            task_id, exc,
        )
        emit_finding(
            source="media.transcribe_narration",
            kind="caption_failed",
            title="ASR transcription raised an exception",
            body=f"provider.transcribe raised for task {task_id}: {exc}",
            severity="warn",
            dedup_key=f"caption_failed:{task_id}",
            extra={"task_id": str(task_id or ""), "error": str(exc)},
        )
        return dict(_EMPTY)

    # Derive the transcript from the segments (falls back to "" if none).
    asr_transcript = " ".join(
        seg.text for seg in (result.segments or []) if seg.text
    ).strip()

    if not result.success or not result.srt_text:
        # Captions unavailable (e.g. whisper not installed) — best-effort, so
        # log + an informational finding, but still surface whatever transcript
        # we got. The video renders without burned-in captions.
        logger.info(
            "[media.transcribe_narration] task=%s captions unavailable "
            "(success=%s, srt=%s) — rendering without burned-in captions",
            task_id, result.success, bool(result.srt_text),
        )
        emit_finding(
            source="media.transcribe_narration",
            kind="caption_unavailable",
            title="ASR produced no usable caption track",
            body=(
                f"transcribe for task {task_id} returned success="
                f"{result.success}, srt_text empty={not result.srt_text}: "
                f"{result.error or 'no error detail'}"
            ),
            severity="info",
            dedup_key=f"caption_unavailable:{task_id}",
            extra={"task_id": str(task_id or ""), "error": result.error},
        )
        return {"caption_srt_path": "", "asr_transcript": asr_transcript}

    # Success: persist the SRT document where the compositor can burn it in.
    srt_path = f"{tempfile.gettempdir()}/captions_{task_id}.srt"
    try:
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(result.srt_text)
    except OSError as exc:
        logger.warning(
            "[media.transcribe_narration] task=%s failed to write SRT %s: %s",
            task_id, srt_path, exc,
        )
        emit_finding(
            source="media.transcribe_narration",
            kind="caption_failed",
            title="Failed to write caption SRT to disk",
            body=f"writing {srt_path} for task {task_id} raised: {exc}",
            severity="warn",
            dedup_key=f"caption_failed:{task_id}",
            extra={"task_id": str(task_id or ""), "error": str(exc)},
        )
        return {"caption_srt_path": "", "asr_transcript": asr_transcript}

    # Fidelity QA (#676 part b): compare the ASR transcript to the source
    # script. Only when both are non-empty — nothing to compare otherwise.
    script = state.get("podcast_script") or ""
    if asr_transcript and script:
        threshold = _resolve_threshold(site_config)
        ratio = _fidelity_ratio(asr_transcript, script)
        if ratio < threshold:
            emit_finding(
                source="media.transcribe_narration",
                kind="caption_fidelity",
                title=f"ASR fidelity {ratio:.2f} < {threshold}",
                body=(
                    f"The narration ASR transcript for task {task_id} diverged "
                    f"from the source script (normalized SequenceMatcher ratio "
                    f"{ratio:.3f} < {threshold}). Likely a TTS dropout or "
                    "truncation — the spoken audio doesn't match what was "
                    "scripted. Captions still burned in; advisory only."
                ),
                severity="warn",
                dedup_key=f"caption_fidelity:{task_id}",
                extra={
                    "task_id": str(task_id or ""),
                    "ratio": ratio,
                    "threshold": threshold,
                    "asr_len": len(asr_transcript),
                    "script_len": len(script),
                },
            )

    logger.info(
        "[media.transcribe_narration] task=%s wrote captions to %s "
        "(transcript=%dc)",
        task_id, srt_path, len(asr_transcript),
    )
    return {"caption_srt_path": srt_path, "asr_transcript": asr_transcript}


__all__ = ["ATOM_META", "run"]
