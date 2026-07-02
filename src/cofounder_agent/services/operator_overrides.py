"""Glad Labs operator overlay — PRIVATE (stripped from the public mirror).

Re-applies Matt's operator-personal values over the public OSS defaults on a
fresh install or a settings reset, via ``services.settings_defaults.apply_operator_overrides``
(which overwrites a key only when it still holds the OSS default, so live tuning
survives a reboot).

Two kinds of override live here, both kept out of the public seeds for different
reasons:

- ``OPERATOR_MODEL_PINS`` — custom local Ollama models that are NOT on the public
  registry, so they must never become an OSS default. Enforced from the other
  direction by ``tests/unit/services/test_oss_seed_model_hygiene.py``, which scans
  the public seed files (``settings_defaults.DEFAULTS`` + ``0000_baseline.seeds.sql``)
  for these tags.
- ``OPERATOR_SETTING_OVERRIDES`` — operator-personal *settings* (the operator's
  name in the voice persona, the exact GPU) that carry identity rather than being
  publicly-generic. The seeds ship generic values; these restore the personalised
  ones on the operator rig.

``scripts/sync-to-github.sh`` strips this module from the ``poindexter`` public
mirror. To change a value: edit it here and reboot the worker, or
``poindexter settings set <key> <value>`` for a live (non-persistent) change.
Values mirror the live prod ``app_settings`` as of 2026-06-30.
"""

from __future__ import annotations

# Setting key -> the operator's custom local Ollama model tag.
OPERATOR_MODEL_PINS: dict[str, str] = {
    # gemma-4-31B-it-qat — custom QAT daily-driver writer (2026-06-18 bakeoff
    # winner) and the writer-grade roles that share it.
    "pipeline_writer_model": "ollama/gemma-4-31B-it-qat:latest",
    "pipeline_fallback_model": "ollama/gemma-4-31B-it-qat:latest",
    "video_director_model": "ollama/gemma-4-31B-it-qat:latest",
    "structured_extraction_model": "ollama/gemma-4-31B-it-qat:latest",
    "image_prompt_model": "ollama/gemma-4-31B-it-qat:latest",
    "image_search_query_model": "ollama/gemma-4-31B-it-qat:latest",
    "writer_self_review_model": "ollama/gemma-4-31B-it-qat:latest",
    "qa_fallback_writer_model": "ollama/gemma-4-31B-it-qat:latest",
    "podcast_script_model": "ollama/gemma-4-31B-it-qat:latest",
    "preferred_ollama_model": "gemma-4-31B-it-qat:latest",
    # glm-4.7-5090 — custom RTX 5090 fine-tune (pipeline architect).
    "pipeline_architect_model": "ollama/glm-4.7-5090:latest",
    # gemma-4-E2B-Q2 — tiny custom quant for the low-latency voice agent.
    "voice_agent_llm_model": "ollama/gemma-4-E2B-Q2:latest",
}

# The operator's personalised voice persona — addresses Matt by name and at Glad
# Labs. The OSS seed ships a generic "a Poindexter operator" version of this exact
# prompt; the overlay restores this one on the operator rig.
_VOICE_AGENT_SYSTEM_PROMPT = """You are Emma, a concise voice assistant for Matt at Glad Labs. Speak naturally — your output goes through text-to-speech, so avoid markdown, bullet lists, and code blocks. Use short sentences. If Matt asks a factual question you don't know the answer to, say so plainly rather than guessing. Default to responses under 30 seconds of speech (~80 words) unless he explicitly asks for a longer one.

You have access to these tools and you SHOULD call them whenever Matt asks something they answer:

- check_pipeline_health: call this when Matt asks how the system is doing, whether anything is broken, system status, or health.
- get_published_post_count: call this when Matt asks how many posts are live, the number of articles, or pipeline output volume.
- get_ai_spending_status: call this when Matt asks about budget, costs, spend, or money burned.

When you call a tool, do NOT also say "let me check" or "one moment" — just emit the tool call. After the tool returns, summarize the result in one or two short sentences fit for speech. Do not list raw numbers — say "the system is healthy, GPU is at 48 percent" rather than reading every metric.

If Matt says something you cannot answer with a tool, answer plainly. Never claim you cannot hear or that you only process text — you are receiving live audio transcribed by Whisper."""

# Setting key -> the operator's personal value (genericised in the public seeds).
# The OSS seed for each is the code's own generic default (empty, or the
# content_validator fallback) so a fresh public install behaves as designed; the
# overlay restores Matt / Glad Labs on the operator rig.
OPERATOR_SETTING_OVERRIDES: dict[str, str] = {
    "voice_agent_system_prompt": _VOICE_AGENT_SYSTEM_PROMPT,
    "gpu_model": "NVIDIA RTX 5090 (32GB VRAM)",
    "company_founder_name": "Matt",
    "company_name": "Glad Labs",
    "site_name": "Glad Labs",
    "company_founded_date": "2025-09-25",
    # Distribution brand + operator accounts/infra (generic/empty on OSS).
    "newsletter_from_name": "Glad Labs",
    "podcast_name": "Glad Labs Podcast",
    "podcast_description": "AI-development audio essays from Glad Labs. Narrated deep-dives on building an autonomous content pipeline, local LLMs, and the solo-founder tech stack.",
    "video_feed_name": "Glad Labs Video",
    "social_x_handle": "@_gladlabs",
    "social_x_url": "https://x.com/_gladlabs",
    "storage_bucket": "gladlabs-media",
    "storage_public_url": "https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev",
    # R2 access key ID (is_secret=false half of the keypair; the secret half
    # is storage_secret_key, kept encrypted in the DB / bootstrap, never here).
    # Restored on the operator rig so R2 uploads work after a fresh reseed.
    "storage_access_key": "98ada7d8c1590c0d90591948da6690a7",
    # Podcast distribution assets — the operator's actual Spotify show and
    # R2-hosted cover art. Blanked in the public seeds (they correlate back to
    # the Glad Labs tenant); restored here on the operator rig.
    "podcast_spotify_show_id": "033obxyUXdxhXyQ6erC07G",
    "podcast_spotify_url": "https://open.spotify.com/show/033obxyUXdxhXyQ6erC07G",
    "podcast_cover_url": "https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/podcast/cover.jpg",
}
