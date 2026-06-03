"""ClaudeCodeBridgeLLMService — Pipecat LLM stage backed by `claude -p`.

Subprocess-bridges every voice turn to the Claude Code CLI in headless
mode. No Anthropic API tokens are used — the CLI authenticates via the
operator's existing Claude Code Max OAuth session, so usage hits the
$200/mo subscription's 5-hour rate window rather than per-token billing.

## Why this beats wiring AnthropicLLMService directly

- Reuses Matt's Max sub (zero incremental cost vs. dollars per voice
  turn through ``AnthropicLLMService``)
- Same agent harness as his terminal Claude Code: file ops, MCP servers
  (Poindexter, Forgejo, Discord, Grafana, etc.), skills, slash commands
- Same project context (CLAUDE.md, memory dir) loaded automatically
  because the subprocess inherits cwd
- "Dev on the go" works literally — Matt can say "fix the bug in
  voice_agent.py line 200" and Claude actually reads the file, edits it,
  runs the tests, commits. From his phone.

## Conversation continuity

Each PipelineTask gets a fresh ``--session-id`` UUID at construction.
The first turn uses ``claude -p --session-id <uuid> "<text>"`` (creates
the session). Every subsequent turn uses ``claude -p --resume <uuid>
"<text>"`` to continue. The session persists on disk in
``~/.claude/projects/<cwd-hash>/``, so a dropped LiveKit connection
mid-call doesn't lose context — reconnect with the same session_id and
resume.

## Latency

Cold first turn: ~10s (loads CLAUDE.md, hydrates MCP server defs,
caches ~60k tokens).
Warm subsequent turns: ~3-8s for chat replies, 15-60s for turns that
trigger tool use. The voice surface should set Matt's expectations
accordingly — Emma's a snappy local-LLM mode for "what's the post
count"; the Claude brain is a "let me work on it" mode where you ask
something substantial and listen.

## What this does NOT do

- Does NOT bridge to Matt's *currently running* terminal Claude Code
  session. That session has its own UUID and conversation history; we
  intentionally start a fresh session-per-call so the voice bot doesn't
  pollute the dev's interactive context (and vice versa). If Matt wants
  the voice session to "see" his terminal session's work, he just asks
  Claude to read the relevant files / git log / memory files —
  everything is on disk and in the same MCP namespaces.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid as _uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)


# Pipecat imports gated so this module is collectable even on hosts that
# don't ship the runtime voice deps. Without the gate, the unit suite's
# collection phase crashed on every clean checkout where pipecat isn't
# installed (closes Glad-Labs/poindexter#509). At construction time
# (``ClaudeCodeBridgeLLMService.__init__``) the missing-pipecat case
# fails loud — voice-bridge needs pipecat to actually do anything.
try:
    from pipecat.frames.frames import (  # type: ignore[import-not-found]
        Frame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )
    from pipecat.processors.aggregators.llm_context import (  # type: ignore[import-not-found]
        LLMContext,
    )
    from pipecat.processors.frame_processor import (  # type: ignore[import-not-found]
        FrameDirection,
    )
    from pipecat.services.llm_service import LLMService  # type: ignore[import-not-found]

    _PIPECAT_AVAILABLE = True
    _PIPECAT_IMPORT_ERROR: ImportError | None = None
except ImportError as _exc:  # noqa: BLE001
    _PIPECAT_AVAILABLE = False
    _PIPECAT_IMPORT_ERROR = _exc

    # Provide a usable base class so the ``class
    # ClaudeCodeBridgeLLMService(LLMService)`` declaration below parses.
    # Any attempt to actually instantiate the service will fail loud in
    # ``__init__`` — collection-time imports succeed, runtime use does
    # NOT silently degrade.
    class LLMService:  # type: ignore[no-redef]
        """Placeholder used only when pipecat isn't installed."""

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise ImportError(
                "pipecat is required to instantiate the Claude-Code voice "
                "bridge. Install poindexter with the [voice] extra or set "
                "voice_bridge_enabled=false in app_settings."
            ) from _PIPECAT_IMPORT_ERROR

    # Stub the symbols referenced at module scope so ``from
    # voice_agent_claude_code import X`` doesn't NameError before
    # __init__ has a chance to raise.
    Frame = None  # type: ignore[assignment,misc]
    LLMFullResponseEndFrame = None  # type: ignore[assignment,misc]
    LLMFullResponseStartFrame = None  # type: ignore[assignment,misc]
    LLMTextFrame = None  # type: ignore[assignment,misc]
    LLMContext = None  # type: ignore[assignment,misc]
    FrameDirection = None  # type: ignore[assignment,misc]


# Cap how long we'll wait for `claude -p` to return before declaring a
# stuck turn. Most chat-only replies finish well under this; anything
# longer probably means tool use is in flight (still OK, but the human
# will start to wonder whether the voice bot is dead).
_TURN_TIMEOUT_SECONDS = 90.0


# Matches claude CLI's stderr when --session-id targets a UUID whose
# JSONL already exists on disk: "Error: Session ID <uuid> is already in
# use." Whatever spawned the session (pipecat warmup probe, healthcheck,
# crash-recovery from a prior bot) wrote the JSONL before the user's
# first audio turn — at this point claude refuses --session-id but
# happily accepts --resume on the same UUID. See poindexter#431.
_SESSION_ALREADY_IN_USE = re.compile(r"already in use", re.IGNORECASE)


# The mirror of #431: claude's stderr when --resume targets a UUID that has
# no JSONL on disk — "No conversation found with session ID: <uuid>". This
# happens on the FIRST deploy of a pinned session (run_bot mints + persists a
# fresh UUID, then asks the service to --resume it before it's been created),
# and whenever a previously-pinned session's on-disk JSONL gets cleaned up
# (claude prunes ~/.claude/projects/ by cleanupPeriodDays). In both cases the
# fix is the same: create the session with that same id (flip --resume ->
# --session-id) and keep the pinned id stable. See #1006.
_NO_CONVERSATION_FOUND = re.compile(
    r"no conversation found", re.IGNORECASE,
)


# Manual session-rotation phrases the operator can speak to start a fresh
# claude -p session mid-call (#1006). Matched against the user turn at the
# top of the turn handler so the rest of that same turn lands in the new
# session. Kept liberal: "start fresh", "new session", and
# "reset (the) session/conversation".
_MANUAL_RESET_PHRASE = re.compile(
    r"^\s*(start fresh|new session|reset (the )?(session|conversation))\b",
    re.IGNORECASE,
)


class ClaudeCodeBridgeLLMService(LLMService):
    """Replaces the model-API LLM stage with a subprocess to the Claude CLI.

    Receives an :class:`LLMContextFrame` per user turn (via the standard
    Pipecat LLM-aggregator wiring). Pulls the latest user message, runs
    ``claude -p`` with the session-resume trick, and pushes the reply
    back as a single ``LLMTextFrame`` (sandwiched between
    ``LLMFullResponseStartFrame`` and ``LLMFullResponseEndFrame`` so the
    downstream TTS knows when to start synthesis and when to flush).
    """

    def __init__(
        self,
        *,
        cwd: str | None = None,
        claude_binary: str = "claude",
        session_id: str | None = None,
        permission_mode: str = "dontAsk",
        extra_args: list[str] | None = None,
        token_budget: int | None = None,
        max_age_seconds: int | None = None,
        persist_session_id: "Callable[[str], Any] | None" = None,
        monotonic: "Callable[[], float]" = time.monotonic,
        **kwargs,
    ):
        """
        Args:
            cwd: Directory to run ``claude`` in. Defaults to the current
                process cwd. Determines which project's CLAUDE.md the CLI
                loads.
            claude_binary: Path to the claude CLI. Defaults to whatever's
                on PATH.
            session_id: Force a specific session UUID. Defaults to a
                fresh random one (the bot's pipeline lifetime).
            permission_mode: Passed to ``--permission-mode``. Default
                ``dontAsk`` so the bot can run tool calls without
                prompting the operator (who isn't watching a terminal).
            extra_args: Additional CLI flags appended to every invocation.
                Example: ``["--allowed-tools", "Read,Bash,Edit"]``.
            token_budget: Rotate the pinned session once cumulative
                input+output tokens exceed this. ``None`` disables the
                token-based rotation (#1006).
            max_age_seconds: Rotate the pinned session once it's older
                than this many seconds. ``None`` disables the age-based
                rotation (#1006).
            persist_session_id: Async callable invoked with the freshly
                minted UUID whenever the session rotates, so the new
                pinned id survives a container restart. ``None`` = don't
                persist (e.g. tests, ad-hoc invocations). (#1006)
            monotonic: Injectable monotonic clock. Defaults to
                :func:`time.monotonic`; tests pass a fake for
                deterministic age-trip assertions (#1006).
        """
        super().__init__(**kwargs)
        self._cwd = cwd or os.getcwd()
        self._claude_binary = claude_binary
        # If the operator supplied a session_id (e.g. via env var to resume
        # a prior bot's conversation), treat the very first turn as a RESUME
        # not a CREATE — claude refuses --session-id on an already-existing
        # session with "session is already in use". Auto-generated uuids are
        # always fresh, so they CREATE on first turn.
        if session_id:
            self._session_id = session_id
            self._first_turn = False
            self._resumed = True
        else:
            self._session_id = str(_uuid.uuid4())
            self._first_turn = True
            self._resumed = False
        self._permission_mode = permission_mode
        self._extra_args = list(extra_args or [])
        # Auto-reset config (#1006). The session rotates when it ages out,
        # burns through its token budget, or the operator says a manual
        # phrase — see _maybe_reset.
        self._token_budget = token_budget
        self._max_age_seconds = max_age_seconds
        self._persist_session_id = persist_session_id
        self._monotonic = monotonic
        self._session_started = monotonic()
        self._cumulative_tokens = 0
        logger.info(
            "ClaudeCodeBridgeLLMService init session_id=%s resumed=%s cwd=%s "
            "permission=%s token_budget=%s max_age_seconds=%s",
            self._session_id, self._resumed, self._cwd, self._permission_mode,
            self._token_budget, self._max_age_seconds,
        )

    # ------------------------------------------------------------------
    # Pipecat plumbing
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # Pipecat 1.1 names: ``LLMContextFrame`` is the canonical
        # universal context-carrying frame. Imported lazily so older
        # Pipecat releases that lack it don't break import.
        from pipecat.frames.frames import LLMContextFrame

        if isinstance(frame, LLMContextFrame):
            await self._process_context(frame.context)
        else:
            await self.push_frame(frame, direction)

    async def _process_context(self, context: LLMContext) -> None:
        user_text = self._latest_user_text(context)
        if not user_text:
            logger.debug("Empty user turn — nothing to send to Claude")
            return

        # Rotate the pinned session BEFORE the send if it aged out, blew its
        # token budget, or the user spoke a manual-reset phrase (#1006). Done
        # at the top of the turn so a manual "start fresh" lands this very
        # turn in the new session.
        await self._maybe_reset(user_text)

        await self.push_frame(LLMFullResponseStartFrame())
        try:
            reply = await self._run_claude(user_text)
        except Exception as e:  # noqa: BLE001
            logger.exception("Claude bridge failed")
            reply = (
                "Sorry, I had trouble talking to Claude Code. "
                f"The bridge reported: {e}."
            )
        if reply:
            spoken = _strip_markdown_for_speech(reply)
            await self.push_frame(LLMTextFrame(spoken))
            # Best-effort transcript mirror to Telegram. Fire-and-forget;
            # never let a Telegram failure interrupt the audio path.
            try:
                await _push_transcript_to_telegram(user_text, reply)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Telegram transcript push failed (audio unaffected): %s: %s",
                    type(e).__name__, e,
                )
        await self.push_frame(LLMFullResponseEndFrame())

    # ------------------------------------------------------------------
    # Auto-reset (#1006)
    # ------------------------------------------------------------------

    async def _maybe_reset(self, user_text: str) -> None:
        """Rotate the pinned ``claude -p`` session when it should roll over.

        Mints a fresh UUID, flips back to first-turn CREATE state, resets the
        age/token counters, logs the reason, and (if configured) awaits the
        persist callback so the new id survives a container restart. Rotates
        when ANY of:

        * the session is older than ``max_age_seconds`` (measured by the
          injected ``monotonic`` clock);
        * cumulative input+output tokens have exceeded ``token_budget``;
        * the user text matches a manual-reset phrase.
        """
        reason: str | None = None
        if (
            self._max_age_seconds is not None
            and self._monotonic() - self._session_started >= self._max_age_seconds
        ):
            reason = (
                f"age {self._monotonic() - self._session_started:.0f}s "
                f">= max_age_seconds {self._max_age_seconds}"
            )
        elif (
            self._token_budget is not None
            and self._cumulative_tokens > self._token_budget
        ):
            reason = (
                f"cumulative_tokens {self._cumulative_tokens} "
                f"> token_budget {self._token_budget}"
            )
        elif _MANUAL_RESET_PHRASE.match(user_text or ""):
            reason = "manual reset phrase"

        if reason is None:
            return

        old_id = self._session_id
        new_id = str(_uuid.uuid4())
        self._session_id = new_id
        self._first_turn = True
        self._resumed = False
        self._session_started = self._monotonic()
        self._cumulative_tokens = 0
        logger.info(
            "voice-agent: rotating claude session %s -> %s (reason: %s) (#1006)",
            old_id, new_id, reason,
        )
        if self._persist_session_id is not None:
            await self._persist_session_id(new_id)

    # ------------------------------------------------------------------
    # Subprocess driver
    # ------------------------------------------------------------------

    def _build_argv(self) -> list[str]:
        argv = [
            self._claude_binary,
            "-p",
            "--output-format", "json",
            "--permission-mode", self._permission_mode,
        ]
        if self._first_turn:
            argv += ["--session-id", self._session_id]
        else:
            argv += ["--resume", self._session_id]
        argv += list(self._extra_args)
        return argv

    async def _run_claude(self, user_text: str) -> str:
        stdout_bytes = await self._spawn_claude(user_text)
        # First turn establishes the session — every subsequent turn
        # must use --resume.
        self._first_turn = False
        # Accumulate this turn's token spend so _maybe_reset can roll the
        # session over once it crosses the budget (#1006).
        self._cumulative_tokens += self._extract_usage(stdout_bytes)
        return self._extract_text(stdout_bytes)

    async def _spawn_claude(self, user_text: str, *, _recovered: bool = False) -> bytes:
        """Run `claude -p` once; recover from a session create/resume mismatch.

        Split off of :meth:`_run_claude` so the session-recovery path can
        re-enter the same exec without re-decoding the prior call's stdout.
        Two symmetric recoveries, each attempted at most once (``_recovered``
        guards against ping-ponging between them):

        * #431 — a first-turn ``--session-id`` hits an already-existing JSONL
          ("already in use") -> flip to ``--resume``.
        * #1006 — a ``--resume`` hits a missing JSONL ("no conversation
          found": a freshly-minted pinned id on first deploy, or a pinned id
          whose JSONL was pruned) -> flip to ``--session-id`` (create), so the
          pinned id stays stable.
        """
        argv = self._build_argv()
        logger.info(
            "claude turn (first=%s session=%s) cmd=%s",
            self._first_turn, self._session_id, " ".join(argv[:6]) + " ...",
        )
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=user_text.encode("utf-8")),
                timeout=_TURN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"claude -p timed out after {_TURN_TIMEOUT_SECONDS}s",
            ) from None

        if proc.returncode != 0:
            stderr_txt = stderr_bytes.decode("utf-8", errors="replace").strip()
            if (
                not _recovered
                and self._first_turn
                and _SESSION_ALREADY_IN_USE.search(stderr_txt)
            ):
                # poindexter#431 — something spawned claude with this
                # UUID before the user's first turn (pipecat warmup,
                # healthcheck, restarted bot reusing a UUID). Flip to
                # --resume and retry once; the JSONL on disk IS the
                # session we want to continue.
                logger.warning(
                    "voice-agent: session %s already exists on disk "
                    "before first user turn; retrying with --resume "
                    "(poindexter#431). claude stderr=%r",
                    self._session_id, stderr_txt[:200],
                )
                self._first_turn = False
                self._resumed = True
                return await self._spawn_claude(user_text, _recovered=True)
            if (
                not _recovered
                and not self._first_turn
                and _NO_CONVERSATION_FOUND.search(stderr_txt)
            ):
                # #1006 — we asked claude to --resume a pinned session whose
                # JSONL isn't on disk: either a freshly-minted pinned id on
                # its first-ever turn (run_bot persisted the id but never
                # created the session), or a pinned id whose JSONL was pruned.
                # Create it with the SAME id (flip to --session-id) so the pin
                # stays stable and continuity resumes from here.
                logger.warning(
                    "voice-agent: session %s has no conversation on disk; "
                    "creating it with --session-id (the pin stays stable, "
                    "#1006). claude stderr=%r",
                    self._session_id, stderr_txt[:200],
                )
                self._first_turn = True
                self._resumed = False
                return await self._spawn_claude(user_text, _recovered=True)
            raise RuntimeError(
                f"claude -p exited {proc.returncode}: {stderr_txt[:300]}",
            )

        return stdout_bytes

    @staticmethod
    def _extract_text(stdout_bytes: bytes) -> str:
        """``--output-format json`` returns a JSON object (or array) with
        a top-level ``result`` field that holds the assistant's reply.

        Some claude versions emit an array of events ending in a
        ``{"type":"result"}`` object; others return a plain object. Be
        liberal in what we accept.
        """
        text = stdout_bytes.decode("utf-8", errors="replace").strip()
        if not text:
            return ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Fallback — return the raw stdout. Better something than nothing.
            return text

        def _find_result(obj: Any) -> str | None:
            if isinstance(obj, dict):
                if obj.get("type") == "result" and isinstance(obj.get("result"), str):
                    return obj["result"]
                if isinstance(obj.get("result"), str):
                    return obj["result"]
            return None

        if isinstance(parsed, list):
            # Walk events end-to-start so we get the final result.
            for event in reversed(parsed):
                hit = _find_result(event)
                if hit:
                    return hit
            # No result event — concatenate any assistant text we find.
            chunks: list[str] = []
            for event in parsed:
                if (
                    isinstance(event, dict)
                    and event.get("type") == "assistant"
                    and isinstance(event.get("message"), dict)
                ):
                    for block in event["message"].get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            chunks.append(block.get("text", ""))
            return " ".join(c for c in chunks if c).strip()

        if isinstance(parsed, dict):
            hit = _find_result(parsed)
            if hit:
                return hit

        return text

    @staticmethod
    def _extract_usage(stdout_bytes: bytes) -> int:
        """Pull ``input_tokens + output_tokens`` from the result event's
        ``usage`` block, for cumulative token accounting (#1006).

        The ``--output-format json`` result element looks like
        ``{"type":"result","result":"...","usage":{"input_tokens":N,
        "output_tokens":N,...}}``. Returns 0 when the payload is
        unparseable or carries no usage block — never raises, so a turn
        with a weird payload doesn't crash the audio path.
        """
        text = stdout_bytes.decode("utf-8", errors="replace").strip()
        if not text:
            return 0
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return 0

        def _usage_tokens(obj: Any) -> int | None:
            if not isinstance(obj, dict):
                return None
            usage = obj.get("usage")
            if not isinstance(usage, dict):
                return None
            in_tok = usage.get("input_tokens", 0) or 0
            out_tok = usage.get("output_tokens", 0) or 0
            try:
                return int(in_tok) + int(out_tok)
            except (TypeError, ValueError):
                return None

        candidates: list[dict[str, Any]] = []
        if isinstance(parsed, list):
            candidates = [e for e in reversed(parsed) if isinstance(e, dict)]
        elif isinstance(parsed, dict):
            candidates = [parsed]

        for event in candidates:
            tokens = _usage_tokens(event)
            if tokens is not None:
                return tokens
        return 0

    @staticmethod
    def _latest_user_text(context: LLMContext) -> str:  # noqa: D401
        # (definition follows; the helper below is a module-level
        # function intentionally outside the class so it's reusable.)
        return _latest_user_text_impl(context)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


_MD_FENCED_CODE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_BOLD_ITALIC = re.compile(r"(\*{1,3})([^*\n]+?)\1")
_MD_UNDERSCORE_EMPH = re.compile(r"(?<!\w)(_{1,3})([^_\n]+?)\1(?!\w)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_NUMBERED = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_MD_HORIZONTAL_RULE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)
_MD_BLOCKQUOTE = re.compile(r"^>\s*", re.MULTILINE)
_MD_TABLE_PIPE = re.compile(r"\s*\|\s*")
_WHITESPACE_RUN = re.compile(r"[ \t]+")
_NEWLINE_RUN = re.compile(r"\n{3,}")

# Path-shaped tokens (`a/b/c.py`, `feat/oauth-phase-1`) — Kokoro reads
# every `/` as the literal word "slash" which is brutal in dev contexts.
# Replace with a space so "mcp-server/server.py" → "mcp-server server.py".
_PATH_LIKE = re.compile(r"(?<=\S)/(?=\S)")
# CLI flags: `--brain claude-code` → "brain claude code" (no "dash dash"
# chant). Long form first, then short.
_LONG_FLAG = re.compile(r"(?<![\w-])--([a-zA-Z][\w-]*)")
_SHORT_FLAG = re.compile(r"(?<![\w-])-([a-zA-Z])(?![\w-])")


def _strip_markdown_for_speech(text: str) -> str:
    """Convert markdown-formatted text into something a TTS won't choke on.

    Claude leaks markdown into voice responses despite a "no markdown"
    system prompt — even more often when summarising tool output. We
    sanitise here so the user doesn't hear "asterisk asterisk" or
    "open bracket close bracket open paren". The transformation is
    lossy by design (the spoken form omits link URLs, code blocks
    become a single "code block" placeholder, etc.) — TTS is the
    consumer, not a debugger.
    """
    # Drop fenced code entirely — reading source code aloud is useless.
    text = _MD_FENCED_CODE.sub(" (code block omitted) ", text)
    # Inline code → bare content
    text = _MD_INLINE_CODE.sub(r"\1", text)
    # Links: keep label, drop URL
    text = _MD_LINK.sub(r"\1", text)
    # Emphasis / strong → bare content (works for *, **, ***)
    for _ in range(3):
        text = _MD_BOLD_ITALIC.sub(r"\2", text)
        text = _MD_UNDERSCORE_EMPH.sub(r"\2", text)
    # Headers / bullets / numbered / blockquotes / hrules → strip leader
    text = _MD_HEADER.sub("", text)
    text = _MD_BULLET.sub("", text)
    text = _MD_NUMBERED.sub("", text)
    text = _MD_HORIZONTAL_RULE.sub("", text)
    text = _MD_BLOCKQUOTE.sub("", text)
    # Tables look terrible spoken. Replace pipes with commas so each
    # cell at least gets a beat of pause.
    text = _MD_TABLE_PIPE.sub(", ", text)
    # Path-like tokens: replace `/` between non-space chars with a space.
    text = _PATH_LIKE.sub(" ", text)
    # CLI flags: drop the leading dashes.
    text = _LONG_FLAG.sub(lambda m: m.group(1).replace("-", " "), text)
    text = _SHORT_FLAG.sub(r"\1", text)
    # Collapse whitespace runs
    text = _WHITESPACE_RUN.sub(" ", text)
    text = _NEWLINE_RUN.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Telegram transcript mirror — pushes each turn (user → reply) to Matt's
# Telegram chat so he can scan a written transcript while listening.
# Bot token is stored encrypted in app_settings; chat_id is plaintext.
# ---------------------------------------------------------------------------


_TG_BOT_TOKEN_CACHE: str | None = None
_TG_CHAT_ID_CACHE: str | None = None


async def _telegram_creds() -> tuple[str, str] | None:
    """Load + cache the Telegram transcript-push credentials.

    Reads ``telegram_bot_token`` (encrypted) and ``telegram_chat_id``
    (plaintext) from app_settings via the centralized
    ``SiteConfig.get_secret`` path. That delegates decryption to
    ``plugins.secrets.get_secret``, which is the single owner of the
    ``enc:v1:`` Fernet handling — inlined decryption here was a sharp
    edge that could drift from the canonical scheme. The voice agent's
    bot-startup path can absorb the extra ~5ms of the get_secret call
    (one DB round-trip per cred, after which both are cached on this
    module).
    """
    global _TG_BOT_TOKEN_CACHE, _TG_CHAT_ID_CACHE
    if _TG_BOT_TOKEN_CACHE and _TG_CHAT_ID_CACHE:
        return _TG_BOT_TOKEN_CACHE, _TG_CHAT_ID_CACHE

    import asyncpg
    from brain.bootstrap import resolve_database_url
    from services.site_config import SiteConfig

    db_url = resolve_database_url()
    if not db_url:
        logger.warning(
            "Telegram transcript: no DATABASE_URL resolvable; "
            "transcript push disabled.",
        )
        return None

    # Pool sized at min=1/max=1 because get_secret runs at most twice on
    # this path and the bot never re-uses the pool elsewhere.
    pool = await asyncpg.create_pool(
        db_url, min_size=1, max_size=1, timeout=2.0, command_timeout=5.0,
    )
    try:
        site_config = SiteConfig(pool=pool)
        telegram_bot_token = await site_config.get_secret(
            "telegram_bot_token", "",
        )
        telegram_chat_id = await site_config.get_secret(
            "telegram_chat_id", "",
        )

        if not telegram_bot_token or not telegram_chat_id:
            logger.warning(
                "Telegram transcript: missing creds "
                "(token_present=%s, chat_id_present=%s). "
                "Transcript push disabled.",
                bool(telegram_bot_token), bool(telegram_chat_id),
            )
            return None

        _TG_BOT_TOKEN_CACHE = str(telegram_bot_token)
        _TG_CHAT_ID_CACHE = str(telegram_chat_id)
        logger.info(
            "Telegram transcript: creds loaded for chat_id=%s",
            _TG_CHAT_ID_CACHE,
        )
        return _TG_BOT_TOKEN_CACHE, _TG_CHAT_ID_CACHE
    finally:
        await pool.close()


async def _push_transcript_to_telegram(user_text: str, reply: str) -> None:
    creds = await _telegram_creds()
    if not creds:
        return
    token, chat_id = creds
    import httpx

    user_block = user_text if len(user_text) <= 800 else user_text[:780] + "…"
    reply_block = reply if len(reply) <= 3000 else reply[:2900] + "…"
    # HTML mode — only <, >, & need escaping. MarkdownV2's "every period
    # must be backslash-escaped" rule was a footgun; HTML is the calm path.
    body = (
        "🎙 <b>You</b>\n"
        f"<i>{_html_escape(user_block)}</i>\n\n"
        "🤖 <b>Claude</b>\n"
        f"{_html_escape(reply_block)}"
    )
    async with httpx.AsyncClient(timeout=4.0) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": body,
                "parse_mode": "HTML",
                "disable_notification": True,  # don't ping the phone for every turn
            },
        )
        if resp.status_code >= 400:
            logger.warning(
                "Telegram transcript: sendMessage returned %s — body: %s",
                resp.status_code, resp.text[:300],
            )


def _html_escape(text: str) -> str:
    """Escape the three chars Telegram's HTML parser cares about."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _latest_user_text_impl(context: LLMContext) -> str:
        """Pull the most-recent user message from the context.

        Pipecat keeps the running history in the LLMContext; we only
        forward the last user turn to Claude (Claude maintains its own
        full conversation state via --resume, so re-sending the history
        would double-count).
        """
        messages = list(context.get_messages())
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    parts = [
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    return " ".join(p for p in parts if p).strip()
        return ""
