"""Operator-identity scrub patterns — STRIPPED from the public mirror.

The regex SOURCES here are copied verbatim from the operator-identity subset of
``scripts/ci/check_public_mirror_safety.py::_LEAK_PATTERNS`` (name, home paths,
Tailnet host, GitHub handle). ``test_operator_leak_patterns`` pins them equal to
the guard so the two can't drift. This file carries the operator-name literal, so
it lives in the guard's ``_STRIP_FILES`` and never ships — ``rag_scrub`` imports it
via a no-op-when-absent hook (OSS installs get generic scrub only).
"""
from __future__ import annotations

import re

OPERATOR_SCRUB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Every operator tailnet address, past and present — NOT the whole CGNAT
    # range; see the rationale on the matching pattern in
    # check_public_mirror_safety.py. Add the new IP here on re-addressing.
    (re.compile(r"\b100\.(?:81\.93\.12|111\.15\.72)\b"), "[operator-host]"),
    (re.compile(r"taild4f626\.ts\.net"), "[operator-host]"),
    (re.compile(r"\bnightrider\b"), "[operator-host]"),
    (re.compile(r"C:[\\/]+Users[\\/]+mattm"), "[operator-path]"),
    (re.compile(r"/c/Users/mattm"), "[operator-path]"),
    (re.compile(r"C--Users-mattm", re.IGNORECASE), "[operator-path]"),
    (re.compile(r"mattg-stack"), "[operator]"),
    (re.compile(r"matthew-gladding"), "[operator]"),
    (re.compile(r"[Mm]atthew (?:[A-Z]\.\s+)?[Gg]ladding"), "[operator]"),
    (re.compile(r"[Mm]att [Gg]ladding"), "[operator]"),
)

# Transcript-specific aggressive scrub — a deliberate SUPERSET of the source-code
# identity patterns above, for grounding text pulled from session transcripts.
# Transcripts carry operator identity in forms the source-code leak guard never
# sees: a bare username in an ``ls -l`` owner column (``drwxr-xr-x 1 mattm``),
# bash-mangled paths with the separators stripped (``C:Usersmattm``), lowercase
# drive letters (``c:\Users\mattm``), and bare private-repo mentions with no
# ``Glad-Labs/`` prefix (``glad-labs-stack#928``). Over-redacting CONTENT is
# always safe — these tokens are never relevant to a published AI/ML/gaming/
# hardware post — so these match aggressively. Deliberately NOT part of the
# guard-subset contract (``test_patterns_are_subset_of_leak_guard`` pins only
# OPERATOR_SCRUB_PATTERNS): the mirror guard intentionally omits them because
# source files reference ``glad-labs-stack`` legitimately and the sync rewrites
# it cosmetically — flagging it there would redden the whole mirror.
# No word-boundary anchors: bash mangles paths by stripping the separators, so
# the username glues to its neighbours (``C:Usersmattm``) and ``\bmattm\b`` would
# miss it. Both tokens are distinctive enough (never legitimate English
# substrings) that an unanchored, case-insensitive match has no realistic
# false-positive surface in published content.
OPERATOR_SCRUB_EXTRA_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)mattm"), "[operator]"),
    (re.compile(r"(?i)glad-labs-stack"), "poindexter"),
)
