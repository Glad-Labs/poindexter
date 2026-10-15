"""Per-render shot clip cache: redo one shot without re-rendering the rest.

Chat drafting, phase 4. A screenplay render animates every shot, and a
talking shot costs about six minutes of GPU, so "redo shot 4 with the hands
lower" must not re-render the other twenty. ``render_shot_list`` saves each
shot's final clip here with a key over everything that decides its render
(source, prompts, motion, speaker, timing). A later render of the same piece
given the same cache dir reuses every clip whose key still matches and
renders only the shots that changed; the compositor joins them as usual.

A kept clip is final: it is not rescored or re-rolled (``_ShotState.rung``
``"kept"``), because the operator already watched it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MANIFEST = "clips.json"
_KEY_FIELDS = (
    "source", "prompt", "frame_prompt", "motion", "speaker", "characters",
    "after_prompt", "after_url", "transition",
    "duration_s", "narration_offset_s", "query", "demo_id", "kenburns_zoom",
)


def shot_key(shot: Any) -> str:
    """A key over everything that decides how a shot renders."""
    data = {f: getattr(shot, f, None) for f in _KEY_FIELDS}
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _manifest(cache_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((cache_dir / MANIFEST).read_text())
    except (OSError, ValueError):
        return {}


def reusable(cache_dir: str | None, shots: list[Any], work_dir: Path) -> dict[int, tuple[str, float]]:
    """Shots whose cached clip still matches: ``idx -> (local copy, duration_s)``.

    Copies each into ``work_dir`` so the render never edits the cache.
    """
    if not cache_dir:
        return {}
    root = Path(cache_dir)
    entries = _manifest(root)
    out: dict[int, tuple[str, float]] = {}
    for shot in shots:
        entry = entries.get(str(shot.idx))
        if not entry or entry.get("key") != shot_key(shot):
            continue
        src = root / str(entry.get("file") or "")
        if not src.is_file():
            continue
        dst = work_dir / f"kept_{shot.idx:02d}{src.suffix}"
        shutil.copyfile(src, dst)
        out[shot.idx] = (str(dst), float(entry.get("duration_s") or shot.duration_s))
    if out:
        logger.info("[clip_cache] reusing %d of %d shot clips from %s", len(out), len(shots), root)
    return out


def save(cache_dir: str | None, pairs: list[tuple[Any, Any, str]]) -> int:
    """Store final clips: ``pairs`` of ``(shot, result, rung)``. Returns how many.

    Only real renders are kept (``primary`` or an earlier ``kept``): a
    substitute or a branded card is a fallback a revision should retry.
    """
    if not cache_dir:
        return 0
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    entries = _manifest(root)
    saved = 0
    for shot, result, rung in pairs:
        clip = getattr(result, "clip_path", None)
        if rung not in ("primary", "kept") or not getattr(result, "success", False) or not clip:
            continue
        src = Path(clip)
        if not src.is_file():
            continue
        name = f"shot_{shot.idx:02d}{src.suffix}"
        if src.resolve() != (root / name).resolve():
            shutil.copyfile(src, root / name)
        entries[str(shot.idx)] = {
            "key": shot_key(shot), "file": name, "source": shot.source,
            "duration_s": float(getattr(result, "duration_s", 0.0) or shot.duration_s),
        }
        saved += 1
    (root / MANIFEST).write_text(json.dumps(entries, indent=1))
    return saved


INPUTS = {"audio": "dialogue.wav", "captions": "captions.srt", "score": "score.wav", "shot_list": "shot_list.json",
          "timeline": "timeline.json", "screenplay": "screenplay.json"}


def save_inputs(
    cache_dir: str, *, audio_path: str | None, caption_path: str | None,
    shot_list: dict[str, Any], timeline: list[Any] | None, screenplay: dict[str, Any] | None,
    score_path: str | None = None,
) -> None:
    """Keep what a revision re-renders against: the track, captions, plan and script."""
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    for src, name in (
        (audio_path, INPUTS["audio"]), (caption_path, INPUTS["captions"]), (score_path, INPUTS["score"]),
    ):
        if src and Path(src).is_file() and Path(src).resolve() != (root / name).resolve():
            shutil.copyfile(src, root / name)
    (root / INPUTS["shot_list"]).write_text(json.dumps(shot_list, default=str))
    (root / INPUTS["timeline"]).write_text(json.dumps(timeline or [], default=str))
    (root / INPUTS["screenplay"]).write_text(json.dumps(screenplay or {}, default=str))


def load_inputs(cache_dir: str) -> dict[str, Any]:
    """What ``save_inputs`` kept; raises FileNotFoundError when there is no cache."""
    root = Path(cache_dir)
    if not (root / INPUTS["shot_list"]).is_file():
        raise FileNotFoundError(f"no kept render in {root}")
    return {
        "audio_path": str(root / INPUTS["audio"]) if (root / INPUTS["audio"]).is_file() else None,
        "caption_path": str(root / INPUTS["captions"]) if (root / INPUTS["captions"]).is_file() else None,
        "score_path": str(root / INPUTS["score"]) if (root / INPUTS["score"]).is_file() else None,
        "shot_list": json.loads((root / INPUTS["shot_list"]).read_text()),
        "timeline": json.loads((root / INPUTS["timeline"]).read_text() or "[]"),
        "screenplay": json.loads((root / INPUTS["screenplay"]).read_text() or "{}"),
    }


def prune(root: str, keep_days: float) -> int:
    """Delete per-task caches untouched for ``keep_days``. Returns how many."""
    base = Path(root)
    if keep_days <= 0 or not base.is_dir():
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for child in base.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


__all__ = ["INPUTS", "MANIFEST", "load_inputs", "prune", "reusable", "save", "save_inputs", "shot_key"]
