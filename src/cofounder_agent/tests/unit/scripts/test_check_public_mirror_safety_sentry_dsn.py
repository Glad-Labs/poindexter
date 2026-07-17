"""Contract test for the Sentry/GlitchTip DSN LEAK_GUARD pattern.

Pins the 2026-07-17 audit finding: ``0000_baseline.seeds.sql`` seeded the
operator's real GlitchTip DSN into the PUBLIC baseline. It survived the
squash's secret filter because the row is ``is_secret=false``, and
``sync-to-github.sh`` does not strip ``baseline.seeds.sql`` — so the DSN
shipped to Glad-Labs/poindexter verbatim.

The DSN's host was compose-internal (``glitchtip-web``), so this was not a
live credential compromise. The guard exists because the *mechanism* is
generic: any ``is_secret=false`` row carrying a DSN-shaped value lands in
the public default set, and CLAUDE.md documents the Sentry SDK as
auto-initialising whenever ``sentry_dsn`` is set — so a leaked DSN also
points every fresh OSS install's error reporting at a host that is not
theirs.

Exercises the pattern against both DSN key forms (GlitchTip UUID and
Sentry-classic 32-hex) and guards the false-positive surface (ordinary URLs
carrying userinfo, e.g. postgres/redis, must not trip).

All DSN fixtures here are assembled at runtime from synthetic keys — see the
note above ``_DUMMY_UUID_KEY``. This file ships to the public mirror, so a
literal DSN in it would trip the very guard it tests;
``test_this_file_carries_no_literal_dsn`` keeps that honest.
"""

from __future__ import annotations

import re
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_check_module():
    repo_root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )
    script = repo_root / "scripts" / "ci" / "check_public_mirror_safety.py"
    spec = spec_from_file_location("check_public_mirror_safety", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load_check_module()

# DSN fixtures are ASSEMBLED AT RUNTIME, never written as literals.
#
# This file ships to the public mirror, so a literal DSN here would trip the
# very guard it tests — the self-report trap that sent the leak guard's own
# pattern literals public in Glad-Labs/poindexter#1287. Splitting the key from
# the scheme means no source line matches ``scheme://<key>@``, while the
# assembled runtime string still exercises the regex exactly.
#
# The keys below are synthetic. The operator's real key is deliberately absent:
# a same-shape dummy proves the same thing, because the pattern keys off the
# shape (32-hex or UUID), not the value.
_DUMMY_UUID_KEY = "00000000-1111-2222-3333-444444444444"
_DUMMY_HEX_KEY = "0123456789abcdef0123456789abcdef"

# Same shape as the DSN that leaked: UUID key, compose-internal host.
_DSN_UUID_FORM = f"http://{_DUMMY_UUID_KEY}@glitchtip-web:8000/1"
# Sentry-classic: 32-hex key, public ingest host.
_DSN_HEX_FORM = f"https://{_DUMMY_HEX_KEY}@o1.ingest.sentry.io/42"


def _dsn_pattern() -> re.Pattern[str]:
    for lp in CHECK._LEAK_PATTERNS:
        if lp.regex.search(_DSN_UUID_FORM):
            return lp.regex
    raise AssertionError(
        "No LEAK_PATTERNS entry matches a Sentry/GlitchTip DSN. The DSN "
        "guard added 2026-07-17 must be present."
    )


def test_dsn_pattern_is_registered() -> None:
    """A LEAK_PATTERNS entry must match a UUID-key DSN."""
    assert _dsn_pattern() is not None


def test_dsn_pattern_matches_real_dsn_shapes() -> None:
    """Both DSN key forms GlitchTip/Sentry emit must match."""
    pat = _dsn_pattern()
    samples = [
        _DSN_UUID_FORM,
        _DSN_HEX_FORM,
        # embedded in a SQL seed line, which is how it actually leaked
        f"VALUES ('sentry_dsn', '{_DSN_UUID_FORM}', 'api_keys', '', false, true)",
    ]
    unmatched = [s for s in samples if not pat.search(s)]
    assert not unmatched, f"pattern missed: {unmatched}"


def test_this_file_carries_no_literal_dsn() -> None:
    """This test file ships to the public mirror, so it must never contain a
    DSN literal — only runtime-assembled ones.

    The guard's own pattern literals went public exactly this way
    (Glad-Labs/poindexter#1287): a would-ship file that names the value it
    guards self-reports. Asserting it here means the invariant is checked by
    the unit suite, not only by the slower mirror-safety job.
    """
    pat = _dsn_pattern()
    source = Path(__file__).read_text(encoding="utf-8")
    hits = [line.strip() for line in source.splitlines() if pat.search(line)]
    assert not hits, (
        "literal DSN(s) in this file would trip the mirror guard; "
        f"assemble them at runtime instead: {hits}"
    )


def test_dsn_pattern_does_not_match_ordinary_userinfo_urls() -> None:
    """URLs that legitimately carry user:pass@host must not trip."""
    pat = _dsn_pattern()
    safe = [
        "postgresql://poindexter:hunter2@postgres-local:5432/poindexter_brain",
        "redis://:password@redis:6379/0",
        "https://user@example.com/path",
        "http://langfuse-web:3000",
        "sentry_dsn",  # the bare key name is not a leak
        "VALUES ('sentry_dsn', '', 'api_keys', '', false, true)",  # empty = correct
    ]
    matched = [s for s in safe if pat.search(s)]
    assert not matched, f"false positive on: {matched}"


def test_dsn_pattern_remediation_is_actionable() -> None:
    """A PR author tripping this guard must be told what to do."""
    pat = _dsn_pattern()
    for lp in CHECK._LEAK_PATTERNS:
        if lp.regex is pat:
            assert "DSN" in lp.why or "dsn" in lp.why
            assert lp.why.strip(), "remediation copy must not be empty"
            return
    raise AssertionError("DSN pattern is missing its remediation copy")
