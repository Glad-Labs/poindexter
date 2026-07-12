"""UTF-8 stdout/stderr guarantee for the whole ``poindexter`` CLI.

Plain Windows consoles (and any process launched under a non-UTF-8 locale,
e.g. a container with ``LANG=C``) bind ``sys.stdout``/``sys.stderr`` to the
legacy codepage (``cp1252``/``cp437`` on Windows) rather than UTF-8. Click
writes success/failure glyphs (``✅ ❌ ⚠ ✋ →``) straight through the stream's
own ``.write()`` — including through colorama's Windows ANSI wrapper, which
ultimately delegates to the same underlying stream — with no encoding safety
net, so any character outside that codepage's repertoire raises
``UnicodeEncodeError`` and kills the process mid-command, even after the
underlying operation already succeeded.

``TextIOWrapper.reconfigure()`` mutates the stream object in place rather than
replacing it, so calling this before any output happens is sufficient even
though colorama wraps stdout lazily (on the first ``echo``) — the wrapper
ends up delegating to this same, already-reconfigured object.
"""

from __future__ import annotations

import sys


def ensure_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8 so emoji/symbol output never crashes.

    ``errors="replace"`` is deliberate: a still-running process with a
    mangled glyph beats a crashed one. Guarded by ``hasattr`` since some
    substituted streams (e.g. a bare ``io.StringIO``) have no ``reconfigure``.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


__all__ = ["ensure_utf8_streams"]
