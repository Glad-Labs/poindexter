"""Contract test for the multi-line VALUES leak pattern.

Cycle-4 audit #243 found the leak guard missed multi-line SQL VALUES
tuples — the existing regex required ``VALUES (`` on the same line as
the offending literal. The 2026-05-27 fix:

* Added ``multiline: bool`` to ``LeakPattern``.
* Promoted the gladlabs.io-in-VALUES pattern to ``multiline=True`` with
  ``re.DOTALL`` so the keyword and the literal can land on different
  lines and still be caught.
* ``scan()`` does a second pass over the whole-file text for
  ``multiline=True`` patterns, mapping match position back to a line
  number for the error report.

This test pins all three pieces by feeding synthetic text through the
real pattern + the helper scanner logic.
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
    spec = spec_from_file_location(
        "check_public_mirror_safety_multiline", script,
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load_check_module()


def _gladlabs_pattern():
    for lp in CHECK._LEAK_PATTERNS:
        if "gladlabs.io as a seeded default" in lp.label:
            return lp
    raise AssertionError("expected a gladlabs.io VALUES leak pattern")


# ---------------------------------------------------------------------------
# Pattern shape — multiline flag is set
# ---------------------------------------------------------------------------


def test_pattern_is_marked_multiline():
    pat = _gladlabs_pattern()
    assert pat.multiline is True
    assert pat.regex.flags & re.DOTALL, "must have re.DOTALL for line-spanning"


# ---------------------------------------------------------------------------
# Single-line shape — backward-compatible match
# ---------------------------------------------------------------------------


def test_single_line_values_tuple_still_matches():
    """Existing leak shape (everything on one line) must still fire."""
    pat = _gladlabs_pattern()
    leaky = "VALUES ('site_url', 'https://gladlabs.io')"
    assert pat.regex.search(leaky) is not None


# ---------------------------------------------------------------------------
# Multi-line shape — the audit's actual finding
# ---------------------------------------------------------------------------


def test_multi_line_values_tuple_is_caught():
    """The exact shape that bypassed the old single-line regex.

    Pretty-printed SQL fixtures (test_taps_db.py and the like) land
    ``VALUES`` on one line, ``(`` on the next, and the offending literal
    a line or two deeper. The audit caught this in
    ``test_taps_db.py:223-224`` — now it must fire."""
    pat = _gladlabs_pattern()
    leaky = (
        "INSERT INTO brain_knowledge (entity, attribute, value)\n"
        "VALUES\n"
        "  ('site', 'site_url', 'https://gladlabs.io'),\n"
        "  ('operator', 'role', 'founder')\n"
    )
    assert pat.regex.search(leaky) is not None


def test_legitimate_brand_mention_not_caught():
    """Brand-attribution prose in README / CLAUDE.md must NOT trigger.

    The pattern's discriminator is the SQL ``VALUES`` context — bare
    URLs in markdown are not flagged by this pattern (they're handled
    by an operator-policy pass on `_STRIP_FILES`, not the VALUES regex).
    """
    pat = _gladlabs_pattern()
    benign_readme = "This repo powers https://www.gladlabs.io content pipeline."
    assert pat.regex.search(benign_readme) is None


# ---------------------------------------------------------------------------
# scan() integration — the whole-file pass reports the correct line
# ---------------------------------------------------------------------------


def test_scan_reports_line_of_match_start(tmp_path, monkeypatch):
    """When the multiline pattern matches, ``scan()`` must report the
    line containing the *first character* of the match (where ``VALUES``
    appears) — not a later line containing the literal. Operators
    debugging a leak need to see the keyword start so they can locate
    the surrounding tuple."""
    # Drop a fake "would-ship" file under tmp_path with the leak.
    rel = "src/cofounder_agent/tests/integration/fake_test.py"
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# line 1 placeholder\n"
        "# line 2 placeholder\n"
        "sql = '''\n"
        "    INSERT INTO t VALUES\n"          # line 4
        "      ('site', 'https://gladlabs.io')\n"  # line 5
        "'''\n"
    )
    full.write_text(body, encoding="utf-8")

    # Stub the git-ls-files helper and would_ship/_is_text_file gates so
    # scan() processes our synthetic file. ``_LEAK_GUARD_ALLOW`` doesn't
    # include this fake path so the lint actually runs against it.
    monkeypatch.setattr(CHECK, "_list_tracked_files", lambda _root: [rel])
    monkeypatch.setattr(CHECK, "would_ship", lambda _r: True)
    monkeypatch.setattr(CHECK, "_is_text_file", lambda _r: True)

    hits = CHECK.scan(tmp_path)
    gladlabs_hits = [
        h for h in hits if "gladlabs.io" in h.pattern.label
    ]
    assert len(gladlabs_hits) == 1, (
        f"expected exactly one gladlabs.io hit, got {len(gladlabs_hits)} "
        f"(all hits: {[h.pattern.label for h in hits]})"
    )
    hit = gladlabs_hits[0]
    # The match starts at ``VALUES`` on line 4 of the synthetic body.
    assert hit.line_no == 4, (
        f"expected line 4 (where VALUES appears), got {hit.line_no}"
    )
    assert "VALUES" in hit.line_text


def test_line_strips_apply_to_multiline_pass(tmp_path, monkeypatch):
    """``_SUBSTRATE_LINE_STRIPS`` exemptions must still apply when the
    multi-line pattern's match-start line happens to be a stripped one.
    Otherwise the line-strip mechanism would be a single-pass artifact
    and the new multiline pass would re-fire on lines the sync filter
    already drops at push time."""
    rel = "CHANGELOG.md"  # CHANGELOG has substrate line strips configured
    full = tmp_path / rel
    full.write_text(
        "mercury_balance_setup VALUES\n"
        "  ('site', 'https://gladlabs.io')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(CHECK, "_list_tracked_files", lambda _root: [rel])
    monkeypatch.setattr(CHECK, "would_ship", lambda _r: True)
    monkeypatch.setattr(CHECK, "_is_text_file", lambda _r: True)

    hits = CHECK.scan(tmp_path)
    # The match starts on the ``mercury_balance_setup VALUES`` line,
    # which contains 'mercury_' — listed in _SUBSTRATE_LINE_STRIPS for
    # CHANGELOG.md so the line is filtered at sync time. The leak guard
    # must skip it.
    gladlabs_hits = [
        h for h in hits if "gladlabs.io" in h.pattern.label
    ]
    assert gladlabs_hits == [], (
        f"line-strip exemption was not honored — got {gladlabs_hits!r}"
    )
