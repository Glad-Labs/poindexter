"""Migration: fix the phantom ``poindexter set`` command in app_settings descriptions.

Four seeded ``app_settings`` rows carried operator-facing setup hints that told
operators to run ``poindexter set <key> <value>`` — a command that does not
exist. The CLI registers a ``settings`` group (``poindexter settings set ...``);
there is no top-level ``set``, so following the hint errors with
``No such command 'set'``. The same class of bug was reconciled across the docs
tree and the secret-setting commands in Glad-Labs/poindexter#1556; this
migration fixes the DB-stored descriptions on EXISTING installs.

Why a migration (and not just ``settings_defaults`` / the baseline seed): the
baseline seed file is corrected in lockstep for FRESH installs, but its
``INSERT ... ON CONFLICT (key) DO NOTHING`` never rewrites an already-seeded
row, so an existing operator's DB keeps the stale description forever without
an explicit ``UPDATE``.

The descriptions surface to operators two ways: ``poindexter settings get
<key>`` prints the description verbatim, and ``scripts/regen-app-settings-doc.py``
renders them into ``docs/reference/app-settings.md``. Only
``writing_style_reference``'s hint falls inside the doc's 117-char render
truncation; the other three are fixed for the ``settings get`` surface and
for consistency. The secret key (``cloudflare_analytics_api_token``,
``is_secret=true``) gets the ``--secret`` form; the rest are plain non-secret
keys.

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# key -> (old description carrying the phantom command, canonical replacement).
# The replacements are byte-identical to the corrected baseline seed values so
# fresh installs (seed) and existing installs (this migration) converge.
_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "writing_style_reference": (
        "Operator-specific writing style traits injected into content-generation "
        "prompts. Set via `poindexter set writing_style_reference <description>` "
        "with a description of the desired voice and style.",
        "Operator-specific writing style traits injected into content-generation "
        "prompts. Set via `poindexter settings set writing_style_reference "
        "<description>` with a description of the desired voice and style.",
    ),
    "cloudflare_analytics_api_token": (
        "Cloudflare API token scoped to Account Analytics Read. Consumed by the "
        "sync_cloudflare_analytics job. Operator fills in via `poindexter set` "
        "after deploying the Worker.",
        "Cloudflare API token scoped to Account Analytics Read. Consumed by the "
        "sync_cloudflare_analytics job. Operator fills in via `poindexter settings "
        "set cloudflare_analytics_api_token <token> --secret` after deploying the "
        "Worker.",
    ),
    "voice_agent_public_join_url": (
        "Public URL the operator (or Claude, via the start_voice_call MCP tool) "
        "taps to join the always-on LiveKit voice room. Set via `poindexter set "
        "voice_agent_public_join_url <url>` after deployment. Read by the "
        "start_voice_call MCP tool — kept DB-backed so each operator configures "
        "their own host rather than inheriting a hardcoded URL.",
        "Public URL the operator (or Claude, via the start_voice_call MCP tool) "
        "taps to join the always-on LiveKit voice room. Set via `poindexter "
        "settings set voice_agent_public_join_url <url>` after deployment. Read by "
        "the start_voice_call MCP tool — kept DB-backed so each operator configures "
        "their own host rather than inheriting a hardcoded URL.",
    ),
    "voice_default_room": (
        "Default LiveKit room name when voice_join_room is called without an "
        "explicit channel_id. Distinct from voice_agent_room_name (the always-on "
        "ollama agent's room) so the bridge and the agent don't accidentally "
        "collide on the same room. Operators on custom deployments override at "
        "runtime: `poindexter set voice_default_room ops`.",
        "Default LiveKit room name when voice_join_room is called without an "
        "explicit channel_id. Distinct from voice_agent_room_name (the always-on "
        "ollama agent's room) so the bridge and the agent don't accidentally "
        "collide on the same room. Operators on custom deployments override at "
        "runtime: `poindexter settings set voice_default_room ops`.",
    ),
}


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, (_, new) in _DESCRIPTIONS.items():
            # Match any phantom `poindexter set ...` form (including the bare
            # `poindexter set` with no args) while skipping rows already on the
            # canonical `poindexter settings set` form — so re-running is a
            # no-op and an operator-customised description is never clobbered.
            await conn.execute(
                """
                UPDATE app_settings
                SET description = $1
                WHERE key = $2
                  AND description LIKE '%poindexter set%'
                  AND description NOT LIKE '%poindexter settings set%'
                """,
                new,
                key,
            )
    logger.info(
        "fix_poindexter_set_in_app_settings_descriptions: reconciled %d description(s)",
        len(_DESCRIPTIONS),
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, (old, new) in _DESCRIPTIONS.items():
            await conn.execute(
                """
                UPDATE app_settings
                SET description = $1
                WHERE key = $2
                  AND description = $3
                """,
                old,
                key,
                new,
            )
    logger.info("fix_poindexter_set_in_app_settings_descriptions: restored prior descriptions")
