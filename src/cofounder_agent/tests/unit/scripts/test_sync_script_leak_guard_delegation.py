"""Contract test: sync-to-github.sh delegates leak guard to the Python module.

Pre-2026-05-27 the bash sync script duplicated the LEAK_PATTERNS and
LEAK_GUARD_ALLOW arrays inline, mirroring the lists in
``scripts/ci/check_public_mirror_safety.py``. The duplication drifted —
PR #619 added a new test fixture to the CI-side allowlist but missed
the bash-side allowlist, silently failing the public-mirror sync for
5+ consecutive pushes.

This test pins the consolidation: the bash script MUST invoke the
Python guard (single source of truth) rather than re-declaring the
pattern + allowlist arrays. A future agent that tries to "fix a leak"
by adding it directly to the bash script will instead trip this test
and be redirected to the Python module.

The lone permitted inline check is the post-rewrite belt-and-suspenders
for ``Glad-Labs/glad-labs-stack`` — that pattern is intentionally absent
from the Python module's list (would false-positive on release-please
CHANGELOG entries in the source tree) and only makes sense after the
cosmetic sed rewrite has already run.
"""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[5]
_SYNC_SCRIPT = _REPO_ROOT / "scripts" / "sync-to-github.sh"


def _read_sync_script() -> str:
    return _SYNC_SCRIPT.read_text(encoding="utf-8")


def test_sync_script_invokes_python_guard() -> None:
    """Sync script must call the Python guard as the leak-guard step."""
    text = _read_sync_script()
    assert "python3 scripts/ci/check_public_mirror_safety.py" in text, (
        "scripts/sync-to-github.sh must delegate the leak guard to "
        "scripts/ci/check_public_mirror_safety.py (single source of "
        "truth). If you added a new pattern, add it to _LEAK_PATTERNS "
        "in the Python module, not as a new bash array entry."
    )


def test_sync_script_has_no_inline_leak_patterns_array() -> None:
    """The legacy LEAK_PATTERNS=( bash array must stay deleted.

    A future agent re-introducing the inline array would re-open the
    drift hole that took down the public mirror sync 5+ times in May
    2026. The Python guard is the single source of truth.
    """
    text = _read_sync_script()
    assert "LEAK_PATTERNS=(" not in text, (
        "scripts/sync-to-github.sh must not declare its own "
        "LEAK_PATTERNS bash array. Patterns belong in "
        "scripts/ci/check_public_mirror_safety.py::_LEAK_PATTERNS."
    )


def test_sync_script_has_no_inline_allowlist_array() -> None:
    """The legacy LEAK_GUARD_ALLOW=( bash array must stay deleted.

    Same reasoning as the patterns array. Allowlist additions go in
    the Python module's ``_LEAK_GUARD_ALLOW`` tuple — both layers
    pick up the change automatically.
    """
    text = _read_sync_script()
    assert "LEAK_GUARD_ALLOW=(" not in text, (
        "scripts/sync-to-github.sh must not declare its own "
        "LEAK_GUARD_ALLOW bash array. Allowlist entries belong in "
        "scripts/ci/check_public_mirror_safety.py::_LEAK_GUARD_ALLOW."
    )


def test_sync_script_keeps_post_rewrite_internal_repo_check() -> None:
    """The Glad-Labs/glad-labs-stack belt-and-suspenders stays inline.

    This pattern is intentionally NOT in the Python guard — it'd
    false-positive on every release-please CHANGELOG entry on the
    pre-rewrite source tree. It only makes sense at sync time, after
    the cosmetic sed rewrite of ``Glad-Labs/glad-labs-stack`` →
    ``Glad-Labs/poindexter`` has already run. Documented in the
    Python module's docstring as the lone permitted exception.
    """
    text = _read_sync_script()
    assert "Glad-Labs/glad-labs-stack" in text, (
        "scripts/sync-to-github.sh must keep the post-rewrite "
        "belt-and-suspenders grep for Glad-Labs/glad-labs-stack — "
        "catches anything the sed rewrite missed."
    )
    assert "INTERNAL_REPO_HITS" in text, (
        "Expected the post-rewrite check to use the INTERNAL_REPO_HITS "
        "shell variable. If you renamed it, update this test."
    )


def test_sync_rewrite_delegates_text_detection_to_guard() -> None:
    """The cosmetic-rewrite pass must reuse the guard's ``_is_text_file``.

    Root cause of the 2026-06-04 sync abort: the rewrite kept its OWN
    inline ``text_exts`` set that had drifted from the guard's
    ``_TEXT_EXTS`` — it lacked ``.ps1``, so voice-brain-host.ps1's
    internal-repo doc-comment link (Glad-Labs/glad-labs-stack#1006) was
    never normalized and tripped the post-rewrite belt-and-suspenders
    check. Sharing one predicate makes the rewrite's coverage track the
    guard's scan set automatically — the same single-source-of-truth move
    that killed the LEAK_PATTERNS/allowlist drift.
    """
    text = _read_sync_script()
    assert "from check_public_mirror_safety import _is_text_file" in text, (
        "scripts/sync-to-github.sh's cosmetic-rewrite pass must import "
        "_is_text_file from the leak guard (single source of truth for "
        "'rewritable text file'), not re-declare an extension allowlist."
    )
    assert "text_exts = {" not in text, (
        "The rewrite must not keep its own inline `text_exts` set — that "
        "list drifted from the guard's _TEXT_EXTS and leaked a .ps1 file. "
        "Delegate to the shared _is_text_file predicate instead."
    )


def test_sync_rewrite_preserves_line_endings() -> None:
    """The cosmetic rewrite must use byte-level IO so CRLF files survive.

    ``.ps1`` / ``.cmd`` / ``.bat`` are ``eol=crlf`` per .gitattributes.
    The old text-mode write forced ``newline="\\n"``, which would convert
    them to LF the moment the rewrite touched one — corrupting the file
    and desyncing the mirror blob from the eol attribute. A raw
    ``read_bytes`` / ``replace`` / ``write_bytes`` leaves endings intact.
    """
    text = _read_sync_script()
    assert "read_bytes()" in text and "write_bytes(" in text, (
        "scripts/sync-to-github.sh's cosmetic-rewrite pass must do a "
        "byte-level substitution (read_bytes/write_bytes) so it can "
        "safely rewrite CRLF files (.ps1 etc.) without normalizing their "
        "line endings to LF."
    )


def test_guard_text_predicate_covers_powershell() -> None:
    """PowerShell scripts must stay a recognized rewritable text type.

    ``.ps1`` files ship to the public mirror (e.g. voice-brain-host.ps1,
    which documents itself as public-safe) and can carry internal-repo
    links in doc comments. Now that the rewrite delegates to the guard's
    predicate, dropping ``.ps1`` from the guard's text set would silently
    stop normalizing those links and re-open the leak.
    """
    import sys

    sys.path.insert(0, str(_REPO_ROOT / "scripts" / "ci"))
    from check_public_mirror_safety import _TEXT_EXTS, _is_text_file

    assert ".ps1" in _TEXT_EXTS
    assert _is_text_file("scripts/voice-brain-host.ps1") is True
