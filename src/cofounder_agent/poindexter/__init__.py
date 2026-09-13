"""poindexter -- the open-source AI content pipeline, as one importable package.

Everything the backend ships lives under this root: ``poindexter.services``,
``poindexter.plugins``, ``poindexter.modules``, ``poindexter.utils``,
``poindexter.routes``, ``poindexter.schemas``, ``poindexter.config``,
``poindexter.tasks``, ``poindexter.brain`` (the standalone watchdog daemon),
``poindexter.cli`` and ``poindexter.memory``. The flat spellings those packages
had before Glad-Labs/poindexter#1046 (``import services.x``) are gone: no alias,
no stub, one module object per name.

Filesystem note: the package sits at ``src/cofounder_agent/poindexter/`` because
``src/cofounder_agent`` is the worker image's build context and the process
working directory (``main.py``, ``tests/``, ``skills/`` live beside it). The
import namespace does not depend on that -- callers always write
``from poindexter.services.x import ...``.
"""

from __future__ import annotations as _annotations  # noqa: E402 -- keeps the docstring first

import importlib.metadata as _importlib_metadata  # noqa: E402
import pathlib as _pathlib  # noqa: E402


def package_version() -> str:
    """The version this tree ships as, for release tags and health payloads.

    Read from the nearest ``pyproject.toml`` above the package (``[project]``
    for the backend manifest, ``[tool.poetry]`` for the brain's), which is
    what release-please stamps. Falls back to the installed distribution
    metadata (a ``pip install poindexter`` consumer has no pyproject on disk),
    then to ``0.0.0+unknown`` -- never raises: a version string is telemetry
    seasoning, not a reason to fail boot. The images install with
    ``poetry install --no-root``, so the distribution lookup alone would miss.
    """
    try:
        import tomllib
    except ImportError:  # pragma: no cover - 3.11+ only
        tomllib = None  # type: ignore[assignment]
    here = _pathlib.Path(__file__).resolve().parent
    if tomllib is not None:
        for candidate in (here / "pyproject.toml", *(parent / "pyproject.toml" for parent in list(here.parents)[:3])):
            try:
                if not candidate.is_file():
                    continue
                data = tomllib.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):  # unreadable or malformed manifest: try the next candidate
                continue
            version = (data.get("project") or {}).get("version") or (
                (data.get("tool") or {}).get("poetry") or {}
            ).get("version")
            if version:
                return str(version)
    try:
        return _importlib_metadata.version("poindexter")
    except _importlib_metadata.PackageNotFoundError:
        return "0.0.0+unknown"
