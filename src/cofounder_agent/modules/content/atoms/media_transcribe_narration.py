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
   that lane's source script (``video_long_script`` / ``short_summary_script``)
   with a normalized ``difflib.SequenceMatcher`` ratio. A low ratio (below the
   DB-configurable ``media.caption.fidelity_min_ratio``, default 0.80) emits an
   advisory ``caption_fidelity`` finding — catches TTS dropouts / truncation.

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
    task_id: Any,
    label: str,
    site_config: Any,
) -> str:
    """One ASR pass over a single lane's narration → its SRT caption path.

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
            body=f"provider.transcribe raised for task {task_id} lane {label}: {exc}",
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

    srt_path = f"{tempfile.gettempdir()}/captions_{task_id}_{label}.srt"
    try:
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(result.srt_text)
    except OSError as exc:
        logger.warning(
            "[media.transcribe_narration] task=%s lane=%s failed to write SRT %s: %s",
            task_id, label, srt_path, exc,
        )
        emit_finding(
            source="media.transcribe_narration",
            kind="caption_failed",
            title=f"Failed to write caption SRT to disk ({label})",
            body=f"writing {srt_path} for task {task_id} lane {label} raised: {exc}",
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
                    f"diverged from its source script (normalized SequenceMatcher "
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
    task_id = state.get("task_id")
    site_config = state.get("site_config")
    long_srt = await _transcribe_one(
        audio_path=state.get("long_narration_audio_path") or "",
        script=state.get("video_long_script") or state.get("podcast_script") or "",
        task_id=task_id, label="long", site_config=site_config,
    )
    short_srt = await _transcribe_one(
        audio_path=state.get("short_narration_audio_path") or "",
        script=state.get("short_summary_script") or "",
        task_id=task_id, label="short", site_config=site_config,
    )
    return {
        "long_caption_srt_path": long_srt,
        "short_caption_srt_path": short_srt,
    }


__all__ = ["ATOM_META", "run"]
