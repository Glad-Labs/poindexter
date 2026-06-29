"""Contract + behavioral tests for the front-end-extension blind spot.

Pins the 2026-06-29 PII audit finding: the leak guard's ``_TEXT_EXTS`` listed
``.py/.md/.json/.yml/.toml/.sh/.ps1/.sql/...`` but **no** front-end extensions,
so every shipping ``.js/.jsx/.ts/.tsx`` file was invisible to ``scan()``. The
operator console (``src/cofounder_agent/console/``) ships to the public mirror,
and ``console/js/settings-data.js`` carried the operator's personal email
(``owner_email``) + site URL as mock data — live on Glad-Labs/poindexter — past
both the CI gate and the sync-time guard.

Two independent gaps combined to let it through:

1. ``.js`` (and the rest of the front-end family) was not in ``_TEXT_EXTS``, so
   the file was never opened.
2. The ``gladlabs.io`` pattern is SQL-``VALUES``-scoped by design, so even a
   scanned JS object literal (``value: 'matt@gladlabs.io'``) would not match —
   and there was no pattern for the operator's personal email at all.

Fix (this change):
- Add ``.js/.jsx/.ts/.tsx`` (+ ``.mjs/.cjs/.css/.html``) to ``_TEXT_EXTS``.
- Add an operator-personal-email ``LeakPattern`` that fires on
  ``matt|mattg|matthew @ gladlabs.io`` but NOT on role aliases
  (``support@``/``hello@``/``security@``) which legitimately ship.
- Skip vendored / minified bundles (``vendor/``, ``*.min.js``) so the scan
  doesn't choke on (or false-positive against) third-party blobs like
  ``console/js/vendor/babel.min.js``.

This test file tests stripped operator mirror-tooling, so it is itself stripped
from the public mirror (it loads the stripped guard). We construct the operator
email from fragments so the raw file never contains the literal leak.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _load_check_module():
    script = _repo_root() / "scripts" / "ci" / "check_public_mirror_safety.py"
    spec = spec_from_file_location("check_public_mirror_safety_frontend", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load_check_module()

# Build the operator email from fragments so this file never embeds the literal.
_OPERATOR_EMAIL = "matt" + "@" + "gladlabs" + ".io"
_ROLE_ALIASES = tuple(
    f"{alias}@gladlabs.io" for alias in ("support", "hello", "security", "conduct", "sales")
)

# This test module's own repo-relative path — it must be stripped from the mirror.
_SELF_REL = "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_frontend_exts.py"


# ---------------------------------------------------------------------------
# 1. Front-end extensions are scanned
# ---------------------------------------------------------------------------


def test_frontend_extensions_are_in_text_exts() -> None:
    """``.js/.jsx/.ts/.tsx`` must be in ``_TEXT_EXTS`` so the guard opens them."""
    missing = [ext for ext in (".js", ".jsx", ".ts", ".tsx") if ext not in CHECK._TEXT_EXTS]
    assert not missing, (
        f"Front-end extensions missing from _TEXT_EXTS: {missing}. The shipping "
        "operator console (src/cofounder_agent/console/) is invisible to the leak "
        "guard until these are scanned — the 2026-06-29 audit blind spot."
    )


def test_is_text_file_accepts_frontend_files() -> None:
    """``_is_text_file`` must return True for front-end source files."""
    for rel in (
        "src/cofounder_agent/console/js/settings-data.js",
        "src/cofounder_agent/console/js/app.jsx",
        "infrastructure/cloudflare/page-views-beacon/src/index.ts",
    ):
        assert CHECK._is_text_file(rel), f"_is_text_file({rel!r}) should be True"


# ---------------------------------------------------------------------------
# 2. Operator-personal-email leak pattern
# ---------------------------------------------------------------------------


def _operator_email_pattern():
    """Return the LeakPattern that matches the operator's personal email."""
    for lp in CHECK._LEAK_PATTERNS:
        if lp.regex.search(_OPERATOR_EMAIL):
            return lp.regex
    return None


def test_operator_email_pattern_is_registered() -> None:
    """A LEAK_PATTERNS entry must match the operator's personal email."""
    assert _operator_email_pattern() is not None, (
        "No _LEAK_PATTERNS entry matches the operator personal email "
        "(firstname@operator-domain). The gladlabs.io pattern is VALUES-scoped, "
        "so a JS object literal like `value: 'matt@gladlabs.io'` slips past — add "
        "a dedicated operator-email pattern."
    )


def test_operator_email_pattern_matches_name_variants() -> None:
    """The pattern catches matt/mattg/matthew @ the operator domain, any case."""
    pat = _operator_email_pattern()
    assert pat is not None
    for variant in ("matt", "mattg", "matthew", "Matt", "MATTHEW"):
        candidate = f"{variant}@gladlabs.io"
        assert pat.search(candidate), f"operator-email pattern missed {candidate!r}"


def test_operator_email_pattern_ignores_role_aliases() -> None:
    """Role aliases (support@/hello@/security@…) legitimately ship — never flag them.

    README.md, SUPPORT.md, SECURITY.md, and the pyproject author fields all carry
    role aliases at the operator domain. The personal-email pattern must be scoped
    to first-name local-parts so it does not turn those public files red.
    """
    pat = _operator_email_pattern()
    assert pat is not None
    matched = [a for a in _ROLE_ALIASES if pat.search(a)]
    assert not matched, f"operator-email pattern false-positived on role aliases: {matched}"


def test_operator_email_pattern_ignores_placeholder_domain() -> None:
    """The neutral placeholder we scrub TO (owner@example.com) must NOT fire."""
    pat = _operator_email_pattern()
    assert pat is not None
    for safe in ("owner@example.com", "matt@example.com", "matt@other.dev"):
        assert not pat.search(safe), f"operator-email pattern false-positived on {safe!r}"


# ---------------------------------------------------------------------------
# 3. Vendored / minified bundles are skipped
# ---------------------------------------------------------------------------


def test_vendored_and_minified_files_are_skipped() -> None:
    """``_is_vendored_or_minified`` flags vendored/minified bundles for skip."""
    assert hasattr(CHECK, "_is_vendored_or_minified"), (
        "check_public_mirror_safety must expose _is_vendored_or_minified() so "
        "scan() skips third-party blobs like console/js/vendor/babel.min.js."
    )
    skip = CHECK._is_vendored_or_minified
    assert skip("src/cofounder_agent/console/js/vendor/babel.min.js")
    assert skip("some/bundle.min.js")
    assert skip("a/vendor/lib.js")
    # Normal first-party source must NOT be skipped.
    assert not skip("src/cofounder_agent/console/js/settings-data.js")
    assert not skip("src/cofounder_agent/console/js/app.jsx")


# ---------------------------------------------------------------------------
# 4. Behavioral: scan() catches the leak in a JS file, but skips vendored blobs
# ---------------------------------------------------------------------------


def test_scan_flags_operator_email_in_shipping_js(tmp_path, monkeypatch) -> None:
    """A shipping ``.js`` object literal with the operator email is flagged.

    This is the end-to-end proof of the audit fix: the same shape that shipped
    live in console/js/settings-data.js (`value: 'matt@gladlabs.io'`) must now
    abort the sync. A sibling file carrying only a role alias must NOT be flagged.
    """
    leak_rel = "src/cofounder_agent/console/js/synthetic_fixture.js"
    safe_rel = "src/cofounder_agent/console/js/role_alias_fixture.js"
    for rel, body in (
        (leak_rel, f"  const S = [{{ key: 'owner_email', value: '{_OPERATOR_EMAIL}' }}];\n"),
        (safe_rel, "  const S = [{ key: 'support_email', value: 'support@gladlabs.io' }];\n"),
    ):
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body, encoding="utf-8")

    monkeypatch.setattr(CHECK, "_list_tracked_files", lambda *_: [leak_rel, safe_rel])
    hits = CHECK.scan(tmp_path)
    hit_files = {h.file for h in hits}
    assert leak_rel in hit_files, (
        "scan() did not flag the operator email in a shipping .js file — the "
        "front-end blind spot is still open."
    )
    assert safe_rel not in hit_files, (
        "scan() flagged a role-alias email (support@) — role aliases ship "
        "legitimately and must not be treated as operator-identity leaks."
    )


def test_scan_skips_operator_email_in_vendored_bundle(tmp_path, monkeypatch) -> None:
    """A vendored/minified bundle is never scanned even if it contains the email."""
    vendor_rel = "src/cofounder_agent/console/js/vendor/thirdparty.min.js"
    full = tmp_path / vendor_rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(f"/* bundled */ var x='{_OPERATOR_EMAIL}';\n", encoding="utf-8")

    monkeypatch.setattr(CHECK, "_list_tracked_files", lambda *_: [vendor_rel])
    hits = CHECK.scan(tmp_path)
    assert not hits, (
        "scan() examined a vendored/minified bundle. These third-party blobs are "
        "FP magnets (the word 'operator' is all over minified Babel) and must be "
        "skipped via _is_vendored_or_minified()."
    )


# ---------------------------------------------------------------------------
# 5. This stripped test file stays stripped (guard/sync lock-step)
# ---------------------------------------------------------------------------


def test_self_is_in_strip_files() -> None:
    """This test loads the stripped guard, so it must be stripped from the mirror."""
    assert _SELF_REL in CHECK._STRIP_FILES, (
        f"{_SELF_REL} is not in _STRIP_FILES. It imports the stripped leak guard "
        "and would ImportError on the mirror's unit-tests run. Add it to "
        "_STRIP_FILES AND git-rm it in scripts/sync-to-github.sh."
    )
    assert not CHECK.would_ship(_SELF_REL)


def test_sync_script_strips_self() -> None:
    """scripts/sync-to-github.sh must git-rm this test file too (lock-step)."""
    sync_script = _repo_root() / "scripts" / "sync-to-github.sh"
    text = sync_script.read_text(encoding="utf-8")
    assert _SELF_REL in text, (
        f"scripts/sync-to-github.sh has no reference to {_SELF_REL}. It's in "
        "_STRIP_FILES (so the guard skips scanning it) but the sync won't strip "
        "it — add a `git rm --cached` line in the mirror-tooling block."
    )
