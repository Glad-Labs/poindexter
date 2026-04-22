#!/usr/bin/env python3
"""Fail-fast lint for new `os.getenv` / `os.environ` reads in services/.

Enforces Phase 3 of GH#93 (DB-first config): no new env-var reads should
land in `src/cofounder_agent/services/` outside a small allowlist of
bootstrap / config / subprocess-propagation modules. Everything else
should go through `services.site_config.site_config.get()`.

Run as:
    python scripts/check-no-os-getenv-in-services.py

Designed to be wired into `.pre-commit-config.yaml` as a local hook. Exits
0 when every hit is allowlisted; non-zero and a diff-style report when an
unallowlisted hit is found. Tracks file + line so the fix is one grep away.

To allow a new file, add its path (relative to the repo root) to
`ALLOWED_FILES` below with a one-line justification. Adding new entries
should require a PR comment from a reviewer — the whole point of the
check is to notice these additions, not to auto-suppress.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

SERVICES_ROOT = Path(__file__).resolve().parent.parent / "src" / "cofounder_agent" / "services"

# Files where `os.getenv` / `os.environ` is justified. Keep the
# justification in the value — "because it runs before site_config
# exists" is the ONLY acceptable category, plus narrow subprocess-env
# propagation. Anything else needs a PR and a reason.
ALLOWED_FILES: dict[str, str] = {
    # --- bootstrap-time reads (run before site_config.load) ---
    "services/database_service.py": (
        "Bootstrap: resolves DATABASE_URL / LOCAL_DATABASE_URL / DEPLOYMENT_MODE "
        "before the pool exists, so site_config can't be consulted yet."
    ),
    "services/logger_config.py": (
        "Bootstrap: ENVIRONMENT / LOG_LEVEL / LOG_FORMAT / LOG_DIR / LOG_FILE_NAME / "
        "LOG_TO_FILE are read during module import, before anything else in the "
        "process can exist."
    ),
    "services/sync_service.py": (
        "Bootstrap: CLOUD_DATABASE_URL / LOCAL_DATABASE_URL module constants are "
        "defined at import time for the cloud-sync service — same bootstrap "
        "category as database_service."
    ),
    "services/site_config.py": (
        "Core config module itself: site_config's internal env-var fallback for "
        "keys not yet in the DB (last-resort lookup). By definition cannot use "
        "site_config to look itself up."
    ),
    "services/settings_service.py": (
        "Core settings module: internal env-var fallback for uninitialized "
        "settings, same category as site_config."
    ),
    "services/publish_service.py": (
        "_should_run_post_publish_hooks() reads LOCAL_DATABASE_URL as a "
        "legitimate 'am I running in the local coordinator container?' signal "
        "— not a config value, a mode flag."
    ),
    # --- subprocess env propagation (not config reads) ---
    "services/jobs/db_backup.py": (
        "os.environ.copy() to propagate PG env vars to a pg_dump subprocess. "
        "Not a config read; plumbing only."
    ),
    # --- OTel instrumentation env keys (writes, not reads) ---
    "services/telemetry.py": (
        "Writes to os.environ to configure the OpenTelemetry SDK in-process "
        "(OTEL_* keys read by the SDK's own bootstrap). Not a config read."
    ),
    # --- one-shot legacy migrations ---
    "services/migrations/0023_alter_settings_table.py": (
        "Standalone migration script run before the app boots — has to resolve "
        "DATABASE_URL itself."
    ),
}

# Patterns that aren't ACTUAL env reads:
#   - comments / docstrings mentioning os.getenv as prose
#   - string literals ("os.getenv is forbidden") in error messages
# AST gives us that for free — we only flag real Call nodes.


def _relpath(p: Path) -> str:
    try:
        return str(p.relative_to(SERVICES_ROOT.parent.parent.parent))
    except ValueError:
        return str(p)


def _find_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Return [(lineno, call_repr)] for os.getenv / os.environ reads."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # os.getenv(...)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "getenv"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            ):
                hits.append((node.lineno, "os.getenv"))
            # os.environ.get(...) / os.environ.copy() / os.environ[...] — the
            # Attribute + Subscript cases
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "os"
            ):
                hits.append((node.lineno, f"os.environ.{func.attr}"))
        if isinstance(node, ast.Subscript):
            v = node.value
            if (
                isinstance(v, ast.Attribute)
                and v.attr == "environ"
                and isinstance(v.value, ast.Name)
                and v.value.id == "os"
            ):
                hits.append((node.lineno, "os.environ[...]"))
        if isinstance(node, ast.Assign):
            # `os.environ["X"] = ...`
            for t in node.targets:
                if isinstance(t, ast.Subscript):
                    v = t.value
                    if (
                        isinstance(v, ast.Attribute)
                        and v.attr == "environ"
                        and isinstance(v.value, ast.Name)
                        and v.value.id == "os"
                    ):
                        hits.append((node.lineno, "os.environ[...] ="))
    return hits


def main() -> int:
    if not SERVICES_ROOT.is_dir():
        sys.stderr.write(
            f"lint: {SERVICES_ROOT} not found — run from the repo root.\n",
        )
        return 2

    violations: list[tuple[str, int, str]] = []
    files_scanned = 0
    allowed_hits = 0

    for py in SERVICES_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        files_scanned += 1
        rel = _relpath(py).replace("\\", "/")
        # Normalize to services/*.py (drop src/cofounder_agent/)
        norm = re.sub(r"^src/cofounder_agent/", "", rel)

        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue

        hits = _find_calls(tree)
        if not hits:
            continue

        if norm in ALLOWED_FILES:
            allowed_hits += len(hits)
            continue

        for lineno, call in hits:
            violations.append((norm, lineno, call))

    if violations:
        print(
            "FAIL: Found os.getenv / os.environ reads in services/ outside "
            f"the Phase-3 allowlist ({len(violations)} total):\n",
        )
        for path, line, call in violations:
            print(f"  {path}:{line}: {call}")
        print(
            "\nFix paths (most → least preferred):\n"
            "  1. Read via `services.site_config.site_config.get(key)` or "
            "`.get_int(key)` / `.get_float(key)` / `.get_secret(key)`.\n"
            "  2. If the value is only ever a bootstrap signal (not a tunable "
            "config), add the specific file to ALLOWED_FILES in "
            "scripts/check-no-os-getenv-in-services.py with a one-line "
            "justification.\n"
            "  3. If you're adding a truly new DB-configurable knob, "
            "include it in a migration under services/migrations/ so fresh "
            "installs get the default row.\n",
        )
        return 1

    print(
        f"OK: services/ os.getenv lint: {files_scanned} files scanned, "
        f"{allowed_hits} allowed hits in {len(ALLOWED_FILES)} allowlisted files, "
        "no violations.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
