"""
Telegram ``/cli`` passthrough — run ``poindexter`` CLI from your phone.

Matt's pain point: routine ops (approve a draft, check status, flip a
setting) currently require sitting at the PC. The Telegram bot already
has a few hand-rolled commands (/health, /approve, /reject, ...) but
each one needs its own handler — adding a new ops surface means a code
change. This module is the escape hatch: anything the local
``poindexter`` CLI can do, you can do over Telegram by typing
``/cli <args>``.

Wiring
------

The Telegram bot (``scripts/telegram-bot.py``) calls
``handle_cli_message(text, chat_id, ...)`` for every incoming message.
If the message doesn't start with ``/cli ``, we return ``None`` and the
bot falls through to its existing slash-command handlers. If it does,
we:

1. **Auth** — confirm ``chat_id`` matches the configured operator chat
   (``telegram_chat_id``). Unknown chat IDs get **silent reject**: we
   return ``None`` so the bot doesn't even acknowledge ``/cli`` exists.
2. **Kill-switch** — if ``telegram_cli_enabled`` is ``"false"``, reply
   with a single line and stop.
3. **Allowlist gate** — first whitespace token after ``/cli`` must be
   in ``telegram_cli_safe_commands``. Any of the hard-deny tokens
   (``rm``, ``drop``, ``delete``, ``truncate``, ``--force``, ``mcp``)
   anywhere in the args = immediate reject. Destructive secret writes
   (``settings set <key>`` for known secret keys) are rejected.
4. **Subprocess** — ``python -m poindexter.cli <args>`` with cwd set to
   the package root and a wall-clock timeout. We capture stdout +
   stderr together (text mode, utf-8, replace decode errors).
5. **Format reply** — header line ``exit=N duration=X.Ys`` then the
   captured output, truncated to ``telegram_cli_max_output_chars``.
6. **Audit log** — one row, source ``telegram_cli``, with chat_id,
   sanitized command, exit code, and duration.

Design notes
------------

- We do NOT import the CLI in-process. Subprocess isolation gives us a
  hard timeout (process kill), prevents the CLI from clobbering shared
  module state in the worker, and matches the "what you'd run at the
  shell" mental model.
- ``site_config`` is dependency-injected — no module-level singleton
  reads. Callers from inside the worker get the canonical instance via
  ``app.state.site_config``; the standalone bot script builds its own.
- All logging goes through a private logger; we never echo the bot
  token or any secret. The audit row includes the raw command line —
  if you put a secret on the CLI, it WILL be logged. (CLI doesn't
  accept secrets as args today; add a redactor here if it ever does.)
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Trigger token. Must appear at start of message followed by whitespace.
_PREFIX = "/cli"

# Hard-deny tokens. If any of these appear (as whole words) in the
# tokenized command line, the request is rejected outright — even if
# the top-level subcommand is allowlisted. Defense-in-depth against an
# allowlisted command shelling out to a destructive operation.
_DENY_TOKENS = frozenset({
    "rm",
    "drop",
    "delete",
    "truncate",
    "--force",
    "-f",       # legacy short force flag
    "mcp",      # the meta surface — explicitly out-of-scope
})

# Settings keys that, if targeted by ``settings set <key> ...``, should
# be refused. These are credentials/secrets where a typo on a phone
# could lock the system out. Keep conservative.
_SECRET_KEY_PATTERNS = (
    "_token",
    "_secret",
    "_password",
    "_api_key",
    "_apikey",
    "database_url",
    "dsn",
)

# Default fallbacks, used only if site_config is missing the key.
# Mirror the migration 0136 seed values so behavior stays identical
# whether the migration has run yet or not.
_DEFAULT_SAFE_COMMANDS = (
    "post,settings,validators,auth,check_health,"
    "get_post_count,health,version"
)
_DEFAULT_MAX_OUTPUT = 3500
_DEFAULT_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliReply:
    """The bot should send ``text`` back to ``chat_id``."""

    text: str
    handled: bool = True


@dataclass(frozen=True)
class _Decision:
    """Internal: outcome of the safety/auth checks before subprocess run."""

    proceed: bool
    args: tuple[str, ...] = ()
    deny_reason: str = ""
    silent: bool = False  # True = drop without replying


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def handle_cli_message(
    text: str,
    chat_id: str,
    *,
    site_config: Any,
    audit_logger: Any | None = None,
    runner: Optional[Callable[[list[str], int], Awaitable["_RunResult"]]] = None,
) -> Optional[CliReply]:
    """Process one incoming Telegram message; return a reply or None.

    Args:
        text: Raw message body. We only act on messages starting with
            ``/cli`` followed by whitespace.
        chat_id: The Telegram chat id the message came from. Matched
            against ``site_config.get('telegram_chat_id')`` for auth.
        site_config: A ``SiteConfig`` instance (DI seam). We read the
            five ``telegram_cli_*`` keys plus ``telegram_chat_id``.
        audit_logger: Optional ``AuditLogger`` instance (or anything
            with a ``log(event_type, source, details, severity)``
            coroutine). When provided + ``telegram_cli_audit_logged``
            is true, every invocation writes one row.
        runner: Optional injectable subprocess runner — primarily for
            tests. Default uses ``_run_cli_subprocess``.

    Returns:
        ``CliReply`` to send back, or ``None`` if the message wasn't a
        ``/cli`` command (or if it was from an unauthorized chat — we
        reject silently to avoid leaking the surface).
    """
    if not _is_cli_message(text):
        return None

    decision = await _evaluate(text, chat_id, site_config)
    if decision.silent:
        # Unauthorized / disabled / not-actually-/cli — no reply, no audit.
        return None

    if not decision.proceed:
        await _audit(
            audit_logger,
            site_config,
            event_type="telegram_cli_denied",
            chat_id=chat_id,
            command=_safe_command_line(text),
            extra={"reason": decision.deny_reason},
            severity="warning",
        )
        return CliReply(text=_format_denial(decision.deny_reason))

    timeout = _int_setting(site_config, "telegram_cli_timeout_seconds", _DEFAULT_TIMEOUT)
    max_chars = _int_setting(site_config, "telegram_cli_max_output_chars", _DEFAULT_MAX_OUTPUT)

    runner = runner or _run_cli_subprocess
    started = time.monotonic()
    try:
        result = await runner(list(decision.args), timeout)
    except Exception as exc:  # pragma: no cover — defensive
        duration = time.monotonic() - started
        logger.exception("telegram_cli: subprocess crashed: %s", exc)
        await _audit(
            audit_logger,
            site_config,
            event_type="telegram_cli_error",
            chat_id=chat_id,
            command=_safe_command_line(text),
            extra={"error": str(exc), "duration_s": round(duration, 2)},
            severity="error",
        )
        return CliReply(text=f"command failed: {exc}")

    await _audit(
        audit_logger,
        site_config,
        event_type="telegram_cli_invoked",
        chat_id=chat_id,
        command=_safe_command_line(text),
        extra={
            "exit_code": result.exit_code,
            "duration_s": round(result.duration_s, 2),
            "timed_out": result.timed_out,
            "output_chars": len(result.output),
        },
        severity="info" if result.exit_code == 0 and not result.timed_out else "warning",
    )

    return CliReply(text=_format_reply(result, max_chars=max_chars))


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------


def _is_cli_message(text: str) -> bool:
    """True if ``text`` starts with the ``/cli`` trigger followed by whitespace.

    A bare ``/cli`` (no args) is NOT routed — we don't want to reply to
    every casual mention of the word.
    """
    if not text or not text.startswith(_PREFIX):
        return False
    rest = text[len(_PREFIX):]
    return bool(rest) and rest[0].isspace() and rest.strip() != ""


async def _evaluate(text: str, chat_id: str, site_config: Any) -> _Decision:
    """Run all gates: auth → kill-switch → allowlist → deny tokens → secrets."""

    # 1. Auth — must match the configured operator chat.
    expected_chat = (site_config.get("telegram_chat_id", "") or "").strip()
    if not expected_chat or str(chat_id).strip() != expected_chat:
        # Silent reject: don't leak that /cli is a surface.
        return _Decision(proceed=False, silent=True)

    # 2. Kill-switch.
    if not _bool_setting(site_config, "telegram_cli_enabled", True):
        return _Decision(
            proceed=False,
            deny_reason="/cli passthrough is disabled (telegram_cli_enabled=false)",
        )

    # 3. Tokenize.
    raw = text[len(_PREFIX):].strip()
    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError as exc:
        return _Decision(proceed=False, deny_reason=f"could not parse args: {exc}")

    if not tokens:
        return _Decision(proceed=False, deny_reason="usage: /cli <subcommand> [args]")

    # 4. Hard-deny tokens — anywhere in the command line.
    for tok in tokens:
        if tok.lower() in _DENY_TOKENS:
            return _Decision(
                proceed=False,
                deny_reason=f"token {tok!r} is on the deny list",
            )

    # 5. Allowlist gate — first token must be a known safe subcommand.
    safe = _csv_setting(site_config, "telegram_cli_safe_commands", _DEFAULT_SAFE_COMMANDS)
    top = tokens[0].lower()
    if top not in safe:
        return _Decision(
            proceed=False,
            deny_reason=(
                f"subcommand {top!r} not on the safe list. "
                f"Allowed: {', '.join(sorted(safe))}"
            ),
        )

    # 6. Secret-write guard: refuse `settings set <secret-ish-key> ...`.
    if top == "settings" and len(tokens) >= 3 and tokens[1].lower() == "set":
        target_key = tokens[2].lower()
        if any(pat in target_key for pat in _SECRET_KEY_PATTERNS):
            return _Decision(
                proceed=False,
                deny_reason=(
                    f"refusing to set secret-ish key {target_key!r} from Telegram. "
                    "Use the worker shell or `poindexter setup` instead."
                ),
            )

    return _Decision(proceed=True, args=tuple(tokens))


# ---------------------------------------------------------------------------
# Subprocess runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RunResult:
    exit_code: int
    output: str       # combined stdout + stderr
    duration_s: float
    timed_out: bool


async def _run_cli_subprocess(args: list[str], timeout_s: int) -> _RunResult:
    """Run ``python -m poindexter.cli <args>``, return combined output.

    On timeout we kill the process group (best-effort across platforms)
    and return ``timed_out=True`` with whatever was captured so far.
    """
    cmd = [sys.executable, "-m", "poindexter.cli", *args]
    started = time.monotonic()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # merge for simpler capture
    )

    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        timed_out = False
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
        except asyncio.TimeoutError:
            stdout_bytes = b""

    duration = time.monotonic() - started
    output = (stdout_bytes or b"").decode("utf-8", errors="replace")
    exit_code = proc.returncode if proc.returncode is not None else -1
    return _RunResult(
        exit_code=exit_code,
        output=output,
        duration_s=duration,
        timed_out=timed_out,
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_denial(reason: str) -> str:
    return f"/cli denied: {reason}"


def _format_reply(result: _RunResult, *, max_chars: int) -> str:
    """Compose the reply text — header line + (truncated) output."""
    if result.timed_out:
        header = (
            f"/cli timed out after {result.duration_s:.1f}s "
            f"(limit configured via telegram_cli_timeout_seconds)"
        )
    else:
        header = f"/cli exit={result.exit_code} duration={result.duration_s:.1f}s"

    body = result.output.strip() or "(no output)"
    # Reserve some characters for the header + newline + truncation marker.
    cap = max(0, max_chars - len(header) - 64)
    if len(body) > cap:
        truncated_chars = len(body) - cap
        body = body[:cap] + f"\n[... output truncated, {truncated_chars} more chars]"

    return f"{header}\n{body}"


def _safe_command_line(text: str) -> str:
    """Strip the ``/cli`` prefix; never log the bot token or chat secrets.

    We don't currently inject any secret into the CLI args, so the raw
    args are safe to log. If that ever changes, redact here.
    """
    rest = text[len(_PREFIX):].strip() if text.startswith(_PREFIX) else text
    return rest[:500]  # cap audit-row size


# ---------------------------------------------------------------------------
# site_config helpers (string-typed app_settings → typed values)
# ---------------------------------------------------------------------------


def _bool_setting(site_config: Any, key: str, default: bool) -> bool:
    raw = site_config.get(key, "true" if default else "false")
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


def _int_setting(site_config: Any, key: str, default: int) -> int:
    raw = site_config.get(key, str(default))
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return default


def _csv_setting(site_config: Any, key: str, default: str) -> frozenset[str]:
    raw = site_config.get(key, default) or default
    return frozenset(
        token.strip().lower()
        for token in str(raw).split(",")
        if token.strip()
    )


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------


async def _audit(
    audit_logger: Any | None,
    site_config: Any,
    *,
    event_type: str,
    chat_id: str,
    command: str,
    extra: dict[str, Any] | None = None,
    severity: str = "info",
) -> None:
    """Write one audit_log row if logging is enabled + a logger is wired."""
    if audit_logger is None:
        return
    if not _bool_setting(site_config, "telegram_cli_audit_logged", True):
        return

    details: dict[str, Any] = {"chat_id": str(chat_id), "command": command}
    if extra:
        details.update(extra)

    try:
        await audit_logger.log(
            event_type,
            "telegram_cli",
            details,
            severity=severity,
        )
    except Exception:  # pragma: no cover — never let audit crash the bot
        logger.warning(
            "telegram_cli: audit log write failed for event=%s",
            event_type,
            exc_info=True,
        )
