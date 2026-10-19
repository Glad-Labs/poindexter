"""Set the CLI's log level before any CLI submodule loads.

Earned 2026-10-18 by the first recorded how-to (the quickstart-e2e footage):
on a plain ``pip install``, a new user's ``poindexter tasks list`` opened with
"Redis SDK not installed", a core sample "not shipped in this build" and
"LiteLLMProvider requires the 'litellm' package". None of it was about the
command. Two things happen while the CLI is still loading, before
``app.main`` can set anything:

* a command group's import chain initialises ``services/logger_config``,
  which reads ``LOG_LEVEL`` once and defaults to INFO when it is unset;
* ``app.py`` calls ``plugins.registry.get_modules()`` to mount module
  commands, which loads every core sample and logs each one this install
  does not carry.

So the package ``__init__`` calls :func:`apply` first. It acts only when this
process IS the CLI (the ``poindexter`` console script, or ``python -m
poindexter[.cli]``, whose package import runs while ``sys.argv[0]`` is still
``-m``), never when a test or another program imports the package. ``-v`` /
``--verbose`` before the subcommand keeps everything, as it always did.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import MutableMapping, Sequence

#: The logger whose plugin-discovery notes a CLI install should not print.
REGISTRY_LOGGER = "poindexter.plugins.registry"


def root_verbose(args: Sequence[str]) -> bool:
    """True when ``-v``/``--verbose`` is among the options before the subcommand."""
    for arg in args:
        if not arg.startswith("-"):
            return False
        if arg in ("-v", "--verbose"):
            return True
    return False


def is_cli_process(argv: Sequence[str]) -> bool:
    if not argv:
        return False
    return argv[0] == "-m" or os.path.basename(argv[0]) == "poindexter"


def apply(argv: Sequence[str] | None = None, environ: MutableMapping[str, str] | None = None) -> bool:
    """Quiet the CLI unless verbose. Returns True when it acted."""
    argv = sys.argv if argv is None else argv
    environ = os.environ if environ is None else environ
    if not is_cli_process(argv):
        return False
    verbose = root_verbose(argv[1:])
    environ.setdefault("LOG_LEVEL", "INFO" if verbose else "WARNING")
    if not verbose:
        logging.getLogger(REGISTRY_LOGGER).setLevel(logging.ERROR)
    return True


__all__ = ["REGISTRY_LOGGER", "apply", "is_cli_process", "root_verbose"]
