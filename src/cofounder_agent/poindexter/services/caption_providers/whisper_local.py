"""WhisperLocalCaptionProvider — local whisper.cpp transcription.

Wraps the ``whisper-cli`` (or legacy ``main``) binary from
`ggerganov/whisper.cpp <https://github.com/ggerganov/whisper.cpp>`_.
Outputs structured segments via ``-oj`` (JSON) and emits sidecar
``.srt`` / ``.vtt`` files that the video pipeline's ``stitch`` Stage
hands to YouTube auto-CC and to ffmpeg's ``-filter_complex subtitles``.

License is MIT for both the binary and the GGML-format model weights
that the project ships — commercial use clean. Runs CPU or
CUDA / Metal GPU; the 5090 build sits in CUDA mode by default.

Install (handled by the operator on the host):

.. code:: bash

    git clone https://github.com/ggerganov/whisper.cpp
    cd whisper.cpp
    cmake -B build -DGGML_CUDA=1
    cmake --build build --config Release
    bash ./models/download-ggml-model.sh base.en

Then point the provider at the resulting binary + model file via
``plugin.caption_provider.whisper_local.*`` settings.

Config keys (all under ``plugin.caption_provider.whisper_local`` in
``app_settings``):

- ``enabled`` (bool, default True) — kill switch. When False the
  provider returns ``CaptionResult(success=False, error=...)`` rather
  than silently producing empty results.
- ``binary_path`` (str, default ``"whisper-cli"``) — path or PATH-name
  of the whisper.cpp binary. Older builds still call it ``main``;
  set this accordingly.
- ``model_path`` (str, required) — absolute path to a ``ggml-*.bin``
  model file. No default — the operator must download a model and
  point us at it.
- ``model_name`` (str, default ``"base.en"``) — purely metadata, used
  in the cost-log row and the result's ``metadata`` dict.
- ``threads`` (int, default 8) — passed to ``-t``. Whisper.cpp scales
  near-linearly to ~8 threads then plateaus.
- ``use_gpu`` (bool, default True) — when False, passes ``-ng`` to
  force CPU even on a CUDA-enabled build.
- ``beam_size`` (int, default 5) — passed to ``--beam-size``. Higher
  is more accurate, slower.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess  # noqa: S404 — whisper.cpp is a local binary, args are validated
import tempfile
import time
from typing import Any, Literal

from plugins.caption_provider import CaptionResult, CaptionSegment
from services.cost_guard import CostGuard

logger = logging.getLogger(__name__)


_DEFAULT_BINARY = "whisper-cli"
_DEFAULT_MODEL_NAME = "base.en"
_DEFAULT_THREADS = 8
_DEFAULT_BEAM = 5


def _resolve_binary(configured: str) -> str | None:
    """Return an absolute path to the whisper.cpp binary, or ``None``.

    Accepts an absolute path verbatim. For PATH-name lookups, falls
    back through the historical names (``whisper-cli`` was renamed
    from ``main`` in late 2024).
    """
    if configured and os.path.isabs(configured):
        return configured if os.path.exists(configured) else None
    for candidate in (configured, "whisper-cli", "main"):
        if not candidate:
            continue
        located = shutil.which(candidate)
        if located:
            return located
    return None


def _build_command(
    *,
    binary: str,
    model_path: str,
    audio_path: str,
    output_dir: str,
    output_stem: str,
    threads: int,
    beam_size: int,
    use_gpu: bool,
    language: str,
    granularity: Literal["segment", "word"],
) -> list[str]:
    """Construct the whisper.cpp invocation argv list.

    Split out so tests can assert on the exact flags without spawning a
    subprocess. Uses long-form flags everywhere for readability.
    """
    cmd = [
        binary,
        "--model", model_path,
        "--file", audio_path,
        "--output-json",
        "--output-srt",
        "--output-vtt",
        "--output-file", os.path.join(output_dir, output_stem),
        "--threads", str(threads),
        "--beam-size", str(beam_size),
        "--print-progress",  # whisper.cpp prints to stderr; we ignore stderr
    ]
    if not use_gpu:
        cmd.append("--no-gpu")
    if language:
        cmd.extend(["--language", language])
    if granularity == "word":
        # whisper.cpp produces one segment per word with --max-len 1
        # combined with --split-on-word.
        cmd.extend(["--max-len", "1", "--split-on-word"])
    return cmd


def _parse_segments(json_path: str) -> tuple[list[CaptionSegment], str]:
    """Parse whisper.cpp's ``-oj`` JSON into our segment dataclasses.

    Returns ``(segments, language)``. Whisper.cpp emits timestamps as
    millisecond integers; we convert to fractional seconds.
    Zero-duration segments are dropped per the Protocol contract.
    """
    with open(json_path, encoding="utf-8") as f:
        payload = json.load(f)

    raw_language = ""
    result_meta = payload.get("result")
    if isinstance(result_meta, dict):
        raw_language = str(result_meta.get("language") or "")

    segments: list[CaptionSegment] = []
    for entry in payload.get("transcription", []) or []:
        offsets = entry.get("offsets") or {}
        start_ms = offsets.get("from")
        end_ms = offsets.get("to")
        if start_ms is None or end_ms is None or end_ms <= start_ms:
            continue
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            CaptionSegment(
                start_s=float(start_ms) / 1000.0,
                end_s=float(end_ms) / 1000.0,
                text=text,
                speaker=None,
                confidence=None,
            ),
        )
    return segments, raw_language


def _read_text_if_exists(path: str) -> str:
    """Read a file path or return ``""`` if it doesn't exist.

    Whisper.cpp writes ``<stem>.srt`` and ``<stem>.vtt`` next to the
    JSON; the caller depends on this helper to handle build
    configurations that disable one of the output formats.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _run_whisper_blocking(cmd: list[str]) -> tuple[int, str, str]:
    """Execute the whisper.cpp subprocess synchronously.

    Returns ``(returncode, stdout, stderr)``. Split out so the async
    ``transcribe`` method can wrap this in :func:`asyncio.to_thread`
    and so tests can monkeypatch the subprocess call without spawning
    real processes.
    """
    proc = subprocess.run(  # noqa: S603 — argv list, no shell
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


class WhisperLocalCaptionProvider:
    """Transcribe audio via a local whisper.cpp install.

    Designed as the default first-line CaptionProvider for the video
    pipeline — zero per-call dollar cost, runs entirely on the host's
    GPU, and produces SRT/VTT sidecars in one shot.
    """

    name = "whisper_local"
    # Whisper trained on 99 languages — accept anything and let the
    # model figure it out unless the caller pinned a hint. Empty tuple
    # signals "auto-detect, we accept anything" per the Protocol.
    supported_languages: tuple[str, ...] = ()
    supports_diarization = False

    def __init__(self, site_config: Any = None) -> None:
        self._site_config = site_config

    def _get(self, key: str, default: Any) -> Any:
        """Fetch a ``plugin.caption_provider.whisper_local.<key>`` value.

        Falls back to ``default`` when no SiteConfig is available
        (e.g. during unit tests that construct the provider standalone).
        """
        if self._site_config is None:
            return default
        return self._site_config.get(
            f"plugin.caption_provider.whisper_local.{key}",
            default,
        )

    def _build_cost_guard(self, kwargs: dict[str, Any]) -> CostGuard:
        """Resolve a CostGuard instance for this call.

        Same shape as the LLM providers — tests inject ``_cost_guard``
        directly; production seeds ``_pool`` on site_config.
        """
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
        # Discipline #2: bail loudly when disabled rather than silently
        # returning empty segments.
        if not bool(self._get("enabled", True)):
            msg = "WhisperLocalCaptionProvider is disabled in app_settings"
            logger.warning(msg)
            return CaptionResult(success=False, error=msg)

        if not audio_path or not os.path.exists(audio_path):
            return CaptionResult(
                success=False,
                error=f"audio_path does not exist: {audio_path!r}",
            )

        configured_binary = str(self._get("binary_path", _DEFAULT_BINARY) or _DEFAULT_BINARY)
        binary = _resolve_binary(configured_binary)
        if binary is None:
            return CaptionResult(
                success=False,
                error=(
                    f"whisper.cpp binary not found (configured={configured_binary!r}). "
                    "Install whisper.cpp and set "
                    "plugin.caption_provider.whisper_local.binary_path."
                ),
            )

        model_path = str(self._get("model_path", "") or "")
        if not model_path or not os.path.exists(model_path):
            return CaptionResult(
                success=False,
                error=(
                    f"whisper.cpp model not found (configured={model_path!r}). "
                    "Download a ggml model and set "
                    "plugin.caption_provider.whisper_local.model_path."
                ),
            )

        threads = int(self._get("threads", _DEFAULT_THREADS) or _DEFAULT_THREADS)
        beam_size = int(self._get("beam_size", _DEFAULT_BEAM) or _DEFAULT_BEAM)
        use_gpu = bool(self._get("use_gpu", True))
        model_name = str(self._get("model_name", _DEFAULT_MODEL_NAME) or _DEFAULT_MODEL_NAME)

        # whisper.cpp writes `<stem>.json`, `<stem>.srt`, `<stem>.vtt`
        # next to wherever we point ``--output-file``. Use a tempdir so
        # the caller's filesystem isn't littered with intermediate JSON.
        with tempfile.TemporaryDirectory(prefix="poindexter_whisper_") as tmpdir:
            stem = "transcript"
            cmd = _build_command(
                binary=binary,
                model_path=model_path,
                audio_path=audio_path,
                output_dir=tmpdir,
                output_stem=stem,
                threads=threads,
                beam_size=beam_size,
                use_gpu=use_gpu,
                language=language_hint,
                granularity=granularity,
            )

            logger.info(
                "[whisper_local] transcribing %s (model=%s, threads=%d, gpu=%s)",
                os.path.basename(audio_path), model_name, threads, use_gpu,
            )

            cost_guard = self._build_cost_guard(kwargs)
            started = time.perf_counter()
            success = True
            error: str | None = None
            segments: list[CaptionSegment] = []
            language = ""
            srt_text = ""
            vtt_text = ""

            try:
                returncode, _, stderr = await asyncio.to_thread(
                    _run_whisper_blocking, cmd,
                )
                if returncode != 0:
                    success = False
                    # Truncate stderr — whisper.cpp logs progress lines
                    # that aren't useful in an error message.
                    error = (
                        f"whisper.cpp exited {returncode}: "
                        f"{stderr.strip()[-500:] or '(no stderr)'}"
                    )
                else:
                    json_path = os.path.join(tmpdir, f"{stem}.json")
                    if not os.path.exists(json_path):
                        success = False
                        error = (
                            "whisper.cpp finished cleanly but produced "
                            "no JSON output — check --output-file path."
                        )
                    else:
                        segments, language = _parse_segments(json_path)
                        srt_text = _read_text_if_exists(
                            os.path.join(tmpdir, f"{stem}.srt"),
                        )
                        vtt_text = _read_text_if_exists(
                            os.path.join(tmpdir, f"{stem}.vtt"),
                        )
                        if not segments:
                            success = False
                            error = "whisper.cpp produced no usable segments"
            except Exception as exc:
                success = False
                error = f"{type(exc).__name__}: {exc}"
                logger.exception("[whisper_local] subprocess wrap raised")

            duration_ms = int((time.perf_counter() - started) * 1000)

            # Discipline #3: route through cost-guard. Local provider →
            # is_local=True, electricity-only. Token args stay 0; the
            # guard derives kWh from duration.
            try:
                await cost_guard.record_usage(
                    provider=f"caption.{self.name}",
                    model=model_name,
                    prompt_tokens=0,
                    completion_tokens=0,
                    phase=str(kwargs.get("phase", "caption")),
                    task_id=kwargs.get("task_id"),
                    success=success,
                    duration_ms=duration_ms,
                    is_local=True,
                )
            except Exception as exc:
                logger.warning(
                    "[whisper_local] cost recording failed: %s", exc,
                )

            return CaptionResult(
                success=success,
                segments=segments,
                language=language,
                srt_text=srt_text,
                vtt_text=vtt_text,
                error=error,
                cost_usd=0.0,
                electricity_kwh=cost_guard.estimate_local_kwh(
                    duration_ms=duration_ms,
                ),
                metadata={
                    "binary": binary,
                    "model_name": model_name,
                    "threads": threads,
                    "beam_size": beam_size,
                    "use_gpu": use_gpu,
                    "duration_ms": duration_ms,
                    "audio_basename": os.path.basename(audio_path),
                },
            )
