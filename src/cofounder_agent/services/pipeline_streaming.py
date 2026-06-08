"""Default ``on_event`` streaming callbacks for TemplateRunner progress.

Glad-Labs/poindexter#361 part 2 — surface multi-minute pipeline runs to the
operator per-node instead of letting them run silent. ``TemplateRunner.run``
accepts an ``on_event(event_type, payload)`` async callback; this module
builds the production callback wired by ``content_router_service`` per the
``pipeline_streaming_channel`` app_setting.

Channel routing (``pipeline_streaming_channel``, default ``discord``):

- ``discord`` — return ``None`` (no callback). Discord progress already fans
  out via ``template_runner._emit_progress`` →
  ``notify_operator(critical=False)``; an on_event callback here would
  double-post, so the default is deliberately a no-op.
- ``telegram`` — opt-in edit-streaming. A SINGLE Telegram message is sent on
  ``run_started`` and EDITED IN PLACE (``editMessageText``) as nodes complete,
  rendering a running checklist (✓ done / ▶ current / ⛔ halted / ❌ failed).
  Telegram is the operator's critical-only channel, so this is off by default.
  Edits are throttled by ``pipeline_streaming_min_edit_interval_s`` (default
  5s) so rapid node completions coalesce into one edit (Telegram rate-limits
  edits) — the FINAL run_completed/run_failed edit always fires regardless of
  the throttle so the last state is never dropped.
- ``off`` — return ``None`` (no callback). Discord ``_emit_progress`` still
  fires per its own ``template_runner_progress_streaming`` flag.

Every callback is defensive: a network/Bot-API failure is swallowed + logged,
and ``TemplateRunner`` additionally wraps the callback in ``_safe_on_event``,
so a streaming failure can NEVER break a pipeline run.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)

OnEventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]

# Telegram Bot API base (the same host the telegram_post handler targets).
_TELEGRAM_API_BASE = "https://api.telegram.org"

# Glyphs for the checklist render. Colorblind-friendly: distinct shapes, not
# color-only (per the operator's colorblind preference).
_GLYPH_DONE = "✓"      # ✓
_GLYPH_CURRENT = "▶"   # ▶
_GLYPH_HALTED = "⛔"    # ⛔
_GLYPH_FAILED = "❌"    # ❌


async def make_streaming_callback(
    pool: Any,
    site_config: SiteConfig,
    task_id: str,
    *,
    template_slug: str = "",
) -> OnEventCallback | None:
    """Build the default on_event callback per ``pipeline_streaming_channel``.

    Returns ``None`` for ``discord`` / ``off`` (no callback needed — Discord
    is handled by ``_emit_progress``), or a Telegram edit-streaming callback
    for ``telegram``. Returns ``None`` (with a logged warning) if Telegram is
    selected but the bot token / chat_id aren't configured, so a misconfigured
    opt-in degrades to "no streaming" rather than failing the run.

    ``pool`` is accepted for signature symmetry with the rest of the pipeline
    wiring (and future channels that may persist progress); the Telegram path
    needs only ``site_config``.
    """
    del pool  # reserved for future channels; Telegram path uses site_config
    channel = (site_config.get("pipeline_streaming_channel", "discord") or "discord").strip().lower()

    if channel in ("discord", "off"):
        # Discord is driven by _emit_progress; off means no on_event.
        return None

    if channel != "telegram":
        logger.warning(
            "[pipeline_streaming] unknown pipeline_streaming_channel=%r — "
            "treating as 'discord' (no on_event callback)",
            channel,
        )
        return None

    # Telegram edit-streaming opt-in. Resolve credentials up front so a
    # misconfigured opt-in degrades to no-streaming instead of erroring
    # mid-run.
    from services.telegram_config import TelegramConfig

    tg = TelegramConfig(site_config=site_config)
    bot_token = (await tg.get_telegram_bot_token()).strip()
    chat_id = tg.get_telegram_chat_id().strip()
    if not bot_token or not chat_id:
        logger.warning(
            "[pipeline_streaming] pipeline_streaming_channel=telegram but "
            "telegram_bot_token/telegram_chat_id not configured — skipping "
            "edit-streaming (set both to enable)."
        )
        return None

    min_interval_s = max(0, site_config.get_int("pipeline_streaming_min_edit_interval_s", 5))

    return _TelegramStreamCallback(
        bot_token=bot_token,
        chat_id=chat_id,
        task_id=task_id,
        template_slug=template_slug,
        min_interval_s=min_interval_s,
    )


class _TelegramStreamCallback:
    """Stateful on_event callback that edits one Telegram message in place.

    Tracks the ordered list of nodes seen, their status, and the message_id
    of the single status message. On ``run_started`` it sends the initial
    message and captures its id; subsequent node events update the in-memory
    checklist and (subject to the throttle) edit the message. The terminal
    ``run_completed`` / ``run_failed`` edit always fires.

    Callable: ``await cb(event_type, payload)``. Never raises — Bot API
    failures are swallowed + logged (TemplateRunner also wraps it).
    """

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        task_id: str,
        template_slug: str,
        min_interval_s: int,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._task_id = task_id
        self._template_slug = template_slug
        self._min_interval_s = min_interval_s

        self._message_id: Any = None
        # Ordered node status: node_name -> glyph. dict preserves insertion
        # order so the checklist renders in execution order.
        self._nodes: dict[str, str] = {}
        self._last_edit_ts: float = 0.0
        self._finished: bool = False
        self._last_text: str = ""

    async def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            await self._handle(event_type, payload)
        except Exception as exc:  # noqa: BLE001 — never break the run
            logger.debug(
                "[pipeline_streaming] telegram callback failed on %s: %s",
                event_type, exc,
            )

    async def _handle(self, event_type: str, payload: dict[str, Any]) -> None:
        node = payload.get("node")

        if event_type == "run_started":
            await self._start()
            return

        if event_type == "node_started" and node:
            self._nodes[node] = _GLYPH_CURRENT
            await self._maybe_edit()
            return

        if event_type == "node_completed" and node:
            self._nodes[node] = _GLYPH_DONE
            await self._maybe_edit()
            return

        if event_type == "node_halted" and node:
            self._nodes[node] = _GLYPH_HALTED
            await self._maybe_edit(force=True)
            return

        if event_type == "node_failed" and node:
            self._nodes[node] = _GLYPH_FAILED
            await self._maybe_edit(force=True)
            return

        if event_type in ("run_completed", "run_failed"):
            self._finished = True
            await self._maybe_edit(force=True, terminal=event_type)
            return

    async def _start(self) -> None:
        """Send the initial status message + capture its message_id."""
        from services.integrations.handlers.outbound_telegram import (
            send_telegram_message,
        )

        text = self._render(header="\U0001f680 Pipeline starting…")  # 🚀
        result = await send_telegram_message(
            _TELEGRAM_API_BASE, self._bot_token, self._chat_id, text,
        )
        self._message_id = result.get("message_id")
        self._last_edit_ts = time.monotonic()
        self._last_text = text

    async def _maybe_edit(
        self, *, force: bool = False, terminal: str | None = None,
    ) -> None:
        """Edit the status message, honoring the throttle.

        ``force`` bypasses the throttle (halts / failures / terminal events
        always render immediately). Identical text is never re-sent (Telegram
        rejects an unchanged editMessageText with "message is not modified").
        """
        if self._message_id is None:
            # run_started never landed (or its send failed) — nothing to edit.
            return

        now = time.monotonic()
        if not force and (now - self._last_edit_ts) < self._min_interval_s:
            return

        header = None
        if terminal == "run_completed":
            header = "\U0001f3c1 Pipeline complete"  # 🏁
        elif terminal == "run_failed":
            header = "\U0001f4a5 Pipeline failed"  # 💥

        text = self._render(header=header)
        if text == self._last_text:
            return  # avoid "message is not modified" rejections

        from services.integrations.handlers.outbound_telegram import (
            edit_telegram_message,
        )

        await edit_telegram_message(
            _TELEGRAM_API_BASE, self._bot_token, self._chat_id,
            self._message_id, text,
        )
        self._last_edit_ts = now
        self._last_text = text

    def _render(self, *, header: str | None = None) -> str:
        """Render the checklist message body."""
        slug = self._template_slug or "pipeline"
        title = header or f"⚙️ {slug}"  # ⚙️
        lines = [
            f"{title}",
            f"task {self._task_id[:8]}" if self._task_id else "",
        ]
        for node, glyph in self._nodes.items():
            lines.append(f"{glyph} {node}")
        return "\n".join(line for line in lines if line)
