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
