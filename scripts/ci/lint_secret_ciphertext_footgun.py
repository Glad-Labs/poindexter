#!/usr/bin/env python3
"""CI lint: no ``site_config.get(<secret key>)`` — that returns ciphertext.

Security audit finding (#2131). ``SiteConfig`` filters ``is_secret=true``
rows out of its sync in-memory cache, so ``site_config.get(key)`` on a
secret key returns the ``enc:v1:...`` ciphertext blob instead of raising —
the caller has to know to use ``await site_config.get_secret(key)`` instead.
Three separate 2026-04-23 incidents (Alertmanager dispatch, Vercel ISR,
auto-Telegram post-publish) hit this exact bug, tracked in GH-107 and fixed
ad hoc each time — the tell that the durable fix is a ratchet, not another
manual sweep. ``services/integrations/secret_resolver.py`` centralizes the
correct path for integration-row secrets; this lint stops a new call site
from reaching around it (or around ``get_secret`` directly) for any
secret-shaped key.

## What is flagged

A flag fires on ``<name>.get("<key>", ...)`` where:

  - ``<name>`` is (or ends in) ``site_config`` — the DI convention this
    codebase uses everywhere (route handlers take ``site_config:
    SiteConfig``, services store ``self._site_config`` / ``self.site_config``
    — see CLAUDE.md's SiteConfig-DI section). Any other ``.get(...)`` call
    (dict, os.environ, a non-SiteConfig object) is not this bug and is not
    flagged.
  - the key argument is a string literal matching ``SECRET_KEY_PATTERN``
    (mirrored from ``services/logger_config.py`` — keep the two in sync).

``.get_bool(...)`` / ``.get_float(...)`` / ``.get_int(...)`` / ``.get_secret(...)``
are different method names and never match — only the plain sync ``.get``
returns raw cache contents (ciphertext included).

## Ratchet posture

The tree is clean today, so this is a FAIL-ON-ANY guard (no baseline file
to drift). Escape hatch for a deliberate, reviewed case (e.g. a key that
matches the name pattern but is genuinely never marked ``is_secret=true`` —
prefer renaming the key instead, but the door is here): add
``# secret-get-ok: <reason>`` on the offending line. (Deliberately NOT
``# noqa: secret-get-ok`` — ruff's own noqa parser treats anything after
``# noqa:`` as a rule-code list and warns on an unrecognized one.)

Run:
    python scripts/ci/lint_secret_ciphertext_footgun.py   # exit 0 clean, 1 found
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_scanned  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# Production trees where a SiteConfig instance is reachable. Tests construct
# their own SiteConfig fixtures and assert on behavior — never a real leak.
SCAN_ROOTS: list[tuple[Path, tuple[str, ...]]] = [
    (REPO_ROOT / "src" / "cofounder_agent", ("tests",)),
]

# The resolver itself (and its own tests) legitimately names secret-shaped
# keys while implementing the correct get_secret path — never flag it.
ALLOWED_FILES = {"services/integrations/secret_resolver.py"}

OVERRIDE_MARKER = "secret-get-ok"

# Mirrors services/logger_config.py::SECRET_KEY_PATTERN — keep in sync.
SECRET_KEY_PATTERN = re.compile(
    r"(?i)("
    r"token|secret|password|api_key|api-key|authorization|cookie|"
    r"bearer|dsn|signing_key|signing-key|access_key|access-key|"
    r"client_secret|client-secret|webhook_url|webhook-url|"
    r"x[-_]revalidate[-_]secret|x[-_]api[-_]key|langfuse_secret|"
    r"discord_ops_webhook|indexnow_key"
    r")"
)


def _receiver_name(expr: ast.expr) -> str | None:
    """Return the dotted-tail identifier a ``.get(...)`` call hangs off of.

    ``site_config.get(...)`` -> "site_config"; ``self._site_config.get(...)``
    -> "_site_config"; ``self.site_config.get(...)`` -> "site_config".
    Anything else (a bare Name that isn't site_config-shaped, a subscript,
    a call result, ...) returns None.
    """
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


def _is_site_config_get(call: ast.Call) -> bool:
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "get"):
        return False
    name = _receiver_name(func.value)
    return name is not None and name in ("site_config", "_site_config")


def _secret_key_literal(call: ast.Call) -> str | None:
    """Return the string key argument if it looks like a secret name."""
    if not call.args:
        return None
    first = call.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    key = first.value
    return key if SECRET_KEY_PATTERN.search(key) else None


def _has_override(call: ast.Call, lines: list[str]) -> bool:
    start = call.lineno
    end = call.end_lineno or start
    for ln in range(start, end + 1):
        if 0 <= ln - 1 < len(lines) and OVERRIDE_MARKER in lines[ln - 1]:
            return True
    return False


def scan_source(source: str) -> list[tuple[int, str]]:
    """[(lineno, key)] for every ciphertext-footgun call in one source string."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_site_config_get(node)):
            continue
        key = _secret_key_literal(node)
        if key is None:
            continue
        if _has_override(node, lines):
            continue
        found.append((node.lineno, key))
    return found


def scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return scan_source(source)


def main() -> int:
    offenders: list[str] = []
    scanned = 0
    for root, excluded in SCAN_ROOTS:
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            rel_parts = py.relative_to(root).parts
            if excluded and rel_parts and rel_parts[0] in excluded:
                continue
            scanned += 1
            rel = str(py.relative_to(REPO_ROOT)).replace("\\", "/")
            if rel in ALLOWED_FILES:
                continue
            for lineno, key in scan_file(py):
                offenders.append(f"  {rel}:{lineno} — site_config.get({key!r}, ...)")

    if offenders:
        print("site_config.get() called on a secret-shaped key (returns ciphertext):")
        for o in offenders:
            print(o)
        print(
            "\nSiteConfig filters is_secret=true rows out of its sync cache, so "
            "site_config.get(<secret key>) returns the enc:v1:... ciphertext blob "
            "instead of the plaintext value (GH-107 — 3 prior incidents from this "
            "exact bug). Use `await site_config.get_secret(key)` instead, or route "
            "through services/integrations/secret_resolver.py::resolve_secret for "
            "integration-row secrets. If this key is genuinely not secret despite "
            "the name, add `# secret-get-ok: <reason>`."
        )
        return 1

    require_scanned(
        scanned, lint="lint_secret_ciphertext_footgun", roots=[r for r, _ in SCAN_ROOTS]
    )
    print(f"lint_secret_ciphertext_footgun: clean — no ciphertext-footgun reads ({scanned} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
