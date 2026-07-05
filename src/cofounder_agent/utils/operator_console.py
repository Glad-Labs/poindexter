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
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.staticfiles import StaticFiles

from services.logger_config import get_logger

logger = get_logger(__name__)


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
        StaticFiles(directory=console_dir, html=True),
        name="console",
    )
    logger.info("[STARTUP] ✅ Operator console mounted at /console/")
    return True
