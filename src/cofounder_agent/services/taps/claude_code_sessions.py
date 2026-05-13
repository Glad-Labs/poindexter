"""ClaudeCodeSessionsTap — ingest Claude Code session transcripts.

Every Claude Code conversation is stored as a JSONL file under
``~/.claude/projects/<scope>/<session_uuid>.jsonl``. This Tap parses
those files, filters noise (system reminders, hook outputs, oversized
tool results), scrubs known secret patterns, and yields one Document
per session — ready for chunking + embedding by the runner.

## Why per-session (not per-message)

Session-level grain captures the full conversational context: user
intent + tool calls + decisions in one chunk. Per-message grain bloats
the embeddings table with thousands of tiny rows that rarely retrieve
meaningfully on their own. The runner still splits long sessions on
message boundaries via ``_chunking.classify_file`` conventions.

## Noise filter

Events dropped entirely:

- ``file-history-snapshot`` — editor metadata, no semantic content
- ``system`` — internal bookkeeping
- Assistant ``thinking`` blocks — provider-specific signatures, not
  human-readable reasoning
- System-reminder user messages (``<system-reminder>...</system-reminder>``
  tags that the runtime injects automatically)
- Automated loop fires (``<<autonomous-loop*>>`` sentinel messages)

Events kept but summarized:

- ``tool_use`` — rendered as ``[tool: Name(<input-summary>)]``
- ``tool_result`` — truncated past ``max_tool_result_chars``, prefixed
  with ``[tool result]:``

## Secret scrubbing

The embedded text is run through a small set of regex patterns that
replace known credential formats with ``[REDACTED]`` before embedding.
Patterns cover OpenAI/Anthropic ``sk-...`` keys, GitHub PATs
(``ghp_...``), JWT tokens (``eyJ...``), AWS access keys, and our own
``enc:v1:...`` ciphertext prefix (so encrypted secrets don't end up
embedded in plaintext-adjacent context).

The scrub happens inside this Tap, not in the runner, so sessions that
carry secrets are stored redacted. Reversing = re-running the Tap
with an updated pattern list is safe; the embeddings table uniques
on ``content_hash`` so re-embedding a scrubbed session replaces the
old entry.

## Config (``plugin.tap.claude_code_sessions`` in ``app_settings``)

- ``enabled`` (default ``true``)
- ``interval_seconds`` (default ``7200`` — 2h, since sessions don't
  grow rapidly after save)
- ``config.claude_projects_dir`` — override the default
  ``~/.claude/projects`` path. Same semantic as MemoryFilesTap.
- ``config.max_sessions_per_run`` (default ``0`` — unlimited)
- ``config.session_age_max_days`` (default ``0`` — no age cap). When
  >0, sessions whose mtime is older than N days are skipped entirely.
- ``config.max_tool_result_chars`` (default ``2000``) — tool-result
  bodies are truncated past this length.
- ``config.extra_scrub_patterns`` (default empty list) — additional
  regex patterns (as strings) to apply on top of the built-in set.
  Useful for per-install tokens (e.g. gitea_password value).

## Source IDs

``claude-code-sessions/<scope>/<session_uuid>`` — matches the existing
MemoryFilesTap's scope-aware naming so the two data sources don't
collide on the embeddings unique constraint.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from plugins.tap import Document

logger = logging.getLogger(__name__)


# Dense technical sessions (JSON-ish tool results, long URLs, code blocks)
# tokenize at ~1.5-2 tokens/char — the shared runner's MAX_CHARS=6000
# assumption overshoots nomic-embed-text's 8192-token context for this
# kind of content. Pre-chunking in this Tap with a conservative 3500-char
# limit keeps every yielded Document fitting inside one embedding call
# without the runner-level chunker having to do it.
_SESSION_CHUNK_CHARS = 3500


# ---------------------------------------------------------------------------
# Built-in scrub patterns
# ---------------------------------------------------------------------------

# Each tuple is (regex, replacement). Order matters only when one pattern
# would otherwise overlap another — keep the most specific first.
_DEFAULT_SCRUB_PATTERNS: tuple[tuple[str, str], ...] = (
    # Our own app_settings encrypted-secret prefix. If a session captured
    # ciphertext, we don't want it embedded.
    (r"enc:v1:[A-Za-z0-9+/=]{40,}", "[REDACTED:enc]"),
    # OpenAI-style keys (also Anthropic sk-ant-...).
    (r"sk-ant-[A-Za-z0-9_\-]{20,}", "[REDACTED:sk-ant]"),
    (r"sk-[A-Za-z0-9]{32,}", "[REDACTED:sk]"),
    # GitHub personal access tokens + fine-grained tokens.
    (r"ghp_[A-Za-z0-9]{36,}", "[REDACTED:ghp]"),
    (r"github_pat_[A-Za-z0-9_]{50,}", "[REDACTED:github_pat]"),
    # AWS access keys.
    (r"AKIA[A-Z0-9]{16}", "[REDACTED:aws]"),
    # JWT — three base64url segments separated by dots, header starts
    # with eyJ (base64 of {" ...).
    (r"eyJ[A-Za-z0-9_\-=]{10,}\.[A-Za-z0-9_\-=]{10,}\.[A-Za-z0-9_\-/+=]{20,}", "[REDACTED:jwt]"),
    # Slack tokens.
    (r"xox[baprs]-[A-Za-z0-9\-]{10,}", "[REDACTED:slack]"),
)


def _compile_scrub_patterns(extra: list[str] | None) -> list[tuple[re.Pattern[str], str]]:
    compiled = [(re.compile(p), repl) for p, repl in _DEFAULT_SCRUB_PATTERNS]
    for pat in extra or []:
        # Caller-supplied patterns get a generic replacement.
        compiled.append((re.compile(pat), "[REDACTED:custom]"))
    return compiled


def _scrub(text: str, patterns: list[tuple[re.Pattern[str], str]]) -> str:
    for regex, repl in patterns:
        text = regex.sub(repl, text)
    return text


# ---------------------------------------------------------------------------
# Event filters
# ---------------------------------------------------------------------------

# User messages to drop: system reminders, autonomous-loop sentinels.
_NOISE_USER_PATTERNS = (
    re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL),
    re.compile(r"<command-name>.*?</command-name>", re.DOTALL),
    re.compile(r"<<autonomous-loop[^>]*>>"),
)


def _strip_user_noise(text: str) -> str:
    """Remove system-reminder + autonomous-loop sentinels from a user
    message body. If the whole message is one of those tags, returns ""."""
    for pattern in _NOISE_USER_PATTERNS:
        text = pattern.sub("", text)
    return text.strip()


def _extract_user_text(event: dict[str, Any]) -> str:
    """Extract the human-relevant text from a ``type:user`` event.

    Handles both the simple string form (``content: "..."``) and the
    richer array form (``content: [{type: "text", text: "..."}, ...]``).
    Returns the empty string if the event is pure noise.
    """
    msg = event.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return _strip_user_noise(content)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(_strip_user_noise(block.get("text", "")))
            elif block.get("type") == "tool_result":
                # Tool result echoed in a user turn. Keep the first N
                # chars — often this is the diff the user pasted as
                # context.
                body = block.get("content") or ""
                if isinstance(body, list):
                    body = " ".join(
                        b.get("text", "") for b in body
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                parts.append(f"[tool result]: {str(body)[:200]}")
        return "\n".join(p for p in parts if p).strip()
    return ""


def _summarize_tool_input(inp: dict[str, Any] | None) -> str:
    """Render a tool-input dict as a short one-liner.

    Shows the first 2-3 keys + their values truncated. Keeps embeddings
    focused on WHAT the tool call was, not the full JSON payload.
    """
    if not inp:
        return ""
    parts = []
    for key in list(inp.keys())[:3]:
        value = inp[key]
        if isinstance(value, str):
            value = value.replace("\n", " ")[:80]
        else:
            value = str(value)[:80]
        parts.append(f"{key}={value}")
    return ", ".join(parts)


def _extract_assistant_text(event: dict[str, Any], max_tool_result_chars: int) -> str:
    """Extract human-relevant text from a ``type:assistant`` event.

    Iterates the message.content blocks:

    - ``text`` — verbatim
    - ``thinking`` — dropped entirely (cryptographic signatures, not
      retrievable text)
    - ``tool_use`` — summarized as ``[tool: Name(args)]``
    - ``tool_result`` — truncated
    """
    msg = event.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", "").strip())
        elif btype == "tool_use":
            name = block.get("name", "Tool")
            summary = _summarize_tool_input(block.get("input"))
            parts.append(f"[tool: {name}({summary})]")
        elif btype == "tool_result":
            body = block.get("content") or ""
            if isinstance(body, list):
                body = " ".join(
                    b.get("text", "") for b in body
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            body = str(body)[:max_tool_result_chars]
            parts.append(f"[tool result]: {body}")
        # thinking / image / etc. — dropped
    return "\n".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _resolve_projects_dir(config: dict[str, Any]) -> Path:
    """Resolve the ``.claude/projects`` root.

    Priority: ``config.claude_projects_dir`` > ``site_config.claude_projects_dir``
    > ``~/.claude/projects``. Same ordering as MemoryFilesTap so the two
    taps agree on which scopes exist.

    Reads site_config from the DI seam (config["_site_config"], seeded
    by the tap dispatcher per CLAUDE.md / glad-labs-stack#330).
    """
    override = config.get("claude_projects_dir")
    if override:
        return Path(override)
    sc = config.get("_site_config")
    if sc is not None:
        try:
            sc_val = sc.get("claude_projects_dir", "")
            if sc_val:
                return Path(sc_val)
        except Exception as exc:
            # poindexter#455 — used to be silent. If the operator pinned
            # a non-default claude_projects_dir, a site_config read
            # failure silently fell back to ~/.claude/projects and the
            # tap quietly ingested the wrong scope.
            logger.warning(
                "[claude_code_sessions] site_config.get('claude_projects_dir') "
                "failed (%s: %s) — falling back to ~/.claude/projects",
                type(exc).__name__, exc,
            )
    return Path.home() / ".claude" / "projects"


# ---------------------------------------------------------------------------
# Tap
# ---------------------------------------------------------------------------


class ClaudeCodeSessionsTap:
    """Ingest Claude Code session transcripts (.jsonl) as embeddings."""

    name = "claude_code_sessions"
    interval_seconds = 7200  # 2 hours

    async def extract(
        self,
        pool: Any,  # unused; filesystem Tap
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        del pool

        projects_dir = _resolve_projects_dir(config)
        if not projects_dir.is_dir():
            logger.warning(
                "ClaudeCodeSessionsTap: projects dir %s not found, skipping",
                projects_dir,
            )
            return

        max_sessions = int(config.get("max_sessions_per_run", 0) or 0)
        age_max_days = int(config.get("session_age_max_days", 0) or 0)
        max_tool_chars = int(config.get("max_tool_result_chars", 2000) or 2000)
        extra_patterns = config.get("extra_scrub_patterns") or []
        scrub_patterns = _compile_scrub_patterns(
            extra_patterns if isinstance(extra_patterns, list) else []
        )

        now_ts = time.time()
        age_cutoff = now_ts - (age_max_days * 86400) if age_max_days > 0 else 0.0

        emitted = 0
        skipped_age = 0
        skipped_empty = 0
        scanned = 0

        for scope_dir in sorted(projects_dir.iterdir()):
            if not scope_dir.is_dir():
                continue
            for session_file in sorted(scope_dir.glob("*.jsonl")):
                scanned += 1
                if max_sessions and emitted >= max_sessions:
                    logger.info(
                        "ClaudeCodeSessionsTap: hit max_sessions_per_run=%d",
                        max_sessions,
                    )
                    return

                stat = session_file.stat()
                if age_cutoff and stat.st_mtime < age_cutoff:
                    skipped_age += 1
                    continue

                text = _render_session(
                    session_file, max_tool_chars=max_tool_chars,
                )
                if not text:
                    skipped_empty += 1
                    continue

                text = _scrub(text, scrub_patterns)

                session_uuid = session_file.stem
                base_source_id = (
                    f"claude-code-sessions/{scope_dir.name}/{session_uuid}"
                )

                # Pre-chunk on USER:/ASSISTANT: boundaries so each yielded
                # Document fits inside nomic-embed-text's context window.
                # If a session is small enough, chunks == [text] so we
                # yield one Document; otherwise we yield multi-part docs
                # with ``/part-N`` suffixed source_ids.
                chunks = _split_session(text)
                total_parts = len(chunks)
                for part_num, chunk_text in enumerate(chunks, 1):
                    if total_parts > 1:
                        source_id = f"{base_source_id}/part-{part_num:03d}"
                    else:
                        source_id = base_source_id
                    yield Document(
                        source_id=source_id,
                        source_table="claude_sessions",
                        text=chunk_text,
                        metadata={
                            "session_uuid": session_uuid,
                            "scope": scope_dir.name,
                            "origin_path": str(session_file),
                            "file_size_bytes": stat.st_size,
                            "mtime_epoch": stat.st_mtime,
                            "chars": len(chunk_text),
                            "part": part_num,
                            "total_parts": total_parts,
                        },
                        writer="claude-code-sessions",
                    )
                emitted += 1

        logger.info(
            "ClaudeCodeSessionsTap: scanned=%d emitted=%d skipped_age=%d skipped_empty=%d",
            scanned, emitted, skipped_age, skipped_empty,
        )


# ---------------------------------------------------------------------------
# Session rendering
# ---------------------------------------------------------------------------


def _render_session(session_file: Path, max_tool_chars: int) -> str:
    """Parse a JSONL session file into a single cleaned transcript string.

    Streams the file line-by-line (sessions can hit 30+ MB) and only
    accumulates the filtered text. Bad JSON lines are logged + skipped;
    one malformed line should not skip the whole session.

    Returns the empty string if no meaningful content was extracted —
    caller uses that to decide whether to emit a Document.
    """
    parts: list[str] = []
    with session_file.open("r", encoding="utf-8", errors="replace") as fh:
        for line_num, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            # Narrow except: only JSON decode errors. Anything else
            # (IO failure, etc.) should surface to the caller.
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.debug(
                    "ClaudeCodeSessionsTap: bad JSON in %s line %d: %s",
                    session_file.name, line_num, e,
                )
                continue
            if not isinstance(event, dict):
                continue
            etype = event.get("type")
            if etype == "user":
                text = _extract_user_text(event)
                if text:
                    parts.append(f"USER: {text}")
            elif etype == "assistant":
                text = _extract_assistant_text(event, max_tool_chars)
                if text:
                    parts.append(f"ASSISTANT: {text}")
            # All other event types (file-history-snapshot, system,
            # tool_use toplevel, etc.) are ignored.

    if not parts:
        return ""
    return "\n\n".join(parts)


def _split_session(text: str, max_chars: int = _SESSION_CHUNK_CHARS) -> list[str]:
    """Split session text into chunks at turn boundaries.

    Splits preferentially on ``\\n\\nUSER:`` / ``\\n\\nASSISTANT:`` — those
    are the stable semantic breaks in a rendered session. If a single
    turn is larger than ``max_chars``, hard-slices that turn (rare —
    happens when a tool_result was huge and the operator didn't set
    ``max_tool_result_chars`` aggressively enough).

    Returns at least one chunk. A session short enough to fit in one
    embed call returns ``[text]`` unchanged.
    """
    if len(text) <= max_chars:
        return [text]

    # Split on turn boundaries. Keep the USER:/ASSISTANT: prefix on each
    # subsequent chunk for context preservation.
    # Matches the "\n\nUSER: " / "\n\nASSISTANT: " separator in _render_session.
    turns = re.split(r"(?=\n\nUSER: |\n\nASSISTANT: )", text)

    chunks: list[str] = []
    current = ""
    for turn in turns:
        if len(current) + len(turn) <= max_chars:
            current += turn
        else:
            if current:
                chunks.append(current.strip())
            # If this single turn still exceeds the limit, hard-slice it.
            if len(turn) > max_chars:
                for start in range(0, len(turn), max_chars):
                    chunks.append(turn[start:start + max_chars].strip())
                current = ""
            else:
                current = turn

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c]
