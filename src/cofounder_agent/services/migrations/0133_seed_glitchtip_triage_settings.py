"""Migration 0133: seed GlitchTip triage probe settings.

Pairs with the brain probe shipped in
brain/glitchtip_triage_probe.py. The probe runs every brain cycle
(5-min), pulls every unresolved GlitchTip issue from the configured
org, applies an operator-controlled ruleset to auto-resolve known
noise, and pages on novel high-count issues.

Settings introduced:

- ``glitchtip_triage_enabled`` (default ``"true"``) — master kill switch.
- ``glitchtip_triage_api_token`` (default ``""``, ``is_secret=true``) —
  GlitchTip API token. Mint via the audit script
  (``scripts/glitchtip_audit.py``) which logs in with the bootstrap
  admin password and creates a token via the
  ``/api/0/api-tokens/`` endpoint, then store with
  ``poindexter set glitchtip_triage_api_token <token> --secret``.
  Without this, the probe degrades to ``status=unconfigured`` and
  no-ops; the brain cycle continues.
- ``glitchtip_base_url`` (default ``"http://glitchtip-web:8000"``) —
  reachable from inside the docker network. ``localize_url()``
  rewrites localhost → host.docker.internal when the brain runs on
  the host.
- ``glitchtip_triage_org_slug`` (default ``"glad-labs"``) — the org
  the probe queries. Renaming the org doesn't require a brain
  redeploy.
- ``glitchtip_triage_alert_threshold_count`` (default ``"100"``) —
  issues with ``count`` ≥ this AND no matching auto-resolve rule get
  a ``notify_operator()`` page. Per-process dedupe so brain restarts
  re-page on still-active issues, but cycles inside a single uptime
  don't.
- ``glitchtip_triage_auto_resolve_patterns`` (JSONB array, seeded
  with patterns derived from the 506-issue audit on 2026-05-01) —
  each entry is::

      {
        "title_pattern": "<python regex>",
        "action": "resolve" | "ignore",
        "reason": "<freeform>",
        "max_count": <int> | null,    # only auto-act when count <= this
        "level_in": [<level>, ...] | null
      }

The seeded ruleset is tuned for the patterns Matt's pipeline emits as
of the audit date. Operators on a different niche get their own audit
output and can replace the default rules without code changes.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — re-running leaves
operator-set values alone.

Cross-references:
- Probe: brain/glitchtip_triage_probe.py
- Wiring: brain/brain_daemon.py run_cycle (alongside compose_drift)
- Audit script: scripts/glitchtip_audit.py
- Auto-triage design principle: feedback_alert_auto_triage.md
"""

import json

from services.logger_config import get_logger

logger = get_logger(__name__)


# Patterns derived from the 2026-05-01 audit:
#   - Langfuse exporter spam (2258 occurrences) when langfuse-web is
#     restarting / unreachable. The exporter retries on its own; the
#     fault clears on its own. Pure noise.
#   - ANSI-decorated structlog lines that GlitchTip splits into a
#     separate "issue" per task ID. The underlying root cause shows up
#     as one of the explicit AllModelsFailedError / RuntimeError issues
#     anyway — these are duplicate noise. Resolve aggressively.
#   - "Failed to update task" lines are the side-effect of a single
#     deeper bug (worker losing track of a task ID). Once the parent is
#     fixed the children stop. Resolve when count is small.
#   - The ModuleNotFoundError: cofounder_agent only fires from a stale
#     dev process, not real workers. Resolve on sight.
_SEEDED_RULES = [
    {
        "title_pattern": r"Failed to export span batch.*langfuse-web",
        "action": "resolve",
        "reason": (
            "Langfuse OTel exporter retries on its own when langfuse-web "
            "restarts. Closing as expected transient noise. If langfuse-web "
            "is consistently unreachable that's a separate compose-drift / "
            "container-down issue that the compose_drift probe will catch."
        ),
        "max_count": None,  # always — the spammier this gets, the more we want it gone
        "level_in": None,
    },
    {
        "title_pattern": r"\x1b\[\d+m\d{4}-\d{2}-\d{2}T",
        "action": "resolve",
        "reason": (
            "ANSI-decorated structlog line. GlitchTip splits these into a "
            "separate 'issue' per task ID, which is noise — the parent "
            "exception is captured as its own (deduplicated) issue. "
            "Resolving these does not lose information."
        ),
        "max_count": 50,  # only auto-resolve quiet ones; loud ones might be a real wave
        "level_in": None,
    },
    {
        "title_pattern": r"^\[error\s*\]\s*=+",
        "action": "resolve",
        "reason": (
            "Decorative '====' banner emitted by structlog when the "
            "pipeline prints a multi-line error block. Not a real error "
            "on its own — the actual exception is captured separately."
        ),
        "max_count": None,
        "level_in": None,
    },
    {
        "title_pattern": r"^\[error\s*\]\s*Failed to update task [a-f0-9-]{36}",
        "action": "resolve",
        "reason": (
            "Per-task update failure — symptom of a deeper bug (worker "
            "loses track of a task ID). The parent exception is captured "
            "as its own issue; these per-task lines are duplicate noise. "
            "max_count gate so a real update-system meltdown still pages."
        ),
        "max_count": 20,
        "level_in": None,
    },
    {
        "title_pattern": r"ModuleNotFoundError:.*cofounder_agent",
        "action": "resolve",
        "reason": (
            "Stale dev process trying to import the package by its old "
            "name. Real workers import from `services.*` and don't "
            "trigger this. Resolving on sight."
        ),
        "max_count": None,
        "level_in": None,
    },
    {
        "title_pattern": r"AllModelsFailedError|ALL AI MODELS FAILED|Ollama:\s+False|Stage 'generate_content' halted",
        "action": "ignore",
        "reason": (
            "Already tracked elsewhere — the model-router fallback chain "
            "and Ollama liveness probes both surface this, and the audit "
            "showed all of these strings (the bare exception name, the "
            "structlog 'ALL AI MODELS FAILED' line, the 'Ollama: False' "
            "stage diagnostic, and the 'generate_content halted' "
            "RuntimeError wrapper) burst together during a single Ollama "
            "outage. Marking 'ignore' (not 'resolve') so operators can "
            "still see them in the GlitchTip UI for triage, but the "
            "brain probe stops paging on them."
        ),
        "max_count": None,
        "level_in": None,
    },
]


_SETTINGS = [
    {
        "key": "glitchtip_triage_enabled",
        "value": "true",
        "category": "monitoring",
        "is_secret": False,
        "description": (
            "Master enable for the brain GlitchTip triage probe. When "
            "'true' (default), the probe runs every cycle (5-min), pulls "
            "open issues, auto-resolves matches against "
            "glitchtip_triage_auto_resolve_patterns, and pages on novel "
            "high-count issues. Set to 'false' to silence it (e.g. while "
            "GlitchTip itself is being upgraded)."
        ),
    },
    {
        "key": "glitchtip_triage_api_token",
        "value": "",
        "category": "monitoring",
        "is_secret": True,
        "description": (
            "GlitchTip API token used by the brain triage probe to read "
            "and resolve issues. Mint via scripts/glitchtip_audit.py "
            "(which logs in with the bootstrap admin password and posts "
            "to /api/0/api-tokens/), then store with `poindexter set "
            "glitchtip_triage_api_token <token> --secret`. Required "
            "scopes: org:read, project:read, event:read, event:admin, "
            "member:read. Empty default — probe degrades to "
            "status=unconfigured and no-ops without a token."
        ),
    },
    {
        "key": "glitchtip_base_url",
        "value": "http://glitchtip-web:8000",
        "category": "monitoring",
        "is_secret": False,
        "description": (
            "Base URL for the GlitchTip API the brain triage probe "
            "queries. Default is the compose-internal hostname; "
            "brain.docker_utils.localize_url() rewrites it to "
            "host.docker.internal:8080 when the brain runs on the host. "
            "Override only if you're running GlitchTip outside the "
            "default poindexter compose stack."
        ),
    },
    {
        "key": "glitchtip_triage_org_slug",
        "value": "glad-labs",
        "category": "monitoring",
        "is_secret": False,
        "description": (
            "GlitchTip organization slug the brain triage probe queries. "
            "Default 'glad-labs' matches the org the bootstrap installer "
            "creates. Change this if you renamed the org in the "
            "GlitchTip UI."
        ),
    },
    {
        "key": "glitchtip_triage_alert_threshold_count",
        "value": "100",
        "category": "monitoring",
        "is_secret": False,
        "description": (
            "Brain triage probe pages via notify_operator() when a "
            "GlitchTip issue has count >= this AND matches no entry in "
            "glitchtip_triage_auto_resolve_patterns. Default 100 keeps "
            "noise quiet while still surfacing genuinely-recurring novel "
            "errors. Per-issue dedupe by id within a single brain "
            "process uptime — restart the brain to re-page."
        ),
    },
    {
        "key": "glitchtip_triage_auto_resolve_patterns",
        "value": json.dumps(_SEEDED_RULES),
        "category": "monitoring",
        "is_secret": False,
        "description": (
            "JSONB array of triage rules for the brain GlitchTip probe. "
            "Each entry: {title_pattern: <regex>, action: 'resolve' or "
            "'ignore', reason: <text>, max_count: <int|null>, level_in: "
            "[<level>...] | null}. Default rules derived from the "
            "2026-05-01 audit of 506 open issues — Langfuse exporter "
            "spam, ANSI-decorated structlog duplicates, decorative "
            "banner lines, per-task update failures, stale "
            "cofounder_agent imports, and AllModelsFailedError "
            "(ignore-only since the model-router and Ollama probes "
            "already cover it). Edit this setting to add tenant-"
            "specific rules without code changes."
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
                "Table 'app_settings' missing — skipping migration 0133"
            )
            return

        for setting in _SETTINGS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (key) DO NOTHING
                """,
                setting["key"],
                setting["value"],
                setting["category"],
                setting["description"],
                setting["is_secret"],
            )
            if result == "INSERT 0 1":
                # Don't log the value of the secret token field on insert.
                shown = (
                    "<empty — operator-supplied>"
                    if setting["is_secret"]
                    else (setting["value"][:80] + ("…" if len(setting["value"]) > 80 else ""))
                )
                logger.info(
                    "Migration 0133: seeded %s=%s",
                    setting["key"], shown,
                )
            else:
                logger.info(
                    "Migration 0133: %s already set, leaving operator value alone",
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
            "Migration 0133 rolled back: removed %d glitchtip-triage settings",
            len(_SETTINGS),
        )
