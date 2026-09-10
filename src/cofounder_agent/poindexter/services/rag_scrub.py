"""Shared RAG scrub — redact secrets, private-repo refs, and operator identity
from any text before it reaches a writer prompt or the embeddings table.

Ships to the public mirror (generic mechanism). Operator-identity patterns load
from the stripped ``services.operator_leak_patterns`` overlay via a
no-op-when-absent hook (mirrors ``settings_defaults.apply_operator_overrides``),
so OSS installs get secret + private-repo scrub only.
"""
from __future__ import annotations

import re

# Secret formats — canonical home (was taps/claude_code_sessions._DEFAULT_SCRUB_PATTERNS).
SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"enc:v1:[A-Za-z0-9+/=]{40,}"), "[REDACTED:enc]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "[REDACTED:sk-ant]"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "[REDACTED:sk]"),
    (re.compile(r"ghp_[A-Za-z0-9]{36,}"), "[REDACTED:ghp]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{50,}"), "[REDACTED:github_pat]"),
    # Both installation-token shapes — classic ``ghs_`` + 36 alphanumerics
    # and the stateless ``ghs_<APPID>_<JWT>`` format (GitHub rollout began
    # 2026-04-27). Ordered above the JWT pattern so a stateless token is
    # redacted whole rather than losing only its JWT tail.
    (re.compile(r"ghs_[A-Za-z0-9._-]{36,}"), "[REDACTED:ghs]"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "[REDACTED:aws]"),
    (
        re.compile(
            r"eyJ[A-Za-z0-9_\-=]{10,}\.[A-Za-z0-9_\-=]{10,}\.[A-Za-z0-9_\-/+=]{20,}"
        ),
        "[REDACTED:jwt]",
    ),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED:slack]"),
)

# Private-repo refs — generalized Glad-Labs org form (excludes the public mirror).
_PRIV = r"Glad-Labs/(?!poindexter\b)[A-Za-z0-9._-]+"
_PRIVATE_REPO_PULL_INLINE = re.compile(
    r"\[([^]]+)\]\(https?://github\.com/" + _PRIV + r"/pull/(\d+)\)"
)
_PRIVATE_REPO_COMMIT_INLINE = re.compile(
    r"\[([^]]+)\]\(https?://github\.com/" + _PRIV + r"/commit/([0-9a-fA-F]{7})[0-9a-fA-F]*\)"
)
_PRIVATE_REPO_PULL_AUTOLINK = re.compile(
    r"<https?://github\.com/" + _PRIV + r"/pull/(\d+)>"
)
_PRIVATE_REPO_COMMIT_AUTOLINK = re.compile(
    r"<https?://github\.com/" + _PRIV + r"/commit/([0-9a-fA-F]{7})[0-9a-fA-F]*>"
)
_PRIVATE_REPO_PULL_BARE = re.compile(
    r"https?://github\.com/" + _PRIV + r"/pull/(\d+)"
)
_PRIVATE_REPO_COMMIT_BARE = re.compile(
    r"https?://github\.com/" + _PRIV + r"/commit/([0-9a-fA-F]{7})[0-9a-fA-F]*"
)
_PRIVATE_REPO_MENTION = re.compile(r"\b" + _PRIV + r"\b")


def scrub_private_repo_refs(text: str) -> str:
    """Rewrite private Glad-Labs repo URLs/mentions to the public mirror."""
    if not text:
        return text
    text = _PRIVATE_REPO_PULL_INLINE.sub(r"\1 (PR #\2)", text)
    text = _PRIVATE_REPO_COMMIT_INLINE.sub(r"\1 (`\2`)", text)
    text = _PRIVATE_REPO_PULL_AUTOLINK.sub(r"(PR #\1)", text)
    text = _PRIVATE_REPO_COMMIT_AUTOLINK.sub(r"(`\1`)", text)
    text = _PRIVATE_REPO_PULL_BARE.sub(r"(PR #\1)", text)
    text = _PRIVATE_REPO_COMMIT_BARE.sub(r"(`\1`)", text)
    text = _PRIVATE_REPO_MENTION.sub("Glad-Labs/poindexter", text)
    return text


def _load_operator_leak_patterns() -> list[tuple[re.Pattern[str], str]]:
    """Operator-identity patterns from the stripped overlay; [] on OSS installs.

    Mirrors ``settings_defaults.apply_operator_overrides`` — the module is absent
    on the public mirror, so this is a no-op there. Composes the guard-synced
    identity subset (``OPERATOR_SCRUB_PATTERNS``) with the aggressive
    transcript-scrub superset (``OPERATOR_SCRUB_EXTRA_PATTERNS`` — bare
    usernames, mangled/lowercase paths, bare private-repo refs).
    """
    try:
        from services.operator_leak_patterns import (
            OPERATOR_SCRUB_EXTRA_PATTERNS,
            OPERATOR_SCRUB_PATTERNS,
        )
    except ImportError:
        return []
    return list(OPERATOR_SCRUB_PATTERNS) + list(OPERATOR_SCRUB_EXTRA_PATTERNS)


def scrub_rag_text(
    text: str,
    *,
    extra_patterns: list[tuple[re.Pattern[str], str]] | None = None,
) -> str:
    """Redact secrets + operator identity and rewrite private-repo refs."""
    if not text:
        return ""
    for rx, repl in SECRET_PATTERNS:
        text = rx.sub(repl, text)
    for rx, repl in extra_patterns or []:
        text = rx.sub(repl, text)
    for rx, repl in _load_operator_leak_patterns():
        text = rx.sub(repl, text)
    text = scrub_private_repo_refs(text)
    return text
