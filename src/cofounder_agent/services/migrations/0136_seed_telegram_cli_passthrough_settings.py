"""Migration 0136: seed Telegram ``/cli`` passthrough settings.

Pairs with ``services/integrations/telegram_cli_passthrough.py`` and the
``/cli ...`` route added to ``scripts/telegram-bot.py``. Lets Matt run
``poindexter`` CLI commands from his phone without touching his PC
("approve a draft, check status, flip a setting").

The bot wraps incoming ``/cli <args>`` messages, hands them to the
passthrough module, which:

1. Verifies the chat_id is the configured operator chat (silent reject
   otherwise — never leak that ``/cli`` exists to a stranger).
2. Verifies the top-level subcommand is in the allowlist + does not
   contain a hard-deny token (``rm``, ``drop``, ``--force``, ...).
3. Subprocesses ``python -m poindexter.cli <args>`` with a timeout.
4. Truncates stdout/stderr to fit in a single Telegram message.
5. Writes an ``audit_log`` row with chat_id, command, exit code,
   duration.

Five knobs the passthrough reads:

- ``telegram_cli_enabled`` (default ``"true"``) — global kill-switch.
  Flip to ``"false"`` to disable ``/cli`` without redeploying the bot.
- ``telegram_cli_safe_commands`` (default
  ``"post,settings,validators,auth,check_health,get_post_count,health,version"``)
  — comma-separated allowlist of top-level CLI subcommands. Anything
  not on the list is rejected with an explanation. Tighten or widen
  per operator preference.
- ``telegram_cli_max_output_chars`` (default ``"3500"``) — cap on the
  characters of stdout+stderr the bot will send back. Telegram's per-
  message limit is 4096; we reserve headroom for headers + a "[...
  truncated]" marker.
- ``telegram_cli_timeout_seconds`` (default ``"30"``) — kill the
  subprocess after this many seconds and reply "command timed out".
  Prevents a runaway query from hanging the polling loop.
- ``telegram_cli_audit_logged`` (default ``"true"``) — when ``"true"``,
  every ``/cli`` invocation writes an ``audit_log`` row tagged
  ``source='telegram_cli'``. Disable for local-dev noise reduction;
  leave on in prod.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — safe to re-run.
Operator-set values are preserved.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SETTINGS = [
    {
        "key": "telegram_cli_enabled",
        "value": "true",
        "category": "integrations",
        "description": (
            "Global kill-switch for the Telegram /cli passthrough. "
            "When 'true' (default), '/cli <args>' messages from the "
            "configured operator chat are routed to the poindexter CLI "
            "and the captured output is replied back. When 'false', "
            "/cli messages are ignored (no reply, no audit row). Use "
            "this to disable mobile CLI access without redeploying the "
            "bot — for example during a sensitive maintenance window."
        ),
    },
    {
        "key": "telegram_cli_safe_commands",
        "value": "post,settings,validators,auth,check_health,get_post_count,health,version",
        "category": "integrations",
        "description": (
            "Comma-separated allowlist of top-level poindexter CLI "
            "subcommands the Telegram /cli passthrough will execute. "
            "The first whitespace-separated token after '/cli' must "
            "match an entry here, otherwise the bot rejects the command "
            "with a 'not on allowlist' message. Default covers the "
            "routine ops set: post show/approve/reject, settings get, "
            "validators list/enable/disable, auth list-clients, plus a "
            "few read-only health checks. Add more entries here as "
            "needed (e.g. 'topics' once you trust the surface)."
        ),
    },
    {
        "key": "telegram_cli_max_output_chars",
        "value": "3500",
        "category": "integrations",
        "description": (
            "Maximum characters of combined stdout+stderr the Telegram "
            "/cli passthrough will reply with. Telegram's hard per-"
            "message limit is 4096; the default 3500 leaves headroom "
            "for the 'exit=N duration=Xs' header line and a '[output "
            "truncated, N more chars]' marker. Lower this to keep "
            "replies short; raise it (up to ~4000) if you need to see "
            "more output and don't mind the bot occasionally hitting "
            "the Telegram cap."
        ),
    },
    {
        "key": "telegram_cli_timeout_seconds",
        "value": "30",
        "category": "integrations",
        "description": (
            "Wall-clock timeout (seconds) for a /cli subprocess. After "
            "this many seconds the passthrough kills the process group "
            "and replies 'command timed out'. Prevents a slow query "
            "(or a hung CLI subcommand) from blocking the bot's "
            "Telegram long-poll loop. Default 30s comfortably covers "
            "all read-only CLI commands; bump if you find yourself "
            "regularly hitting it on legitimate calls."
        ),
    },
    {
        "key": "telegram_cli_audit_logged",
        "value": "true",
        "category": "integrations",
        "description": (
            "When 'true' (default), every /cli invocation writes one "
            "row to the audit_log table (event_type='telegram_cli_"
            "invoked' or 'telegram_cli_denied', source='telegram_cli'). "
            "Captures chat_id, the raw command line, exit code, and "
            "duration. Disable only for local-dev noise reduction; "
            "leave 'true' in production so you have a forensic trail "
            "of every mobile-issued CLI command."
        ),
    },
]


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing — skipping migration 0136"
            )
            return

        for setting in _SETTINGS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (key) DO NOTHING
                """,
                setting["key"],
                setting["value"],
                setting["category"],
                setting["description"],
            )
            if result == "INSERT 0 1":
                logger.info(
                    "Migration 0136: seeded %s=%r",
                    setting["key"], setting["value"],
                )
            else:
                logger.info(
                    "Migration 0136: %s already set, leaving operator value alone",
                    setting["key"],
                )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        for setting in _SETTINGS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                setting["key"],
            )
        logger.info(
            "Migration 0136 rolled back: removed %d telegram_cli_* settings",
            len(_SETTINGS),
        )
