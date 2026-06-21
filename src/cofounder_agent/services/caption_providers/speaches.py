"""SpeachesCaptionProvider — ASR captions via the Speaches faster-whisper sidecar.

The stack already runs Speaches for narration TTS and voice STT
(``Systran/faster-whisper-medium``). This provider reuses that running
service for caption transcription instead of requiring a second, separate
whisper.cpp install in the worker container (which was never installed —
``whisper-cli`` is absent, so the legacy ``WhisperLocalCaptionProvider``
silently produced no captions). It POSTs each lane's narration audio to
Speaches' OpenAI-compatible ``/audio/transcriptions`` endpoint and turns the
returned ``verbose_json`` segments into an SRT track for the render to burn in.

License: faster-whisper (CTranslate2) is MIT; the Systran model weights are
MIT — commercial use clean.

Config keys (under ``plugin.caption_provider.speaches`` in ``app_settings``):

- ``enabled`` (bool, default True) — kill switch. When False the provider
  returns ``CaptionResult(success=False, error=...)`` rather than silently
  producing empty results.
- ``base_url`` (str, default ``"http://speaches:8000/v1"``) — OpenAI-compatible
  base URL of the Speaches server. The same host the TTS service already uses.
- ``model`` (str, default ``"Systran/faster-whisper-medium"``) — ASR model id.
- ``timeout_seconds`` (float, default 180.0) — per-call HTTP timeout.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Literal

import httpx

from plugins.caption_provider import CaptionResult, CaptionSegment
from services.cost_guard import CostGuard

logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "http://speaches:8000/v1"
_DEFAULT_MODEL = "Systran/faster-whisper-medium"
_DEFAULT_TIMEOUT = 180.0


def _format_ts(seconds: float) -> str:
    """Render seconds as an SRT timestamp ``HH:MM:SS,mmm``."""
    if seconds < 0:
        seconds = 0.0
    ms_total = int(round(seconds * 1000))
    hours, rem = divmod(ms_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _segments_to_srt(segments: list[CaptionSegment]) -> str:
    """Build an SRT document from time-aligned segments.

    Returns ``""`` for an empty segment list so the caller treats it as
    "no caption track" (consistent with the CaptionResult contract).
    """
    if not segments:
        return ""
    blocks: list[str] = []
    for index, seg in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n"
            f"{_format_ts(seg.start_s)} --> {_format_ts(seg.end_s)}\n"
            f"{seg.text}\n"
        )
    return "\n".join(blocks)


def _parse_verbose_json(payload: dict[str, Any]) -> tuple[list[CaptionSegment], str]:
    """Parse an OpenAI ``verbose_json`` transcription into segments.

    faster-whisper / Speaches emit ``{"language", "segments": [{"start",
    "end", "text"}, ...]}`` with float seconds. Zero/negative-duration and
    empty-text segments are dropped per the Protocol contract.
    """
    language = str(payload.get("language") or "")
    segments: list[CaptionSegment] = []
    for entry in payload.get("segments") or []:
        start = entry.get("start")
        end = entry.get("end")
        if start is None or end is None:
            continue
        start_s = float(start)
        end_s = float(end)
        if end_s <= start_s:
            continue
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        segments.append(CaptionSegment(start_s=start_s, end_s=end_s, text=text))
    return segments, language


class SpeachesCaptionProvider:
    """Transcribe audio via the Speaches (faster-whisper) HTTP sidecar.

    Local provider — zero per-call dollar cost (``is_local=True`` on the
    cost-guard record); the Speaches container runs on the operator's own
    box. Produces SRT from the returned segments in one shot.
    """

    name = "speaches"
    # faster-whisper auto-detects across 99 languages — accept anything.
    supported_languages: tuple[str, ...] = ()
    supports_diarization = False

    def __init__(self, site_config: Any = None) -> None:
        self._site_config = site_config

    def _get(self, key: str, default: Any) -> Any:
        """Fetch a ``plugin.caption_provider.speaches.<key>`` value."""
        if self._site_config is None:
            return default
        return self._site_config.get(
            f"plugin.caption_provider.speaches.{key}",
            default,
        )

    def _build_cost_guard(self, kwargs: dict[str, Any]) -> CostGuard:
        """Resolve a CostGuard — tests inject ``_cost_guard``; prod seeds ``_pool``."""
        injected = kwargs.get("_cost_guard")
        if isinstance(injected, CostGuard):
            return injected
        site_config = kwargs.get("_site_config", self._site_config)
        pool = kwargs.get("_pool")
        if pool is None and site_config is not None:
            pool = getattr(site_config, "_pool", None)
        return CostGuard(site_config=site_config, pool=pool)

    async def transcribe(
        self,
        *,
        audio_path: str,
        language_hint: str = "",
        granularity: Literal["segment", "word"] = "segment",
        **kwargs: Any,
    ) -> CaptionResult:
        if not bool(self._get("enabled", True)):
            msg = "SpeachesCaptionProvider is disabled in app_settings"
            logger.warning(msg)
            return CaptionResult(success=False, error=msg)

        if not audio_path or not os.path.exists(audio_path):
            return CaptionResult(
                success=False,
                error=f"audio_path does not exist: {audio_path!r}",
            )

        base_url = str(self._get("base_url", _DEFAULT_BASE_URL) or _DEFAULT_BASE_URL).rstrip("/")
        model = str(self._get("model", _DEFAULT_MODEL) or _DEFAULT_MODEL)
        timeout = float(self._get("timeout_seconds", _DEFAULT_TIMEOUT) or _DEFAULT_TIMEOUT)
        url = f"{base_url}/audio/transcriptions"

        data: dict[str, str] = {"model": model, "response_format": "verbose_json"}
        if language_hint:
            data["language"] = language_hint
        if granularity == "word":
            # OpenAI-compatible word timestamps; Speaches honours the list form.
            data["timestamp_granularities[]"] = "word"

        logger.info(
            "[speaches_caption] transcribing %s via %s (model=%s)",
            os.path.basename(audio_path), url, model,
        )

        cost_guard = self._build_cost_guard(kwargs)
        started = time.perf_counter()
        success = True
        error: str | None = None
        segments: list[CaptionSegment] = []
        language = ""
        srt_text = ""

        try:
            with open(audio_path, "rb") as handle:
                files = {
                    "file": (os.path.basename(audio_path), handle, "application/octet-stream"),
                }
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, data=data, files=files)
            if resp.status_code != 200:
                success = False
                error = (
                    f"speaches transcription HTTP {resp.status_code}: "
                    f"{(resp.text or '')[:300]}"
                )
            else:
                payload = resp.json()
                segments, language = _parse_verbose_json(payload)
                srt_text = _segments_to_srt(segments)
                if not segments:
                    success = False
                    error = "speaches returned no usable segments"
        except Exception as exc:  # noqa: BLE001 — caller chooses how to handle
            success = False
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("[speaches_caption] transcription raised")

        duration_ms = int((time.perf_counter() - started) * 1000)

        # Route through cost-guard — local provider, electricity-only.
        try:
            await cost_guard.record_usage(
                provider=f"caption.{self.name}",
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                phase=str(kwargs.get("phase", "caption")),
                task_id=kwargs.get("task_id"),
                success=success,
                duration_ms=duration_ms,
                is_local=True,
            )
        except Exception as exc:  # noqa: BLE001 — cost accounting must not fail the job
            logger.warning("[speaches_caption] cost recording failed: %s", exc)

        return CaptionResult(
            success=success,
            segments=segments,
            language=language,
            srt_text=srt_text,
            vtt_text="",
            error=error,
            cost_usd=0.0,
            electricity_kwh=cost_guard.estimate_local_kwh(duration_ms=duration_ms),
            metadata={
                "model": model,
                "base_url": base_url,
                "duration_ms": duration_ms,
                "audio_basename": os.path.basename(audio_path),
            },
        )
