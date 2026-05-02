"""Makes `python -m poindexter ...` route to the CLI."""

# CLI processes are short-lived and share the worker's log directory.
# RotatingFileHandler.doRollover() calls os.rename(), which fails on
# Windows with WinError 32 when the worker process holds the log file
# open. Override LOG_TO_FILE=false BEFORE any other imports so the
# canonical logger_config.py skips the file handler entirely for CLI
# invocations. CLI logs go to stderr only (which is what operators see
# anyway). Closes the noisy traceback Matt hit on `poindexter auth
# migrate-mcp-gladlabs` 2026-05-02.
import os as _os

_os.environ.setdefault("LOG_TO_FILE", "false")

from poindexter.cli import main

if __name__ == "__main__":
    main()
