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

import ast
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_check_module():
    repo_root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )
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
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_frontend_exts.py",
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_gitea.py",
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_multiline.py",
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_name_regex.py",
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_sentry_dsn.py",
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_distribution_target.py",
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_strip_list.py",
    "src/cofounder_agent/tests/unit/scripts/test_regen_app_settings_doc.py",
    "src/cofounder_agent/tests/unit/scripts/test_sync_script_leak_guard_delegation.py",
)

# Directory holding the leak guard's own test siblings. Derived rather than
# hardcoded — see test_every_mirror_safety_test_sibling_is_stripped.
_MIRROR_SAFETY_TEST_DIR = "src/cofounder_agent/tests/unit/scripts"


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


def test_every_mirror_safety_test_sibling_is_stripped() -> None:
    """EVERY ``test_check_public_mirror_safety_*.py`` on disk must be stripped.

    Derived from the filesystem on purpose. ``_MIRROR_TOOLING_STRIP`` above is a
    hand-maintained tuple, and a hand-maintained list is exactly what failed:
    ``test_check_public_mirror_safety_sentry_dsn.py`` landed in #2662 and was
    added to none of the three places that must agree, so the mirror received a
    test whose subject (``scripts/ci/check_public_mirror_safety.py``) is
    stripped. It loads that script BY PATH via ``spec_from_file_location``, so
    the import-based guard below could not see it either — the mirror's
    unit-tests job went red on every push with ``FileNotFoundError``.

    Keying off the directory listing means the next sibling is caught the moment
    it is written, without anyone remembering this file exists.
    """
    root = _repo_root()
    test_dir = root / _MIRROR_SAFETY_TEST_DIR
    siblings = sorted(
        f"{_MIRROR_SAFETY_TEST_DIR}/{p.name}"
        for p in test_dir.glob("test_check_public_mirror_safety_*.py")
    )
    assert siblings, (
        f"No mirror-safety test siblings found under {test_dir} — this test "
        "silently passes if the glob stops matching. Check the path."
    )
    missing = [p for p in siblings if p not in CHECK._STRIP_FILES]
    assert not missing, (
        f"Mirror-safety test siblings not stripped: {missing}. Each one loads "
        "scripts/ci/check_public_mirror_safety.py by path, which does NOT exist "
        "on the mirror, so shipping it turns the public unit-tests job red. Add "
        "each to ALL THREE: _STRIP_FILES in scripts/ci/check_public_mirror_safety.py, "
        "the mirror-tooling block in scripts/sync-to-github.sh, and "
        "_MIRROR_TOOLING_STRIP above."
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


# ---------------------------------------------------------------------------
# Claude-projects path encoding (2026-06-13) — voice-host-brain.md is STRIPPED.
#
# Claude Code flattens C:\Users\<user> into C--Users-<user> for project-dir
# namespaces. docs/operations/voice-host-brain.md documents the operator's
# Claude-memory junction using literal C--Users-mattm paths as load-bearing
# subject matter (plus operator per-turn cost figures and a Windows-host
# runbook) — operator-overlay content. Once the C--Users-mattm leak pattern was
# added to the guard, this file would abort the sync, so it joins the sibling
# operator-overlay runbooks (finance-module-operator.md, self-hosted-ci-runner.md)
# in the strip list. (taps/memory.py + its test, which also referenced the path,
# are a generic OSS feature and were genericized instead of stripped.)
# ---------------------------------------------------------------------------

_VOICE_HOST_BRAIN_DOC = "docs/operations/voice-host-brain.md"


def test_voice_host_brain_is_in_strip_files() -> None:
    """docs/operations/voice-host-brain.md must be stripped from the mirror."""
    assert _VOICE_HOST_BRAIN_DOC in CHECK._STRIP_FILES, (
        f"{_VOICE_HOST_BRAIN_DOC} is not in _STRIP_FILES. It documents the "
        "operator's Claude-memory junction with literal C--Users-mattm paths "
        "(load-bearing subject matter) plus operator cost figures — "
        "operator-overlay content that must not ship. Add it here AND add a "
        "`git rm --cached` line in scripts/sync-to-github.sh."
    )


def test_would_ship_rejects_voice_host_brain() -> None:
    """would_ship() must classify voice-host-brain.md as NOT shipping."""
    assert not CHECK.would_ship(_VOICE_HOST_BRAIN_DOC), (
        f"would_ship() still classifies {_VOICE_HOST_BRAIN_DOC} as shipping. "
        "It must be stripped (operator Claude-memory paths + per-turn cost "
        "figures). Verify the _STRIP_FILES entry."
    )


def test_sync_script_strips_voice_host_brain() -> None:
    """sync-to-github.sh must git-rm the doc too (guard/sync lock-step).

    _STRIP_FILES drives would_ship() (the scan-skip); sync-to-github.sh does
    the actual stripping. If they drift — file in _STRIP_FILES but not
    git-rm'd in the sync script — would_ship() returns False (so the guard
    skips scanning it) while the sync still SHIPS it with the operator paths
    intact: a silent leak the guard cannot see. Pin both ends together.
    """
    repo_root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )
    sync_script = repo_root / "scripts" / "sync-to-github.sh"
    text = sync_script.read_text(encoding="utf-8")
    assert _VOICE_HOST_BRAIN_DOC in text, (
        f"scripts/sync-to-github.sh has no reference to {_VOICE_HOST_BRAIN_DOC}. "
        "It's in _STRIP_FILES (so the guard skips scanning it) but the sync "
        "won't actually strip it — a silent leak. Add a `git rm --cached` line "
        "in the strip block."
    )


_OPERATOR_OVERRIDES = "src/cofounder_agent/services/operator_overrides.py"


def test_operator_overrides_is_stripped() -> None:
    """The private operator model overlay must be stripped from the mirror.

    It pins the operator's custom local models (not on the public Ollama
    registry); the public apply hook no-ops without it, so OSS installs keep the
    public defaults.
    """
    assert _OPERATOR_OVERRIDES in CHECK._STRIP_FILES, (
        f"{_OPERATOR_OVERRIDES} is not in _STRIP_FILES. It carries the operator's "
        "custom local model tags and must not ship to the public mirror."
    )


def test_sync_script_strips_operator_overrides() -> None:
    """sync-to-github.sh must git-rm the overlay too (guard/sync lock-step)."""
    repo_root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )
    text = (repo_root / "scripts" / "sync-to-github.sh").read_text(encoding="utf-8")
    assert _OPERATOR_OVERRIDES in text, (
        f"scripts/sync-to-github.sh has no reference to {_OPERATOR_OVERRIDES}. "
        "It's in _STRIP_FILES but the sync won't strip it — a silent leak."
    )


# ---------------------------------------------------------------------------
# GENERAL drift invariant (2026-07-07) — every _STRIP_FILES entry must be
# actually git-rm'd by sync-to-github.sh, not just the hand-picked few above.
#
# The two lists are separate: _STRIP_FILES drives would_ship() (the guard's
# scan-SKIP decision); sync-to-github.sh does the ACTUAL stripping. When a file
# is added to _STRIP_FILES but NOT to the sync git-rm list, would_ship() returns
# False — the guard skips scanning it — while the sync still SHIPS it intact.
# A file that carries operator literals then leaks to the public mirror UNSEEN.
#
# That is exactly what happened: operator_leak_patterns.py + its test were added
# to _STRIP_FILES but not to the sync git-rm block, so they shipped to
# Glad-Labs/poindexter on the PR #2200 squash-merge carrying the operator's real
# name / home paths / Tailnet IP / GitHub handle. The per-file
# test_sync_script_strips_* tests above didn't catch it because nobody adds the
# per-file test either. This iterates the WHOLE list so the class can't recur.
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def test_every_strip_files_entry_is_stripped_by_sync_script() -> None:
    """Every _STRIP_FILES entry must have a matching `git rm` in the sync script.

    Guards against the drift class that leaked operator_leak_patterns.py to the
    public mirror (#2200): a file the guard treats as stripped (would_ship=False,
    so no leak scan) but the sync never removes — it ships unscanned.
    """
    sync = (_repo_root() / "scripts" / "sync-to-github.sh").read_text(encoding="utf-8")
    missing: list[str] = []
    for entry in CHECK._STRIP_FILES:
        # A file under a stripped directory is removed by that dir's `git rm -r`.
        if any(entry.startswith(d) for d in CHECK._STRIP_DIR_PREFIXES):
            continue
        # Wildcard entries (e.g. .github/COMMIT_MESSAGE_*.txt) are removed by the
        # same glob line; assert the literal prefix before the wildcard appears.
        needle = entry.split("*")[0] if "*" in entry else entry
        if needle not in sync:
            missing.append(entry)
    assert not missing, (
        "These _STRIP_FILES entries have no `git rm` line in "
        "scripts/sync-to-github.sh, so would_ship() skips scanning them while "
        "the sync SHIPS them to the public mirror unscanned — a silent "
        f"operator-info leak: {missing}. Add a `git rm --cached` line for each "
        "in the strip block (lock-step with _STRIP_FILES)."
    )


def test_every_strip_dir_prefix_is_stripped_by_sync_script() -> None:
    """Every _STRIP_DIR_PREFIXES entry must have a matching `git rm -r` too.

    Sibling of the file invariant above: a directory the guard treats as
    stripped but the sync never `git rm -r`'s would ship its whole contents
    unscanned.
    """
    sync = (_repo_root() / "scripts" / "sync-to-github.sh").read_text(encoding="utf-8")
    missing = [d for d in CHECK._STRIP_DIR_PREFIXES if d not in sync]
    assert not missing, (
        "These _STRIP_DIR_PREFIXES entries have no `git rm -r` line in "
        f"scripts/sync-to-github.sh — their contents ship unscanned: {missing}. "
        "Add a `git rm -r --cached` line for each."
    )


# ---------------------------------------------------------------------------
# #2200 incident anchor — the operator-identity RAG scrub overlay + its test.
# Named assertion so the specific files that leaked are greppable; the general
# invariants above cover the sync-side lock-step.
# ---------------------------------------------------------------------------

_OPERATOR_LEAK_OVERLAY = (
    "src/cofounder_agent/services/operator_leak_patterns.py",
    "src/cofounder_agent/tests/unit/services/test_operator_leak_patterns.py",
)


def test_operator_leak_patterns_overlay_is_stripped() -> None:
    """The operator-identity scrub overlay + its literal-carrying test must be stripped.

    operator_leak_patterns.py carries OPERATOR_SCRUB_PATTERNS (the operator's
    real name / home paths / Tailnet IP / GitHub handle) and its test carries
    matching fixtures. rag_scrub imports the overlay via a no-op-when-absent
    hook, so OSS installs get generic scrub only — the overlay never needs to
    ship. Both must be in _STRIP_FILES (would_ship=False) AND git-rm'd by the
    sync (covered by the general invariant above).
    """
    missing = [p for p in _OPERATOR_LEAK_OVERLAY if p not in CHECK._STRIP_FILES]
    assert not missing, (
        f"Operator-identity scrub overlay files missing from _STRIP_FILES: {missing}. "
        "They carry the operator's real name / home paths / Tailnet IP / GitHub "
        "handle. Add them here AND in scripts/sync-to-github.sh's strip block."
    )
    shipping = [p for p in _OPERATOR_LEAK_OVERLAY if CHECK.would_ship(p)]
    assert not shipping, (
        f"would_ship() still classifies these operator-overlay files as shipping: "
        f"{shipping}. Verify the _STRIP_FILES entries and that no _LEAK_GUARD_ALLOW "
        "entry re-exempts them."
    )


# ---------------------------------------------------------------------------
# Operator console (2026-07-09) — STRIPPED from the mirror but STILL SCANNED.
#
# The operator console SPA (src/cofounder_agent/console/) is a Pro-tier overlay
# stripped from the public mirror by sync-to-github.sh (#2137 — verified 404 on
# Glad-Labs/poindexter). Unlike every other stripped tree it is deliberately NOT
# in _STRIP_DIR_PREFIXES: would_ship() stays True for it so the leak guard keeps
# scanning the console — defense-in-depth on the operator UI, the most
# operator-context-heavy tree (it caught a hardcoded Tailnet IP in a launcher
# comment, #2227). That is the safe ship=no/scan=yes asymmetry.
#
# Because the console lives only on the sync side (not in _STRIP_*), the general
# drift invariants above — which iterate _STRIP_FILES / _STRIP_DIR_PREFIXES —
# don't cover its strip. These two tests pin BOTH halves explicitly: the sync
# really strips it (so it can't silently regress to shipping) AND the guard
# really scans it (so nobody "aligns the lists" and drops the operator-literal
# net).
# ---------------------------------------------------------------------------

_OPERATOR_CONSOLE_DIR = "src/cofounder_agent/console/"


def test_sync_script_strips_operator_console() -> None:
    """sync-to-github.sh must git-rm the operator console (Pro-tier overlay, #2137).

    The console is stripped from the public mirror (verified 404 on
    Glad-Labs/poindexter). This pins the ``git rm -r`` line so the strip can't be
    silently deleted: the console is intentionally absent from _STRIP_DIR_PREFIXES
    (it stays in the guard's scan scope), so the general drift invariant above —
    which only iterates the strip lists — does not otherwise cover it.
    """
    text = (_repo_root() / "scripts" / "sync-to-github.sh").read_text(encoding="utf-8")
    assert _OPERATOR_CONSOLE_DIR in text, (
        f"scripts/sync-to-github.sh has no `git rm` line for {_OPERATOR_CONSOLE_DIR}. "
        "The operator console is a Pro-tier overlay that must NOT ship to the public "
        "mirror (#2137). Add a `git rm -r --cached` line in the strip block."
    )


def test_console_is_scanned_despite_being_stripped() -> None:
    """The guard must STILL scan the console even though the sync strips it.

    The console is deliberately absent from _STRIP_DIR_PREFIXES / _STRIP_FILES so
    would_ship() returns True and scan() opens its .js/.jsx files — defense-in-
    depth that catches operator literals (e.g. the hardcoded Tailnet IP in #2227)
    in the operator UI before any strip regression could leak them. Adding the
    console to a strip list to silence dev-time false-positives would retire that
    net — this test forbids it. (test_scan_flags_operator_email_in_shipping_js in
    the frontend-exts suite proves the scan is load-bearing end-to-end.)
    """
    for rel in (
        "src/cofounder_agent/console/js/settings-data.js",
        "src/cofounder_agent/console/js/app.jsx",
    ):
        assert CHECK.would_ship(rel), (
            f"would_ship({rel!r}) is False — the console has been added to "
            "_STRIP_DIR_PREFIXES (or _STRIP_FILES), dropping it out of the leak "
            "guard's scan scope. Keep the console OUT of the strip lists: the sync "
            "already strips it (test_sync_script_strips_operator_console) and "
            "scanning it is intentional defense-in-depth."
        )


# ---------------------------------------------------------------------------
# General import-safety invariant: a test file that SHIPS to the public
# mirror must not have a module-level import that resolves into a STRIPPED
# tree. Unlike the leak-pattern scan above (which catches operator-private
# literals), this catches a different failure shape entirely — a plain
# `from modules.finance.probes import X` at the top of an otherwise public
# test file. Nothing about that line looks private, so _LEAK_PATTERNS can't
# flag it; but `modules/finance/` doesn't exist on the mirror, so pytest
# fails to even COLLECT the file (ModuleNotFoundError), which aborts the
# entire xdist worker and cascades to skip every later shard in the same CI
# job. This exact shape shipped in glad-labs-stack#2407 (the silent-excepts
# burn-down batch 6b test for modules/finance/probes.py landed in the
# general-purpose tests/unit/services/test_misc_silent_failures.py instead
# of the stripped tests/unit/modules/finance/ tree) and broke the public
# poindexter unit-tests job on every push until fixed.
# ---------------------------------------------------------------------------

_SRC_PREFIX = "src/cofounder_agent/"


def _top_level_import_modules(tree: ast.Module) -> list[str]:
    """Dotted module names imported at module scope (not inside a def/try).

    Deliberately shallow — only ``tree.body`` (top-level statements), so an
    import guarded inside a function body or a ``try/except ImportError``
    block is NOT flagged. Those forms only run when the test itself
    executes, not when pytest merely collects the file, so they can't
    reproduce the collection-time crash this test guards against.
    """
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — can't reach another top-level package
                continue
            if node.module:
                names.append(node.module)
    return names


def test_no_shipping_test_file_imports_a_stripped_module() -> None:
    """A public test file's module-level imports must all resolve on the mirror.

    Sweeps every git-tracked ``tests/**/*.py`` file that ``would_ship()``
    classifies as public, parses its top-level imports, and checks whether
    any of them translate to a path ``would_ship()`` classifies as
    STRIPPED. A hit means the file will ImportError at collection time on
    the public mirror — see the module docstring above for the incident
    this reproduces (glad-labs-stack#2407).
    """
    repo_root = _repo_root()
    violations: list[str] = []
    for rel in CHECK._list_tracked_files(repo_root):
        if not rel.startswith("src/cofounder_agent/tests/") or not rel.endswith(".py"):
            continue
        if not CHECK.would_ship(rel):
            continue  # this test file itself never reaches the mirror
        try:
            text = (repo_root / rel).read_text(encoding="utf-8")
            tree = ast.parse(text, filename=rel)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue  # not this test's concern — collection would fail for other reasons
        for mod_name in _top_level_import_modules(tree):
            candidate = f"{_SRC_PREFIX}{mod_name.replace('.', '/')}.py"
            if not CHECK.would_ship(candidate):
                violations.append(f"{rel} imports {mod_name!r} (-> {candidate}, stripped)")

    assert not violations, (
        "The following public test files have a module-level import that "
        "resolves into a directory stripped from the public poindexter mirror. "
        "This crashes pytest COLLECTION on the mirror (ModuleNotFoundError), "
        "which aborts the whole shard and cascades to skip every later shard "
        "in the same CI job (glad-labs-stack#2407). Fix: move the affected "
        "test(s) into the matching stripped test tree (e.g. "
        "tests/unit/modules/finance/ for a modules.finance import), or defer "
        "the import inside the test function body if the dependency is "
        "genuinely optional.\n\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Grafana dashboard strip drift (2026-07-13) — the "Premium Grafana
# dashboards" block named 6 filenames under a retired $9/mo monetization
# model. Four no longer existed on disk at all (folded into
# pipeline-merged.json / system-health-merged.json by the 2026-06-03
# dashboard restructuring, poindexter#654); a 5th (cost-analytics.json) had
# no independent leak reason. A `git rm --cached` on a missing path silently
# no-ops, so this drifted for weeks unnoticed: qa-rails.json and its
# replacement-merged siblings shipped un-audited by this list (harmless —
# dashboards were never meant to be code-gated, see SUPPORT.md: "the
# subscription buys freshness, not gated features") while cost-analytics.json
# shipped withheld for a monetization model that no longer exists. Only
# mission-control.json had a real (privacy, not monetization) leak reason —
# confirmed via grep against nightrider/taild4f626.ts.net — and stays.
#
# This is the SAME failure class as the #1288 / #2200 incidents (two lists,
# or a list vs. renamed reality, silently falling out of sync) applied to a
# new axis: does the STRIPPED FILE STILL EXIST. The general tests above this
# comment (test_every_strip_files_entry_is_stripped_by_sync_script /
# test_every_strip_dir_prefix_is_stripped_by_sync_script) already guard
# "is every _STRIP_FILES entry actually git-rm'd" — they do NOT guard "does
# the named file still exist", which is the gap this section closes.
# ---------------------------------------------------------------------------

_RETIRED_DASHBOARD_MONETIZATION_STRIPS = (
    "infrastructure/grafana/dashboards/approval-queue.json",
    "infrastructure/grafana/dashboards/cost-analytics.json",
    "infrastructure/grafana/dashboards/infrastructure-data.json",
    "infrastructure/grafana/dashboards/link-registry.json",
    "infrastructure/grafana/dashboards/quality-content.json",
)


def test_retired_dashboard_monetization_strips_are_not_reintroduced() -> None:
    """The 5 filenames from the retired dashboard-monetization strip must stay gone.

    Dashboards are not feature-gated for Poindexter Pro (SUPPORT.md: "the
    subscription buys freshness, not gated features; the engine itself stays
    fully functional under Apache 2.0"). Pro ships a curated dashboard COPY
    via the wholly separate Glad-Labs/poindexter-pro repo, not by withholding
    files from this OSS mirror. If one of these reappears in _STRIP_FILES,
    either the monetization model changed back (update this test deliberately)
    or someone silently reintroduced the drift this section fixed.
    """
    reintroduced = [p for p in _RETIRED_DASHBOARD_MONETIZATION_STRIPS if p in CHECK._STRIP_FILES]
    assert not reintroduced, (
        f"These dashboard files are back in _STRIP_FILES: {reintroduced}. "
        "Dashboards ship free in the OSS mirror -- Pro is a separate curated "
        "repo, not a code-level gate. If this is a genuine new leak concern "
        "(not monetization), document the specific reason in _STRIP_FILES "
        "the way mission-control.json's entry does."
    )


_MISSION_CONTROL_DASHBOARD = "infrastructure/grafana/dashboards/mission-control.json"


def test_mission_control_dashboard_is_still_stripped_for_privacy() -> None:
    """mission-control.json must stay in _STRIP_FILES -- a privacy strip, not monetization.

    It embeds the operator's real Tailscale Funnel hostname
    (nightrider.taild4f626.ts.net) plus Pyroscope/Loki/Tempo deep-links.
    Unlike the other 5 dashboard filenames this block used to strip, this one
    has independent leak content and must not be removed just because the
    monetization rationale for its siblings went away.
    """
    assert _MISSION_CONTROL_DASHBOARD in CHECK._STRIP_FILES, (
        f"{_MISSION_CONTROL_DASHBOARD} is not in _STRIP_FILES. It leaks the "
        "operator's Tailscale hostname and Pyroscope/Loki/Tempo links -- strip "
        "it here AND keep the `git rm --cached` line in scripts/sync-to-github.sh."
    )


def test_would_ship_rejects_mission_control_dashboard() -> None:
    """would_ship() must classify mission-control.json as NOT shipping."""
    assert not CHECK.would_ship(_MISSION_CONTROL_DASHBOARD), (
        f"would_ship({_MISSION_CONTROL_DASHBOARD!r}) is True -- it leaks the "
        "operator's Tailscale hostname and must be stripped."
    )


def test_every_dashboard_strip_entry_exists_on_disk() -> None:
    """Every infrastructure/grafana/dashboards/ entry in _STRIP_FILES must exist on disk.

    The general safeguard the 2026-07-13 incident was missing: a dashboard
    strip entry naming a file that no longer exists is a silent no-op -- the
    guard 'passes' while whatever REPLACED that file (a merge, a rename)
    ships completely unaudited by this list. This catches the exact failure
    class before it can recur on a future dashboard restructuring, without
    the false-positive risk of applying the same check to the whole
    _STRIP_FILES tuple (several non-dashboard entries -- e.g. the
    intentionally-transient skills/openclaw/ legacy-path strip -- are allowed
    to reference files that don't exist on every branch).
    """
    repo_root = _repo_root()
    missing = [
        entry
        for entry in CHECK._STRIP_FILES
        if entry.startswith("infrastructure/grafana/dashboards/")
        and not (repo_root / entry).exists()
    ]
    assert not missing, (
        f"These _STRIP_FILES dashboard entries name files that no longer "
        f"exist on disk: {missing}. A dashboard rename/merge has silently "
        "made this strip a no-op -- either update the entry to the new "
        "filename (if the leak content moved with it) or remove the stale "
        "entry (if the leak content is gone, as happened 2026-07-13)."
    )


# ---------------------------------------------------------------------------
# Path-load guard (2026-08-25, stack#3147 fallout) — a shipping test that
# loads a stripped ``scripts/*.py`` BY PATH kills mirror collection.
#
# test_regen_app_settings_doc_guard.py landed in #3147 with a module-level
# ``_load_script()`` that asserts scripts/regen-app-settings-doc.py exists.
# The script is stripped; the new test was added to none of the strip lists
# (only its older sibling test_regen_app_settings_doc.py was), so the mirror's
# unit-tests job died at COLLECTION on every push for 16 days. Neither
# existing guard could see it: the import-based sweep above only parses
# ``import`` statements (a path-load isn't one), and the filename-glob sweep
# only covers the test_check_public_mirror_safety_* family. The new
# ``check_stripped_script_test_references()`` in the leak-guard script closes
# the gap by content-matching stripped scripts/*.py basenames across every
# SHIPPING test file — the third occurrence of this class (#2662, #2407,
# #3147), so it now gets the general check the first two earned piecemeal.
# ---------------------------------------------------------------------------

_REGEN_GUARD_TEST = (
    "src/cofounder_agent/tests/unit/scripts/test_regen_app_settings_doc_guard.py"
)


def test_regen_guard_test_is_stripped() -> None:
    """The #3147 guard test must be stripped — it path-loads a stripped script."""
    assert _REGEN_GUARD_TEST in CHECK._STRIP_FILES, (
        f"{_REGEN_GUARD_TEST} is not in _STRIP_FILES. Its module-level "
        "_load_script() asserts scripts/regen-app-settings-doc.py exists, and "
        "that script is stripped — shipping this test collection-errors the "
        "mirror's ENTIRE unit-tests run. Add it here AND in "
        "scripts/sync-to-github.sh's mirror-tooling block."
    )
    assert not CHECK.would_ship(_REGEN_GUARD_TEST), (
        f"would_ship({_REGEN_GUARD_TEST!r}) is True — verify the _STRIP_FILES "
        "entry and that no _LEAK_GUARD_ALLOW entry re-exempts it."
    )


def test_stripped_python_script_basenames_scope() -> None:
    """The path-load guard covers stripped .py scripts and ONLY those.

    ``.sh``/``.ps1``/``.json`` strips are deliberately out of scope: they
    cannot be import-loaded at collection time, and shipping tests DO mention
    them in comments (sync-to-github.sh in test_misc_silent_failures.py,
    claude-sessions.ps1 in test_ops_sessions_wiring.py) — including them
    would turn those legitimate mentions into false positives.
    """
    basenames = CHECK._stripped_python_script_basenames()
    assert "regen-app-settings-doc.py" in basenames
    assert "check_public_mirror_safety.py" in basenames
    assert "sync-to-github.sh" not in basenames
    assert "bootstrap.sh" not in basenames
    assert "claude-sessions.ps1" not in basenames


def test_no_shipping_test_references_a_stripped_script() -> None:
    """No test that ships to the mirror may reference a stripped scripts/*.py.

    Mirrors the check the leak-guard script runs in CI
    (``check_stripped_script_test_references``); running it here too means a
    violation fails the unit suite even before the public-mirror-safety
    workflow runs.
    """
    violations = CHECK.check_stripped_script_test_references(_repo_root())
    assert not violations, (
        "Shipping test files reference stripped scripts — on the mirror the "
        "script is absent, so a path-load at import time collection-errors "
        f"the whole unit-tests run: {violations}. Either strip the test "
        "(add to _STRIP_FILES AND scripts/sync-to-github.sh) or make it skip "
        "when the script is absent."
    )


def test_stripped_script_reference_check_catches_planted_violation(
    tmp_path, monkeypatch
) -> None:
    """The path-load guard actually fires on the #3147 shape, not just passes."""
    rel = "src/cofounder_agent/tests/unit/scripts/test_planted_example.py"
    planted = tmp_path / rel
    planted.parent.mkdir(parents=True)
    planted.write_text(
        "# path-loads scripts/regen-app-settings-doc.py at module scope\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(CHECK, "_list_tracked_files", lambda root: [rel])
    violations = CHECK.check_stripped_script_test_references(tmp_path)
    assert violations == [(rel, "scripts/regen-app-settings-doc.py")], (
        f"Expected the planted reference to be flagged, got: {violations}. "
        "The guard would miss the exact shape that broke the mirror in #3147."
    )
