#!/usr/bin/env python3
"""Generate src/cofounder_agent/services/settings_defaults.py from the
AST extract + secret-key blocklist.

Reads:
    scripts/settings_defaults_extract.json
    scripts/settings_secret_keys.json

Writes:
    src/cofounder_agent/services/settings_defaults.py

The output module is hand-edit safe: re-running this regenerator will
re-create it from the same inputs, so the regenerator is the source of
truth, not the .py file. (We commit BOTH for review-ability.)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACT = ROOT / "scripts" / "settings_defaults_extract.json"
SECRETS = ROOT / "scripts" / "settings_secret_keys.json"
OUT = ROOT / "src" / "cofounder_agent" / "services" / "settings_defaults.py"


# Group keys into readable sections by prefix. Order matters — earlier
# patterns win. Keys that don't match any pattern fall into "general".
GROUPS: list[tuple[str, str]] = [
    ("Identity / branding", r"^(site_|company_|owner_|operator_|app_|environment$|repo_root$|host_home$|development_mode$|disable_auth_for_dev$|gpu_)"),
    ("Cost / billing", r"^(monthly_spend_limit|electricity_rate)"),
    ("LLM model selection", r"^(pipeline_|qa_fallback_writer_model|default_ollama_model|local_llm_|use_ollama|embed_model|embedding_model|inline_image_prompt_model|model_role_)"),
    ("LLM providers / endpoints", r"^(ollama_|sdxl_|flux_|wan_|stable_audio|video_server_url|plugin\.)"),
    ("LiteLLM / cost-lookup", r"^(litellm_)"),
    ("RAG / retrieval", r"^(rag_|writer_rag_|internal_rag|niche_internal_rag)"),
    ("Quality assurance pipeline", r"^(qa_)"),
    ("Topic discovery / dedup / ranking", r"^(topic_|niche_)"),
    ("Content router / writer / self-review", r"^(content_router_|enable_writer_self_review|self_consistency_|enable_training_capture|guardrails_|deepeval_|ragas_)"),
    ("Image generation", r"^(image_|enable_sdxl)"),
    ("Video / podcast / TTS", r"^(podcast_|video_|tts_|audio_gen_|scheduled_publisher)"),
    ("Voice agent", r"^(voice_agent_)"),
    ("Devto / external publishing", r"^(devto_|mastodon_)"),
    ("Newsletter / email", r"^(newsletter_|smtp_)"),
    ("Static export / public site", r"^(static_export_|next_public)"),
    ("Storage / R2", r"^(r2_|storage_)"),
    ("Observability / monitoring", r"^(sentry_|enable_pyroscope|pyroscope_|enable_tracing|langfuse_|template_runner_)"),
    ("Security / auth", r"^(disable_auth_for_dev|max_approval_queue|oauth_)"),
    ("Logging", r"^(max_log_)"),
    ("Brain daemon", r"^(brain_)"),
    ("Title originality / SEO", r"^(title_|google_sitemap|indexnow_)"),
    ("Auto-publish gate (dev_diary)", r"^(dev_diary_auto_publish_)"),
    ("Gitea / external integrations", r"^(gitea_|publish_quiet_hours)"),
    ("Telegram / Discord", r"^(telegram_|discord_)"),
    ("Trusted source domains", r"^(trusted_source_domains)$"),
]


def _classify(key: str) -> str:
    for label, pat in GROUPS:
        if re.match(pat, key):
            return label
    return "Misc"


def _python_value(value: str, value_type: str):
    """Render the JSON-extracted (value, value_type) as a Python literal."""
    if value_type == "bool":
        # Stored as string in app_settings — render as canonical lowercase
        return repr("true" if value.lower() in ("true", "1", "yes", "on") else "false")
    if value_type in ("int", "float"):
        return repr(value)  # store as str — app_settings.value is text
    if value_type == "none":
        return repr("")
    # str (and anything else) → repr
    return repr(value)


HEADER = '''"""Consolidated default values for app_settings keys (#379).

Centralises every default that previously lived inline in
``site_config.get(key, default)`` calls across ~120 service files.

Why this exists
---------------
On a fresh DB, only the ~149 keys explicitly seeded by
``services/migrations/`` exist in ``app_settings``. The remaining
~300 are inserted lazily by SettingsService the first time the worker
queries them. That violates ``feedback_no_silent_defaults`` (defaults
appear at query-time, not loud at install-time) and makes
``poindexter setup --check`` report ``SKIP api_base_url unset`` on a
fresh install even though the worker would happily use a default.

``seed_all_defaults(pool)`` walks ``DEFAULTS`` and inserts each row
with ``ON CONFLICT (key) DO NOTHING`` so:

* Operator-tuned values are NEVER clobbered (the ON CONFLICT branch
  keeps the existing row untouched).
* Re-running on an up-to-date DB is a fast no-op (no rows inserted).
* Migrations and this seeder can both write the same key — first one
  wins, the other is a no-op.

Wired into:

* ``StartupManager._run_migrations`` — every worker boot.
* ``cli/setup.py`` — runs after ``poindexter setup`` finishes
  migrations, so a fresh install ends with a complete app_settings
  table.

What this module is NOT
-----------------------
* **Not for secrets.** Keys matching ``*_api_key``, ``*_password``,
  ``*_secret``, ``database_url``, ``operator_id`` etc. are
  deliberately excluded. They must remain unset on fresh install —
  the operator sets them via ``poindexter setup`` prompts or the
  ``set_secret`` API. Putting placeholder values here would trigger
  the ``app_settings`` auto-encrypt trigger (migration 0130) and
  bury a bogus ciphertext in the DB.
* **Not the source of truth at runtime.** ``site_config.get(key,
  default)`` callers still pass their own default — this registry
  just makes sure the DB row exists so the call returns the DB value
  instead of the inline default. Removing the inline default in code
  is a separate cleanup pass (#198 follow-up).
* **Not auto-generated reflectively.** The list is committed source
  to keep it grep-able and review-able. ``scripts/extract_settings_defaults.py``
  + ``scripts/generate_settings_defaults_module.py`` regenerate it
  from the codebase if a sweep adds new keys; the diff is the audit.

Auto-generated from:

* ``scripts/extract_settings_defaults.py``  (AST sweep of
  ``site_config.get*(key, default)`` call sites)
* ``scripts/extract_secret_keys.py``         (secret-key blocklist)
* ``scripts/generate_settings_defaults_module.py`` (this writer)
"""
from __future__ import annotations

from typing import Any
'''


# Operator-specific values that the AST happened to pick up but which
# would be wrong on a fresh install. Excluded from the registry — the
# operator's own first call will fall back to the inline default in
# code (or, for required keys, hit `require()` and fail loudly).
HARD_EXCLUDE = {
    "host_home",      # Matt's local checkout path — never portable
    "repo_root",      # Container/host path — set per deployment
    "gpu_name",       # Hardware identity
    "gpu_tdp_map",    # Hardware identity
    "gpu_vram_gb",    # Hardware identity
    "gitea_repo",     # Operator's repo URL
    "gitea_url",      # Operator's gitea instance (gitea is retired)
    "gitea_user",     # Operator's gitea username
    "podcast_cdn_version",  # CDN version pinning is per-deployment
}

# Keys where the AST picked a wrong / too-specific default. Override to
# something safer for a fresh install.
OVERRIDES: dict[str, str] = {
    # Several call sites pass unrelated literals like 'Author', 'Site',
    # 'podcast' as positional defaults — none of them are correct
    # canonical defaults. Empty string is the only safe choice; the
    # require() path will fail loudly if a flow actually needs them.
    "site_name": "",
    "site_domain": "",
    "site_url": "",
    "company_name": "",
    "owner_name": "",
    "podcast_name": "",
    "podcast_description": "",
    "video_feed_name": "",
    # Several callers default this to 'glm-4.7-5090:latest' but the
    # canonical free-OSS shipping default is gemma3:27b (per
    # feedback_no_paid_apis + feedback_fallback_chain). Keep call sites
    # untouched; just seed the safer DB value.
    "pipeline_writer_model": "gemma3:27b",
    "pipeline_fallback_model": "gemma3:27b",
}


def main() -> int:
    extract = json.loads(EXTRACT.read_text(encoding="utf-8"))
    secrets = set(json.loads(SECRETS.read_text(encoding="utf-8")))

    by_key = extract["by_key"]
    keys = sorted(by_key)

    # Drop secrets — they MUST stay unset.
    seedable = [k for k in keys if k not in secrets and k not in HARD_EXCLUDE]

    # Bucket by group label, applying OVERRIDES along the way
    grouped: dict[str, list[tuple[str, str, str]]] = {}
    for k in seedable:
        info = by_key[k]
        if k in OVERRIDES:
            value = OVERRIDES[k]
            value_type = "str"
        else:
            value = info["value"]
            value_type = info["value_type"]
        label = _classify(k)
        grouped.setdefault(label, []).append((k, value, value_type))

    # Stable group order — sections in the order GROUPS declares
    section_order = [g[0] for g in GROUPS] + ["Misc"]

    body_lines = [HEADER, ""]
    body_lines.append("# Every value is stored as `str` because `app_settings.value` is a TEXT")
    body_lines.append("# column. Numeric / bool consumers parse via `site_config.get_int()`,")
    body_lines.append("# `get_float()`, `get_bool()` etc.")
    body_lines.append("DEFAULTS: dict[str, str] = {")

    seen_in_output: set[str] = set()
    for section in section_order:
        if section not in grouped:
            continue
        rows = sorted(grouped[section])
        body_lines.append(f"    # ----- {section} -----")
        for key, value, vtype in rows:
            if key in seen_in_output:
                continue
            seen_in_output.add(key)
            py_value = _python_value(value, vtype)
            body_lines.append(f"    {key!r}: {py_value},")
        body_lines.append("")
    body_lines.append("}")
    body_lines.append("")

    # The seeder
    body_lines.append('''
async def seed_all_defaults(pool: Any) -> int:
    """Insert every DEFAULTS entry into app_settings, skipping existing rows.

    Returns the count of rows actually inserted (i.e. fresh-install gap
    closed). On an up-to-date DB this is 0.

    Operator-tuned values survive — the ``ON CONFLICT (key) DO NOTHING``
    clause means an existing row is never overwritten by this seeder.

    Args:
        pool: An asyncpg pool. ``DatabaseService`` instances expose this
            as ``database_service.pool``; the migrate CLI uses a bare
            pool directly.

    Returns:
        Number of rows inserted (0 ≤ n ≤ len(DEFAULTS)).
    """
    if pool is None:
        return 0

    inserted = 0
    async with pool.acquire() as conn:
        for key, value in DEFAULTS.items():
            # asyncpg returns "INSERT 0 1" / "INSERT 0 0" status string.
            # We parse the count off the end to know whether ON CONFLICT
            # fired or the row was actually new.
            status = await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active, updated_at)
                VALUES
                    ($1, $2, 'general',
                     'Auto-seeded by services.settings_defaults (#379)',
                     FALSE, TRUE, NOW())
                ON CONFLICT (key) DO NOTHING
                """,
                key,
                value,
            )
            try:
                # Status looks like "INSERT 0 N"
                if status.endswith(" 1"):
                    inserted += 1
            except Exception:
                pass
    return inserted


def keys() -> list[str]:
    """Return the sorted list of keys this module knows about.

    Useful for diagnostics (``poindexter setup --check`` could compare
    DEFAULTS.keys() against the live DB to flag drift).
    """
    return sorted(DEFAULTS.keys())
''')

    OUT.write_text("\n".join(body_lines), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"  total seedable keys: {len(seedable)}")
    print(f"  excluded as secret:  {len(set(keys) & secrets)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
