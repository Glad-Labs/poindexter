"""The lints' "am I on the public mirror?" check must survive the mirror sync.

The sync byte-rewrites the source repository's ``org/name`` into the mirror's in
every text file it publishes. A check that names the source repository is
therefore inverted on the mirror, which is how three workflow guards ended up
running exactly where they meant to skip (#1481). ``lib_public_mirror`` names
only the mirror. These tests pin that, and pin the answer for each place a lint
actually runs.

The source repository's name is assembled at runtime below. Spelled out, the
sync would rewrite this file too, and on the mirror the "source repository is
not the mirror" assertion would compare the mirror's name with itself.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = next(p for p in Path(__file__).resolve().parents
            if (p / "scripts" / "ci" / "lib_public_mirror.py").exists())
LIB_PATH = REPO / "scripts" / "ci" / "lib_public_mirror.py"

_spec = importlib.util.spec_from_file_location("lib_public_mirror_under_test", LIB_PATH)
lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lib)

SOURCE_REPOSITORY = "Glad-Labs/" + "glad-labs-stack"
MIRROR_REPOSITORY = "Glad-Labs/" + "poindexter"


def test_the_mirrors_ci_is_the_mirror():
    assert lib.on_public_mirror({"GITHUB_REPOSITORY": MIRROR_REPOSITORY}) is True


def test_repository_names_compare_case_insensitively_like_actions():
    assert lib.on_public_mirror({"GITHUB_REPOSITORY": MIRROR_REPOSITORY.upper()}) is True
    assert lib.on_public_mirror({"GITHUB_REPOSITORY": f" {MIRROR_REPOSITORY}\n"}) is True


def test_the_source_repositorys_ci_is_not():
    assert lib.on_public_mirror({"GITHUB_REPOSITORY": SOURCE_REPOSITORY}) is False


def test_a_local_run_is_strict():
    """No GITHUB_REPOSITORY means a developer's checkout. Fail-closed: the lint
    applies its full source-repository rule there."""
    assert lib.on_public_mirror({}) is False
    assert lib.on_public_mirror({"GITHUB_REPOSITORY": ""}) is False


def test_a_fork_of_the_mirror_is_strict():
    """Same as the workflows, which key their mirror behaviour on this exact name."""
    assert lib.on_public_mirror({"GITHUB_REPOSITORY": "someone/poindexter"}) is False


def test_reads_the_process_environment_by_default(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", MIRROR_REPOSITORY)
    assert lib.on_public_mirror() is True
    monkeypatch.delenv("GITHUB_REPOSITORY")
    assert lib.on_public_mirror() is False


def test_the_sync_rewrite_leaves_the_module_unchanged():
    """The check stays the same check on both sides of the sync.

    Applies the sync's substitution to the module's bytes. Any change means the
    module spells the source repository's name somewhere, in code or prose, and
    the mirror would run a different module than the source repository.
    """
    raw = LIB_PATH.read_bytes()
    rewritten = raw.replace(SOURCE_REPOSITORY.encode(), MIRROR_REPOSITORY.encode())
    assert rewritten == raw, (
        "lib_public_mirror.py names the source repository. The mirror sync "
        "rewrites that name to the mirror's, which inverts a check against it. "
        "Name only the mirror."
    )
    assert lib.PUBLIC_MIRROR_REPOSITORY == MIRROR_REPOSITORY
