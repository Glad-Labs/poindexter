"""Presence-based mount for the operator-console SPA (Pro-tier overlay).

The operator console is a static React single-page app living at
``src/cofounder_agent/console/``. It is a **Pro-tier overlay**: the
``scripts/sync-to-github.sh`` filter strips the ``console/`` directory from the
public ``poindexter`` mirror, so open-source installs never carry it.

FastAPI's ``StaticFiles`` raises ``RuntimeError`` the moment it is constructed
against a missing ``directory`` (``check_dir=True`` is the default). An
unconditional ``app.mount("/console", StaticFiles(directory=...))`` would
therefore crash the OSS backend at startup as soon as the console is stripped.
Guarding the mount behind an ``is_dir()`` check turns that hard dependency into
a soft, presence-based one: the console is served where it exists and simply
absent where it doesn't — the same convention the private business modules use
(they aren't shipped to OSS, and the substrate copes with their absence). See
``docs/architecture/2026-06-04-module-visibility-sync-design.md`` and
``feedback_no_operator_info_to_public_repo``.

This is the single call site for that mount; ``main.py`` invokes
:func:`mount_operator_console` after the API routers are registered so the
static handler can never shadow an ``/api/...`` path.

Caching
-------
The console has no build step and no content hashes in its filenames: its ~33
assets are plain ``<script>``/``<link>`` tags sharing one global lexical scope
and a ``window.PX``/``PXR`` contract. A browser that serves *some* of those
files from cache while fetching others fresh therefore runs a **mixed version**
of the app — partial rendering, strobing, runtime errors — until a hard refresh.

Left alone, that is exactly what happens. ``StaticFiles`` sets no
``Cache-Control`` of its own, so console assets fall through
``middleware/cache_control.py`` to its catch-all ``private, max-age=60``: a
60-second window per file in which the browser reuses the cached copy **without
revalidating**. Reload inside that window after a deploy and the freshly
revalidated ``index.html`` can pull cached copies of the scripts it names.

:class:`NoCacheStaticFiles` closes the window by stamping ``no-cache``, so the
mount invalidates atomically on deploy. See ``console/README.md`` §1 for the
operator-facing version of this story.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from services.logger_config import get_logger

logger = get_logger(__name__)

# "cache but revalidate before every use" — NOT "don't cache" (that's
# ``no-store``). The browser keeps each file and re-asks with the ETag
# ``StaticFiles`` already emits, so an unchanged asset costs a bodiless 304 and
# a changed one returns 200 with fresh bytes. Atomic by construction: there is
# no window in which a subset of the console can be served stale.
#
# Deliberately a constant and not an ``app_settings`` key. The DB-first config
# rule asks "could a customer tune this?" — here the answer is that they must
# not: any positive ``max-age`` re-opens the mixed-version window this exists to
# close, so a knob would only be a way to reintroduce the bug. The mount also
# runs at import time, before ``SiteConfig`` is loaded in the lifespan.
CONSOLE_CACHE_CONTROL = "no-cache"


class NoCacheStaticFiles(StaticFiles):
    """``StaticFiles`` that forces revalidation of every file it serves.

    Stamps :data:`CONSOLE_CACHE_CONTROL` on the response after the parent has
    built it, which covers all three shapes ``StaticFiles`` can return: the
    ``200`` file body, the ``304`` produced when the request's ``If-None-Match``
    matches (Starlette keeps ``cache-control`` when it filters headers onto a
    not-modified response), and the ``html=True`` directory redirect.

    Setting the header here rather than special-casing ``/console`` inside
    ``middleware/cache_control.py`` is what keeps the two in agreement: that
    middleware explicitly defers to any response that already carries a
    ``Cache-Control``, so policy stays next to the mount it describes.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = CONSOLE_CACHE_CONTROL
        return response


def _default_console_dir() -> Path:
    """Return the in-tree console directory (``src/cofounder_agent/console``).

    Resolved relative to this file (``utils/operator_console.py``) rather than
    the process CWD, so it points at the package root's ``console/`` no matter
    where the worker is launched from. Mirrors the pre-extraction main.py path
    ``Path(__file__).parent / "console"``.
    """
    return Path(__file__).resolve().parent.parent / "console"


def mount_operator_console(app: Any, *, console_dir: Path | None = None) -> bool:
    """Mount the operator-console SPA at ``/console/`` when it is present.

    Serves the static console (``html=True`` so a bare ``/console/`` request
    returns ``index.html``) only if ``console_dir`` exists. On the public OSS
    mirror the directory is stripped, so the mount is skipped and the backend
    boots without a ``/console`` route instead of raising ``RuntimeError``.

    Uses :class:`NoCacheStaticFiles` so a deploy invalidates the console's
    unhashed assets atomically — see the module docstring's Caching section.

    Args:
        app: the FastAPI application to mount onto.
        console_dir: the console directory to serve; defaults to the in-tree
            ``src/cofounder_agent/console`` via :func:`_default_console_dir`.

    Returns:
        ``True`` if the console was mounted, ``False`` if it was skipped because
        the directory is absent (the Pro-tier-overlay / OSS-mirror case).
    """
    if console_dir is None:
        console_dir = _default_console_dir()

    if not console_dir.is_dir():
        logger.info(
            "[STARTUP] Operator console not present (Pro-tier overlay) — "
            "/console/ not mounted"
        )
        return False

    app.mount(
        "/console",
        NoCacheStaticFiles(directory=console_dir, html=True),
        name="console",
    )
    logger.info("[STARTUP] ✅ Operator console mounted at /console/")
    return True
