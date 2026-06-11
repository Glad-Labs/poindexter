"""Contract tests for _STRIP_FILES entries added in the 2026-05-27 security audit.

Pins two new entries added to the public-mirror safety check:

1. ``scripts/bootstrap.sh`` — the legacy bootstrap script references
   stripped files (``.env.example``, ``docker-compose.local.yml``) and the
   dead Woodpecker CI secret (``WOODPECKER_SECRET``). On the public mirror it
   would fail immediately for any OSS user. Replaced by ``poindexter setup --auto``.

2. ``docs.json`` rewrite — the Mintlify config ships to the public mirror but
   its operator-branded URLs (``gladlabs.io/product``, ``www.gladlabs.io``) are
   rewritten at sync time to poindexter-neutral equivalents. This test verifies
   the ``_SUBSTRATE_LINE_STRIPS`` entry for ``docs.json`` documents those lines.

Test approach: load the ``check_public_mirror_safety`` module and inspect its
``_STRIP_FILES`` tuple and ``_SUBSTRATE_LINE_STRIPS`` dict directly, so the
assertions stay coupled to the code rather than requiring a live filesystem scan.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_check_module():
    repo_root = Path(__file__).resolve().parents[5]
    script = repo_root / "scripts" / "ci" / "check_public_mirror_safety.py"
    spec = spec_from_file_location("check_public_mirror_safety_strip", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load_check_module()


def test_bootstrap_sh_is_in_strip_files() -> None:
    """scripts/bootstrap.sh must be listed in _STRIP_FILES.

    The legacy bootstrap script references stripped files (.env.example,
    docker-compose.local.yml) and the dead Woodpecker CI. On a fresh OSS
    clone it fails immediately. poindexter setup --auto is the replacement.
    Strip added in the 2026-05-27 security audit.
    """
    assert "scripts/bootstrap.sh" in CHECK._STRIP_FILES, (
        "scripts/bootstrap.sh is not in _STRIP_FILES. "
        "The legacy bootstrap script breaks on fresh OSS clones because it "
        "references .env.example and docker-compose.local.yml which are "
        "stripped from the public mirror. Add it to _STRIP_FILES "
        "(and the matching entry in scripts/sync-to-github.sh)."
    )


def test_would_ship_rejects_bootstrap_sh() -> None:
    """would_ship('scripts/bootstrap.sh') must return False after the strip."""
    assert not CHECK.would_ship("scripts/bootstrap.sh"), (
        "would_ship() classifies scripts/bootstrap.sh as shipping to the "
        "public mirror, but it must be stripped. Verify the _STRIP_FILES "
        "entry is correct and would_ship() checks it."
    )


def test_docs_json_gladlabs_lines_are_in_substrate_line_strips() -> None:
    """The two gladlabs.io lines in docs.json must be listed in _SUBSTRATE_LINE_STRIPS.

    The sync filter rewrites these URLs at sync time; the CI lint must
    know they'll be replaced so it doesn't false-positive on the source tree.
    """
    strips = CHECK._SUBSTRATE_LINE_STRIPS
    assert "docs.json" in strips, (
        "docs.json is missing from _SUBSTRATE_LINE_STRIPS. "
        "The sync filter rewrites its gladlabs.io URLs before pushing; "
        "the CI lint needs this entry to skip the pre-rewrite source lines."
    )
    doc_strips = strips["docs.json"]
    assert any("gladlabs.io/product" in s for s in doc_strips), (
        "The 'gladlabs.io/product' href is not listed in _SUBSTRATE_LINE_STRIPS['docs.json']. "
        "Add it so the CI lint skips the line that the sync filter rewrites."
    )
    assert any("www.gladlabs.io" in s for s in doc_strips), (
        "The 'www.gladlabs.io' website is not listed in _SUBSTRATE_LINE_STRIPS['docs.json']. "
        "Add it so the CI lint skips the line that the sync filter rewrites."
    )


# ---------------------------------------------------------------------------
# Glad-Labs/poindexter#1287 — the operator mirror-tooling cluster must be
# STRIPPED (not allowlisted) so the leak guard's own operator-private
# literals stop shipping to the public mirror.
# ---------------------------------------------------------------------------

# The two scripts + their unit tests that load them. Stripping the whole
# cluster together keeps the mirror's unit-tests run from ImportError-ing on
# the now-absent scripts. Keep this list in lock-step with the mirror-tooling
# block in _STRIP_FILES and the matching git-rm block in sync-to-github.sh.
_MIRROR_TOOLING_STRIP = (
    "scripts/ci/check_public_mirror_safety.py",
    "scripts/regen-app-settings-doc.py",
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_gitea.py",
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_multiline.py",
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_name_regex.py",
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_strip_list.py",
    "src/cofounder_agent/tests/unit/scripts/test_regen_app_settings_doc.py",
    "src/cofounder_agent/tests/unit/scripts/test_sync_script_leak_guard_delegation.py",
)


def test_mirror_tooling_cluster_is_in_strip_files() -> None:
    """The leak guard, the doc generator, and their tests must all be stripped.

    They carry operator-private literals inline (the blocklist of values they
    redact). Shipping them put the guard's own ``_LEAK_PATTERNS`` figures on
    the public mirror — the guard was itself the leak (#1287).
    """
    missing = [p for p in _MIRROR_TOOLING_STRIP if p not in CHECK._STRIP_FILES]
    assert not missing, (
        f"Operator mirror-tooling files missing from _STRIP_FILES: {missing}. "
        "Add them here AND in scripts/sync-to-github.sh's mirror-tooling block."
    )


def test_would_ship_rejects_mirror_tooling_cluster() -> None:
    """would_ship() must classify every mirror-tooling file as NOT shipping."""
    shipping = [p for p in _MIRROR_TOOLING_STRIP if CHECK.would_ship(p)]
    assert not shipping, (
        f"would_ship() still classifies these as shipping to the mirror: {shipping}. "
        "A leftover _LEAK_GUARD_ALLOW entry takes precedence over _STRIP_FILES "
        "in would_ship() — make sure none of these are allowlisted."
    )


def test_leak_guard_allow_is_empty() -> None:
    """The self-exemption list must stay empty (#1287 root-cause #1).

    An allowlisted file still ships; that is exactly how the guard's own
    operator literals leaked. Every former exemption is now a strip instead.
    A future genuinely-public pattern-definition file may be added back here,
    but only after it's confirmed to carry NO operator literals.
    """
    assert CHECK._LEAK_GUARD_ALLOW == (), (
        "_LEAK_GUARD_ALLOW is not empty. Allowlisting a public-bound file "
        "exempts it from the leak scan while it still SHIPS — the #1287 bug. "
        "Strip operator-private files via _STRIP_FILES instead."
    )


# ---------------------------------------------------------------------------
# #1288 — .env.example ships to public; must NOT be in _STRIP_FILES.
#
# The divergence: .env.example was in _STRIP_FILES (scanner skipped it) while
# sync-to-github.sh shipped it (poindexter#607 deliberately restored the file
# after it was stripped, to fix the quickstart `cp .env.example .env` flow).
# The scanner was therefore skipping a file that the public mirror actually
# received — a blind spot closed by this fix.
# ---------------------------------------------------------------------------


def test_env_example_is_not_in_strip_files() -> None:
    """.env.example must NOT appear in _STRIP_FILES.

    It ships to the public mirror (poindexter#607) and must be scanned for
    operator-private patterns. Adding it to _STRIP_FILES causes would_ship()
    to return False and the scanner to skip it — the blind spot fixed in #1288.
    """
    assert ".env.example" not in CHECK._STRIP_FILES, (
        ".env.example is in _STRIP_FILES but it intentionally SHIPS to the public "
        "mirror (poindexter#607 restored it so `cp .env.example .env` works for "
        "quickstart users). Remove it from _STRIP_FILES so the leak scanner "
        "examines it. If you want to stop shipping it, also update the "
        "'poindexter#607' comment block in scripts/sync-to-github.sh."
    )


def test_env_example_would_ship() -> None:
    """would_ship('.env.example') must return True so the scanner processes it."""
    assert CHECK.would_ship(".env.example"), (
        "would_ship() classifies .env.example as NOT shipping to the mirror. "
        "It is intentionally public (poindexter#607). Remove it from _STRIP_FILES "
        "and confirm _LEAK_GUARD_ALLOW doesn't skip it either."
    )


def test_ships_to_public_not_in_strip_files() -> None:
    """No file in _SHIPS_TO_PUBLIC may appear in _STRIP_FILES.

    This is the coherence invariant introduced in #1288. A file listed as
    'ships to public' and also listed in _STRIP_FILES is a contradictory state:
    the scanner skips it (would_ship returns False) while the sync filter
    delivers it — exactly the blind spot that caused #1288.
    """
    conflicts = CHECK.check_strip_coherence()
    assert not conflicts, (
        f"Ship/strip coherence violation — files in both _SHIPS_TO_PUBLIC and "
        f"_STRIP_FILES: {conflicts}. Either remove the file from _STRIP_FILES "
        f"(so the scanner examines it) or remove it from _SHIPS_TO_PUBLIC "
        f"(if it was stripped intentionally)."
    )
