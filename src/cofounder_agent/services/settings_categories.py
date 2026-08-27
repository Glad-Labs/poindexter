"""Canonical app_settings category taxonomy + deterministic resolver.

Single source of truth for which category a settings key belongs to. Every
writer (the boot seeder, ``admin_db.set_setting``, the boot reconcile) defers to
``resolve_category()`` so the ``app_settings.category`` column has exactly one
computed answer and cannot drift back into the 62-category / 698-in-'general'
mess it grew into.

``category`` is display-only (it drives the console settings sidebar); nothing
branches on it. Secrets are NOT special-cased — a credential routes to its
subsystem by prefix like any other key (``resend_api_key`` -> ``integrations``,
``anthropic_api_key`` -> ``models``), and the ``is_secret`` DB flag remains
the sole "this is a credential" signal (the console masks on it).

See docs/superpowers/specs/2026-07-11-settings-category-taxonomy-design.md.
"""

from __future__ import annotations

# id -> label, in sidebar order. ``general`` is the fallback, deliberately NOT
# a member here (it is what resolve_category returns when nothing matches).
CATEGORIES: list[dict[str, str | int]] = [
    {"id": "identity", "label": "Identity & Site", "order": 1},
    {"id": "pipeline", "label": "Pipeline", "order": 2},
    {"id": "content", "label": "Content & Knowledge", "order": 3},
    {"id": "quality", "label": "Quality / QA", "order": 4},
    {"id": "models", "label": "Models & LLM", "order": 5},
    {"id": "media", "label": "Media", "order": 6},
    {"id": "distribution", "label": "Distribution", "order": 7},
    {"id": "cost", "label": "Cost & Finance", "order": 8},
    {"id": "observability", "label": "Observability", "order": 9},
    {"id": "self_healing", "label": "Brain & Self-Healing", "order": 10},
    {"id": "plugins", "label": "Plugins", "order": 11},
    {"id": "integrations", "label": "Integrations", "order": 12},
    {"id": "infrastructure", "label": "Infrastructure", "order": 13},
]

CATEGORY_IDS: frozenset[str] = frozenset(str(c["id"]) for c in CATEGORIES)

# Exact-key overrides for names whose prefix would otherwise mislead. Checked
# before the prefix rules.
_OVERRIDES: dict[str, str] = {
    "default_media_to_generate": "media",
    "default_ollama_model": "content",
    "default_template_slug": "pipeline",
    "default_workflow_gates": "pipeline",
    "preferred_ai_video_style": "media",
    "preferred_ollama_model": "models",
    "generative_video_model": "media",
    "structured_extraction_model": "models",
    "embed_model": "models",
    "embed_num_gpu": "models",
    "use_ollama": "models",
    "use_prefect_orchestration": "pipeline",
    "trace_recent_limit": "observability",
    "agent_persona_name": "identity",
    "console_public_url": "identity",
    "host_home": "infrastructure",
    "repo_root": "infrastructure",
    "environment": "infrastructure",
    "development_mode": "infrastructure",
    "app_version": "infrastructure",
    "privacy_email": "identity",
    "enforce_niche_allowlist": "content",
    "hn_min_score": "content",
    "hn_top_stories": "content",
    "trusted_source_domains": "content",
    "hallucination_whitelist_additions": "quality",
    "guardrails_competitor_list": "quality",
    "guardrails_enabled": "quality",
    "link_check_skip_status_codes": "observability",
    "thinking_model_substrings": "models",
    "router_feedback_alpha": "models",
    "create_post_dedup_threshold": "content",
    "enabled_topic_sources": "content",
    "flux_schnell_server_url": "media",
    "wan_server_url": "media",
    "nvidia_exporter_url": "observability",
    "allowed_origins": "infrastructure",
    "disable_auth_for_dev": "infrastructure",
    "internal_api_base_url": "infrastructure",
    "local_database_url": "infrastructure",
    "local_llm_api_url": "models",
    "use_prefect": "pipeline",
    "skill_importer_allowed_licenses": "plugins",
    "youtube_attribution_enabled": "media",
    "youtube_oembed_timeout_seconds": "media",
    "enable_image_gen_warmup": "media",
    "enable_writer_self_review": "content",
    "enable_tracing": "observability",
    "enable_pyroscope": "observability",
    # Seed-gap audit 2026-08-26 (stack#3366): bare-named keys with no
    # shared prefix rule. Categorized here so they leave the "general"
    # residual (test_general_residual_is_small).
    "arxiv_base_url": "content",  # url_scraper research source (no url_scraper prefix)
    "scraper_bot_name": "content",  # url_scraper UA (no url_scraper prefix)
    "banned_transition_opener_threshold": "quality",  # content_validator threshold
    "buzzword_density_threshold": "quality",  # content_validator threshold
    "enable_query_monitoring": "observability",
    "slow_query_threshold_ms": "observability",
    "operational_metrics_window_hours": "observability",
    "worker_hang_dump_seconds": "infrastructure",  # matches worker_heartbeat -> infrastructure
}

# (prefix, category). First match wins; the list is sorted longest-prefix-first
# at module load so a specific prefix (``daily_spend``) beats a general one
# (``daily``), ``mcp_http_probe`` beats ``mcp``, ``auto_publish`` beats ``auto``.
_PREFIX_RULES_RAW: list[tuple[str, str]] = [
    # identity
    ("identity", "identity"),
    ("site", "identity"),
    ("company", "identity"),
    ("owner", "identity"),
    ("support", "identity"),
    ("brand", "identity"),
    ("legal", "identity"),
    ("operator_id", "identity"),
    # pipeline
    ("pipeline", "pipeline"),
    ("prefect", "pipeline"),
    ("auto_publish", "pipeline"),
    ("staging", "pipeline"),
    ("stage_", "pipeline"),
    ("require_human", "pipeline"),
    ("publish_", "pipeline"),
    ("approval", "pipeline"),
    ("gate_", "pipeline"),
    ("stale_task", "pipeline"),
    ("scheduled_", "pipeline"),
    ("scheduler_", "pipeline"),
    ("max_posts", "pipeline"),
    ("max_task", "pipeline"),
    ("max_approval", "pipeline"),
    ("experiment", "pipeline"),
    ("active_pipeline", "pipeline"),
    ("atom_runs", "pipeline"),
    ("dev_diary", "pipeline"),
    ("daily_post", "pipeline"),
    ("daily_budget", "pipeline"),
    ("regen_", "pipeline"),
    ("template_runner", "pipeline"),
    # content & knowledge
    ("content", "content"),
    ("writer", "content"),
    ("writing", "content"),
    ("rag_", "content"),
    ("seo", "content"),
    ("topic", "content"),
    ("niche", "content"),
    ("citation", "content"),
    ("embedding", "content"),
    ("embed_", "content"),
    ("auto_embed", "content"),
    ("auto_append", "content"),
    ("memory", "content"),
    ("newsletter", "content"),
    ("title", "content"),
    ("code_density", "content"),
    ("internal_link", "content"),
    ("serper", "content"),
    ("igdb", "content"),
    ("research", "content"),
    ("hn_", "content"),
    ("devto", "content"),
    ("content_originality", "content"),
    ("url_scraper", "content"),
    ("seed_url", "content"),
    ("trusted_source", "content"),
    # quality
    ("qa_", "quality"),
    ("qa.", "quality"),
    ("quality", "quality"),
    ("deepeval", "quality"),
    ("ragas", "quality"),
    ("self_consistency", "quality"),
    ("unlinked", "quality"),
    ("min_curation", "quality"),
    ("guardrails", "quality"),
    ("hallucination", "quality"),
    ("content_validator", "quality"),
    # models & llm
    ("model", "models"),
    ("cloud_api", "models"),
    ("llm", "models"),
    ("anthropic", "models"),
    ("openai", "models"),
    ("openrouter", "models"),
    ("gemini", "models"),
    ("groq", "models"),
    ("ollama", "models"),
    ("thinking_model", "models"),
    ("router_feedback", "models"),
    ("structured_extraction", "models"),
    # media
    ("media", "media"),
    ("image", "media"),
    ("post_edit", "media"),
    ("short_video", "media"),
    ("inline_image", "media"),
    ("video", "media"),
    # VHS-recorded CLI footage used as a video shot source (poindexter#937).
    # Sits before the bare "demo" prefix would ever be tempting: these are
    # media-lane rendering knobs, not demo/sample-data toggles.
    ("demo_clip", "media"),
    ("podcast", "media"),
    ("voice", "media"),
    ("tts", "media"),
    ("vision", "media"),
    ("stable_audio", "media"),
    ("audio", "media"),
    ("sdxl", "media"),
    ("caption", "media"),
    ("livekit", "media"),
    ("elevenlabs", "media"),
    ("pexels", "media"),
    ("unsplash", "media"),
    ("flux", "media"),
    ("wan", "media"),
    ("alt_text", "media"),
    ("preferred_ai_video", "media"),
    ("youtube", "media"),
    # Media-lane watchdogs + recovery jobs (subsystem-first, like the
    # infra-component probes above): the narration-failure pager, the hero
    # i2v fallback pager, and the two Stage-1 backfill jobs are all knobs on
    # the media pipeline, not generic observability.
    ("narration", "media"),
    ("hero_fallback", "media"),
    ("backfill_media_scripts", "media"),
    ("backfill_video_shot_lists", "media"),
    # distribution
    ("social", "distribution"),
    ("publishing", "distribution"),
    ("postiz", "distribution"),
    ("offsite", "distribution"),
    ("reddit", "distribution"),
    ("bluesky", "distribution"),
    ("linkedin", "distribution"),
    ("indexnow", "distribution"),
    # cost & finance
    ("affiliate", "cost"),
    ("cost", "cost"),
    ("finance", "cost"),
    ("mercury", "cost"),
    ("lemon", "cost"),
    ("pro_delivery", "cost"),
    ("stripe", "cost"),
    ("electricity", "cost"),
    ("daily_spend", "cost"),
    ("monthly_spend", "cost"),
    # observability
    ("findings", "observability"),
    ("grafana", "observability"),
    ("prometheus", "observability"),
    ("langfuse", "observability"),
    ("glitchtip", "observability"),
    ("sentry", "observability"),
    ("uptime", "observability"),
    ("alertmanager", "observability"),
    ("alert_", "observability"),
    ("monitoring", "observability"),
    ("observability", "observability"),
    ("logging", "observability"),
    ("log_", "observability"),
    ("max_log", "observability"),
    ("tracing", "observability"),
    ("trace_", "observability"),
    ("otel", "observability"),
    ("pyroscope", "observability"),
    ("data_fabric", "observability"),
    ("data_freshness", "observability"),
    ("beacon", "observability"),
    ("live_activity", "observability"),
    # Cofounder chat surface (poindexter#947/#949) — operator UI + agent
    # knobs live with the other console-surface settings.
    ("console_chat", "integrations"),
    ("smart_monitor", "observability"),
    ("branch_drift", "observability"),
    ("pr_staleness", "observability"),
    ("morning_brief", "observability"),
    ("page_view", "observability"),
    ("analytics", "observability"),
    ("performance", "observability"),
    ("probe_webhook", "observability"),
    ("webhook_freshness", "observability"),
    ("cadence_slo", "observability"),
    ("nvidia_exporter", "observability"),
    ("task_failure_alert", "observability"),
    # self-healing — the firefighter/brain engine itself. Infra-component
    # probes (compose_drift, docker_port_forward, migration_drift, clock_skew,
    # prefect_stuck, finance_poll) route to their subsystem by prefix, per the
    # spec's subsystem-first grouping — keeps each namespace in one bucket.
    ("brain", "self_healing"),
    ("ops_firefighter", "self_healing"),
    ("ops_triage", "self_healing"),
    ("firefighter", "self_healing"),
    ("self_heal", "self_healing"),
    ("mcp_http_probe", "self_healing"),
    ("notification", "self_healing"),
    ("anticipation", "self_healing"),
    # plugins
    ("plugin", "plugins"),
    # integrations
    ("integration", "integrations"),
    ("mcp", "integrations"),
    ("webhook", "integrations"),
    ("cloudflare", "integrations"),
    ("cloudinary", "integrations"),
    ("storage", "integrations"),
    ("resend", "integrations"),
    ("smtp", "integrations"),
    ("google", "integrations"),
    ("tap_", "integrations"),
    ("external_tap", "integrations"),
    ("discord", "integrations"),
    ("telegram", "integrations"),
    ("notion", "integrations"),
    ("openclaw", "integrations"),
    ("gh_", "integrations"),
    ("github", "integrations"),
    # infrastructure (incl. app auth/access config)
    ("infrastructure", "infrastructure"),
    ("system", "infrastructure"),
    ("gpu", "infrastructure"),
    # Game mode parks containers and gates GPU admission — same family as the
    # gpu/docker/compose keys above (services/game_mode.py).
    ("game_mode", "infrastructure"),
    ("docker", "infrastructure"),
    ("compose", "infrastructure"),
    ("restore", "infrastructure"),
    ("backup", "infrastructure"),
    ("migration", "infrastructure"),
    ("clock", "infrastructure"),
    ("rate_limit", "infrastructure"),
    ("cli_", "infrastructure"),
    ("shared_http", "infrastructure"),
    ("redis", "infrastructure"),
    ("scripts", "infrastructure"),
    ("revalidate", "infrastructure"),
    ("settings", "infrastructure"),
    ("operator", "infrastructure"),
    ("database", "infrastructure"),
    ("worker_heartbeat", "infrastructure"),
    ("vram", "infrastructure"),
    ("preview_base", "infrastructure"),
    ("public_site", "infrastructure"),
    ("api_", "infrastructure"),
    ("auth", "infrastructure"),
    ("oauth", "infrastructure"),
    ("cors", "infrastructure"),
    ("jwt", "infrastructure"),
    ("secret", "infrastructure"),
]

# Longest prefix first so specific beats general.
_PREFIX_RULES: list[tuple[str, str]] = sorted(
    _PREFIX_RULES_RAW, key=lambda r: len(r[0]), reverse=True
)


def resolve_category(key: str) -> str:
    """Deterministic key -> canonical category id. Pure function, no I/O.

    Order: exact override -> longest-matching prefix -> ``general`` fallback.
    """
    if key in _OVERRIDES:
        return _OVERRIDES[key]
    for prefix, category in _PREFIX_RULES:
        if key.startswith(prefix):
            return category
    return "general"
