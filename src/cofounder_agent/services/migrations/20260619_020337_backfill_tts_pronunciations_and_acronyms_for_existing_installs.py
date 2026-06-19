"""Migration 20260619_020337_backfill_tts_pronunciations_and_acronyms_for_existing_installs: backfill tts pronunciation defaults for existing installs

settings_defaults.py seeds tts_pronunciations / tts_acronym_replacements using
ON CONFLICT DO NOTHING — so existing rows that were seeded with '' (the pre-#1699
default) are never updated by the seeder.

PR #1700 removed all pronunciation/abbreviation entries from the hardcoded
_SPOKEN_REPLACEMENTS list in podcast_service.py; those entries now live entirely
in the DB under tts_pronunciations.  This migration updates the empty rows on
existing installs to the full JSON defaults so pronunciation works without a
reinstall.  Rows already customised by operators (non-empty values) are left
untouched.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_TTS_PRONUNCIATIONS = (
    '{"VRAM": "Vee RAM", "SRAM": "Ess RAM", "DRAM": "Dee RAM",'
    ' "PB": "petabyte", "TB": "terabyte", "GB": "gigabyte",'
    ' "MB": "megabyte", "KB": "kilobyte",'
    ' "GHz": "gigahertz", "MHz": "megahertz", "kHz": "kilohertz",'
    ' "Gbps": "gigabits per second", "Mbps": "megabits per second",'
    ' "Kbps": "kilobits per second", "fps": "frames per second",'
    ' "GitFlow": "git flow", "GitHub": "git hub", "GitLab": "git lab",'
    ' "DevSecOps": "dev sec ops", "DevOps": "dev ops", "DevEx": "dev ex",'
    ' "FastAPI": "fast A P I", "PostgreSQL": "postgres", "MongoDB": "mongo D B",'
    ' "GraphQL": "graph Q L", "WebSocket": "web socket",'
    ' "TypeScript": "type script", "JavaScript": "java script",'
    ' "Next.js": "next J S", "Node.js": "node J S", "Vue.js": "view J S",'
    ' "CI/CD": "CI CD", "I/O": "I O", "TCP/IP": "TCP IP", "OS/2": "OS 2",'
    ' "e.g.": "for example", "i.e.": "that is", "etc.": "and so on",'
    ' "vs": "versus", "vs.": "versus",'
    ' "approx.": "approximately", "incl.": "including",'
    ' "w/": "with", "w/o": "without"}'
)

_TTS_ACRONYM_REPLACEMENTS = (
    '{"SOC": "security operations", "CRM": "customer relationship management",'
    ' "SLA": "service level agreement", "KPI": "key performance indicator",'
    ' "ROI": "return on investment", "MVP": "minimum viable product",'
    ' "POC": "proof of concept", "EOL": "end of life"}'
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE app_settings
               SET value = $1
             WHERE key = 'tts_pronunciations'
               AND value = ''
            """,
            _TTS_PRONUNCIATIONS,
        )
        await conn.execute(
            """
            UPDATE app_settings
               SET value = $1
             WHERE key = 'tts_acronym_replacements'
               AND value = ''
            """,
            _TTS_ACRONYM_REPLACEMENTS,
        )
    logger.info(
        "backfill_tts_pronunciations_and_acronyms: updated empty pronunciation/acronym rows to defaults"
    )


async def down(pool) -> None:
    # One-way data migration — revert is a no-op (operator can reset via
    # `poindexter settings set tts_pronunciations ''` if needed).
    pass
