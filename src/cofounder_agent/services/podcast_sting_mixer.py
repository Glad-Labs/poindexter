"""Podcast sting mixer — intro/outro music around the narration (ffmpeg).

Finishes the orphaned half of poindexter#690: ``generate_media_scripts``
has synthesized a musical intro sting (``podcast_intro_audio_path``, via
the stable-audio sidecar) since May, but nothing ever mixed it — episodes
shipped as dry TTS. ``podcast.render`` now calls :func:`mix_intro_outro`
when a sting exists.

Sound design (deliberately edges-only): the sting plays solo for
``podcast_sting_solo_seconds``, fades out over ``podcast_sting_fade_seconds``
as the voice enters, and returns after the last word — fading in, holding
``podcast_sting_outro_seconds`` total, fading out. No continuous bed under
narration: under a solo synthetic voice a wall-to-wall bed hurts
intelligibility and reads as filler; music belongs at the boundaries.

Every knob is an ``app_settings`` key (DB-first config). Fail-soft: any
mixing problem returns ``None`` and the caller ships the dry narration —
losing polish must never lose the episode.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_FFMPEG_TIMEOUT_DEFAULT = 120.0


@dataclass(frozen=True)
class StingMixConfig:
    """Resolved mix parameters (seconds / dB), clamped to sane ranges."""

    solo_s: float
    fade_s: float
    outro_s: float
    gain_db: float
    timeout_s: float

    @property
    def intro_len_s(self) -> float:
        return self.solo_s + self.fade_s


def _cfg_float(site_config: Any, key: str, default: float,
               lo: float, hi: float) -> float:
    raw = site_config.get(key, str(default)) if site_config is not None else default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        val = default
    return min(hi, max(lo, val))


def resolve_config(site_config: Any) -> StingMixConfig:
    return StingMixConfig(
        solo_s=_cfg_float(site_config, "podcast_sting_solo_seconds", 2.5, 0.5, 15.0),
        fade_s=_cfg_float(site_config, "podcast_sting_fade_seconds", 3.0, 0.5, 15.0),
        outro_s=_cfg_float(site_config, "podcast_sting_outro_seconds", 6.0, 1.0, 30.0),
        gain_db=_cfg_float(site_config, "podcast_sting_gain_db", -7.0, -30.0, 6.0),
        timeout_s=_cfg_float(
            site_config, "podcast_sting_mix_timeout_seconds",
            _FFMPEG_TIMEOUT_DEFAULT, 10.0, 600.0,
        ),
    )


@dataclass(frozen=True)
class StingResolution:
    """Which sting file to mix, and why there isn't one."""

    path: str        # "" when nothing usable was found
    source: str      # "state" | "curated" | ""
    expected: bool   # a sting WAS carried/configured — a dry cut is a downgrade
    detail: str      # operator-readable reason when ``path`` is empty


def resolve_sting_path(state_path: Any, site_config: Any = None) -> StingResolution:
    """Resolve the sting to mix, falling back to the curated show theme.

    The per-episode path is a SNAPSHOT: ``generate_media_scripts`` writes it
    into ``task_metadata`` at Stage-1 and ``podcast.load_script`` replays it at
    Stage-3, days later. That snapshot goes stale two ways — a task scripted
    before the operator pinned ``podcast_sting_file_path`` carries an empty
    path forever, and a generated sting points at a ``/tmp`` file that no longer
    exists in the rendering process. Both shipped dry episodes while a perfectly
    good curated theme sat on disk, so the curated file is resolved HERE, at
    render time, where it is a durable show-wide constant rather than a
    per-episode guess.
    """
    candidate = str(state_path or "").strip()
    if candidate and os.path.exists(candidate):
        return StingResolution(candidate, "state", True, "")

    curated = ""
    if site_config is not None:
        curated = str(site_config.get("podcast_sting_file_path", "") or "").strip()
    if curated and os.path.exists(curated):
        return StingResolution(curated, "curated", True, "")

    if candidate:
        return StingResolution(
            "", "", True,
            f"per-episode sting '{candidate}' no longer exists and "
            + (
                f"the curated podcast_sting_file_path '{curated}' does not either"
                if curated else "podcast_sting_file_path is unset"
            ),
        )
    if curated:
        return StingResolution(
            "", "", True,
            f"podcast_sting_file_path '{curated}' does not exist "
            "(is it readable inside the worker container?)",
        )
    # No theme configured and none generated — the OSS default. Not a downgrade.
    return StingResolution("", "", False, "no sting configured for this show")


def build_mix_filtergraph(*, narration_s: float, cfg: StingMixConfig) -> str:
    """The ffmpeg ``filter_complex`` for intro + outro around the voice.

    Inputs: ``[0]`` narration, ``[1]`` sting. The voice is delayed by the
    sting's solo window; the outro reuses the same sting file, fading in
    just before the final word lands (1s overlap) and out at the tail.
    ``amix normalize=0`` keeps the voice at its original level — only the
    sting is gained down; an ``alimiter`` catches overlap peaks.
    """
    solo_ms = int(cfg.solo_s * 1000)
    overlap_s = min(1.0, narration_s)
    outro_at_ms = int((cfg.solo_s + max(0.0, narration_s - overlap_s)) * 1000)
    outro_fade_in = min(2.0, cfg.outro_s / 2)
    outro_fade_out = min(2.0, cfg.outro_s / 2)
    fmt = "aformat=sample_rates=44100:channel_layouts=stereo"
    return (
        f"[1:a]{fmt},atrim=0:{cfg.intro_len_s:.3f},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={cfg.solo_s:.3f}:d={cfg.fade_s:.3f},"
        f"volume={cfg.gain_db:.1f}dB[intro];"
        f"[0:a]{fmt},adelay={solo_ms}|{solo_ms}[voice];"
        f"[1:a]{fmt},atrim=0:{cfg.outro_s:.3f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={outro_fade_in:.3f},"
        f"afade=t=out:st={cfg.outro_s - outro_fade_out:.3f}:d={outro_fade_out:.3f},"
        f"volume={cfg.gain_db:.1f}dB,adelay={outro_at_ms}|{outro_at_ms}[outro];"
        f"[voice][intro][outro]amix=inputs=3:duration=longest:normalize=0,"
        f"alimiter=limit=0.97[mix]"
    )


async def _probe_duration_s(path: str) -> float | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        raw = (out or b"").decode().strip()
        return float(raw) if raw else None
    except Exception as exc:  # noqa: BLE001 — probe failure → caller skips the mix
        logger.warning("[sting_mixer] duration probe failed for %s: %s", path, exc)
        return None


async def mix_intro_outro(
    narration_path: str,
    sting_path: str,
    *,
    site_config: Any = None,
    task_id: str | None = None,
) -> str | None:
    """Mix the sting around the narration; return the mixed MP3 path.

    ``None`` on ANY failure — the caller ships the dry narration instead.
    The output is a fresh temp file; the original narration is untouched.
    """
    if not (narration_path and os.path.exists(narration_path)):
        return None
    if not (sting_path and os.path.exists(sting_path)):
        return None

    cfg = resolve_config(site_config)
    narration_s = await _probe_duration_s(narration_path)
    if not narration_s or narration_s <= 0:
        return None

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        out_path = tmp.name

    graph = build_mix_filtergraph(narration_s=narration_s, cfg=cfg)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", narration_path, "-i", sting_path,
        "-filter_complex", graph, "-map", "[mix]",
        "-c:a", "libmp3lame", "-q:a", "4", out_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=cfg.timeout_s)
        if proc.returncode != 0:
            logger.warning(
                "[sting_mixer] ffmpeg failed for task %s (rc=%s): %s",
                task_id, proc.returncode,
                (err or b"").decode(errors="replace")[-400:],
            )
            os.unlink(out_path)
            return None
        if os.path.getsize(out_path) < 1024:
            logger.warning(
                "[sting_mixer] suspiciously small mix output for task %s — "
                "keeping the dry narration", task_id,
            )
            os.unlink(out_path)
            return None
        logger.info(
            "[sting_mixer] mixed intro/outro sting for task %s "
            "(narration=%.1fs solo=%.1fs fade=%.1fs outro=%.1fs gain=%.1fdB)",
            task_id, narration_s, cfg.solo_s, cfg.fade_s, cfg.outro_s,
            cfg.gain_db,
        )
        return out_path
    except Exception as exc:  # noqa: BLE001 — mixing is polish, never load-bearing
        logger.warning("[sting_mixer] mix failed for task %s: %s", task_id, exc)
        try:
            os.unlink(out_path)
        except OSError:  # silent-ok: best-effort temp cleanup — the mix
            # already failed and is being reported; a stale temp file is
            # tmpdir-retention noise, never state.
            pass
        return None


__all__ = [
    "StingMixConfig",
    "StingResolution",
    "build_mix_filtergraph",
    "mix_intro_outro",
    "resolve_config",
    "resolve_sting_path",
]
