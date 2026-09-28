# scan-floor-exempt: helper library imported by the lints, not a lint
"""Is this ``scripts/ci`` lint running on the public mirror's stripped tree?

The public mirror (Glad-Labs/poindexter) is built from the source repository by
a sync that deletes the operator-private files before pushing. Its CI runs the
same workflows. The pytest steps degrade to ``--collect-only`` there, but every
``python scripts/ci/<lint>.py`` step runs for real, against a tree that is
missing files the source repository still has.

A lint whose verdict depends on which files EXIST can therefore disagree with
itself between the two repositories. ``comment_reference_lint`` flags a comment
citing a file that is not in the tree. On the mirror, a comment citing one of
the stripped files is not stale (the file exists, just not here), but the lint
cannot tell it from a file that was deleted. That turned the mirror's
unit-tests job red on every sync from 2026-09-20.

Only the environment can say which repository this is, not the tree. The tree
cannot tell a stripped file from a deleted one, and a list of stripped files
shipped inside a lint would disclose them.

Why ``GITHUB_REPOSITORY``, compared with the MIRROR's name
-------------------------------------------------------------
The sync byte-rewrites the source repository's ``org/name`` to the mirror's in
every text file it ships, this one included. A check written against the source
repository's name is rewritten into a check against the mirror's name, which
inverts it (#1481 found three workflow guards flipped that way). Naming only the
mirror keeps the literal identical in both trees, the same reason the workflows
guard with ``github.repository == 'Glad-Labs/poindexter'``. This file must
never spell the source repository's name, not even in prose: the rewrite does
not know prose from code.

Actions sets ``GITHUB_REPOSITORY`` on every job and every event, including the
nightly ``schedule`` run. That payload is not guaranteed to carry
``github.event.repository``, so ``repository.private`` is not an option here.
A local run has no ``GITHUB_REPOSITORY`` and gets the strict, source-repository
behaviour. That is the fail-closed direction: a lint that is wrong about where
it runs goes red rather than quietly gating less. Reproduce the mirror locally
with ``GITHUB_REPOSITORY=Glad-Labs/poindexter python scripts/ci/<lint>.py``.

A fork of the mirror carries another name and runs strict. That matches the
workflows, which also key their mirror behaviour on this exact repository.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

__all__ = ["PUBLIC_MIRROR_REPOSITORY", "on_public_mirror"]

# The ONLY repository name this module may contain. See the docstring: a
# reference to the source repository's name would be rewritten by the sync and
# invert the check on the mirror.
PUBLIC_MIRROR_REPOSITORY = "Glad-Labs/poindexter"


def on_public_mirror(environ: Mapping[str, str] | None = None) -> bool:
    """True when running in the public mirror's CI, where the tree is stripped.

    GitHub treats repository names case-insensitively, and so does this.
    """
    env = os.environ if environ is None else environ
    return env.get("GITHUB_REPOSITORY", "").strip().casefold() == (
        PUBLIC_MIRROR_REPOSITORY.casefold()
    )
