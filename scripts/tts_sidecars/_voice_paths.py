"""Contain request-supplied voice reference paths to the voices directories.

``audio_prompt_path`` arrives in the request body and used to be handed to
``os.path.exists`` and then the model as-is, so any file the sidecar could read
was a valid "voice" (CodeQL py/path-injection #228). Allowed roots: the
directory of ``CHATTERBOX_PROMPT_WAV`` (the pinned default voice) and every
entry of ``CHATTERBOX_VOICE_DIRS`` (colon-separated, default ``/app/voices``,
which is where the worker's ``plugin.tts_provider.chatterbox.audio_prompt_path``
points on the operator stack). Stdlib only so it can be unit-tested without
the model stack.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_VOICE_DIRS = "/app/voices"


def voice_roots(
    *, default_prompt: str | None = None, env: dict[str, str] | None = None
) -> list[Path]:
    env = os.environ if env is None else env
    roots: list[Path] = []
    for raw in (env.get("CHATTERBOX_VOICE_DIRS") or DEFAULT_VOICE_DIRS).split(":"):
        raw = raw.strip()
        if raw:
            roots.append(Path(raw).resolve())
    if default_prompt:
        roots.append(Path(default_prompt).resolve().parent)
    return roots


def contained_voice_path(candidate: str, roots: list[Path]) -> Path | None:
    """The resolved path when ``candidate`` is a file under one of ``roots``, else None."""
    try:
        path = Path(candidate).resolve()
    except (OSError, RuntimeError):
        return None
    if not path.is_file():
        return None
    for root in roots:
        try:
            if path.is_relative_to(root):
                return path
        except ValueError:  # pragma: no cover - defensive on exotic paths
            continue
    return None
