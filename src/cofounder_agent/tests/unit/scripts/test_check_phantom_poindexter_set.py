"""Contract tests for ``scripts/ci/check_phantom_poindexter_set.py``.

The CI guard fails the build if any tracked file documents the *phantom*
``poindexter set <key> <value>`` command. The real CLI registers a
``settings`` group — the command is ``poindexter settings set <key> <value>``
(``--secret`` for encrypted keys, ``--allow-new`` for unseeded keys); there is
no top-level ``poindexter set`` (running it errors ``No such command 'set'``).

The phantom form kept creeping into operator docs/runbooks, runtime error
strings, and seeded ``app_settings`` descriptions. It was fixed twice already
(#1556 for the secret commands, #1562 for everything else) and reappeared each
time, so this guard makes the regression fail loud.

These tests pin three layers:

1. ``line_has_phantom_set`` — the pure line predicate — against the exact
   FAIL/PASS strings the guard must tell apart (``poindexter set foo`` FAIL vs.
   ``poindexter settings set foo`` / ``poindexter setup`` / ``set-secret`` PASS).
2. The path allow-list — the four files that legitimately carry the phantom
   string as *data* (the remediation migration's old-text constants, the
   auto-generated CHANGELOG entry describing the fix, this checker, and this
   test).
3. ``scan()`` — the inline ``allow-phantom-set`` escape hatch + path allow-list.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return next(
        p
        for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _load_check_module():
    script = _repo_root() / "scripts" / "ci" / "check_phantom_poindexter_set.py"
    spec = spec_from_file_location("check_phantom_poindexter_set", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load_check_module()


# ---------------------------------------------------------------------------
# Line predicate — phantom forms MUST be detected (the build should FAIL).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line",
    [
        "poindexter set foo",  # followed by a space
        "`poindexter set`",  # followed by a backtick
        "poindexter set <key>",  # followed by '<'
        "poindexter set",  # bare, end-of-line
        "run `poindexter set bar` to configure",  # mid-line, space after
        'the CLI: "poindexter set"',  # followed by a double-quote
    ],
)
def test_phantom_forms_are_detected(line: str) -> None:
    assert CHECK.line_has_phantom_set(line), (
        f"Phantom `poindexter set` should be detected but was not: {line!r}. "
        "The canonical command is `poindexter settings set <key> <value>`."
    )


# ---------------------------------------------------------------------------
# Line predicate — legitimate forms MUST pass (no false positives).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line",
    [
        "poindexter settings set foo",  # the real command
        "poindexter settings set writing_style_reference <desc> --secret",
        "poindexter setup",  # the real bootstrap command
        "poindexter setup --auto",
        "poindexter set-secret legacy_key",  # legacy, harmless
        "poindexter set_secret legacy_key",  # legacy, harmless
        "the poindexter settings group exposes get/set/list",
        "poindexter settings get foo --reveal",
    ],
)
def test_legitimate_forms_pass(line: str) -> None:
    assert not CHECK.line_has_phantom_set(line), (
        f"False positive — a legitimate command was flagged as phantom: {line!r}"
    )


# ---------------------------------------------------------------------------
# Path allow-list — the four files that legitimately carry the phantom string.
# ---------------------------------------------------------------------------
_REMEDIATION_MIGRATION = (
    "src/cofounder_agent/services/migrations/"
    "20260613_120000_fix_poindexter_set_in_app_settings_descriptions.py"
)


def test_remediation_migration_is_allowlisted() -> None:
    """The #1562 remediation migration embeds the phantom string on purpose.

    Its ``_DESCRIPTIONS`` map stores the *old* (phantom) text it matches and
    reverts in ``app_settings`` rows on existing installs. Scanning it would
    flag the very strings it exists to remove, so it must be exempt.
    """
    assert _REMEDIATION_MIGRATION in CHECK._ALLOWLIST_PATHS, (
        f"{_REMEDIATION_MIGRATION} must be in _ALLOWLIST_PATHS — it embeds the "
        "phantom string as the old-text it reverts (see #1556 / #1562)."
    )


def test_self_referential_files_are_allowlisted() -> None:
    """The checker + this test necessarily contain the phantom string as data.

    The checker defines the detection regex and the canonical-fix message; this
    test feeds the predicate phantom-form fixtures. Without the allow-list the
    whole-repo scan would flag both and the build would never go green.
    """
    assert "scripts/ci/check_phantom_poindexter_set.py" in CHECK._ALLOWLIST_PATHS
    assert (
        "src/cofounder_agent/tests/unit/scripts/test_check_phantom_poindexter_set.py"
        in CHECK._ALLOWLIST_PATHS
    )


def test_generated_changelog_is_allowlisted() -> None:
    """CHANGELOG.md is auto-generated by release-please and quotes the fix.

    release-please copies commit subjects verbatim, so the #1562 entry reads
    "correct phantom `poindexter set` command to `poindexter settings set`".
    Generated content can't carry an inline marker, so the file is exempt
    wholesale (mirrors the leak guard's CHANGELOG special-casing).
    """
    assert "CHANGELOG.md" in CHECK._ALLOWLIST_PATHS, (
        "CHANGELOG.md must be in _ALLOWLIST_PATHS — release-please copies the "
        "phantom-fix commit subject into it and generated entries can't carry "
        "an inline allow-phantom-set marker."
    )


# ---------------------------------------------------------------------------
# scan() — whole-tree behaviour with an injectable file list (no git needed).
# ---------------------------------------------------------------------------
def test_scan_flags_phantom_in_a_normal_file(tmp_path: Path) -> None:
    target = tmp_path / "runbook.md"
    target.write_text("Configure it with `poindexter set foo bar`.\n", encoding="utf-8")
    hits = CHECK.scan(tmp_path, rel_paths=["runbook.md"])
    assert len(hits) == 1, f"expected exactly one hit, got {hits!r}"
    assert hits[0].file == "runbook.md"
    assert hits[0].line_no == 1


def test_scan_skips_inline_allow_marker_hash(tmp_path: Path) -> None:
    target = tmp_path / "settings_defaults.py"
    target.write_text(
        'DESC = "legacy: poindexter set foo"  # allow-phantom-set\n',
        encoding="utf-8",
    )
    hits = CHECK.scan(tmp_path, rel_paths=["settings_defaults.py"])
    assert hits == [], "a line carrying the allow-phantom-set marker must be skipped"


def test_scan_skips_inline_allow_marker_html_comment(tmp_path: Path) -> None:
    target = tmp_path / "notes.md"
    target.write_text(
        "Old hint: `poindexter set foo` <!-- allow-phantom-set -->\n",
        encoding="utf-8",
    )
    hits = CHECK.scan(tmp_path, rel_paths=["notes.md"])
    assert hits == [], "the marker must work inside an HTML comment too"


def test_scan_skips_allowlisted_path(tmp_path: Path) -> None:
    rel = "scripts/ci/check_phantom_poindexter_set.py"
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("pattern = 'poindexter set foo'\n", encoding="utf-8")
    hits = CHECK.scan(tmp_path, rel_paths=[rel])
    assert hits == [], "allow-listed paths must be skipped by scan()"


def test_scan_ignores_binary_and_unlisted_extensions(tmp_path: Path) -> None:
    target = tmp_path / "logo.png"
    target.write_text("poindexter set foo\n", encoding="utf-8")
    hits = CHECK.scan(tmp_path, rel_paths=["logo.png"])
    assert hits == [], "non-text extensions must not be scanned"
