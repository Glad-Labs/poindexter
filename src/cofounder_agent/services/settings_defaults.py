"""Consolidated default values for app_settings keys (#379).

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

from services.settings_categories import resolve_category

# Every value is stored as `str` because `app_settings.value` is a TEXT
# column. Numeric / bool consumers parse via `site_config.get_int()`,
# `get_float()`, `get_bool()` etc.
DEFAULTS: dict[str, str] = {
    # ----- Localization -----
    # IANA timezone the system schedules cron jobs in and renders
    # operator-facing timestamps in (store-UTC / present-local, services/clock.py).
    # OSS default UTC — location-neutral; the operator overlay sets the real zone.
    "operator_timezone": "UTC",
    # ----- Console task-trace (per-node capture) -----
    # Max bytes of a node's changed-output snapshot stored in
    # atom_runs.output_preview (the readable per-node preview the console
    # trace deep-dive shows). Bounded so a full draft never lands in every
    # row. Capture itself is gated by atom_runs_capture_enabled (baseline).
    "atom_runs_output_preview_max_bytes": "2048",
    # How many recently-finished tasks the trace board's "recent" rail shows
    # (GET /api/trace/active). The running list is unbounded (few at a time).
    "trace_recent_limit": "10",
    # ----- Console live-activity pulse (what's the system doing now) -----
    # A running live_activity row counts as "live" only if its heartbeat
    # (updated_at) is within this window — filters out orphaned rows whose
    # producer died mid-run before the reaper marks them stale.
    "live_activity_freshness_seconds": "120",
    # Sidecar-heartbeat cadence: long-running producers (jobs, and the content
    # graph run) bump updated_at this often so a multi-minute run stays inside
    # the freshness window above instead of being hidden as "idle". MUST be
    # shorter than the freshness window (a live producer misses ~4 beats before
    # it would be hidden).
    "live_activity_heartbeat_seconds": "30",
    # Running rows with no heartbeat past this get marked 'stale' by the reaper.
    "live_activity_reaper_seconds": "300",
    # Size of the "Just Happened" recent-trail rail on the console band. Kept
    # compact so the SYSTEM PULSE band stays a glance, not a scrolling log — the
    # trail is the band's height driver when the live columns are idle.
    "live_activity_recent_limit": "8",
    # ----- Cofounder console chat (poindexter#947 P1) -----
    # Master switch for the /api/chat conversation surface. Ships off; the
    # operator flips it once a tool-capable provider is configured for the
    # chat tier (litellm / openai_compat — ollama_native fails loud on tools).
    "console_chat_enabled": "false",
    # Which brain answers: 'local' (tool loop over the routed LLM seam — the
    # OSS default) or 'claude_code' (operator Deep mode, P6 — the store +
    # protocol accept it now so P6 is a brain swap, not a schema change).
    "console_chat_brain": "local",
    # Chat model: small + resident so an interactive turn coexists with
    # render lanes instead of evicting them. Same pick as the voice agent
    # (benchmarked 2026-07-31: right tool 4/4, 0.22s, 4.7 GB).
    "console_chat_model": "qwen2.5:7b",
    # Whole-turn deadline (LLM calls + tool executions). On expiry the turn
    # persists as 'interrupted' and the stream says so — never a silent hang.
    "console_chat_turn_timeout_s": "120",
    # Loop guard: max tool executions per turn before the turn aborts loud.
    "console_chat_max_tool_calls": "8",
    # Daily token budget for the chat surface, independent of the global
    # cost_guard caps — a runaway conversation can't eat the day's budget.
    "console_chat_daily_token_budget": "200000",
    # Context tail: how many recent messages ride into the model context.
    # Older turns are dropped (rolling summary + embedding recall are the
    # documented follow-ups; small local models need the hard cap first).
    "console_chat_context_recent_turns": "12",
    # Tool results enter model context as digests capped at this many chars
    # (cards reference DB rows for the full payload) — a 3k-word draft in a
    # 7B context is how small models die (num_ctx is a shared ceiling).
    "console_chat_tool_result_max_chars": "2000",
    # Persona name rendered into the chat system prompt. A setting, not a
    # constant: one identity across chat + voice surfaces, and a SaaS
    # customer names their own cofounder.
    "agent_persona_name": "Poindexter",
    # Rail poll cadence for watched pipeline runs (seconds). Served to the
    # console via GET /api/chat/tools so the client never hardcodes it.
    "console_chat_watch_poll_seconds": "5",
    # Completion-ping channel for chat-linked runs reaching a terminal
    # status: discord (routine, default) | telegram (pushes to phone —
    # reserve for operators who want draft-ready pings loud) | none.
    # The system message in the thread lands regardless.
    "console_chat_watch_notify": "discord",
    # Browser-facing console base URL for deeplinks in completion pings
    # (e.g. your tailnet URL http://100.x.y.z:8002). Empty = no link,
    # text-only pings.
    "console_public_url": "",
    # ----- Self-healing firefighter (deterministic core, Plan A) -----
    # Master switch. Ships enabled; the remediation_rules table is empty so it's
    # a safe no-op until rules are seeded. Off = pages exactly as today.
    "ops_firefighter_enabled": "true",
    # Circuit breaker: at most N attempts of the same (alert, action) inside the
    # window before the firefighter stops trying and pages.
    "ops_firefighter_max_attempts_per_window": "3",
    "ops_firefighter_window_minutes": "60",
    # Grace period before the verify scan re-checks whether the alert cleared.
    "ops_firefighter_verify_after_seconds": "120",
    # Global backstop across ALL actions, per rolling hour.
    "ops_firefighter_max_actions_per_hour": "10",
    # CSV of enabled action_names; empty = all registered actions allowed.
    # Per-action-type kill switch (e.g. "restart_container" to allow only that).
    "ops_firefighter_action_allowlist": "",
    # ----- LLM long-tail selector (Plan B) -----
    # For an alert with NO deterministic rule, a small local model picks an action
    # from the registry allowlist (or abstains). Runs on the worker via
    # dispatch_complete (the brain image has no LLM libs), so the tag carries the
    # ``ollama/`` litellm prefix like its sibling ops_triage_writer_model.
    "ops_firefighter_model": "ollama/llama3.2:3b",
    # Persistence gate: the LLM path engages only after an un-ruled alert has
    # repeated this many times (a one-off blip pages as usual; only a persistent
    # un-ruled alert is worth an inference call).
    "ops_firefighter_min_repeats": "2",
    # Confidence gate: an LLM selection below this score pages instead of acting.
    "ops_firefighter_min_confidence": "0.6",
    # Long-tail master switch — turn OFF to keep only the deterministic rule path
    # (Plan A) even while ops_firefighter_enabled stays true.
    "ops_firefighter_llm_longtail_enabled": "true",
    # Persistence gate (alternative to min_repeats): an un-ruled alert also
    # qualifies for the LLM path once it has been firing this many minutes.
    "ops_firefighter_min_age_minutes": "10",
    # Circular-dependency guard — the LLM path is SKIPPED for alerts whose name
    # matches this regex, so the model is never asked to fix the substrate it
    # runs on (Ollama / GPU / inference). Those stay deterministic-rule-only.
    # Keep in sync with rules._DEFAULT_LLM_EXCLUDE_REGEX.
    "ops_firefighter_llm_exclude_regex": "(?i)(ollama|gpu|vram|cuda|inference)",
    # ----- Identity / branding -----
    # Operator identity is generic on OSS (each install is its own company); the
    # Glad Labs operator overlay (services.operator_overrides) restores Matt /
    # Glad Labs. Keep these identical to the 0000_baseline.seeds.sql seeds so the
    # overlay's "overwrite only the OSS default" match fires. company_founded_date
    # keeps the content_validator fallback date (non-empty → date math is safe).
    'app_version': '3.0.1',
    'company_name': '',
    'company_founder_name': '',
    'company_founded_date': '2025-01-01',
    'development_mode': 'false',
    'disable_auth_for_dev': 'false',
    'environment': 'development',
    'owner_name': '',
    'site_domain': '',
    'site_name': '',
    'site_url': '',

    # ----- Off-machine backup (Tier 2, #386) -----
    # Non-secret tunables for the backup-offsite restic loop + the brain
    # offsite_backup_watch probe. Seeded every boot (reaches existing
    # deployments, not just fresh installs). The 3 secrets
    # (offsite_backup_restic_password / _s3_access_key_id /
    # _s3_secret_access_key) are NOT here — the wizard writes them encrypted.
    'offsite_backup_enabled': 'true',
    'offsite_backup_interval': '24h',
    'offsite_backup_keep_daily': '7',
    'offsite_backup_keep_monthly': '6',
    'offsite_backup_keep_weekly': '4',
    'offsite_backup_max_age_hours': '26',
    'offsite_backup_prune_enabled': 'false',
    'offsite_backup_repository': '',
    # Stable `restic backup --host` value — the container hostname changes on
    # every recreate, which would otherwise start a new snapshot lineage
    # ("no parent snapshot found") and force a full source rescan.
    'offsite_backup_restic_host': 'poindexter',
    'offsite_backup_restic_image': 'restic/restic:0.16.4',
    'offsite_backup_s3_region': '',
    'offsite_backup_source_tier': 'daily',
    'offsite_backup_verify_enabled': 'true',
    'offsite_backup_verify_interval_hours': '168',
    'offsite_backup_verify_read_data_subset_percent': '5',
    'offsite_backup_watch_enabled': 'true',
    'offsite_backup_watch_max_retries': '2',
    'offsite_backup_watch_retry_delay_seconds': '120',

    # Non-secret tunables for the brain auto_embed_watch probe — the
    # self-heal-before-paging liveness watch for the auto-embed sidecar
    # (sibling of offsite_backup_watch). Reads the auto_embed_succeeded
    # audit_log heartbeat that scripts/auto-embed.py stamps each run; stale =>
    # `docker restart poindexter-auto-embed`, then a warning-level
    # auto_embed_stale alert on escalate. Seeded every boot. auto-embed runs
    # hourly, so 6h ~= 6 missed cycles before paging.
    'auto_embed_max_age_hours': '6',
    'auto_embed_watch_enabled': 'true',
    'auto_embed_watch_max_retries': '2',
    'auto_embed_watch_retry_delay_seconds': '120',

    # brain branch_drift_probe — minimum commits-behind before prod-is-behind
    # PAGES. A continuously-deploying prod sits perpetually 1-2 commits behind
    # origin/main (auto-deploy trails merges by minutes); that transient lag is
    # healthy steady state, not drift, and each deploy churned the per-head
    # fingerprint so it never deduped (#2295: 69 alerts/7d, 57 just "1 behind").
    # Below this the probe logs the lag but never pages; a genuinely stuck prod
    # accrues a bigger backlog fast (main advances several times/day). The other
    # branch_drift_* keys seed via baseline.seeds.sql; this go-forward key seeds
    # here (the code default in brain/branch_drift_probe.py is the backstop).
    'branch_drift_min_commits_behind': '3',

    # Postiz queue-wedge watch (brain/postiz_queue_watch.py) — detects posts
    # stuck in QUEUE/ERROR past their publishDate via the Postiz API (the
    # Temporal-restart wedge: our social_post_drafts rows read 'posted' but
    # nothing publishes) and `docker restart`s poindexter-postiz before
    # paging. No-ops entirely while postiz_api_key is unset, so these are
    # inert on installs without the opt-in Postiz stack. Retry delay is
    # generous because Postiz + its Temporal worker boot slowly.
    'postiz_queue_watch_enabled': 'true',
    'postiz_queue_overdue_minutes': '30',
    'postiz_queue_watch_max_retries': '2',
    'postiz_queue_watch_retry_delay_seconds': '180',

    # ----- Cost / billing -----
    'daily_spend_limit_usd': '2.0',
    'monthly_spend_limit_usd': '100.0',
    # Electricity rate ($/kWh) used by brain_daemon.py::log_electricity_cost
    # (every 5-min cost_logs write) and the console/Grafana cost surfaces.
    # UpdateUtilityRatesJob refreshes this daily from the EIA API once it has
    # run at least once; this bootstraps a real value from first boot on
    # fresh installs instead of leaving the key unset. Approximate recent
    # U.S. national-average residential rate — a bootstrap constant, not a
    # live data point (glad-labs-stack#2626).
    'electricity_rate_kwh': '0.16',
    # Electricity ledger (cost_ledger.get_spend): prefer the brain's measured PSU
    # rows; fall back to per-call kWh estimates for windows the measured feed
    # didn't cover (HX1500i sampling has been flaky). A sample "covers" up to
    # *_gap_minutes after it; below *_min_coverage_pct of the window => estimated.
    'electricity_measured_min_coverage_pct': '80',
    'electricity_source_gap_minutes': '15',
    # PSU wall-power watchdog debounce: consecutive 5-min brain cycles the
    # metered PSU feed (Shelly outlet meter → iCUE tap) must be unavailable —
    # cost fallen back to the software estimate / static floor — before it pages
    # Telegram. A single slow exporter scrape self-heals next cycle; only a
    # sustained outage (default 3 cycles ≈ 15 min) is a critical page. See
    # brain/psu_power.py::psu_watchdog_transition. (incident 2026-07-12:
    # per-request exporter slowness blew the brain's 3s scrape and paged
    # ~15×/day on 1-cycle blips.)
    'psu_watchdog_degraded_cycles_before_page': '3',

    # ----- Spend throttle (P3) — defer NEW work when total_usd (paid API +
    # measured electricity) crosses a soft budget. Consulted at the
    # content_generation_flow new-work seam (NOT the per-call hot path); fails
    # OPEN so a dead DB never becomes a content outage. The per-call HARD stop is
    # cost_guard (daily/monthly_spend_limit_usd). A budget <= 0 disables that
    # axis; daily is a rate limit (clears at midnight, hysteresis on release),
    # monthly is a cumulative backstop (sticky until rollover). Defaults sit
    # comfortably above real burn — see design §10, both "tweak later".
    'cost_throttle_enabled': 'true',
    'cost_throttle_daily_budget_usd': '3.00',
    'cost_throttle_monthly_budget_usd': '60.00',
    'cost_throttle_resume_buffer_pct': '10',
    # Count IDLE electricity toward the throttle ceilings. Default false: the
    # throttle gates work, and idle draw happens whether or not work runs, so
    # counting it makes the cap measure "was the machine on" instead of "did we
    # spend money" — and deferring work cannot bring it back down, so once idle
    # alone crosses the ceiling the pipeline stays throttled until rollover.
    # 2026-07-31: $61.06 monthly vs a $60 cap, of which $41.73 was idle
    # electricity and $11.37 real cloud spend; content generation stopped for
    # the rest of the month over power drawn while sitting idle. Active
    # electricity stays counted — that IS caused by running work. Set true to
    # restore the pre-2026-07-31 sum.
    'cost_throttle_count_idle_electricity': 'false',

    # ----- LLM model selection -----
    # OSS defaults pin only publicly-pullable Ollama tags so a fresh
    # `poindexter setup` install runs (enforced by
    # tests/unit/services/test_oss_seed_model_hygiene.py). The README quick-start
    # pulls gemma3:27b / qwen3:8b / nomic-embed-text — the core blog pipeline runs
    # on those; a few feature roles (vision QA, podcast/video, voice) name other
    # public tags you pull on demand. Glad Labs production overrides several of
    # these in the DB with custom local fine-tunes (gemma-4-31B-it-qat,
    # glm-4.7-5090) that aren't on the public registry; the rationale comments
    # below describe that prod tuning, not the OSS default.
    'default_ollama_model': 'auto',
    'embed_model': 'nomic-embed-text',
    # Public OSS defaults for roles the Glad Labs operator overlay re-pins to
    # custom local models (see services/operator_overrides.py). Listed here so
    # the overlay's "only overwrite the OSS default" guard has a value to match.
    'pipeline_architect_model': 'ollama/gemma3:27b',
    'podcast_script_model': 'ollama/gemma3:27b',
    'preferred_ollama_model': 'gemma3:27b',
    'inline_image_prompt_model': 'ollama/gemma3:27b',  # inline-image prompt-builder (public default; operator overlay pins the custom fine-tune)
    'local_llm_api_url': 'http://host.docker.internal:11434',
    'model_role_image_decision': 'ollama/gemma3:27b',  # image-director reasoning (public default; operator overlay pins the custom fine-tune)
    # Writer places [IMAGE:]/[HERO-IMAGE:] markers; this caps how many inline
    # images survive normalization (feedback_no_hardcoded_lengths_in_prompts —
    # the prompt states no number, the cap lives here).
    'writer_max_inline_images': '3',
    # Body chars per section fed to the image decision agent fallback so its
    # picks are grounded in content, not just heading titles.
    'image_decision_section_body_chars': '500',
    'pipeline_architect_timeout_seconds': '120.0',
    # why: VRAM guard against the writer (~20GB) + image-gen (~12GB) overlap
    # at the stage-5→stage-7 boundary. Default-on fixes the 24GB-card OOM;
    # operators on 80+GB hardware can flip to 'false' to skip the
    # ~3-5s reload tax (see services/llm_providers/ollama_unload.py).
    'pipeline_writer_unload_before_image_gen': 'true',
    'pipeline_fallback_model': 'ollama/gemma3:27b',
    # Daily-driver content writer. gemma-4-31B won the 2026-06-18 writer bakeoff
    # (98/100): it names grounded specifics without the glm writer's [placeholder]
    # hedging or qwen2.5's stat fabrication, and rarely needs the rescue. The
    # rescue reviser (qa_rewrite_model) also defaults to this writer as of
    # 2026-06-28 (was glm — reverted for VRAM thrash; see that key). Tune live.
    'pipeline_writer_model': 'ollama/gemma3:27b',
    # Per-step pins for the writer-adjacent steps. EMPTY = follow the writer
    # (pipeline_writer_model) — the historical behavior, kept as the default
    # so existing installs see no change. Split out (2026-07-11) because a
    # cloud writer canary billed every step riding the writer pin: SEO
    # metadata is structured, formulaic copy a budget local model handles
    # well (pin it local to keep a paid writer from billing it); a title is
    # user-facing writer-grade copy that SHOULD track the writer — pin it
    # only to decouple titles from a writer experiment.
    'pipeline_seo_model': '',
    'pipeline_title_model': '',
    # Guaranteed-LOCAL writer-grade model for SATELLITE phases — the
    # self-consistency QA probe and dev-diary narration — that call
    # resolve_local_writer_model(). These are NOT the blog draft (which has its
    # own resolver in ai_content_generator); they only self-check or narrate,
    # so they must never bill cloud writer prices when pipeline_writer_model is
    # pinned to a paid model for a writer experiment (the 2026-07-07
    # Sonnet-canary billed both via the writer pin). EMPTY = self-adjust:
    # follow pipeline_writer_model when it is itself local, else fail loud
    # asking for this pin. Set it to a local Ollama tag on any install that
    # runs a PAID writer.
    'pipeline_local_writer_model': '',
    # Adversarial critic — must be a DIFFERENT model family from the writer and
    # reviser so biases don't cancel (cross-model QA principle). phi4:14b is fast,
    # reliable at JSON output, and distinct from Gemma (writer) and GLM (reviser).
    # _resolve_critic_model reads this key first; if empty it falls to
    # qa_fallback_critic_model, then raises (notify_operator + RuntimeError).
    # Empty-string sentinel would halt every canonical_blog run — seed a real default.
    'pipeline_critic_model': 'ollama/phi4:14b',
    # Ops alert-triage (firefighter /api/triage) is a one-paragraph diagnosis,
    # NOT content — it defaults to the small free-tier model, never the 19 GB
    # writer. Unset before, it fell through to pipeline_writer_model, so a triage
    # reloaded the writer into VRAM mid-media-render and CUDA-OOM'd the image-gen
    # server (2026-06-21). A 2 GB model coexists with wan + image-gen on a 32 GB card,
    # so triage can't oversubscribe the GPU even when un-gated.
    'ops_triage_writer_model': 'ollama/llama3.2:3b',
    # Video director + self-critique run on the writer model — scene judgment is
    # the top video-quality lever (video-quality spec §3.1). One shared key feeds
    # both the generate_video_shot_list draft pass and the review_video_shot_list
    # critique; kept equal to pipeline_writer_model (asserted in tests). Was unset
    # before, falling through to default_ollama_model=auto → weak standard tier.
    'video_director_model': 'ollama/gemma3:27b',
    # Per-call ceiling (seconds) for the director LLM dispatch — long + short
    # shot lists. Writer-grade director models (gemma-4-31B) emit a full
    # structured shot list (max_tokens 6144) and need well over the old
    # hardcoded 120s, which was timing out and leaving an empty shot list so
    # Stage-2 video never rendered. Read via cfg.get_int in
    # modules/content/stages/generate_video_shot_list.py.
    'video_director_timeout_seconds': '300',
    # Disable the director / reviewer model's reasoning channel (default). The
    # thinking-capable director (gemma-4-31B-it-qat) otherwise spends the shared
    # 6144-token output budget reasoning and starves the JSON shot list — the
    # visible content comes back empty, so _extract_json_object fails and the
    # shot list is lost, meaning Stage-2 video never renders (audit:
    # shot_list_failed phase=json_extract; 2026-07-07 investigation reproduced
    # the empty-content overflow in-container). Mirrors writer_disable_thinking
    # (#2163). Harmless no-op on a non-thinking model, and dropped for cloud
    # targets by the LiteLLM provider. Set false only to deliberately let a
    # reasoning director chain-of-think. Read via _resolve_director_think in
    # modules/content/stages/generate_video_shot_list.py.
    'video_director_disable_thinking': 'true',
    # Output-token ceiling for the director / reviewer JSON dispatch. With
    # thinking disabled the whole budget is the shot list, but a verbose director
    # occasionally serializes a long list past the old hardcoded 6144, truncating
    # the JSON mid-object → no complete object → empty shot list → no video
    # (2026-07-07: a real 300s post, d1979ebb, truncated even with think=False).
    # 8192 gives headroom. Read via cfg.get_int in generate_video_shot_list.py.
    'video_director_max_tokens': '8192',
    # Retries when the director / reviewer emits no extractable JSON object (the
    # truncation / empty-output symptom). A fresh generation almost always lands
    # a complete list — cheaper than losing the post's video to one unlucky run.
    # 0 disables retrying; dispatch *exceptions* (infra) never retry regardless.
    'video_director_max_retries': '1',
    # Per-shot vision-QA render-check loop (video-quality spec §3.2). The render
    # loop in shot_list_renderer scores each rendered frame with qa_vision_model
    # and regenerates (stochastic sources) or falls back to holdover on a miss.
    # site_config=None (legacy/test path) disables it regardless of this default.
    'video_shot_qa_enabled': 'true',
    'video_shot_qa_threshold': '60',
    'video_shot_qa_max_retries': '2',
    # A "pexels" shot tries a real Pexels VIDEO clip before the still-photo
    # fallback (shot_list_renderer._render_pexels_video / _render_pexels_image)
    # — the director prompt has always described this source as real footage.
    # Kill switch: false skips straight to the photo path (e.g. no egress to
    # videos.pexels.com, or an operator wants stills-only for brand reasons).
    'video_pexels_video_enabled': 'true',
    # Video-quality Piece 4 (spec §3.3) — Wan 2.2 TI2V-5B image-to-video hero
    # renderer. ``generative_video_model`` is the swappable model seam (a HF
    # repo id; point at a 14B / LTX checkpoint later with no code change) read
    # by the Wan provider + the wan-server. ``video_hero_shots_max`` caps the
    # per-video count of heavy generative i2v renders; the renderer downgrades
    # excess hero shots to image_kenburns (see shot_list_renderer._cap_hero_shots).
    'generative_video_model': 'Wan-AI/Wan2.2-TI2V-5B',
    'video_hero_shots_max': '3',
    # Hero (i2v) render geometry — Wan 2.2 TI2V-5B's documented 720P@24fps
    # working range. Authored landscape-first; the renderer swaps
    # width/height for the portrait (9:16) short lane
    # (shot_list_renderer._hero_render_dims). Drop back to 832x480@16 to
    # trade quality for VRAM headroom / render time on smaller cards.
    'video_hero_width': '1280',
    'video_hero_height': '704',
    'video_hero_fps': '24',
    # Hard-unload image-gen immediately before each wan hero load
    # (poindexter#907 defect 2). The dispatch-time VRAM gate checks free VRAM
    # once, before the flow starts, and holds no reservation for the minutes
    # the render takes — so image-gen can (and did) load mid-flow and crowd wan
    # out. Measured 2026-07-29: gate passed at 29.4 GB free, image-gen took
    # 25.1 GB illustrating the next article, wan OOM'd with 98 MB free while
    # holding just 1.82 GB itself. 18 hero_render_fallback findings trace here.
    #
    # A soft /unload doesn't return the VRAM (the process keeps its CUDA
    # reserved pool), so this is the hard unload; image-gen lazy-reloads on its
    # next /generate. Repeats are cheap — once it holds nothing the server
    # declines the exit (nothing_to_reclaim), so only the first call pays.
    # Turn OFF on a card that comfortably fits both, to skip the cold reload.
    'video_hero_unload_image_gen': 'true',
    # Pause after the unload so the CUDA context actually returns to the host
    # before wan asks for it — the process exit is asynchronous from the
    # renderer's point of view.
    'video_hero_unload_settle_seconds': '3',
    # Motion direction appended to the i2v prompt when a shot list predates
    # the Shot.motion field (frozen Stage-1 lists are re-read weeks later).
    # The i2v model needs motion language — the still's own description
    # gives it nothing to animate.
    'video_hero_motion_default': (
        'slow cinematic push-in with gentle parallax; ambient particles and '
        'light drift softly; smooth continuous motion, stable composition'
    ),
    # Canonical short-form target length in seconds (issue #867): drives BOTH the
    # short prompt's narration ask (generate_media_scripts._build_scene_prompt)
    # AND the shot-list duration clamp (generate_video_shot_list.
    # _estimate_short_duration), so they can't disagree — they used to (a 60s
    # prompt vs a 45s clamp guaranteed a ~15s frozen tail on every compliant
    # short). The word target is derived: round(target * 2.5 WPS).
    'video_short_target_seconds': '45',
    # Hard cap on the short narration length in seconds (issue #867). A runaway
    # short script (the model ignoring the target) is trimmed to the last full
    # sentence within this budget so a "short" can't balloon to 2-3 minutes. Set
    # > video_short_target_seconds to leave headroom — the renderer's
    # narration-fit stretches the gap between target and this cap. An advisory
    # short_script_trimmed finding fires when a trim bites.
    'video_short_max_seconds': '60',
    # Minimum fraction of shots that must render for a video to SHIP
    # (modules/content/atoms/_media_render.py). Below this ratio the render is
    # treated as failed — empty output key, render_failed-class finding — so
    # the media_reconciliation watchdog retries it instead of a badly degraded
    # video shipping (2026-07-03: task 9318d724 shipped 2/7 shots). Partials
    # at-or-above the ratio still ship and still emit the partial_render
    # finding. '0' disables the gate (every partial ships, prior behaviour).
    # DEPRECATED 2026-07-15: superseded by video_render_min_real_source_ratio.
    # The "never drop a shot" ladder makes shots_rendered == shots_total on the
    # happy path (failures become substitutes/cards), so a drop-count gate no
    # longer sees an all-cards outage. Left seeded (inert) for backcompat.
    'video_render_min_shot_ratio': '0.5',
    # Master switch for the guaranteed rung-3 branded card in the shot-list
    # renderer's fallback ladder. true (default) ⇒ a shot that renders no clip
    # from any real source is filled with a branded card so the timeline stays
    # whole (never drop a shot). false ⇒ legacy behaviour (the shot drops).
    'video_fallback_card_enabled': 'true',
    # Real-source ratio ship gate (2026-07-15). The fraction of a video's shots
    # that must come from a REAL source — primary/holdover render or a
    # cross-family Pexels substitute — vs. rung-3 branded-card fill:
    # (shots_rendered - shots_carded) / shots_total. Below this the render is
    # treated as FAILED (empty output key → media_reconciliation re-dispatch)
    # rather than shipping a mostly-card video during an image-gen/Pexels
    # outage. At-or-above still ships and emits the partial_render finding. '0'
    # disables the reject gate. Replaces video_render_min_shot_ratio.
    'video_render_min_real_source_ratio': '0.5',
    # Short-lane fit-to-narration (issue #867). Master switch: the short renderer
    # rescales the director's shots to span the ACTUAL narration so the final
    # frame is never frozen to cover an overhang (the frozen tail). false =>
    # legacy behaviour (compositor clones the last frame over the overhang).
    'video_narration_fit_enabled': 'true',
    # Per-shot ceiling for the narration-fit rescale (issue #867) — no single
    # image is held longer than this; beyond it the shot sequence cycles
    # (repeating visuals at a steady cadence) instead of stretching one frame.
    'video_short_max_shot_seconds': '9',
    # Canonical LONG-form video narration target in seconds (silent-tail fix,
    # 2026-07-31): drives the video.long_form_narration prompt's ask — the word
    # target is derived (round(target * 2.5 WPS)) — the same one-canonical-target
    # pattern as video_short_target_seconds. Before this the long prompt had NO
    # length ask at all, so narration length was whatever the model felt like
    # (358-592 words observed) while the director planned visuals from the much
    # longer PODCAST script — every long video ran 1-2 minutes past the voice.
    'video_long_target_seconds': '180',
    # Hard cap on the long narration length in seconds. A runaway long script is
    # trimmed to the last full sentence within this budget (advisory
    # long_script_trimmed finding), and the director's target-duration estimate
    # clamps here too — one ceiling for both, so they can't disagree.
    'video_long_max_seconds': '300',
    # Minimum word count for a usable short-form script (2026-08-01). Below
    # this after meta-commentary sanitization the scene call retries ONCE;
    # still under → the task ships with NO short + a short_script_invalid
    # finding, instead of freezing garbage into task_metadata forever (a
    # frozen 1-word "**END**" short TTS'd into a 12s-visuals / 3.8s-voice
    # render that no re-render could fix).
    'video_short_min_words': '25',
    # Per-shot ceiling for the LONG-lane narration-fit rescale — the long-form
    # sibling of video_short_max_shot_seconds. Long-form pacing legitimately
    # holds a shot 15-30s (the director's own per-shot rule caps at 30), so the
    # short lane's 9s ceiling would shred it into cycled fragments on a modest
    # stretch. Beyond this the shot sequence cycles instead of stretching.
    'video_long_max_shot_seconds': '30',
    # Per-shot FLOOR for the narration-fit compression direction (both lanes).
    # When the visuals outrun the narration the fit scales every shot down
    # proportionally; no shot is compressed below this, so a deep over-plan
    # can't turn the hook into a subliminal flicker.
    'video_fit_min_shot_seconds': '2',
    # How long the visuals may outlive the voice, in seconds (both lanes). The
    # narration-fit compression triggers only beyond this and then aims the
    # visual track at narration + this hold — a short outro beat after the voice
    # stops reads better than a hard cut, while minutes of silent footage (the
    # 2026-07 long-video rejections: a 300s plan over a ~175s narration) never
    # ship. media.qa's silent-tail check allows the same hold before flagging.
    'video_fit_trailing_hold_seconds': '3',
    # Render-GPU VRAM preflight (2026-07-12 desktop-lockup fix). The Wan render
    # loads ~24 GB onto pipeline_gpu_index (the RTX 5090 that also drives the
    # desktop). dispatch_media_pipeline defers the whole cycle unless the card
    # has at least media_render_min_free_vram_gb free — so a render can never
    # oversubscribe the 32 GB card and spill WDDM into system RAM (freezing the
    # desktop). Fail-closed: an unreadable Prometheus VRAM reading defers too.
    # Read in services/media_infra_health.py::check_media_infra_health via
    # services/render_vram.py::render_gpu_free_vram_gb.
    'media_render_vram_gate_enabled': 'true',
    'media_render_min_free_vram_gb': '25',
    # VRAM reclaim (PR 2, 2026-07-12): when the gate fails specifically on
    # VRAM (not a wan/image-gen/DNS outage), dispatch_media_pipeline attempts
    # ONE bounded reclaim — evict Ollama + hard-unload image-gen (exits its
    # process so the CUDA context actually returns; empty_cache() alone
    # doesn't under WSL2) — then re-probes once before deferring. Settle
    # gives the freed VRAM time to actually show up in the next Prometheus
    # scrape before the re-probe reads it.
    'media_render_reclaim_enabled': 'true',
    'media_render_reclaim_settle_seconds': '8',
    # Cooldown after a reclaim that ran and left the gate still unhealthy
    # (2026-07-28). The per-cycle logic was already correct — reclaim only with
    # eligible work AND a specifically-VRAM failure — but on a 5-minute cron a
    # reclaim that CANNOT help just repeats forever. Observed 2026-07-27:
    # fired every cycle for 2+ hours, freed nothing each time, and every one
    # restarted image-gen and opened a window that downgraded article images.
    # The render never happened either way, so each exit was pure loss.
    'media_render_reclaim_cooldown_minutes': '30',
    # Idle-only host-side WSL/Docker GPU reset (PR 2, 2026-07-12). Read by
    # scripts/idle_wsl_gpu_reset_check.py (invoked by the host-side
    # scripts/idle-wsl-gpu-reset.ps1 Scheduled Task) to clear the ~8.6 GB of
    # stale vmwp/vmmemWSL GPU retention that survives any container restart —
    # only wsl --shutdown + a Docker Desktop restart returns it. Default OFF:
    # this bounces the whole Docker stack, so it stays opt-in until a
    # supervised live run confirms the stack self-heals cleanly.
    'idle_wsl_reset_enabled': 'false',
    'idle_wsl_reset_min_idle_minutes': '20',
    # MUST stay >= media_render_min_free_vram_gb (25) — the reclaim exists to
    # lift the render GPU back over that floor, so a trigger below it leaves a
    # dead band where renders defer (free < 25) yet the reclaim never fires
    # (free >= trigger). 28 keeps a 3 GB headroom above the floor so the reclaim
    # fires proactively during idle. idle_wsl_gpu_reset_check.decide() also
    # clamps the effective trigger up to the floor as a hard guard (poindexter#881).
    'idle_wsl_reset_trigger_free_vram_gb': '28',
    'idle_wsl_reset_cooldown_hours': '6',
    'idle_wsl_reset_inflight_grace_minutes': '15',
    # Caption ASR engine for media.transcribe_narration. Default 'speaches'
    # reuses the already-running Speaches faster-whisper sidecar (narration TTS /
    # voice STT) instead of a second whisper.cpp install. The prior default,
    # 'whisper_local', shelled a whisper-cli binary that was never baked into the
    # worker image — so transcribe returned success=False and BOTH video lanes
    # rendered with NO burned-in captions (2026-06-21 validation). Set
    # 'whisper_local' to use a locally-installed whisper.cpp instead. Read via
    # services/caption_providers/get_caption_provider.
    # --- demo clips (VHS-recorded CLI footage; Glad-Labs/poindexter#937) ---
    # Real terminal recordings of the `poindexter` CLI, used as video shot
    # sources instead of stock footage. Tapes live in `demo_tapes/` (code, not
    # DB — a tape is a shell script). These keys are the BRAND surface: an
    # operator restyles their clips here without touching a tape.
    #
    # Font size drives terminal COLUMNS (see COLUMNS_AT_FONT_SIZE in
    # services/demo_clips.py) — dropping it too low shrinks text below video
    # legibility; raising it wraps wide tables mid-word. 34 fits ~88 columns
    # at 1920px, which suits most single-command output.
    # Where `poindexter media demos bake` writes clips and the shot-list
    # renderer reads them (cli_demo source). Must be a path BOTH the bake job
    # and the render lane can see — they run in the same worker container
    # today, so a plain host-mounted dir is enough; an object-store URL is a
    # later step (poindexter#937).
    'demo_clip_dir': '/home/appuser/.poindexter/demo-clips',
    'demo_clip_font_family': 'JetBrains Mono',
    'demo_clip_font_size': '34',
    'demo_clip_width': '1920',
    'demo_clip_height': '1080',
    'demo_clip_padding': '60',
    'demo_clip_framerate': '30',
    'demo_clip_typing_speed': '45ms',
    # Palette mirrors the CLI's colourblind-safe status roles
    # (poindexter/cli/_status_style.py) so a recording reads the same way the
    # live terminal does. ANSI green is deliberately mapped onto the amber
    # 'active' colour: nothing in the CLI's semantic maps emits green any
    # more, and any third-party green that slips through should not land on
    # the red-green confusion pair on camera.
    'demo_clip_theme_background': '#18181B',
    'demo_clip_theme_foreground': '#E4E4E7',
    'demo_clip_theme_accent': '#22D3EE',
    'demo_clip_theme_accent_bright': '#67E8F9',
    'demo_clip_theme_active': '#FBBF24',
    'demo_clip_theme_attention': '#C084FC',
    'demo_clip_theme_failure': '#F87171',
    'demo_clip_theme_dim': '#8B8B93',

    'video_caption_engine': 'speaches',
    # Speaches caption provider config (plugin.caption_provider.speaches.*). The
    # base_url is the same OpenAI-compatible host the TTS path already uses; the
    # model is the faster-whisper weight Speaches serves. enabled is a kill switch
    # that fails loud (success=False) rather than silently producing no captions.
    'plugin.caption_provider.speaches.enabled': 'true',
    'plugin.caption_provider.speaches.base_url': 'http://speaches:8000/v1',
    'plugin.caption_provider.speaches.model': 'Systran/faster-whisper-medium',
    'plugin.caption_provider.speaches.timeout_seconds': '180',
    # initial_prompt biases faster-whisper toward the supplied vocabulary (the
    # OpenAI 'prompt' field → initial_prompt). A short comma-separated list of
    # proper nouns nudges the decoder's spelling toward them, so a brand or
    # product name isn't transcribed as an acoustically similar word. Soft bias,
    # not find-replace. Default '' (the NOT-NULL unset sentinel) = no bias =
    # unchanged behaviour; the empty OSS default keeps operator-specific brand
    # vocabulary out of the public mirror — operators set their terms in the DB.
    'plugin.caption_provider.speaches.initial_prompt': '',
    # GPU scheduler — external (non-stack) workload detection. The stack is
    # normally the only thing running models, so cross-process GPU contention is
    # already serialized by the pg_advisory_lock + asyncio.Lock; treating a
    # sibling stack process's legitimate GPU use as "gaming" only causes phantom
    # pipeline pauses (validation finding 4a). Default OFF — set true only when
    # sharing this GPU with a non-stack app (e.g. a game on the same box). Read
    # via _cfg_bool in services/gpu_scheduler.py::_wait_for_gaming_clear.
    'gpu_external_workload_wait_enabled': 'false',
    # GPU power/util are read from Prometheus (which scrapes + caches the
    # nvidia-smi exporter), NOT from the exporter directly. Prometheus serves
    # the last scrape instantly and never blocks on a slow nvidia-smi under
    # render load, and container-internal DNS (prometheus:9090) sidesteps the
    # Windows Docker host-port-forward wedge that made the direct
    # host.docker.internal:9835 read flap with RemoteDisconnected (2026-06-21).
    # Read via services/gpu_scheduler.py::_prometheus_query_url.
    'gpu_metrics_prometheus_url': 'http://prometheus:9090',
    # Operator hardware identity used for brain knowledge. Empty on OSS (each
    # install has its own card; gpu_vram_total_gb='auto' handles VRAM sizing);
    # the Glad Labs operator overlay (services.operator_overrides) restores Matt's
    # exact GPU. Keep '' identical to the 0000_baseline.seeds.sql seed so the
    # overlay's "overwrite only the OSS default" match fires.
    'gpu_model': '',
    # Bounded GPU-lock acquisition (poindexter#807). gpu.lock() used to wait
    # FOREVER for the in-process asyncio.Lock + cross-process pg_advisory_lock;
    # a wedged holder (zombie process from a force-crashed flow run) blocked a
    # graph node indefinitely, the brain probe force-crashed the flow, and the
    # stale sweep requeued the task into the same wall — an invisible
    # crash→requeue loop. On timeout the caller gets GpuLockTimeoutError
    # (fail-soft call sites skip; hard ones fail the node loudly) + a warn
    # gpu_lock_timeout finding. 900s tolerates a legitimate long video render
    # holding the lock; 0 restores the legacy unbounded wait. The release
    # timeout bounds pg_advisory_unlock in the lock's finally block (a hung
    # unlock used to make even stage-level timeouts hang); on timeout the
    # dedicated connection is terminated, which releases session advisory
    # locks server-side. Read via _cfg_int in services/gpu_scheduler.py.
    'gpu_lock_acquire_timeout_seconds': '900',
    'gpu_lock_release_timeout_seconds': '15',
    # --- GPU scheduler P1: queue admission + wait contracts (poindexter#914,
    # spec docs/superpowers/specs/2026-07-26-gpu-scheduler-queue-admission-design.md).
    # Master switch for the admission step in gpu.lock(): when true AND a caller
    # passes a max_wait_s budget, the calculator (services/gpu_admission.py::decide)
    # estimates the current holder's remaining time from its gpu_lease_stats p90
    # and checks VRAM fit, raising GpuBusyError pre-wait on a hopeless request
    # (honest skip) instead of burning the caller's budget behind a long render.
    # DOUBLY inert at ship: default false, and no production call site passes
    # max_wait_s yet (grep-proofed in tests/unit/services/
    # test_gpu_admission_wiring.py) — P2 migrates callers group by group.
    # Flipping true on a stock install therefore changes nothing until then.
    'gpu_sched_enabled': 'false',
    # Holder-remaining ETA assumed when the (owner, phase) key has no
    # gpu_lease_stats row yet (fresh install, brand-new phase). Conservative on
    # purpose: unknown holders look expensive, so short-budget callers skip
    # rather than gamble. Read via _cfg_float in gpu_scheduler.
    'gpu_sched_eta_fallback_seconds': '120',
    # Anti-starvation aging for the in-process priority queue (_PriorityGate):
    # a parked waiter is promoted one priority class (background→operator→
    # pipeline) per full window waited, so a stream of pipeline arrivals can
    # delay but never starve a background job. 0 disables aging (strict class
    # ordering). Read lazily at each wake, so changes apply without restart.
    'gpu_sched_aging_seconds': '300',
    # Wait budget (seconds) for fail-soft QA rails — poindexter#914 P2, the
    # first caller group migrated onto admission. Rails are the cheapest work
    # to skip and the most expensive to block: the 07-26..29 soak measured
    # image_gen holds at ~229s p90 (featured_image) / ~223s (inline_image_batch)
    # while the qa_ragas_judge rail's OWN p90 is 18.4s over 336 samples. Without
    # a budget a rail queued behind a render can burn the full 900s lock
    # ceiling; with one, admission rejects the hopeless wait up front and the
    # rail takes its existing degraded path (no review + a finding — never a
    # fabricated pass, per the QA-rail fail-open contract).
    #
    # 45s is deliberately above ordinary LLM holds (writer_self_review p90 35s,
    # title_generation 34.5s) and well below a render hold, so a rail waits
    # behind normal traffic and skips behind an image/video render. Set to 0 to
    # restore the unbounded legacy contract if skipping proves too aggressive.
    'gpu_sched_qa_rail_max_wait_s': '45',
    # Wait budget (seconds) for the media stages — poindexter#914 P2 group 2
    # (media_scripts / video_director / video_review). Same contract as the QA
    # rails above, sized higher because these stages do real work rather than
    # a judge call: their own p90 holds are 33.6s / 134.0s / 65.5s.
    #
    # All three already degrade without failing the post (the scripts stage
    # logs "non-fatal" and continues; both video stages return None and ship
    # the post without a shot list), so a contention skip costs a nice-to-have.
    # Blocking instead costs the whole article: an unbudgeted media stage
    # queued behind a render is what stranded a finished post past the 30-min
    # stale-reclaim, which then re-ran the pipeline from the draft.
    #
    # 120s sits in the measured gap (gpu_lease_stats, 07-26..30): above the
    # ordinary LLM traffic these should queue behind (generate_content p90
    # 105.3s) and below every long holder they should skip behind (qa_rewrite
    # 210.5s, featured_image 228.7s, inline_image_batch 240.0s, media_render
    # 383.5s). 0 restores the unbounded legacy contract.
    'gpu_sched_media_max_wait_s': '120',
    # VRAM (GB) the admission fit-check holds back on the pipeline GPU for
    # mid-hold invisible claims Prometheus can't see at decision time: desktop
    # compositor transients plus an idle-unloaded resident (e.g. whisper ~3GB)
    # that reloads on its next request. estimate ≤ free−headroom grants;
    # ≤ free−headroom+evictable grants after evicting resident Ollama models;
    # larger rejects (no_fit). Sized for Matt's 32GB RTX 5090 + desktop; a
    # dedicated headless card can drop this toward 2.
    'gpu0_headroom_gb': '6',
    # Case-insensitive substring that identifies the primary Ollama runner in
    # the per-process VRAM series (nvidia_gpu_process_memory_mib{process=...})
    # when computing the admission eviction credit — the per-card share the
    # scheduler could reclaim via unload_loaded_ollama_models. The exporter
    # labels processes by executable basename, and a match is ANY entry of this
    # comma-separated list (case-insensitive substring).
    #
    # Corrected 2026-07-29: this shipped as the single value 'ollama', which
    # matches NOTHING on a stock Linux Ollama install — the runner is
    # /usr/local/lib/ollama/llama-server, so the label is 'llama-server' and
    # 'ollama' is not a substring of it. The credit was therefore 0.0 on every
    # card for the whole P1 soak, and the miss is invisible: 0.0 is also the
    # legitimate "no telemetry" value and the fit gate fails open, so it never
    # errored — it just never granted eviction credit. Read by
    # services/gpu_registry.py::evictable_ollama_gb.
    'gpu_evictable_process_pattern': 'llama-server,ollama',
    # GPU-serialize fix: hold gpu.lock("ollama") around every LOCAL LLM dispatch
    # (services/llm_providers/dispatcher.py::dispatch_complete) so scheduled
    # worker jobs (topic research, SEO, newsletter) can't load the ~19GB writer
    # concurrently with a media render and blow past 32GB VRAM. Reentrant, so
    # it's a no-op inside content stages that already hold the lock. Default ON;
    # operators with abundant VRAM can flip to 'false' to skip the serialization.
    'gpu_serialize_llm_dispatch': 'true',
    # Exempt GPU-PINNED models from the shared GPU lock. A model routed by
    # plugin.llm_provider.litellm.config.model_api_base_overrides to a second
    # Ollama instance on its OWN card (glad-labs-stack#2051 — qwen3-vl on the
    # 3090) contends for nothing that lock protects: _unload_ollama_models only
    # evicts ollama_base_url, and image-gen / wan render on pipeline_gpu_index
    # only. Serializing it anyway just queued it behind the writer until
    # gpu_lock_acquire_timeout_seconds fired, and qa.vision fail-softs on
    # timeout — so the vision rail silently "passed open" on 13-25% of calls
    # (14d of cost_logs: qa_shot_vision 75.3% ok, caption_image 84.2%,
    # qa_vision_image_relevance 86.5%, media_qa_topic_match 50.0%, every one
    # capped at exactly the 900s lock timeout). Default ON — a no-op for any
    # install with no override map (the OSS path). Flip to 'false' if your
    # second instance shares ONE physical GPU with the first, where two servers
    # genuinely do contend for the same VRAM. Read via
    # services/llm_providers/dispatcher.py::_gpu_serialize_local_dispatch.
    'gpu_pinned_endpoint_skips_lock': 'true',
    # VRAM budget guard (services/vram_budget.py + dispatcher clamp). The guard
    # estimates a model's footprint (weights + KV cache + overhead) and clamps
    # num_ctx so the projected footprint stays within (total - desktop_reserve),
    # keeping the NVIDIA driver from spilling VRAM into system RAM (a WDDM
    # sysmem-fallback that freezes the desktop). total/reserve are GB; the KV
    # dtype sets bytes/element for the cache math (mirror OLLAMA_KV_CACHE_TYPE
    # on the host — see docs/operations/single-gpu-vram-tuning.md). Default ON.
    # "auto" (default) → the dispatcher detects the total VRAM pool by summing
    # every GPU's memory.total via Prometheus (GPURegistry). An explicit number
    # overrides (e.g. a multi-tenant cap below physical VRAM). 2026-06-28.
    'gpu_vram_total_gb': 'auto',
    'gpu_desktop_reserve_gb': '3',
    # Conservative VRAM budget (GB) used ONLY when gpu_vram_total_gb="auto" but
    # detection has never succeeded (Prometheus unreachable). Tunable so an
    # operator on a smaller card can lower the floor instead of over-promising.
    'gpu_vram_autodetect_fallback_gb': '32',
    # Which GPU index the content pipeline runs on (the display + Ollama card).
    # The scheduler reads this card's utilisation/power from the nvidia-smi
    # exporter; with >1 GPU in the box an unlabelled query would resolve to a
    # nondeterministic series. Default 0 (the 5090). Re-point only if the
    # pipeline's primary inference GPU changes.
    'pipeline_gpu_index': '0',
    'ollama_kv_cache_type': 'q8_0',
    'vram_budget_guard_enabled': 'true',
    # why: when true, after issuing keep_alive=0 the unload helper re-polls
    # Ollama /api/ps until the model is actually gone BEFORE the next
    # (image-gen/video) model loads — instead of blind-sleeping and hoping. On a
    # single 32GB GPU shared with the Windows desktop, returning while the
    # 18GB writer is still resident overlaps it with the incoming diffusion
    # model, exhausts VRAM, and freezes WDDM. See
    # services/llm_providers/ollama_unload.py. Set false to fall back to the
    # legacy blind pipeline_writer_unload_grace_seconds sleep.
    'pipeline_writer_unload_confirm_enabled': 'true',
    # Upper bound (seconds) on the confirm poll. If the model is still
    # resident after this window the helper logs a WARNING and proceeds —
    # it never hangs the pipeline. Generous default: an 18GB evict is
    # typically 1-5s; 15s leaves headroom under render load.
    'pipeline_writer_unload_confirm_timeout_seconds': '15',
    # why: asyncio.sleep() after issuing keep_alive=0 so Ollama actually
    # releases VRAM before the inline-image /generate lands. Only used when
    # pipeline_writer_unload_confirm_enabled=false (legacy fallback). 2s is
    # the sweet spot — long enough for the kernel to free, short enough to
    # stay invisible in pipeline latency.
    'pipeline_writer_unload_grace_seconds': '2',
    # Interval (seconds) between confirm-poll /api/ps checks. Smaller =
    # tighter handoff (less wasted wait once the model frees), more polls.
    'pipeline_writer_unload_poll_interval_seconds': '0.5',
    # ----- Model-eval loop (champion–challenger; Plan 1 reranker slice) -----
    # Relative improvement a challenger must beat the champion by to be a
    # promotion candidate (0.02 = 2%), plus golden-set bootstrap sizing for
    # the reranker scorer. See docs/architecture/2026-06-29-model-eval-loop-design.md.
    'model_eval_promotion_margin': '0.02',
    'model_eval_reranker_golden_size': '50',
    'model_eval_reranker_candidates_per_case': '20',
    'qa_fallback_critic_model': 'ollama/qwen2.5:32b',
    'qa_fallback_writer_model': 'ollama/gemma3:27b',
    # Cross-model rescue reviser for qa.rewrite. EMPTY = reuse the resident
    # gemma writer (pipeline_writer_model) — the current default. The #1692
    # bakeoff (2026-06-18) picked glm-4.7-5090 here, but on the 5090+3090 rig glm
    # can't stay resident beside the 24GB writer, so each rescue cold-loaded a
    # 19GB thinking model that returned EMPTY ~2x/day. Reverted 2026-06-28;
    # restore only with GPU pinning that keeps glm warm on the 3090.
    'qa_rewrite_model': '',
    # poindexter#716: vision QA model keys — seeded here so the DB always has
    # a value and code never falls back to a hardcoded literal.  Empty string =
    # operator deliberately cleared the key — the vision check is skipped.
    'qa_preview_vision_model': 'ollama/qwen3-vl:30b',
    'qa_vision_model': 'ollama/qwen3-vl:30b',
    'qa_vision_num_predict': '1024',  # #563: room for qwen3-vl <think> + JSON verdict
    # Thinking vision models (qwen3-vl) need a bigger budget than the 1024 base —
    # the <think> trace shares num_predict with the JSON scores and truncates them
    # at 1024, so the vision leg returns None and qa.vision false-pages the model
    # as "unavailable" though it ran fine (vision_scorer_unavailable RCA 2026-07-12).
    'qa_vision_thinking_num_predict': '8000',
    # why: structured-JSON extraction calls (topic discovery distill +
    # candidate ranking) need a JSON-reliable INSTRUCT model. The writer
    # model (pipeline_writer_model) may be a reasoning model that returns
    # empty content under response_format=json_object — which crashed the
    # whole topic-discovery sweep (2026-05-28 content-gen stall). Kept
    # separate + DB-configurable so operators can pin a writing model
    # without breaking structured extraction.
    'structured_extraction_model': 'ollama/gemma3:27b',
    # poindexter#716: vision alt-text + media-qa human-detect model key.
    # The baseline seeds this as 'qwen3-vl:30b'; seeded here too so fresh
    # installs without the baseline seeds can still get a sensible default.
    'vision_alt_model': 'ollama/qwen3-vl:30b',
    # Per-step model pins for utility LLM calls that previously resolved through
    # the (now-removed) cost_tier.* fallback. Each is read directly and fails
    # loud when empty — no tier indirection. Seeded to the model the step used
    # under the old standard/budget tiers so behaviour is unchanged; tune each
    # step freely.
    'image_search_query_model': 'ollama/gemma3:27b',  # image_service Pexels query-gen (was gemma4:31b)
    'image_prompt_model': 'ollama/gemma3:27b',  # image_providers/ai_generation image-gen prompt-gen (was gemma4:31b)
    'writer_self_review_model': 'ollama/gemma3:27b',  # services/self_review writer self-review (was gemma4:31b)
    # NOTE: retention/collapse cold-data summaries keep their existing per-step
    # keys (memory_compression_summary_model / embedding_collapse_summary_model,
    # both seeded ollama/phi4:14b in 0000_baseline.seeds.sql) — no new key here.
    'use_ollama': 'false',
    # Boot-time validation of *_model keys against
    # installed Ollama models (glad-labs-stack#1284). Flip to 'false' on
    # non-Ollama deployments or when Ollama is deliberately unreachable at
    # startup (e.g. remote-only LiteLLM routing).
    'ollama_model_validation_enabled': 'true',
    # Extra `*_model` keys whose value is NOT an Ollama model (CSV), added to
    # the built-in list in utils/startup_manager.py — see there for the rules.
    # Only needed for BARE values; namespaced ones self-classify (#941).
    'ollama_model_validation_skip_keys': '',

    # ----- LLM providers / endpoints -----
    'flux_schnell_server_url': '',
    'ollama_base_url': 'http://host.docker.internal:11434',
    'plugin.audio_gen_provider.stable-audio-open-1.0.default_duration_s': '',
    'plugin.audio_gen_provider.stable-audio-open-1.0.output_format': '',
    'plugin.audio_gen_provider.stable-audio-open-1.0.sample_rate': '',
    'plugin.audio_gen_provider.stable-audio-open-1.0.server_url': '',
    'plugin.image_provider.flux_schnell.server_url': '',
    'plugin.llm_provider.gemini.enabled': 'false',
    # Refuse paid base_url targets unless explicitly opted in. The default
    # base_url is host.docker.internal:11434/v1 (Ollama) so most installs
    # never trip this gate; setting it true is required to dispatch to
    # Groq / OpenRouter / Together / Fireworks / Anthropic-OAI-compat.
    # Enforced by services/llm_providers/openai_compat.py per
    # feedback_no_paid_apis.
    'plugin.llm_provider.openai_compat.allow_paid_base_url': 'false',
    # Same gate, one layer up: LiteLLM is the default router for every
    # cost tier (free/budget/standard/premium/flagship) and auto-discovers
    # OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY from env. A bare
    # model string like 'openai/gpt-4o' with a stray env var fires a paid
    # call. Default-deny refuses both non-local api_base AND non-local
    # model prefixes; flip to 'true' to authorise any paid LiteLLM path.
    'plugin.llm_provider.litellm.allow_paid_base_url': 'false',
    # httpx not aiohttp — see litellm_provider.__init__ (GlitchTip 736).
    'plugin.llm_provider.litellm.disable_aiohttp_transport': 'true',
    # Anthropic prompt caching on the litellm writer path: annotate the
    # system prefix with an ephemeral cache breakpoint on the anthropic/
    # target so a reused writer system prompt bills cached input at ~10%.
    # ON by default; set 'false' to kill it. See
    # litellm_provider._annotate_system_cache_control.
    'plugin.llm_provider.litellm.anthropic_prompt_caching': 'true',
    'plugin.video_provider.wan2.1-1.3b.server_url': '',
    'image_gen_server_url': 'http://image-gen-server:9836',
    'stable_audio_open_server_url': '',
    'wan_server_url': '',

    # ----- RAG / retrieval -----
    'niche_internal_rag_per_kind_limit': '4',
    'niche_internal_rag_snippet_max_chars': '600',
    # Cap internal_rag's discovery-batch share (finding #5); 1.0 disables.
    'niche_internal_rag_batch_share_cap': '0.5',
    # Storyworthy selection (poindexter#820): recency window for the
    # goal-vector-ranked snippet query; snippets older than this never
    # become topic candidates.
    'niche_internal_rag_lookback_days': '30',
    # source_kind → sampling-weight JSON: biases snippet sampling toward
    # story-dense kinds (decisions, memory) over status-dense ops logs
    # (audit, brain). Unlisted kinds weigh 1.0; 0 skips a kind entirely.
    'niche_internal_rag_kind_weights': (
        '{"decision_log": 1.5, "memory_file": 1.5, "claude_session": 1.0, '
        '"post_history": 1.0, "audit_event": 0.5, "brain_knowledge": 0.5}'
    ),
    # External-candidate internal grounding (poindexter#822): softly
    # penalize a popular external topic that has no first-party material in
    # our own corpus, so it can't win a batch slot on popularity alone.
    'niche_external_grounding_enabled': 'true',
    # Content-bearing corpus only — a status/ops row must never manufacture
    # grounding. Kinds map to embeddings.source_table via topic_grounding.
    'niche_external_grounding_source_kinds': (
        'post_history,decision_log,memory_file,claude_session'
    ),
    # Cosine similarity >= this counts as grounded. PROVISIONAL — calibrate
    # from a real sweep's logged _grounding distribution before trusting it.
    'niche_external_grounding_threshold': '0.55',
    # Soft-penalty multiplier applied to an ungrounded external candidate's
    # pre-rank score (1.0 = no penalty).
    'niche_external_grounding_penalty_factor': '0.6',
    'rag_default_top_k': '5',
    'rag_embed_retry_attempts': '3',
    'rag_embed_retry_base_delay_seconds': '0.25',
    # GPU layers to offload for the embedding model (nomic-embed-text).
    # 0 = CPU-only. Keeps the 768-dim embedding model off the GPU so it
    # doesn't evict the ~19 GB writer model from VRAM mid-pipeline.
    # Set to -1 to restore Ollama's default (GPU if available).
    'embed_num_gpu': '0',
    # BM25 tsvector + pgvector RRF fusion — pure SQL, no extra dependency
    # (unlike rag_rerank_enabled below). Default true: glad-labs-stack#2133
    # closed the drift between this defaulting false and CLAUDE.md
    # documenting the three-mode RAG stack as live/stable on prod.
    'rag_hybrid_enabled': 'true',
    'rag_min_similarity': '0.3',
    'rag_rerank_enabled': 'false',
    'rag_rerank_model': 'cross-encoder/ms-marco-MiniLM-L-6-v2',
    'rag_rerank_device': 'cpu',
    'rag_rrf_k': '60',
    # CSV of embeddings.source_table values RAG retrieval may draw from. MUST
    # default to content-only ('posts'): the corpus is ~⅔ claude_sessions /
    # brain / audit ops-logs, and grounding a draft in those leaks
    # meta-commentary + agent instructions into the post (2026-06 contamination
    # incident; memory: project_rag_corpus_pollution). Operators add more
    # content-bearing tables (e.g. 'posts,samples') as their corpus grows.
    # NOTE two consumers, two empty-value semantics: the general rag_engine
    # retriever treats an empty value as "all tables", but the two_pass writer
    # (modules/content/atoms/two_pass_writer._resolve_snippet_source_filter)
    # NEVER queries unfiltered — an empty value falls back to the 'posts'
    # content allowlist there, because generated content must not be grounded
    # in operational telemetry.
    'rag_source_filter': 'posts',
    # Writer-only snippet source allowlist, decoupled from rag_source_filter
    # (which the general rag_engine retriever + internal-link discovery read).
    # Empty = inherit rag_source_filter, then the built-in 'posts' allowlist.
    # Set to e.g. 'claude_sessions,posts' to ground the writer on first-party
    # sessions without leaking them into internal-link suggestions. Like
    # rag_source_filter above, the writer NEVER queries unfiltered.
    'writer_rag_source_filter': '',
    # Per-source snippet caps for the writer, CSV 'source:N'; empty = no caps.
    # Pair with writer_rag_source_filter, e.g. 'posts:2' so prior posts can't
    # crowd out (or re-form the echo loop from) first-party session grounding.
    'writer_rag_source_caps': '',
    # Minimum acceptable writer-draft length; below this the draft is treated
    # as a generation failure (empty/too-short → status='failed' + finding,
    # not a misleading reviewer_count:0 QA reject). A real canonical_blog post
    # is never this short — a sub-threshold draft means the reasoning writer
    # model returned (near-)empty content. poindexter#691.
    'writer_min_draft_chars': '200',
    # Min real-word count before draft/revise/expand-input counts as
    # degenerate (e.g. '...') rather than genuine content. Gates
    # atoms/two_pass_writer.py's "Degenerate-draft guard". #806.
    'writer_min_substance_words': '2',
    # Keep-best soft expansion (length enforcement): when a niche draft lands
    # under target_length * writer_min_length_ratio, two_pass runs ONE expansion
    # pass and keeps the longer of (original, expanded). Disable via the _enabled key.
    'writer_length_expansion_enabled': 'true',
    'writer_min_length_ratio': '0.7',
    # Disable the model's reasoning/thinking channel on the WRITER calls
    # (draft / revise / expand). A thinking-capable writer (e.g. the
    # gemma-4-31B-it-qat QAT default) otherwise spends its generation budget in
    # a hidden reasoning channel and the VISIBLE draft truncates mid-sentence or
    # at a bare heading (2026-07-06 investigation: seed-matched A/B — think on →
    # 708w truncated @3072 tok w/ 1082 hidden think-words; think off → 985w
    # complete @1360 tok). Passed through the dispatcher as ``think=False``;
    # harmless no-op on a non-thinking writer model. Set false only to
    # deliberately let a reasoning writer chain-of-think in the draft channel.
    'writer_disable_thinking': 'true',
    # Writer-side prior-work anchor (#822 consumer half): inject a grounded
    # external topic's internal match into the draft prompt as a soft "PRIOR
    # WORK" section. Independent of the discovery-side
    # niche_external_grounding_enabled; eligibility rides writer_rag_source_filter.
    'writer_internal_grounding_enabled': 'true',
    'writer_rag_context_snippet_max_chars': '500',
    'writer_rag_research_topic_max_sources': '2',
    'writer_rag_two_pass_research_max_sources': '2',
    'writer_rag_two_pass_snippet_limit': '20',
    # Retrieval de-echo (RAG self-echo root fix, 2026-07-07). The two_pass
    # writer grounds on the nearest `posts`; in a dense topic cluster the top
    # slots are the same echo restated, so the writer parrots it. Oversample
    # the candidate pool by this multiplier, drop near-identical priors at/above
    # the dedup ceiling (fail-open), then MMR-select for diversity so an echo
    # cluster collapses to one representative. mmr_lambda=1.0 disables MMR
    # (pure relevance). Ceiling 0.93 sits above the corpus p95 nearest-neighbour
    # cosine (~0.89), so it only strikes the pathological near-republish tail.
    'writer_rag_candidate_multiplier': '3',
    'writer_rag_dedup_ceiling': '0.93',
    'writer_rag_mmr_lambda': '0.5',
    # Web-research grounding. When true (default) the writer's research step
    # FETCHES and extracts real page text from each web source (up to
    # web_research_max_content_chars) via WebResearcher.search — instead of
    # just a title + a 100-char DuckDuckGo snippet — so drafts cite sourced
    # facts/numbers rather than inventing them. Flip false for the cheaper
    # snippet-only path (search_simple) when fetch latency/bandwidth matters.
    # research_web_content_chars_per_source caps how much of each source's
    # extracted text is injected into the generation prompt (token budget;
    # 600 ≈ one substantial paragraph × up-to-5 sources).
    'research_extract_web_content': 'true',
    'research_web_content_chars_per_source': '600',

    # ----- Quality assurance pipeline -----
    'qa_accuracy_bad_link_max_penalty': '2.0',
    'qa_accuracy_bad_link_penalty': '0.5',
    'qa_accuracy_baseline': '7.0',
    'qa_accuracy_citation_bonus': '0.3',
    'qa_accuracy_first_person_max_penalty': '3.0',
    'qa_accuracy_first_person_penalty': '1.0',
    'qa_accuracy_good_link_bonus': '0.3',
    'qa_accuracy_good_link_max_bonus': '1.0',
    'qa_accuracy_meta_commentary_max_penalty': '2.0',
    'qa_accuracy_meta_commentary_penalty': '0.5',
    # First-person QA bypass slugs — lockstep with baseline seed; overlay restores brand.
    'qa_allow_first_person_niches': 'dev_diary,starter-blog',
    'qa_artifact_penalty_max': '20.0',
    'qa_artifact_penalty_per': '5.0',
    'qa_clarity_good_max_wps': '25',
    'qa_clarity_good_min_wps': '10',
    'qa_clarity_ideal_max_wps': '20',
    'qa_clarity_ideal_min_wps': '15',
    'qa_clarity_ok_max_wps': '30',
    'qa_clarity_ok_min_wps': '8',
    'qa_completeness_heading_bonus': '0.3',
    'qa_completeness_heading_max_bonus': '1.5',
    'qa_completeness_truncation_penalty': '3.0',
    'qa_completeness_word_1000_score': '5.0',
    'qa_completeness_word_1500_score': '6.0',
    'qa_completeness_word_2000_score': '6.5',
    'qa_completeness_word_500_score': '3.5',
    'qa_completeness_word_min_score': '2.0',
    'qa_critical_floor': '50.0',
    'qa_engagement_baseline': '6.0',
    'qa_fk_target_max': '12.0',
    'qa_fk_target_min': '8.0',
    'qa_llm_buzzword_fail_threshold': '5',
    'qa_llm_buzzword_max_penalty': '5.0',
    'qa_llm_buzzword_penalty_per': '0.5',
    'qa_llm_buzzword_warn_max_penalty': '2.0',
    'qa_llm_buzzword_warn_penalty_per': '0.3',
    'qa_llm_buzzword_warn_threshold': '3',
    'qa_llm_exclamation_max_penalty': '2.0',
    'qa_llm_exclamation_penalty_per': '0.3',
    'qa_llm_exclamation_threshold': '5',
    'qa_llm_filler_fail_threshold': '4',
    'qa_llm_filler_max_penalty': '4.0',
    'qa_llm_filler_penalty_per': '0.5',
    'qa_llm_filler_warn_penalty_per': '0.3',
    'qa_llm_filler_warn_threshold': '2',
    'qa_llm_formulaic_min_avg_words': '50',
    'qa_llm_formulaic_structure_penalty': '2.0',
    'qa_llm_formulaic_variance': '0.2',
    'qa_llm_hedge_penalty': '2.0',
    'qa_llm_hedge_ratio_threshold': '0.02',
    'qa_llm_listicle_title_penalty': '2.0',
    'qa_llm_opener_penalty': '5.0',
    'qa_llm_patterns_enabled': 'true',
    'qa_llm_repetitive_min_count': '3',
    'qa_llm_repetitive_starter_max_penalty': '4.0',
    'qa_llm_repetitive_starter_penalty_per': '1.0',
    'qa_llm_transition_min_count': '2',
    'qa_llm_transition_penalty_per': '1.0',
    'qa_pass_threshold': '70.0',
    'qa_relevance_high_coverage_score': '8.5',
    'qa_relevance_low_coverage_score': '5.5',
    'qa_relevance_med_coverage_score': '7.0',
    'qa_relevance_no_topic_default': '6.0',
    'qa_relevance_none_coverage_score': '3.0',
    'qa_relevance_stuffing_hard_density': '5.0',
    'qa_relevance_stuffing_soft_density': '3.0',
    'qa_seo_baseline': '6.0',
    'qa_title_originality_enabled': 'true',
    'qa_title_similarity_threshold': '0.6',
    # Points the programmatic validator shaves per (non-critical) warning when
    # scoring an otherwise-clean draft. Default 5 (was a hard-coded 10): soft
    # nits should nudge the score, not sink a clean post under the QA gate.
    'qa_validator_warning_penalty': '5.0',
    'title_max_length': '90',
    # Draft excerpt size for the TITLE-GENERATION digest (content.generate_title
    # + seo.generate_all_metadata prompts; excerpt + section-heading skeleton).
    # The pre-2026-07 hardcoded 500 chars was the root cause of topic-label
    # titling: the model never saw enough of the article to title it.
    'title_content_excerpt_chars': '1500',
    'qa_topic_dedup_hours': '48',
    # --- Title↔body coherence rail (qa.title_coherence, 2026-07-24) ----------
    # Advisory-first LLM verdict on whether the display title honestly
    # represents the article (wrong-domain titles, directive leaks, generic
    # mush — the task-1149dfc8/1afabaf9 class). Scores on every run; veto is
    # DB-gated via qa_gates.title_coherence.
    'qa_title_coherence_enabled': 'true',
    # Judge model. EMPTY = pipeline_seo_model → pipeline_local_writer_model.
    # Deliberately never pipeline_writer_model — a cloud writer canary must
    # not be silently billed by a QA rail.
    'qa_title_coherence_model': '',
    # Draft digest size handed to the judge (opening excerpt + section
    # headings). 3000 was the calibrated setup (2026-07-24) that separated
    # the known-bad titles from the legit corpus 10/10.
    'qa_title_coherence_digest_chars': '3000',
    # Web fact-check rail (qa.web_factcheck) claim-verification heuristics.
    # A claim is treated as VERIFIED when at least `match_ratio` of its key
    # terms (tokens longer than `min_term_len` chars) appear in the first
    # `snippet_chars` characters of the top `num_results` DuckDuckGo hits.
    # `max_claims` caps how many extracted claims are searched per post.
    # Previously hardcoded inline in modules/content/multi_model_qa.py; moved
    # here so the rail's strictness is tunable without a redeploy.
    'qa_web_factcheck_match_ratio': '0.6',
    'qa_web_factcheck_num_results': '3',
    'qa_web_factcheck_snippet_chars': '500',
    'qa_web_factcheck_min_term_len': '2',
    'qa_web_factcheck_max_claims': '3',
    # QA rescue cycle: max bounded rewrite passes before a salvageable reject is
    # hard-rejected. Default 2 = write -> qa -> revise -> qa -> revise (the
    # one-shot default was 0-for-4 at saving a post). 0 disables; clamped [0,3].
    # Fabrication/gate/missing_required vetoes are never rescued — only soft
    # critic vetoes + below-threshold scores. The cycle keeps the best-scoring
    # draft across passes (qa.aggregate keep-best guard), so a worse revision
    # never replaces a better earlier draft.
    'qa_rewrite_max_attempts': '2',

    # Self-heal before paging (#qa-self-heal): when true, qa.aggregate stops
    # discarding a non-approvable draft — after the bounded regen cycle it FLAGS
    # the draft (qa_flagged) and rides the forward edge to awaiting_approval with
    # the per-rail findings attached, never writing rejected/rejected_final
    # (operator-only). Default 'true' since 2026-06-22: shipped OFF behind this
    # switch, e2e-verified live (task ce1f7499 — a 94-score no-veto draft flagged
    # to awaiting_approval instead of discarded), then flipped on prod + here so
    # fresh installs default to never-discard. 'false' restores legacy discard.
    'qa_flag_instead_of_reject': 'true',

    # ----- Topic discovery / dedup / ranking -----
    'niche_batch_expires_days': '7',
    'niche_carry_forward_decay_factor': '0.7',
    'niche_embedding_model': 'nomic-embed-text',
    'niche_goal_descriptions': '{"TRAFFIC": "Topic likely to attract organic search traffic; trending keyword, broad appeal, evergreen demand.", "EDUCATION": "Topic that teaches the reader something concrete and useful they didn\'t know before.", "BRAND": "Topic that reinforces the operator\'s positioning and unique perspective.", "AUTHORITY": "Topic that demonstrates the operator\'s depth and expertise on something specific.", "REVENUE": "Topic that drives a commercial outcome: signups, sales, conversions, paid feature awareness.", "COMMUNITY": "Topic that resonates with the operator\'s existing audience; sparks discussion, shares, replies.", "NICHE_DEPTH": "Topic that goes deep on the operator\'s niche specialty rather than broad-audience content."}',
    'niche_ollama_chat_timeout_seconds': '300.0',
    'niche_top_n_per_pool': '5',
    # b2 pool-reader (poindexter#812): per-source row cap when run_sweep
    # reads topic_pool. The pool accumulates at wildly different rates per
    # source (internal_rag deposits ~40x devto), so the read is balanced
    # per source rather than a flat LIMIT. Higher = more embeds per sweep.
    'niche_pool_read_per_source_limit': '20',
    # Manual-injection dedup (create_post MCP tool / POST /api/tasks): cosine
    # similarity at/above which a caller-supplied topic is refused (409) as a
    # near-duplicate of an already-published post. Auto-discovered topics use
    # the topic_dedup_* engine above; this guards the manual path. force=true
    # overrides. See services/topic_dedup_guard.py.
    'create_post_dedup_threshold': '0.75',
    # Topic-dedup engine (auto-discovery path). 'content_embedding' (default) =
    # compare the candidate against published-post CONTENT via find_similar_posts
    # — catches re-treads whose TITLE differs (e.g. "GPU VRAM Budgeting…" scored
    # only 0.55 title-similarity to "The VRAM Currency Problem" but 0.735 on
    # content). 'semantic' = title-embedding cosine (all-MiniLM); 'word_overlap' =
    # lexical baseline. Thresholds: *_content for content_embedding, *_semantic
    # for semantic, bare *_threshold for word_overlap.
    'topic_dedup_engine': 'content_embedding',
    'topic_dedup_existing_threshold': '0.7',
    'topic_dedup_intra_batch_threshold': '0.65',
    # Content-embedding dedup threshold (topic_dedup_engine='content_embedding').
    # Cosine at/above which a candidate topic duplicates a published post's
    # content. Calibrated 2026-07-12 against the live corpus: the VRAM cluster
    # scores 0.65-0.735, unrelated controls <=0.60.
    'topic_dedup_existing_threshold_content': '0.70',
    # Device for the semantic (title) topic-dedup model (all-MiniLM). Default
    # 'cpu' so dedup never competes with the inference pipeline for VRAM
    # (mirrors rag_rerank_device). Set 'cuda' only on a box with spare VRAM.
    'topic_dedup_device': 'cpu',
    # Recent-coverage guard (services/topic_recent_coverage.py, 2026-07-23
    # incident: internal_rag re-proposed the already-published Grafana-
    # telemetry theme and it auto-resolved into a full generation the
    # operator had to reject). Compares the candidate composite (title +
    # angle) against composites of recently published posts + in-flight
    # tasks — like-for-like short text, which separates true re-treads
    # (0.79-0.86 on the incident pairs) from same-domain neighbours
    # (<=0.70) where title-vs-content cosine cannot. Runs inside the
    # content_embedding dedup engine at sweep/tap intake AND as a hard
    # gate at batch handoff (auto-resolve expires a blocked batch).
    'topic_recent_coverage_enabled': 'true',
    # Cosine at/above which a candidate near-duplicates recent coverage.
    # Calibrated 2026-07-24: incident dups 0.79-0.86, same-domain control
    # 0.70, legitimately-coexisting published pairs <=0.61.
    'topic_recent_coverage_threshold': '0.80',
    # Only posts published within this many days block re-coverage (0 = all
    # time). Older themes are fair game for a deliberate refresh; in-flight
    # tasks are always checked regardless of this window.
    'topic_recent_coverage_lookback_days': '90',
    'topic_discovery_length_distribution': '',
    # Topic-sanity gate (services/topic_sanity.py, 2026-06-30 dots-topic
    # incident): minimum count of alphabetic words (letter-runs of >=2
    # chars) for a topic to pass the task-creation seams. Prod history:
    # every topic under 2 alpha words across 1,867 tasks ended
    # rejected/cancelled — none published. 0 relaxes the word-count rule
    # (empty / zero-letter topics are still always rejected). Sites in
    # non-spaced scripts (CJK) should set 1.
    'topic_sanity_min_alpha_words': '2',
    # Self-reference gate (services/topic_self_reference.py). Extra hosts the
    # operator owns, comma-separated, beyond the site_url / public_site_url
    # hosts that are always excluded automatically. Matching is
    # subdomain-aware and www-insensitive, so list apex domains
    # ('example.com', not 'www.example.com'). Empty = derived hosts only.
    'topic_source_excluded_domains': '',
    # Stale-batch reaper (services/jobs/reap_stale_topic_batches.py). A
    # topic_batch stuck status='open' wedges its niche content-dark via the
    # one-open-batch-per-niche index. The reaper alerts on any batch open
    # past _stuck_hours and — when _reaper_enabled — auto-expires the dead
    # (already past expires_at) ones to self-heal the niche. Default OFF:
    # flipping it on auto-clears expired batches, which reactivates sweeps on
    # any active niche that has since moved to its own content path (e.g.
    # dev_diary's daily cron) — an explicit operator decision. Set such a
    # niche active=false first; the reaper is scoped to active niches.
    'topic_batch_reaper_enabled': 'false',
    'topic_batch_stuck_hours': '24',

    # ----- Content router / writer / self-review -----
    'content_router_contradiction_review_max_tokens': '1500',
    'content_router_contradiction_revise_max_tokens': '8000',
    'content_router_contradiction_timeout_seconds': '120',
    'content_router_qa_rewrite_max_tokens': '8000',
    # The qa.rewrite rescue pass is a writer-class full-revision LLM call, so it
    # gets the same budget as the writer (niche_ollama_chat_timeout_seconds=600).
    # The inherited 240s default (orphaned from the deleted cross_model_qa) was
    # too low: under glm<->gemma single-GPU thrash the revise call always timed
    # out and the rescue (#1674) was silently skipped on salvageable drafts.
    'content_router_qa_rewrite_timeout_seconds': '600',
    'content_router_seo_title_max_tokens': '4000',
    # Extra title-generation attempts when the LLM answers with
    # meta-commentary about the draft instead of a headline ("Focuses on
    # specific metrics…"). 0 disables the retry — junk falls straight
    # through to the H1/topic fallback in choose_canonical_title.
    'title_junk_regen_max_retries': '1',
    # Content-validator per-category promotion thresholds. 0 = never promote
    # this warning category to a hard critical (Glad-Labs/poindexter#692):
    # both rules are pattern heuristics that can't tell a fabricated external
    # ref from a rhetorical phrase / real post-cutoff product / internal file,
    # so the hard veto lives with the LLM critic + qa.web_factcheck (#661).
    # Raise > 0 to re-arm count-based promotion for that category.
    'content_validator_hallucinated_reference_warning_threshold': '0',
    'content_validator_unlinked_citation_warning_threshold': '0',
    # why: advisory rail, cheap to run, data flows to audit_log for tuning per 2026-05-10 Lane D close-out
    'deepeval_enabled': 'true',
    'enable_writer_self_review': 'true',
    # why: advisory rail, cheap to run, data flows to audit_log for tuning per 2026-05-10 Lane D close-out
    'guardrails_enabled': 'true',
    # why: advisory rail, cheap to run, data flows to audit_log for tuning per 2026-05-10 Lane D close-out
    'ragas_enabled': 'true',
    'ragas_judge_model': 'ollama/phi4:14b',
    'self_consistency_enabled': 'false',
    'self_consistency_sample_count': '3',
    'self_consistency_threshold': '0.55',
    # Content-originality rail (renamed from opening_originality 2026-07-12) —
    # flags a draft whose CONTENT near-duplicates an
    # existing published post (RAG self-echo). The two_pass writer grounds each
    # draft in the nearest posts, so a dense topic cluster makes every new post
    # paraphrase the sibling it retrieved (the 2026-06 "VRAM is the only currency
    # that matters" cluster: 4 posts opened near-verbatim). Advisory-first via
    # qa_gates.content_originality — SCORES on every run (QA Rails dashboard) but
    # does not veto until graduated. max_similarity is the cosine ceiling above
    # which a draft chunk counts as a near-copy of its nearest published neighbor.
    # 0.83 calibrated 2026-07-07 against the published corpus (rail median 0.73,
    # p95 0.83); the prior 0.90 sat near p99 and caught 1 of 4 exemplar echoes.
    'content_originality_enabled': 'true',
    'content_originality_max_similarity': '0.83',
    # Whole-post chunking floors for the max-over-chunks scan. min merges tiny
    # paragraphs into a meaningful vector; max windows a long section so it stays
    # focused (mirrors the granularity the embeddings table stores post chunks at).
    'content_originality_chunk_min_chars': '200',
    'content_originality_chunk_max_chars': '600',
    # Templated recurring series exempt from the content_originality HARD veto
    # (they still score/flag). dev_diary/narrate_bundle share a by-template
    # opening cadence — graduating the rail without this would false-veto every
    # shipping log. CSV of template_slug values. Future-defensive: dev_diary runs
    # no qa.* atoms today, so this only bites after a graduation to a hard gate.
    'content_originality_excluded_series': 'dev_diary,narrate_bundle',
    # --- Affiliate-link injection (rebuild 2026-07-11) -----------------------
    # Dark-launched: the content.inject_affiliate_links atom no-ops until this
    # flips true. Real referral rows are added out-of-band via
    # `poindexter affiliate add` (DB-only, never seeded in source).
    'affiliate_injection_enabled': 'false',
    # Max affiliate links injected per post (first-mention only, one per keyword).
    'affiliate_max_links_per_post': '3',
    # URL prefix the injected link points at: `{base}/{code}`. Default is the
    # relative `/go` path (a CF Worker route on the site origin); set to an
    # absolute origin (e.g. https://go.gladlabs.io) if hosting on a subdomain.
    'affiliate_redirect_base_url': '/go',
    # FTC disclosure banner copy, rendered near the top of any post containing an
    # affiliate link. Empty string → frontend uses its built-in default. Generic
    # (brand-free) per OSS-seed hygiene; the operator's branded wording is
    # restored by operator_overrides.OPERATOR_SETTING_OVERRIDES.
    'affiliate_disclosure_text': (
        'Some links in this article are affiliate links — if you sign up '
        'through them, we may earn a commission at no extra cost to you.'
    ),
    # Click-ingest job: pull the affiliate-click Analytics Engine dataset into
    # affiliate_link_clicks every 5 min (mirrors sync_cloudflare_analytics).
    'plugin.job.sync_affiliate_clicks.enabled': 'true',
    'plugin.job.sync_affiliate_clicks.interval_seconds': '300',
    # Case-insensitive regex marking a /go click as bot traffic at ingest, so
    # affiliate_links.clicks and the Grafana panels count humans only
    # (poindexter#930 — crawlers were 52% of rows). Raw rows are still stored;
    # reader surfaces select from the affiliate_link_clicks_human view. Widen
    # this to exclude a newly-spotted crawler without a release. An invalid
    # regex logs loud and falls back to the built-in pattern rather than
    # letting bots through as human.
    'affiliate_click_bot_ua_pattern': (
        r'(bot|crawler|spider|slurp|bingpreview|facebookexternalhit|headless|'
        r'python-requests|python-urllib|curl|wget|scrapy|monitor|uptime|'
        r'preview|fetch|axios|okhttp|go-http-client|java/|libwww)'
    ),
    # Model pin for the CSV-import keyword/display-text derivation
    # (task.affiliate_derive_keywords). why: empty -> structured_extraction_model
    # (a JSON-reliable instruct model). Local by default.
    'affiliate_import_llm_model': '',
    # why: per-row timeout for the derivation call (keep short — one row at a time).
    'affiliate_import_llm_timeout_seconds': '60',
    # Community draft assistant (WS2) — on-demand founder-voice drafts for
    # Reddit / IndieHackers. why: empty model => the writer model
    # (pipeline_writer_model); these are prose drafts, so writer-grade not
    # structured-extraction. Timeout is DB-tunable rather than a hardcoded literal.
    'community_draft_model': '',
    'community_draft_timeout_seconds': '180',
    # Citation reconciliation + advisory unlinked-attribution rail (#765).
    # why: deterministic repair that re-links named sources the writer dropped
    # the URL for, matched against the research corpus by domain handle — free,
    # high-precision, on by default.
    'citation_reconcile_enabled': 'true',
    # why: re-point pass in the same atom — swaps a writer-fabricated path on a
    # single-brand corpus domain for that source's real URL (a 404 the trusted-
    # host scrub keeps) before qa.citations flags it dead. On by default.
    'citation_repoint_enabled': 'true',
    # why: scan-4 strip pass — deterministically REMOVE a dangling source
    # attribution whose subject grounds to no corpus source (keeping the claim),
    # rather than negatively prompting the writer. Runs after link/re-point, so
    # only the truly ungroundable remain; the advisory qa.unlinked_attribution
    # rail flags whatever the conservative strip frames leave behind. On by default.
    'citation_strip_unlinked_enabled': 'true',
    # why: YouTube attribution pass — turn a bare YouTube video URL (or a
    # raw-text YouTube link) into a proper [Channel](url) attribution, resolving
    # the channel via the authoritative oEmbed author_name. Corpus-independent
    # (runs even without research). Fail-soft: a dead/private/unresolvable video
    # is left untouched. On by default.
    'youtube_attribution_enabled': 'true',
    # Per-request timeout (s) for the YouTube oEmbed lookup. Kept tight so a slow
    # YouTube doesn't stall the citation atom; a timeout just leaves the link as-is.
    'youtube_oembed_timeout_seconds': '8',
    # Multi-tenant hosts where "same domain, different path" = DIFFERENT content
    # (re-pointing would mis-cite), so the re-point pass skips them. Empty =
    # use the built-in DEFAULT_MULTITENANT_HOSTS denylist; a non-empty CSV
    # REPLACES it wholesale (mirrors trusted_source_domains' override semantics).
    'citation_repoint_multitenant_hosts': '',
    # why: master switch for the grounded-LLM citation pass (content.
    # llm_reconcile_citations, #765) — the tail-catcher for named-source mentions
    # the deterministic repair can't frame-match. On by default; fail-open.
    'citation_reconcile_llm_enabled': 'true',
    # why: model pin for the pass; empty -> structured_extraction_model (a
    # JSON-reliable instruct model, NOT the reasoning writer). Local by default.
    'citation_reconcile_llm_model': '',
    # why: per-call timeout for the pass (advisory enhancement — keep short).
    'citation_reconcile_llm_timeout_seconds': '60',
    # why: skip the LLM call when the draft exceeds this many chars (avoids a
    # giant prompt on an outlier post; the deterministic pass already ran).
    'citation_reconcile_llm_max_content_chars': '24000',
    # why: advisory rail scoring named-source attributions left unlinked +
    # unmatched against the corpus; on by default, feeds qa_feedback + Grafana.
    'unlinked_attribution_enabled': 'true',
    # Gentle score: each unmatched attribution shaves N points down to a floor,
    # so a single missing link nudges the weighted QA mean without sinking a post.
    'unlinked_attribution_penalty_per': '8',
    'unlinked_attribution_score_floor': '60',
    # check_published_links job: HTTP codes meaning "host is up but refuses our
    # automated/anonymous probe" (bot-block / auth-gate / rate-limit) — counted
    # as access-restricted, NOT broken (the link works for a human reader). CSV;
    # empty falls back to the built-in {401,403,429}. Genuinely-dead links still
    # surface as 404/410/5xx/unreachable. The job also sends the shared crawler
    # User-Agent (utils.crawler_ua, +crawler_contact_url) so WAFs like Wikipedia
    # don't 403 the default httpx UA into a false positive.
    'link_check_skip_status_codes': '401,403,429',

    # ----- Image generation -----
    'image_gen_enabled': 'true',
    'enable_image_gen_warmup': '',
    # Worker in-process diffusers registry default (services/image_providers).
    # The live render path is the image-gen HTTP server, which reads the separate
    # 'image_generation_model' key (seeded in 0000_baseline.seeds.sql); both
    # point at z_image_turbo as of the 2026-06-19 bake-off. #image-zimage-and-variety.
    'image_model': 'z_image_turbo',
    # Operator-supplied negative prompt overrides the built-in default.
    # Leave empty to keep "text, words, letters, watermark, face, person, ..."
    # (Ignored by guidance-distilled models like z_image_turbo, which run at
    # CFG 0 and take no negative prompt.)
    'image_negative_prompt': 'text, words, letters, numbers, watermark, signature, logo, face, person, human, hands, fingers, blurry, low quality, distorted, deformed',
    # Style suffix appended to every image-gen prompt — niche brand voice.
    # Examples: "cyberpunk, neon accents" (tech), "natural light, botanical" (gardening)
    'image_base_style_prompt': '',
    # Pexels orientation default for featured + inline images.
    # Options: landscape (default) | portrait | square
    'image_aspect_ratio': 'landscape',
    # Comma-separated fallback keywords for Pexels when the semantic query
    # returns zero results.  Leave empty to use the built-in generic list.
    # Example for gardening: "plants, garden, outdoor, nature, floral"
    'image_pexels_fallback_keywords': '',
    'image_styles': '[\n    {"name": "flat_vector", "scene": "flat vector illustration", "tags": "simple geometric shapes, limited cyan and dark navy palette, tech iconography, clean minimal design, no text"},\n    {"name": "line_art", "scene": "thin white line art on pure black background", "tags": "wireframe blueprint aesthetic, technical drawing style, no text"},\n    {"name": "mono_diagram", "scene": "monochrome technical diagram", "tags": "white on dark slate, architectural blueprint feel, precise lines, no text"},\n    {"name": "terminal", "scene": "dark terminal aesthetic", "tags": "green and cyan glowing text on black, command line hacker style, no readable text"},\n    {"name": "cyberpunk_neon", "scene": "cyberpunk neon style", "tags": "dark background with glowing cyan and purple neon light lines, futuristic tech, moody, no text"},\n    {"name": "glassmorphism", "scene": "dark glassmorphism UI design", "tags": "frosted glass panels, subtle reflections, dark background, soft cyan glow, modern interface, no text"},\n    {"name": "silhouette", "scene": "dramatic dark silhouette", "tags": "black shapes on deep navy background, single bright cyan accent glow, no text"},\n    {"name": "isometric", "scene": "isometric 3D illustration", "tags": "colorful clean technical, servers and dashboards, low angle, no text"},\n    {"name": "low_poly", "scene": "low poly 3D geometric mesh style", "tags": "modern clean triangulated shapes, cyan and purple tones, no text"},\n    {"name": "watercolor", "scene": "soft watercolor washes with sharp technical line overlays", "tags": "abstract tech art, muted tones with cyan accents, no text"},\n    {"name": "pixel_art", "scene": "retro 16-bit pixel art style", "tags": "detailed pixel illustration, game aesthetic, bright colors on dark background, no text"},\n    {"name": "paper_cutout", "scene": "layered paper cutout craft style", "tags": "shadows between layers, colorful paper pieces, tactile handmade feel, no text"},\n    {"name": "risograph", "scene": "risograph print style", "tags": "grainy texture, limited 3 color palette, vintage print shop aesthetic, halftone dots, no text"}\n  ]',
    # Seconds the CLI waits for a POST /api/tasks/{id}/regen-image response.
    # Image generation blocks the HTTP handler, so this must exceed SDXL inference
    # time (~30-90s typical; longer after a post-crash boot window).
    'post_edit_regen_image_timeout_s': '300',
    # Seconds the CLI waits for POST /api/tasks/{id}/rebuild-images. Rebuilds
    # every image (featured + all inline) sequentially, so it must exceed a
    # single regen by the image count — generous default.
    'post_edit_rebuild_images_timeout_s': '600',
    # Seconds the CLI waits for POST /api/tasks/{id}/{replace,remove}-image.
    # No image generation here, but --which featured on a PUBLISHED post runs
    # export_full_rebuild inline, which re-uploads EVERY published post's JSON
    # to R2 sequentially — so this scales with corpus size, not with the edit.
    # Measured 2026-07-31: 43.4s end-to-end at 164 published posts (~265ms per
    # post). 300 leaves ~7x headroom (~1,100 posts) before the client gives up
    # on a rebuild that is still succeeding server-side.
    'post_edit_image_timeout_s': '300',
    # Inline-illustration style pool (JSON array of style strings). Empty =>
    # the stylized code fallback (modules/content/stages/replace_inline_images.py
    # INLINE_STYLES). Parallels 'image_styles' for the featured image. Photoreal
    # styles were dropped from the fallback — low-step image-gen butchers photoreal
    # detail and the brand is stylized. #image-zimage-and-variety.
    'inline_image_styles': '',
    # Cross-post style-dedup window: how many recently-published posts' image
    # styles to filter out when picking the next featured style
    # (source_featured_image._load_recent_published_styles). 0 disables the
    # cross-post filter (in-process per-worker dedup still applies).
    'image_style_dedup_window': '5',
    # In-process featured-image style-rotation dedup window
    # (services/image_style_rotation.py). `size` caps how many recent picks
    # are remembered; `ttl_seconds` caps how long a pick blocks its own reuse.
    # Previously module-level constants; externalised so the rotation window
    # is tunable (a small style pool wants a shorter window to avoid starving).
    'image_style_history_size': '10',
    'image_style_history_ttl_seconds': '3600',
    # Per-call HTTP timeout (seconds) for a local image inference server
    # (image-gen / FLUX / Z-Image `/generate`) to render one image. Must cover a
    # COLD model load: the image-gen server unloads after 60s idle (so Ollama can
    # use the GPU) and is re-evicted on every Ollama call, so most renders pay
    # the reload. Measured cold-load for Z-Image-Turbo (6B) is ~133s + render.
    # 300 gives headroom for the OCR gate's worst case (cold load + up to
    # image_ocr_gate_max_attempts renders, each a few seconds) while still
    # bounding a genuinely hung GPU server. Wired into the featured + inline +
    # video render calls. #image-zimage-and-variety.
    #
    # THIS IS THE ONLY PLACE THE NUMBER LIVES (2026-07-31 reconciliation). All
    # seven read sites take their fallback from `default_int(...)` instead of
    # repeating a literal. They had drifted to four values — 90 at four sites
    # (the #1566 default), 240 at three (the #1727 default), 300 declared here
    # (raised by #2386 when the OCR gate gained up-to-3 attempts), and 240 live
    # on prod. Prod's copy was not an operator override: it was written the same
    # day #1727 made 240 the default, then froze, because seeding is
    # `ON CONFLICT DO NOTHING`. So prod ran a render timeout that predated the
    # retry loop it had to accommodate. If you change this number, prod does NOT
    # follow — an existing row must be updated deliberately.
    'image_render_timeout_seconds': '300',
    # LLM params for the image-PROMPT generation step — the small model that
    # writes the image-gen prompt from the topic + chosen style (NOT the image
    # render itself). Externalised so prompt creativity / length / patience are
    # tunable without a code edit. #image-zimage-and-variety.
    'image_prompt_temperature': '0.8',
    'image_prompt_max_tokens': '150',
    'image_prompt_timeout_seconds': '90',
    # Render retry (2026-07-28). The dominant inline/featured render failure is
    # a WINDOW, not a verdict — image-gen restarting for a VRAM reclaim (cold
    # start + lazy model reload) or a GPU lock timeout under contention. Both
    # clear in seconds, so one retry recovers most renders that previously
    # became a silent stock substitution. 2 = one initial attempt + one retry.
    'image_gen_render_attempts': '2',
    'image_gen_retry_backoff_seconds': '3',
    # Headroom added on top of the render budget when source_featured_image
    # computes the node timeout it needs (attempts x render_timeout +
    # backoff + THIS). Covers the work that sits inside the node timeout but
    # outside the per-request render timeout: the prompt-build LLM call, the
    # R2 upload, and above all the GPU-lock wait, which is unbounded under
    # video/VRAM contention. RCA 2026-07-31: with a hardcoded 300s node
    # timeout against a 483s configured budget, a hero image that had already
    # rendered AND uploaded was discarded two seconds before the stage
    # returned it, and the run recorded itself clean.
    'image_featured_stage_overhead_seconds': '120',
    # Stock-photo FALLBACK for a failed image-gen render. Default OFF: owned
    # imagery is the brand asset, and an undisclosed stock swap ran unnoticed
    # for weeks because the fallback logged per-image and still reported
    # success. Off means a failed render yields no image plus a warn-severity
    # `image_gen_downgrade` finding, rather than quietly shipping stock.
    #
    # This gates the FALLBACK only — stock chosen DELIBERATELY (the video
    # director picking Pexels for a shot that needs real photography, per the
    # image-media policy) is a separate path and is unaffected.
    #
    # Kept as a setting rather than deleted so a fork that wants stock
    # fallback can turn it back on.
    'image_stock_fallback_enabled': 'false',
    # Reserved-VRAM floor (MB) below which scripts/image-gen-server.py refuses
    # a hard unload. Exiting costs a cold start, and any /generate landing in
    # that window downgrades an article image — so it must actually reclaim
    # something. Measured on RESERVED, not allocated: after unload_pipeline()
    # drops the tensors, allocated is 0 by construction and cannot detect the
    # multi-GB caching-allocator pool an exit would return.
    'image_gen_hard_unload_min_reserved_mb': '512',
    # OCR text-leakage gate (2026-07-13): scripts/image-gen-server.py OCR-scans
    # every /generate render and retries with a fresh seed when leaked text
    # exceeds max_chars, keeping the best-scoring attempt. Deterministic
    # check-and-retry, not a model swap — guidance-distilled models (Z-Image)
    # ignore negative_prompt entirely, and the 2026-07 bake-off
    # (glad-labs-stack#2386) showed a positive-prompt "textless" clause alone
    # doesn't fix it (z_image_turbo still averaged 25.33 leaked chars/image
    # with that clause applied). max_chars=6 sits comfortably above the noise
    # floor of a stray 1-2 char OCR misread on non-text imagery but well below
    # a genuinely mangled label. Read directly by image-gen-server.py via
    # asyncpg (it has no SiteConfig), refreshed every ~60s + on POST /reload.
    'image_ocr_gate_enabled': 'true',
    'image_ocr_gate_max_chars': '6',
    'image_ocr_gate_max_attempts': '3',
    'image_ocr_gate_min_confidence': '0.3',

    # ----- Video / podcast / TTS -----
    'audio_gen_engine': '',
    # Per-call HTTP timeout (seconds) for a local audio inference server
    # (Stable Audio Open `/generate`) to render one clip. Was a hardcoded
    # literal in services/audio_gen_providers/stable_audio_open.py.
    'audio_render_timeout_seconds': '180',
    # Stage-2 media trigger (#689 Plan 7) — the dispatch_media_pipeline job is
    # scheduled but DORMANT until media_pipeline_trigger_enabled flips on; this
    # is what takes media_pipeline from dormant to LIVE in prod.
    # media_pipeline_max_per_cycle caps GPU-bound renders kicked off per cycle.
    'media_pipeline_max_per_cycle': '1',
    'media_pipeline_trigger_enabled': 'false',
    # media_distribute (#689 Plan 8 / 8b-2) links rendered media_assets to their
    # published post + seeds Gate-2 approvals; caps assets linked per cycle.
    # Gated on the same media_pipeline_trigger_enabled master switch.
    'media_distribute_max_per_cycle': '20',
    # Stage-3 podcast lane (#689 deviation — separate isolated graph). Its own
    # master switch so podcast goes live independently of the video media_pipeline
    # (default off — safe for OSS forks; the operator flips prod to 'true').
    # The dispatch job caps renders/cycle; distribute caps link+seed+deliver/cycle.
    'podcast_pipeline_trigger_enabled': 'false',
    'podcast_pipeline_max_per_cycle': '2',
    'podcast_distribute_max_per_cycle': '20',
    # Disable the podcast script model's reasoning channel (default). The
    # thinking-capable gemma-class script model (podcast_script_model) otherwise
    # writes its prompt-echo + planning outline + self-QA checklist into the
    # reasoning channel on every run — and ~71% of the time that plan LEAKS into
    # the visible content instead, so TTS reads the scaffold aloud (the
    # podcast_scaffold_dump finding; 2026-07-07 reproduced in-container: think on
    # → 4.7k-char plan that intermittently lands in content, think=False → empty
    # reasoning, clean full-length script). Podcast output is prose not JSON, so
    # the symptom is a spoken-aloud leak rather than the empty-JSON the video
    # director hit — same root cause, mirrors video_director_disable_thinking
    # (#2191) / writer_disable_thinking (#2163). Harmless no-op on a non-thinking
    # model, dropped for cloud targets by the LiteLLM provider. The #2186 scaffold
    # guard stays as the safety net. Read via _resolve_podcast_think in
    # services/podcast_service.py. Set false only to deliberately let a reasoning
    # script model chain-of-think.
    'podcast_disable_thinking': 'true',
    # media_reconciliation (the Stage-2 drift watchdog) re-dispatches the gated
    # video / podcast pipelines on a genuine miss instead of authoring media
    # directly. Each re-dispatch bumps pipeline_tasks.{media_pipeline,podcast}_
    # redispatch_count; the watchdog refuses to clear the dispatch marker once
    # the count reaches the matching cap below, so a permanently-failing render
    # can't loop forever. Both previously relied on an inline .get(...,'3')
    # fallback — seeded here so they're DB-tunable like every other knob.
    'media_pipeline_redispatch_max': '3',
    'podcast_redispatch_max': '3',
    # Render-infra health gate (2026-07-03): six posts wedged at
    # media_pipeline_redispatch_max because dispatches fired during
    # wan-server/image-gen/DNS outage windows and every fast-fail burned a
    # bounded re-dispatch. dispatch_media_pipeline now probes wan + image-gen
    # /health (plus a DNS canary) before dispatching and defers the cycle
    # while unhealthy; an unhealthy-infra run failure un-claims the piece
    # instead of consuming an attempt. media_reconciliation requires a
    # healthy probe before its bounded cap reset (below). The canary host
    # defaults to the storage_public_url host when unset.
    'media_infra_healthcheck_enabled': 'true',
    'media_infra_health_timeout_seconds': '5',
    'media_infra_dns_canary_host': '',
    # Bounded cap-reset self-heal (feedback_self_heal_not_suppress): when a
    # missing-video post's task sits AT media_pipeline_redispatch_max but the
    # render infra probes healthy again, media_reconciliation resets the
    # counter and re-arms Stage-2 — at most once per cooldown per task
    # (pipeline_tasks.media_pipeline_cap_reset_at) — instead of re-reporting
    # the same media_drift forever with no recovery path.
    'media_redispatch_cap_reset_enabled': 'true',
    'media_redispatch_cap_reset_cooldown_hours': '24',
    # media_feed_reconciliation — converges the published podcast/video RSS
    # feeds on R2 onto the DB's eligible-episode set. ON by default: unlike the
    # Stage-2 media jobs it needs no GPU and no dormant master switch, and its
    # steady state is one render + one GetObject per medium. Every feed rebuild
    # is otherwise event-coupled and best-effort, so one dropped event stranded
    # the feed indefinitely (2026-07-18: R2 listed 71 episodes vs 100 eligible).
    'media_feed_reconciliation_enabled': 'true',
    # Asymmetry guard. The feed route returns a VALID EMPTY feed when its DB
    # query fails, so a convergence loop that trusted every render would wipe
    # the podcast off Apple/Spotify during a DB blip. The reconciler will grow a
    # feed freely but refuses to remove more than this many items in one pass,
    # emitting media_feed_render_collapse instead. Raise it only for a genuine
    # bulk un-publish; '0' means refuse any shrink at all.
    'media_feed_reconcile_max_shrink': '5',
    # Per-medium call-to-action outros (DB-tunable; ML-optimizable later).
    # ``media.cta.podcast`` is LIVE — ``podcast.render`` appends it to the script
    # before TTS so the episode asks for ratings/reviews. The video CTAs are
    # seeded ahead of their reader: the video render appends them as an end beat,
    # which lands with the deferred video-side half of #689 (the video render
    # shares the base narration, so a spoken video CTA needs its own render path —
    # see docs/architecture/podcast-pipeline-stage3.md §11).
    'media.cta.podcast': (
        'If this was useful, follow the show and leave a quick rating or review '
        'on Spotify or Apple Podcasts — it genuinely helps us reach more people.'
    ),
    'media.cta.video': 'If this helped, like the video and subscribe for more.',
    'media.cta.video_short': 'Follow for more — like and subscribe.',
    # Gate-2 earned-autonomy (#531) — automatic Tier-2 approval when the last N
    # dispatches for a (niche, medium) combo all succeeded. Disabled by default;
    # operator flips media.gate2.earned_autonomy_enabled to 'true' once they are
    # satisfied with the track record. min_dispatches is the consecutive-success
    # window; per-niche overrides (niche.<slug>.media.<medium>.earned_autonomy_
    # min_dispatches) take precedence over the global value when set.
    'media.gate2.earned_autonomy_enabled': 'false',
    'media.gate2.earned_autonomy_min_dispatches': '5',
    'podcast_description': '',
    'podcast_name': '',
    # Podcast distribution assets — empty on OSS; the operator overlay
    # (operator_overrides.OPERATOR_SETTING_OVERRIDES) restores the operator's
    # real Spotify show + R2 cover art. Blanked in seeds because they
    # correlate back to the operator tenant (see test_baseline_seeds_no_operator_leaks).
    'podcast_spotify_show_id': '',
    'podcast_spotify_url': '',
    'podcast_cover_url': '',
    'podcast_tts_engine': '',
    'podcast_tts_enabled': 'false',
    'podcast_tts_base_url': 'http://speaches:8000/v1',
    'podcast_tts_voice': 'bf_emma',
    'podcast_tts_model': 'speaches-ai/Kokoro-82M-v1.0-ONNX',
    'podcast_tts_format': 'mp3',
    # Normalize the audio after Speaches byte-concatenates its internal segments
    # (else players cut off mid-episode AND transcoders can mishandle the
    # multi-header structure at the tail). Fail-soft; needs ffmpeg.
    'podcast_tts_remux_enabled': 'true',
    # 'reencode' (default) collapses the per-segment Xing/LAME headers into one
    # clean stream; 'copy' is the legacy lossless `-c copy` header-only repair.
    'podcast_tts_remux_mode': 'reencode',
    # Output bitrate for re-encode mode. 192k (not 96k) is the delivery
    # bitrate for spoken word — effectively transparent; 96k was compounding
    # with the TTS sidecar's own lossy encode into an audible double
    # transcode (the audio-fidelity investigation).
    'podcast_tts_remux_bitrate': '192k',
    # EBU R128 loudness normalization (audio_clipping fix). Kokoro hands the
    # render full-scale audio (peak ~0.0 dBFS), which trips the qa.audio
    # -0.1 dBFS clip gate and risks true-peak distortion after MP3 encode.
    # ffmpeg loudnorm pulls the integrated loudness to the podcast target AND
    # caps the true peak, restoring headroom. Rides the remux re-encode (one
    # ffmpeg pass) and also runs when remux is disabled. Fail-soft; needs ffmpeg.
    'podcast_tts_loudnorm_enabled': 'true',
    # Integrated loudness target in LUFS — the Apple/Spotify podcast standard.
    'podcast_tts_loudnorm_i': '-16',
    # Max true peak in dBTP — the headroom that keeps max_volume under the gate.
    'podcast_tts_loudnorm_tp': '-1.5',
    # Loudness range (LRA) — EBU R128 dynamics target for spoken word.
    'podcast_tts_loudnorm_lra': '11',
    # Output sample rate — loudnorm upsamples to 192 kHz internally, so resample
    # back to a distribution-standard rate (44.1 kHz).
    'podcast_tts_loudnorm_ar': '44100',
    # --- Bake-off TTS engine (opt-in `tts-hq` compose profile) -----------------
    # Emotion-capable challenger to the default Kokoro/Speaches narration,
    # compared offline via `poindexter media tts-bakeoff`. Speaks the OpenAI
    # /v1/audio/speech contract. Wired into the LIVE pipeline (Phase 2,
    # podcast_service._generate_with_chatterbox) when podcast_tts_engine=
    # 'chatterbox' — the bake-off winner over CosyVoice2 (rejected for
    # artifacts, removed entirely — see
    # docs/superpowers/plans/2026-07-10-tts-engine-bakeoff-phase1.md).
    # Chatterbox (MIT) — emotion via the `exaggeration` dial (0.0-1.0).
    'plugin.tts_provider.chatterbox.base_url': 'http://chatterbox:8000/v1',
    'plugin.tts_provider.chatterbox.model': 'chatterbox',
    # 0.5 is the upstream-recommended neutral default; raise for more emotion.
    'plugin.tts_provider.chatterbox.exaggeration': '0.5',
    # cfg_weight pacing knob; lower = slower/more deliberate delivery. Bottoms
    # out around 0.30 — below that it lengthens PAUSES instead of slowing
    # articulation, which reads as hesitant rather than measured.
    'plugin.tts_provider.chatterbox.cfg_weight': '0.5',
    # Pitch-preserving pace multiplier applied after synthesis (<1 = slower).
    # 1.0 = off, and OSS default, since the right value depends on the pinned
    # voice. This is the effective pace control: a clone inherits timbre from
    # audio_prompt_path but not its speaking rate, so re-recording a slower
    # reference moves output pace only marginally (measured: a -19% reference
    # bought -7% output). Rides the existing loudnorm pass — no extra encode.
    'plugin.tts_provider.chatterbox.atempo': '1.0',
    # Client read-timeout (s). Bake-off sidecars can run CPU-only (no spare
    # VRAM), where a full paragraph takes minutes — well past the 120s default.
    'plugin.tts_provider.chatterbox.timeout_s': '600',
    # Zero-shot voice-clone reference — a path INSIDE the chatterbox container
    # (see scripts/tts_sidecars/assets/README.md). Empty = the sidecar's own
    # built-in voice (OSS default, zero setup). The operator overlay restores
    # the real pinned voice on the Glad Labs rig.
    'plugin.tts_provider.chatterbox.audio_prompt_path': '',
    'scheduled_publisher_poll_seconds': '60',
    # TTS pronunciation defaults — JSON objects operators can tune via
    # `poindexter settings set`. The code merges DB values on top of the
    # hardcoded constants in podcast_service, so DB entries add-to / override
    # defaults. See skills/content/tts/SKILL.md for format and examples.
    # NOTE: settings seeding uses ON CONFLICT DO NOTHING, so changing these
    # values here only affects new installs. Existing installs keep whatever
    # was seeded first; update via `poindexter settings set` to override.
    'tts_acronym_replacements': (
        '{"SOC": "security operations", "CRM": "customer relationship management", "SLA": "service level agreement", "KPI": "key performance indicator", "ROI": "return on investment", "MVP": "minimum viable product", "POC": "proof of concept", "EOL": "end of life"}'
    ),
    'tts_pronunciations': (
        '{"I/O": "I O", "CI/CD": "See Eye See Dee", "DevEx": "dev ex", "DevOps": "dev ops", "GitHub": "git hub", "GitLab": "git lab", "TCP/IP": "TCP IP", "Vue.js": "view J S", "FastAPI": "fast A P I", "GitFlow": "git flow", "GraphQL": "graph Q L", "MongoDB": "mongo D B", "Next.js": "next J S", "Node.js": "node J S", "pgvector": "P G vector", "DevSecOps": "dev sec ops", "WebSocket": "web socket", "JavaScript": "java script", "PostgreSQL": "postgres", "TypeScript": "type script", "VRAM": "Vee RAM", "SRAM": "Ess RAM", "DRAM": "Dee RAM", "PB": "petabyte", "TB": "terabyte", "GB": "gigabyte", "MB": "megabyte", "KB": "kilobyte", "GHz": "gigahertz", "MHz": "megahertz", "kHz": "kilohertz", "Gbps": "gigabits per second", "Mbps": "megabits per second", "Kbps": "kilobits per second", "fps": "frames per second", "OS/2": "OS 2", "e.g.": "for example", "i.e.": "that is", "etc.": "and so on", "vs": "versus", "vs.": "versus", "approx.": "approximately", "incl.": "including", "w/": "with", "w/o": "without", "CI": "See Eye", "GLM": "G L M", "LLM": "L L M", "vLLM": "V L L M", "LiteLLM": "lite L L M", "SDXL": "S D X L", "Phi": "fie", "Kokoro": "ko ko ro", "Qwen": "que-wen", "Ollama": "O llama", "QA": "Q A", "iframe": "I frame"}'
    ),
    # Model-identifier families for the TTS speech normalizer. A CSV of base
    # family names; podcast_service collapses a pin like
    # `gemma-4-31B-it-qat:latest` → spoken "gemma four thirty-one B", keeping
    # family + version/size and dropping the quant/tag/GPU config noise. Add a
    # family here (e.g. a new local model) to have its config tail cleaned too.
    'tts_model_name_families': (
        'gemma,glm,qwen,phi,llama,mistral,mixtral,deepseek,codellama,qwq,granite,nemotron,smollm,kokoro'
    ),
    # Domain TLD pronunciation for the spoken podcast outro. The outro speaks
    # site_domain aloud ("Visit gladlabs dot io ..."); a bare two-letter TLD
    # like "io" reads as "eoh" in TTS. This map rewrites ONLY the final domain
    # segment, so — unlike tts_pronunciations, whose entries also run at the
    # render boundary — it can't corrupt body words like "audio". Add niche
    # TLDs as needed, e.g. '{"io": "eye oh", "ai": "A I", "gg": "G G"}'.
    'tts_domain_tld_pronunciations': '{"io": "eye oh"}',
    # Voice selection (#689 Plan 7). Rotation is OPT-IN: when
    # tts_voice_rotation_enabled is false (the default), narration uses the
    # single `podcast_tts_voice`; when true it hash-rotates the pool
    # (`tts_voice_pool`, or the podcast_service VOICE_POOL constant when empty)
    # by post_id for variety. Podcast and video (which reuses the podcast
    # narration voice) both honor this via podcast_service._select_voice.
    'tts_voice_pool': '',
    'tts_voice_rotation_enabled': 'false',
    'video_compositor': '',
    'video_feed_name': '',
    # The canonical Wan negative prompt (model-card English list). Wan
    # quality leans heavily on it — "static / still picture" directly fights
    # the barely-animates i2v failure mode. Historically seeded '' AND the
    # provider always sent the key, clobbering the wan-server's own default
    # (a present-but-empty pydantic field beats the field default), so every
    # hero render ran with no negative guidance at all. The provider now
    # omits the key when this is empty; the value here keeps the configured
    # path and the server default in agreement.
    'video_negative_prompt': (
        'bright tones, overexposed, static, blurred details, subtitles, '
        'style, works, paintings, images, overall gray, worst quality, '
        'low quality, JPEG compression residue, ugly, incomplete, '
        'extra fingers, poorly drawn hands, poorly drawn faces, '
        'deformed, disfigured, misshapen limbs, fused fingers, '
        'still picture, cluttered background, three legs, '
        'many people in the background, walking backwards'
    ),
    'video_tts_engine': '',

    # ----- Voice agent -----
    'voice_agent_brain': 'ollama',
    'voice_agent_brain_mode': 'ollama',
    'voice_agent_default_identity': 'operator',
    'voice_agent_identity': 'poindexter-bot',
    'voice_agent_livekit_enabled': 'true',
    'voice_agent_livekit_url': 'ws://livekit:7880',
    # Bare Ollama tag — NO `ollama/` prefix. Unlike the LiteLLM-routed
    # *_model keys, this one reaches Ollama's own API, which 404s on the
    # prefixed form. Benchmarked 2026-07-31 on the real system prompt + the
    # three voice tools: qwen2.5:7b answers in 0.22s (inside the natural
    # conversational gap), picks the right tool 4/4, and fits in 4.7 GB.
    # llama3.2:3b is faster but got a basic cron question wrong; qwen2.5:32b
    # matches accuracy at 19 GB; glm-4.7-5090 takes 6.81s because it thinks.
    'voice_agent_llm_model': 'qwen2.5:7b',
    'voice_agent_ollama_url': 'http://host.docker.internal:11434/v1',
    'voice_agent_public_join_url': '',
    'voice_agent_public_livekit_url': '',
    'voice_agent_recall_k': '3',
    'voice_agent_recall_min_similarity': '0.5',
    'voice_agent_room_name': 'poindexter',
    # OSS-generic voice persona. The Glad Labs operator overlay
    # (services.operator_overrides) restores Matt's personalised version on the
    # operator rig; keep this text identical to the 0000_baseline.seeds.sql seed
    # so the overlay's "overwrite only the OSS default" match fires.
    'voice_agent_system_prompt': """You are Emma, a concise voice assistant for the operator. Speak naturally — your output goes through text-to-speech, so avoid markdown, bullet lists, and code blocks. Use short sentences. If the operator asks a factual question you don't know the answer to, say so plainly rather than guessing. Default to responses under 30 seconds of speech (~80 words) unless they explicitly ask for a longer one.

You have access to these tools and you SHOULD call them whenever the operator asks something they answer:

- check_pipeline_health: call this when the operator asks how the system is doing, whether anything is broken, system status, or health.
- get_published_post_count: call this when the operator asks how many posts are live, the number of articles, or pipeline output volume.
- get_ai_spending_status: call this when the operator asks about budget, costs, spend, or money burned.

When you call a tool, do NOT also say "let me check" or "one moment" — just emit the tool call. After the tool returns, summarize the result in one or two short sentences fit for speech. Do not list raw numbers — say "the system is healthy, GPU is at 48 percent" rather than reading every metric.

If the operator says something you cannot answer with a tool, answer plainly. Never claim you cannot hear or that you only process text — you are receiving live audio transcribed by Whisper.""",
    'voice_agent_tts_speed': '1.0',
    'voice_agent_tts_voice': 'bf_emma',
    'voice_agent_vad_stop_secs': '0.4',
    # voice_agent_webrtc_* defaults retired 2026-05-08 — livekit is the
    # canonical voice surface. Existing app_settings rows from migrations
    # 0108 + 20260505 stay (orphan but harmless); no new installs seed them.
    'voice_agent_whisper_model': 'base',

    # ----- Devto / external publishing -----
    'devto_api_base': 'https://dev.to/api',
    # Selective syndication (spec 2026-07-12, repointed to content-type
    # 2026-07-13): only posts whose CONTENT-TYPE is in this CSV allowlist AND
    # whose quality_score >= the floor cross-post to Dev.to. Content-types come
    # from post_content_types (ClassifyContentTypesJob). OSS default is empty =
    # syndicate nothing (opt-in); operators set their allowlist via
    # `poindexter settings set` / the operator overlay (e.g. ai-ml,founder-meta).
    'devto_syndicate_content_types': '',
    'devto_syndicate_min_quality': '80',
    # ----- Content-type classification (spec 2026-07-13) -----
    # An additive, multi-label content-type axis populated by
    # ClassifyContentTypesJob into the post_content_types table. The classifier
    # emits ONLY labels from this set (anything else the model returns is
    # dropped). Every taxonomy is a DB setting (feedback_db_first_config).
    'content_type_labels': 'ai-ml,pc-hardware,gaming,software-engineering,founder-meta',
    # Model pin for the classifier. Empty → falls back to the shared
    # structured_extraction_model (JSON output must not use a reasoner —
    # feedback_reasoning_models_empty_json).
    'content_type_classifier_model': '',
    # Master switch for the classifier job.
    'classify_content_types_enabled': 'true',
    # (mastodon_instance_url removed 2026-06-29 — the legacy direct Mastodon
    #  adapter is retired; Mastodon-via-Postiz uses postiz_integration_id_mastodon.)

    # ----- Newsletter / email -----
    'newsletter_batch_delay_seconds': '2',
    'newsletter_batch_size': '50',
    'newsletter_enabled': 'false',
    'newsletter_from_name': '',
    'newsletter_provider': 'resend',
    'smtp_host': '',
    'smtp_port': '587',
    'smtp_use_tls': 'true',

    # ----- Storage (provider-agnostic S3-compatible: R2 / S3 / B2 / MinIO) -----
    # Public base URL for the object store; consumers append the object
    # key. Replaces the deprecated ``r2_public_url`` (storage_* cutover,
    # Glad-Labs/poindexter#731).
    'storage_public_url': '',
    # S3-compatible bucket for media objects. Empty on OSS (operators configure
    # their own bucket); the operator overlay restores Glad Labs' bucket.
    'storage_bucket': '',
    # S3/R2 access key ID (paired with the storage_secret_key secret).
    # is_secret=false by design — it can't authenticate alone, so it's cached.
    # Empty on OSS; the operator overlay restores the operator's key. Blanked
    # in seeds because it's live operator credential material (see
    # test_baseline_seeds_no_operator_leaks).
    'storage_access_key': '',
    # Custom vanity domain for image objects (e.g. ``https://images.gladlabs.io``).
    # When set, image URLs use this base instead of the rate-limited r2.dev
    # public bucket URL. Empty = fall back to storage_public_url (poindexter#732).
    # Configure via: poindexter settings set storage_image_custom_domain https://images.gladlabs.io
    'storage_image_custom_domain': '',
    # Images wider/taller than this are downscaled (aspect-ratio preserved,
    # never upscaled) before the WebP conversion above. 1920 matches the
    # largest entry in the public site's Next.js Image `deviceSizes`
    # (web/public-site/next.config.js) — the responsive <Image> pipeline
    # never requests a wider variant, so storing anything larger than this
    # is pure wasted bucket space with zero possible visual benefit.
    'storage_image_max_width': '1920',
    'storage_image_max_height': '1920',
    # Wait this many seconds after a post publishes before uploading
    # podcast/video/short to the object-store CDN — gives generation
    # time to finish. Storage-agnostic rename of the deprecated
    # ``media_r2_upload_delay_seconds`` (#731).
    'media_upload_delay_seconds': '240',
    # Minimum ASR-vs-script similarity ratio for the Stage-2 caption
    # fidelity check (media.transcribe_narration, Plan 5 #676). When the
    # one-ASR-pass transcript diverges below this normalized
    # SequenceMatcher ratio from the source narration script, an advisory
    # ``caption_fidelity`` finding fires (likely a TTS dropout / truncation).
    # Advisory only — never fails the render.
    'media.caption.fidelity_min_ratio': '0.80',
    # Gate for the Stage-2 media QA frame human-detection check
    # (media.qa, Plan 6 #1193). When 'true', a midpoint frame of each
    # rendered video is vision-checked for a photorealistic human (policy
    # #675). Fail-soft: a missing ffmpeg / vision error is a no-op (no
    # finding). Set 'false' to skip the vision call entirely.
    'media_qa_frame_detection_enabled': 'true',
    # Max allowed drift (seconds) between the probed render duration and the
    # director shot-list's planned total_duration_s before media.qa emits an
    # advisory ``av_desync`` finding (Plan 6 #1193). Advisory only.
    'media.qa.av_sync_tolerance_s': '2.0',

    # ----- Media Quality Layer 2 (semantic scoring) — spec 2026-07-09 -----
    # Replaces the binary 0/1 media quality_score with a 0-100 semantic
    # signal. Advisory-first: informs the score + emits advisory findings,
    # never auto-rejects. Master switch off → Layer 2 skipped (bare-100 pass).
    'media.layer2.enabled': 'true',
    # Vision model for the video topic-match signal. Empty → resolved from
    # qa_vision_model at read time (empty = "not separately configured"; a
    # still-empty qa_vision_model then skips the signal, per no-silent-defaults).
    'media.video.topic_match_model': '',
    # Frames sampled from the composed video for the topic-match check.
    'media.video.topic_match_frames': '3',
    # Below this 0-100 topic-match score → advisory ``video_topic_mismatch``.
    'media.video.topic_match_min': '50',
    # Below this mean rendered-shot vision score → advisory
    # ``video_shot_fidelity_low`` (matches video_shot_qa_threshold).
    'media.video.shot_fidelity_min': '60',
    # Judge model for the podcast faithfulness signal. Empty → resolved from
    # ragas_judge_model at read time (already a faithfulness judge).
    'media.podcast.faithfulness_model': '',
    # Below this 0-100 faithfulness score → advisory ``podcast_faithfulness_low``.
    'media.podcast.faithfulness_min': '60',

    # ----- Observability / monitoring -----
    # DataFabric store URLs (#429). DataFabric clients run inside the
    # worker/brain containers, so the defaults use compose-service DNS — a
    # 'localhost' default would resolve to the container itself (the
    # in-container footgun PR #1827 fixed for the GPU-metrics URL). Internal
    # DNS also avoids the host wslrelay port-forward that can wedge on Windows.
    # prometheus listens on 9090 internally (host-published 9091).
    'data_fabric_prometheus_url': 'http://prometheus:9090',
    'data_fabric_loki_url': 'http://loki:3100',
    'data_fabric_tempo_url': 'http://tempo:3200',
    'data_fabric_pyroscope_url': 'http://pyroscope:4040',
    'enable_pyroscope': 'false',
    # Defaulted true 2026-05-17 (Glad-Labs/poindexter#409) — the
    # OTLP gRPC exporter + Tempo container + per-probe instrumentation
    # have been live on prod since 2026-05-13. Baseline seed already
    # ships true; the in-code default was the last spot where a fresh
    # ``SiteConfig`` or missing DB row silently produced a NoopTracer
    # (spans dropped, Tempo panels empty).
    'enable_tracing': 'true',
    'langfuse_host': 'http://langfuse-web:3000',
    # Browser-facing Langfuse base for the console's trace "waterfall" deeplinks.
    # Distinct from langfuse_host (http://langfuse-web:3000, a Docker-internal
    # name the operator's browser can't resolve). Compose publishes the UI on
    # :3010 (3010→3000). Empty falls back to langfuse_host in traces_routes.
    'langfuse_public_url': 'http://localhost:3010',
    'langfuse_tracing_enabled': 'true',
    # Tempo's OTLP HTTP receiver on /v1/traces. Matches the exporter
    # we actually import (``opentelemetry.exporter.otlp.proto.http``).
    # The gRPC port 4317 is wrong for this exporter — using it produces
    # no spans in Tempo but no errors loud enough to notice, which is
    # exactly the silent-failure mode #505 describes.
    'otel_exporter_otlp_endpoint': 'http://tempo:4318/v1/traces',
    'pyroscope_server_url': 'http://pyroscope:4040',
    'sentry_enabled': 'true',
    # GlitchTip org slug the brain triage probe queries — operators set it to
    # the org they created in the GlitchTip first-login setup. Lockstep with
    # the baseline seed; the operator overlay restores the operator's own slug.
    'glitchtip_triage_org_slug': 'poindexter',
    'template_runner_progress_streaming': 'true',
    # Defaulted true 2026-05-17 (Glad-Labs/poindexter#412) — the
    # AsyncPostgresSaver wiring has been live on prod since 2026-05-13
    # without incident, smoke test at
    # ``scripts/smoke_371_postgres_checkpointer.py`` stays green, and the
    # baseline seeds row matches. The pre-flip default of 'false' meant
    # any fresh install or test SiteConfig without a DB row silently
    # used MemorySaver (no durability across runs) — exactly the kind of
    # silent fallback ``feedback_no_silent_defaults`` calls out.
    'template_runner_use_postgres_checkpointer': 'true',
    # Pipeline progress streaming (#361): where per-node on_event progress lands.
    # 'discord' keeps the existing Discord progress feed (on_event no-op, no double-post);
    # 'telegram' edit-streams a single message in place; 'off' silences on_event.
    'pipeline_streaming_channel': 'discord',
    'pipeline_streaming_min_edit_interval_s': '5',

    # ----- Security / auth -----
    'max_approval_queue': '100',
    'oauth_issuer_url': '',

    # ----- Logging -----
    'max_log_backup_count': '3',
    'max_log_size_mb': '5',

    # ----- Brain daemon -----
    'brain_anomaly_baseline_window_days': '30',
    'brain_anomaly_current_window_hours': '24',
    'brain_digest_window_hours': '6',
    # Cycle watchdog ceiling (seconds): a stuck `await` inside run_cycle (a DB
    # query on a wedged Docker host-port proxy) once parked the daemon for ~37
    # min (2026-06-29). run_cycle is now wrapped in asyncio.wait_for(this); a
    # cycle that exceeds it is cancelled, counted as a failure, and retried next
    # cycle so the daemon never silently hangs. Generously above the normal
    # <2-min cycle, below the 300s cycle interval. (The per-query asyncpg
    # command_timeout is a sibling guard set via the BRAIN_DB_COMMAND_TIMEOUT_SECONDS
    # env var, NOT here — the pool is built before settings load.)
    'brain_cycle_timeout_seconds': '240',
    # Independent liveness-heartbeat cadence (seconds). A dedicated brain task
    # refreshes the brain.cycle_heartbeat audit_log row (the Prometheus
    # dead-man's-switch source) on this cadence, so a hung/cancelled cycle can't
    # starve the switch while the loop is alive and recovering. Comfortably under
    # the switch's 900s stale threshold. Read once at daemon startup. (2026-06-29)
    'brain_heartbeat_interval_seconds': '60',
    # Seconds the brain's event loop may stall before faulthandler dumps every
    # thread's traceback to stderr (0 disables). Deepest hang backstop: a sync
    # C-level freeze parks the single thread so the asyncio cycle-watchdog can't
    # fire — only faulthandler's own thread can then dump the stuck frame.
    # Re-armed each heartbeat tick, so only a genuine freeze trips it; set above
    # brain_cycle_timeout_seconds. Mirrors worker_hang_dump_seconds. (2026-06-29)
    'brain_hang_dump_seconds': '300',

    # ----- Migration-drift in-flight guard (brain/migration_drift_probe.py, #228) -----
    # When true, the migration-drift auto-recover path defers the worker
    # restart while a content task is mid-generation (pipeline_tasks.status
    # = 'in_progress'). A restart mid-run orphans a multi-minute
    # canonical_blog task in 'in_progress' (the claim path never re-picks
    # it) until the 180-min stale sweep. Deferring lets the in-flight job
    # finish before applying pending migrations.
    'migration_drift_defer_while_inflight': 'true',
    # Safety cap on consecutive defers (≈ one per 5-min brain cycle, so 6
    # ≈ 30 min). Once reached the probe STOPS deferring and falls through
    # to the normal restart — pending migrations matter too, and a wedged
    # 'in_progress' row shouldn't block recovery forever.
    'migration_drift_max_inflight_defers': '6',
    # Auto-sync knobs (#228). Ships dark (auto_sync_enabled=false) until the
    # operator wires up the dedicated deploy checkout + bind-mount. When on, the
    # probe resyncs the checkout (git reset --hard origin/main + clean -fd)
    # before restarting, with exponential backoff across recover_max_attempts.
    'migration_drift_auto_sync_enabled': 'false',
    'migration_drift_deploy_checkout_path': '/host-deploy',
    'migration_drift_recover_max_attempts': '3',

    # ----- Cadence SLO probe (brain probe_cadence_slo, issue #525) -----
    # Compares ACTUAL publish output against this CONFIGURED target so a
    # cadence slowdown is caught within hours (existing publish_rate /
    # pipeline_throughput probes are too coarse — 3-day / 7-day windows).
    # NOT derived from prefect_content_flow_cron (that's the flow tick rate,
    # not the production target).
    'cadence_slo_enabled': 'true',
    'cadence_slo_expected_posts_per_day': '1',
    'cadence_slo_window_hours': '24',
    'cadence_slo_shortfall_ratio': '0.5',

    # ----- Scheduler job-failure escalation (#302 / alert audit) -----
    # When a scheduled job returns ok=False or raises, the scheduler emits a
    # finding (most jobs) or directly notifies the operator (alert-delivery
    # jobs). Master switch; default on so failures are never silently swallowed.
    'scheduler_alert_on_job_failure': 'true',
    # Consecutive failed ticks before an alert-delivery ("circular-safe") job
    # fires its direct critical page ("exhaust before paging", #831). A lone
    # failure on these jobs is usually a transient deploy race that the next
    # tick clears (e.g. render_alertmanager_config's single-file bind mount
    # briefly orphaned by an inode-replacing `git reset`); the last-good config
    # stays live meanwhile. 2 = ride out one transient tick, still page on a
    # genuinely persistent failure ~one interval later. 1 restores immediate
    # paging. Only affects the three jobs in scheduler._CIRCULAR_SAFE_JOBS.
    'scheduler_circular_job_page_threshold': '2',

    # ----- Scheduler job-metrics sink (Glad-Labs/poindexter#853) -----
    # Persist every metrics-emitting job fire as an audit_log 'job_run' row
    # (source=job_name, severity=info, details={ok, changes_made, duration_ms,
    # metrics}) so a Grafana panel can read a job's custom JobResult.metrics via
    # the Postgres datasource. Metric-less fires are skipped (job_run_state +
    # failure escalation already cover them). Master switch; default on. Turn
    # off to cut audit_log volume if the 'job_run' rows ever grow too large
    # (they inherit audit_log's existing summarize_to_table retention).
    'scheduler_job_metrics_capture_enabled': 'true',

    # ----- Findings daily digest (job findings_daily_digest, #549) -----
    # Once-a-day Discord rollup of audit_log findings (by kind + delivery
    # policy + pending-delivery backlog). Routine, so Discord, never Telegram.
    # The schedule is tuned like every other job via the
    # plugin.job.findings_daily_digest config.schedule override (default
    # 0 9 * * * = 09:00 local).
    'findings_daily_digest_enabled': 'true',
    'findings_daily_digest_lookback_hours': '24',
    'findings_daily_digest_top_n': '5',

    # ----- Findings per-kind delivery policy (#461) -----
    # Per-kind policy for findings.<kind>.{delivery,fallback,cooldown_minutes,
    # min_severity}. Intended to drive per-kind suppression on the EXISTING
    # findings_alert_router (services/jobs/findings_alert_router.py) — e.g.
    # media_drift -> log_only so it doesn't page. The parallel brain
    # findings_dispatcher that originally read these was reverted as a
    # duplicate of findings_alert_router; these settings are kept for the
    # router enhancement. delivery in {auto_fix, discord, telegram,
    # github_issue, log_only}; findings.default is the unknown-kind catch-all.
    'findings.default.delivery': 'log_only',
    'findings.default.fallback': 'log_only',
    'findings.default.cooldown_minutes': '1440',
    'findings.default.min_severity': 'warn',
    'findings.anomaly.delivery': 'telegram',
    'findings.anomaly.fallback': 'discord',
    'findings.anomaly.cooldown_minutes': '60',
    'findings.anomaly.min_severity': 'critical',
    # db_clock_skew: a wrong DB wall-clock corrupts every timestamp and all
    # real-time correlation — page it like anomaly (telegram/critical).
    'findings.db_clock_skew.delivery': 'telegram',
    'findings.db_clock_skew.fallback': 'discord',
    'findings.db_clock_skew.cooldown_minutes': '60',
    'findings.db_clock_skew.min_severity': 'critical',
    'findings.quality_regression.delivery': 'github_issue',
    'findings.quality_regression.fallback': 'discord',
    'findings.quality_regression.cooldown_minutes': '1440',
    'findings.quality_regression.min_severity': 'warn',
    'findings.broken_link.delivery': 'discord',
    'findings.broken_link.fallback': 'log_only',
    'findings.broken_link.cooldown_minutes': '360',
    'findings.broken_link.min_severity': 'warn',
    'findings.broken_external_link.delivery': 'auto_fix',
    'findings.broken_external_link.fallback': 'discord',
    'findings.broken_external_link.cooldown_minutes': '60',
    'findings.broken_external_link.min_severity': 'warn',
    'findings.broken_internal_link.delivery': 'auto_fix',
    'findings.broken_internal_link.fallback': 'discord',
    'findings.broken_internal_link.cooldown_minutes': '60',
    'findings.broken_internal_link.min_severity': 'warn',
    'findings.missing_seo.delivery': 'auto_fix',
    'findings.missing_seo.fallback': 'github_issue',
    'findings.missing_seo.cooldown_minutes': '1440',
    'findings.missing_seo.min_severity': 'warn',
    'findings.topic_gap.delivery': 'discord',
    'findings.topic_gap.fallback': 'log_only',
    'findings.topic_gap.cooldown_minutes': '1440',
    # Stale-batch reaper (reap_stale_topic_batches). A wedged open batch =
    # niche content-dark, so route the alert to the ops channel. severity is
    # 'warn' when the batch still wedges the niche (pages) and 'info' when the
    # reaper already auto-expired it (dropped by the router floor — no page);
    # 12h cooldown keeps a persistent wedge to twice-daily, not hourly.
    'findings.topic_batch_stuck.delivery': 'discord',
    'findings.topic_batch_stuck.fallback': 'log_only',
    'findings.topic_batch_stuck.cooldown_minutes': '720',
    'findings.topic_batch_stuck.min_severity': 'warn',
    # Published RSS feed drifted from the DB and was republished by
    # media_feed_reconciliation. The feed is already fixed by the time this
    # fires, so it's routine Discord traffic (feedback_telegram_vs_discord) —
    # but it still means an upstream event-coupled rebuild was dropped, which
    # is worth seeing. 6h cooldown: a recurring drift is the signal, not each
    # individual cycle.
    'findings.media_feed_drift.delivery': 'discord',
    'findings.media_feed_drift.fallback': 'log_only',
    'findings.media_feed_drift.cooldown_minutes': '360',
    'findings.media_feed_drift.min_severity': 'warn',
    # The reconciler REFUSED to publish because the render collapsed. This is
    # not routine: the feed route returns a valid EMPTY feed when its DB query
    # fails, so a collapse means the renderer or the database is broken and a
    # public surface is one unguarded write away from being wiped. Telegram —
    # it needs a human, and the guard only holds the line, it doesn't fix it.
    'findings.media_feed_render_collapse.delivery': 'telegram',
    'findings.media_feed_render_collapse.fallback': 'discord',
    'findings.media_feed_render_collapse.cooldown_minutes': '120',
    'findings.media_feed_render_collapse.min_severity': 'error',
    # An image did not come from image-gen: it fell back to stock, or the post
    # shipped without that image. Routed (not log_only) ON PURPOSE — this ran
    # silently for weeks precisely because the fallback logged per-image and
    # still reported success, so a downgraded run was indistinguishable from a
    # clean one. Discord, not Telegram: it degrades a post, it does not break
    # the pipeline (feedback_telegram_vs_discord). 60m cooldown because a
    # single image-gen outage downgrades every image in the batch, and the
    # outage is the signal, not each image.
    'findings.image_gen_downgrade.delivery': 'discord',
    'findings.image_gen_downgrade.fallback': 'log_only',
    'findings.image_gen_downgrade.cooldown_minutes': '60',
    'findings.image_gen_downgrade.min_severity': 'warn',
    # A VRAM reclaim ran, freed nothing, and the media render stayed blocked.
    # Routed because the reclaim is not free — it restarts image-gen, and each
    # restart opens a window where article images downgrade. Repeats mean
    # something outside the pipeline is holding render-GPU VRAM. The cooldown
    # setting bounds the retries; this bounds the noise.
    # A QA rail skipped because GPU admission refused its wait (#914 P2). This
    # is the design working, not a fault, so it stays log_only — routing it
    # would page on ordinary load. It is a SEPARATE kind from qa_rail_degraded
    # on purpose: both produce sentinel scores, so without the split a burst of
    # render pressure reads as the QA stack breaking. Query it to decide
    # whether gpu_sched_qa_rail_max_wait_s is too tight.
    'findings.qa_rail_gpu_busy_skip.delivery': 'log_only',
    'findings.qa_rail_gpu_busy_skip.cooldown_minutes': '60',
    'findings.qa_rail_gpu_busy_skip.min_severity': 'info',
    # A media stage skipped because GPU admission refused its wait (#914 P2
    # group 2). Same reasoning as the QA-rail kind above — the design working,
    # not a fault — so log_only. Kept separate from qa_rail_gpu_busy_skip
    # because the remedies differ: a QA skip means render pressure is crowding
    # out review, while a media skip means the post shipped without a podcast
    # script or shot list. Query it to decide whether gpu_sched_media_max_wait_s
    # is too tight, or whether media is simply scheduled against renders.
    'findings.media_gpu_busy_skip.delivery': 'log_only',
    'findings.media_gpu_busy_skip.cooldown_minutes': '60',
    'findings.media_gpu_busy_skip.min_severity': 'info',
    'findings.vram_reclaim_ineffective.delivery': 'discord',
    'findings.vram_reclaim_ineffective.fallback': 'log_only',
    'findings.vram_reclaim_ineffective.cooldown_minutes': '180',
    'findings.vram_reclaim_ineffective.min_severity': 'warn',
    # Topic-sanity gate (2026-06-30 dots-topic incident) — a tap/RAG source
    # emitting contentless titles is a source bug worth seeing on the routine
    # ops channel, not a page; 6h cooldown keeps a persistently garbage
    # source to a few notes a day. All gate seams emit at severity='warn' so
    # the router severity floor keeps them routable.
    'findings.topic_sanity_rejected.delivery': 'discord',
    'findings.topic_sanity_rejected.fallback': 'log_only',
    'findings.topic_sanity_rejected.cooldown_minutes': '360',
    'findings.topic_sanity_rejected.min_severity': 'warn',
    # Self-reference gate (2026-07-27 batch 6322bd8b) — a source returning the
    # operator's own site means its queries are pointed at the brand rather
    # than at subject matter. Same routing rationale as topic_sanity_rejected:
    # a source-config bug worth seeing on the routine ops channel, not a page.
    'findings.topic_self_referential.delivery': 'discord',
    'findings.topic_self_referential.fallback': 'log_only',
    'findings.topic_self_referential.cooldown_minutes': '360',
    'findings.topic_self_referential.min_severity': 'warn',
    # Ranking degrade (#926) — when the LLM final-score response is unusable,
    # the batch is ranked by raw embedding cosine instead of by judgement. The
    # batch still forms and looks normal, so this needs a channel or it stays
    # invisible (it ran at ~50% undetected for weeks). 6h cooldown matches the
    # other topic findings: a persistently-degrading model is a few notes a day.
    'findings.topic_rank_degraded.delivery': 'discord',
    'findings.topic_rank_degraded.fallback': 'log_only',
    'findings.topic_rank_degraded.cooldown_minutes': '360',
    'findings.topic_rank_degraded.min_severity': 'warn',
    'findings.media_drift.delivery': 'log_only',
    'findings.r2_static_drift.delivery': 'discord',
    'findings.r2_static_drift.fallback': 'log_only',
    'findings.r2_static_drift.cooldown_minutes': '360',
    'findings.r2_static_drift.min_severity': 'warn',
    'findings.post_verification_failure.delivery': 'discord',
    'findings.post_verification_failure.fallback': 'log_only',
    'findings.post_verification_failure.cooldown_minutes': '360',
    'findings.post_verification_failure.min_severity': 'warn',
    'findings.duplicate_post.delivery': 'log_only',
    'findings.stock_image_regenerated.delivery': 'log_only',
    # image-gen server unreachable from the worker → the post fell back to a
    # Pexels stock photo instead of a unique generated image. Routine infra
    # degradation → Discord (per feedback_telegram_vs_discord); the emit site
    # sets a static dedup_key so an outage window pages once, not per-post.
    'findings.image_gen_unreachable.delivery': 'discord',
    'findings.uncategorized_post_autofixed.delivery': 'log_only',
    'findings.broken_external_link_autofixed.delivery': 'log_only',
    'findings.broken_internal_link_autofixed.delivery': 'log_only',
    'findings.cloud_sync_returned_false.delivery': 'discord',
    'findings.cloud_sync_returned_false.fallback': 'log_only',
    'findings.cloud_sync_returned_false.cooldown_minutes': '360',
    'findings.cloud_sync_returned_false.min_severity': 'warn',
    # SEO Harvest Loop (#763) — routine operator notifications, NOT pages.
    # enqueue_seo_refreshes emits seo_refresh_queued when N refresh tasks are
    # parked at seo_refresh_gate awaiting per-post sign-off; measure_seo_refresh
    # _outcomes emits seo_refresh_outcome when post-refresh GSC deltas land.
    # Both emit at severity='warn' so the router fetches them (it filters out
    # 'info'); delivery='discord' pins the Glad Labs ops channel per
    # feedback_telegram_vs_discord (Telegram=critical, Discord=routine).
    'findings.seo_refresh_queued.delivery': 'discord',
    'findings.seo_refresh_queued.fallback': 'log_only',
    'findings.seo_refresh_queued.cooldown_minutes': '360',
    'findings.seo_refresh_queued.min_severity': 'warn',
    'findings.seo_refresh_outcome.delivery': 'discord',
    'findings.seo_refresh_outcome.fallback': 'log_only',
    'findings.seo_refresh_outcome.cooldown_minutes': '1440',
    'findings.seo_refresh_outcome.min_severity': 'warn',
    # expire_stale_seo_refresh_gates emits seo_refresh_gate_expired when
    # unreviewed gate-parked runs age out (seo.refresh.gate_max_parked_days)
    # and are dismissed with their opportunities reopened. Routine hygiene →
    # Discord, same posture as the two kinds above.
    'findings.seo_refresh_gate_expired.delivery': 'discord',
    'findings.seo_refresh_gate_expired.fallback': 'log_only',
    'findings.seo_refresh_gate_expired.cooldown_minutes': '1440',
    'findings.seo_refresh_gate_expired.min_severity': 'warn',
    # atoms.approval_gate emits approval_gate_graduated when a gate's Lock-2
    # graduation fires (trailing clean human approvals >= the gate's
    # graduation_setting) and a run auto-approves instead of pausing. A
    # trust-posture change Matt should see, but routine-severity → Discord;
    # dedup_key is per-gate so steady-state graduated passes ping at most
    # once per cooldown window.
    'findings.approval_gate_graduated.delivery': 'discord',
    'findings.approval_gate_graduated.fallback': 'log_only',
    'findings.approval_gate_graduated.cooldown_minutes': '1440',
    'findings.approval_gate_graduated.min_severity': 'warn',
    # Settings-lifecycle orphan candidates (#756). ProbeZeroReaderSettingsJob
    # emits settings_zero_reader_keys at severity='warn' with a stable dedup_key;
    # delivery='discord' pins the routine ops channel (feedback_telegram_vs_discord).
    # NOTE: the kind MUST stay dot-free — findings_alert_router parses
    # findings.<kind>.<field> as exactly 3 dot-segments, so a dotted kind would
    # make the policy a 4-segment key the loader silently skips.
    'findings.settings_zero_reader_keys.delivery': 'discord',
    'findings.settings_zero_reader_keys.fallback': 'log_only',
    'findings.settings_zero_reader_keys.cooldown_minutes': '1440',
    'findings.settings_zero_reader_keys.min_severity': 'warn',
    # Disabled-capabilities visibility (glad-labs-stack#2133). Emitted at
    # severity='warn' — NOT 'info', even though "off" is often correct —
    # because findings_alert_router's SQL layer only ever fetches
    # severity IN ('warn','warning','critical') before min_severity is even
    # consulted; an info-severity finding can never route regardless of this
    # policy (findings-delivery-needs-warn-severity memory, glad-labs-stack#1471
    # precedent: same mistake previously bit topic_gap). cooldown_minutes is
    # a week (10080), longer than the zero-reader precedent's 24h: this
    # reports slow-changing operator-toggle state, not an actionable list
    # that shrinks as items get fixed, so a daily repeat would just be
    # nagging about unchanged state.
    'findings.disabled_capabilities.delivery': 'discord',
    'findings.disabled_capabilities.fallback': 'log_only',
    'findings.disabled_capabilities.cooldown_minutes': '10080',
    'findings.disabled_capabilities.min_severity': 'warn',
    # Prompt-catalog drift. SyncPromptCatalogToLangfuseJob emits
    # prompt_catalog_drift (dot-free kind — same 3-segment parser note as
    # above) at severity='warn' for orphaned Langfuse names (key gone from the
    # SKILL.md catalog) and for hand-edited mirror versions it replaced. The
    # 1440-minute cooldown re-pages at most once per drift-day.
    'findings.prompt_catalog_drift.delivery': 'discord',
    'findings.prompt_catalog_drift.fallback': 'log_only',
    'findings.prompt_catalog_drift.cooldown_minutes': '1440',
    'findings.prompt_catalog_drift.min_severity': 'warn',
    # Stale-sweep + GPU-lock visibility (poindexter#807). NOTE: kinds without
    # a policy already route loud — findings_alert_router deliberately ignores
    # findings.default.* ("a kind with NO policy => 'route'") — so these seeds
    # are NOT needed for delivery. They exist to pin explicit COOLDOWNS:
    # stale_task_reclaimed uses a short one so a task looping through repeated
    # sweeps re-pings each cycle (the loop IS the signal); the other two are
    # one-shot-per-task/owner events that shouldn't re-page for an hour/day.
    'findings.stale_task_reclaimed.delivery': 'discord',
    'findings.stale_task_reclaimed.fallback': 'log_only',
    'findings.stale_task_reclaimed.cooldown_minutes': '60',
    'findings.stale_task_reclaimed.min_severity': 'warn',
    'findings.task_retries_exhausted.delivery': 'discord',
    'findings.task_retries_exhausted.fallback': 'log_only',
    'findings.task_retries_exhausted.cooldown_minutes': '1440',
    'findings.task_retries_exhausted.min_severity': 'warn',
    'findings.gpu_lock_timeout.delivery': 'discord',
    'findings.gpu_lock_timeout.fallback': 'log_only',
    'findings.gpu_lock_timeout.cooldown_minutes': '60',
    'findings.gpu_lock_timeout.min_severity': 'warn',
    # A stage with halts_on_failure=False that timed out or raised — the graph
    # continued and silently discarded everything that stage produced. Routed
    # explicitly because findings.default.delivery is log_only, and log_only is
    # exactly how this class of failure stayed invisible: the pre-existing
    # signal was one routine progress ping in the Discord spam stream while
    # atom_runs recorded the node as clean (RCA 2026-07-31, a hero image that
    # had already rendered AND uploaded was dropped 2s before it was returned).
    # Discord, not Telegram: it degrades a post, it does not break the pipeline.
    # 60m cooldown because the cause is usually one contention window that hits
    # a whole batch — the window is the signal, not each post.
    'findings.stage_failure_swallowed.delivery': 'discord',
    'findings.stage_failure_swallowed.fallback': 'log_only',
    'findings.stage_failure_swallowed.cooldown_minutes': '60',
    'findings.stage_failure_swallowed.min_severity': 'warn',
    # Langfuse configured-but-unusable (poindexter#815) — the prompt surface
    # silently downgraded to YAML for weeks; once-a-day Discord ping until
    # the operator fixes the preload/credentials. (Not-configured stays a
    # quiet info log — the OSS default path never pages.)
    'findings.langfuse_prompts_unavailable.delivery': 'discord',
    'findings.langfuse_prompts_unavailable.fallback': 'log_only',
    'findings.langfuse_prompts_unavailable.cooldown_minutes': '1440',
    'findings.langfuse_prompts_unavailable.min_severity': 'warn',
    # Vision scorer no-op'd: qa.vision passed open ("could not assess N inline
    # image(s)") or a shot-list render accepted shots it couldn't score. The
    # gate stays fail-open by design — this finding makes the no-op VISIBLE
    # (the image-relevance leg was silently dark for weeks at 100% pass-open).
    # Discord with a cooldown: it's a "fix the vision infra" signal, not
    # per-post noise.
    'findings.vision_scorer_unavailable.delivery': 'discord',
    'findings.vision_scorer_unavailable.fallback': 'log_only',
    'findings.vision_scorer_unavailable.cooldown_minutes': '360',
    'findings.vision_scorer_unavailable.min_severity': 'warn',

    # ----- GPU eviction-credit staleness tolerance (poindexter#914) -----
    # Per-card VRAM that nvidia_gpu_process_memory_mib may leave unattributed
    # before GPURegistry.evictable_ollama_gb treats its process list as stale
    # and returns None (unknown) instead of 0.0 (nothing evictable). Driver /
    # context overhead is never charged to a PID so a small gap is normal
    # (~0.3GB observed); a multi-GB gap means a loaded model has not been
    # scraped yet — the exporter lags ~40s (10s refresh + 30s scrape) and a
    # false 0.0 there rejects admission, degrading a QA rail that then passes
    # OPEN. Sized under the smallest fleet model (~3.5GB), above real overhead.
    'gpu_evictable_unattributed_tolerance_gb': '2.0',
    # ----- Cost-guard local downgrade (instead of hard-failing) -----
    # When the paid daily/monthly cap is reached, dispatch_complete swaps the
    # PAID model for a local one and warns, rather than propagating
    # CostGuardExhausted. Without this a spend ceiling becomes a content outage:
    # the prod writer is pinned to a cloud model, so an exhausted budget failed
    # the whole pipeline instead of producing a cheaper article. Set false to
    # restore hard-fail (surface the error to the operator).
    'cost_guard_local_fallback_enabled': 'true',
    # Model used for that downgrade. Empty -> pipeline_local_writer_model (the
    # existing "guaranteed local, writer-grade" pin). A value here MUST be local;
    # a paid one is refused at dispatch and the original error propagates,
    # because silently retrying against a second paid provider is exactly what
    # CostGuardExhausted's contract forbids.
    'cost_guard_local_fallback_model': '',
    # ----- Pinned-endpoint warm-up (stack#2051 / #2938) -----
    # WarmPinnedLlmEndpointsJob loads each model that
    # plugin.llm_provider.litellm.config.model_api_base_overrides pins to its own
    # GPU-bound Ollama. OLLAMA_KEEP_ALIVE=-1 never evicts but also never LOADS,
    # so without this a restart leaves the pinned GPU empty until the first rail
    # call cold-loads mid-pipeline and the rail passes open. No-ops on installs
    # with no override map configured (the OSS single-endpoint path).
    'warm_pinned_llm_endpoints_enabled': 'true',
    # ----- Settings read-telemetry + orphan probe (#756 items 2-3) -----
    # SiteConfig.get records read keys in-memory; FlushSettingsReadTelemetryJob
    # stamps app_settings.last_read_at each minute; ProbeZeroReaderSettingsJob
    # surfaces keys never read past the grace window as orphan candidates.
    'settings_read_telemetry_enabled': 'true',
    # Re-stamp a hot key at most once per this many seconds (write-amp guard):
    # the per-minute UPDATE only touches rows whose last_read_at is NULL or older
    # than this, so a constantly-read key is written ~1×/hour, not 60×.
    'settings_read_telemetry_min_restamp_seconds': '3600',
    'settings_zero_reader_probe_enabled': 'true',
    # ProbeDisabledCapabilitiesJob (#2133): daily check of a curated list of
    # opt-in capability flags (writer self-review, self-consistency QA,
    # video/podcast/newsletter/social) that ship disabled.
    'disabled_capabilities_probe_enabled': 'true',
    # Langfuse prompt mirror (SyncPromptCatalogToLangfuseJob): pushes SKILL.md
    # catalog defaults into Langfuse every 6h so the UI shows all production
    # prompts for review. No-ops quietly when Langfuse isn't configured, so
    # 'true' is safe on OSS. (Replaces prompt_catalog_drift_probe_enabled —
    # dropped by migration 20260704.)
    'langfuse_prompt_mirror_enabled': 'true',
    # Legacy Langfuse-first prompt override lookup in prompt_manager. OFF by
    # default: SKILL.md packs are authoritative and Langfuse is a read-only
    # mirror (a bulk import once shadowed every SKILL.md edit — the masking
    # trap). Enable only for deliberate live prompt experiments.
    'langfuse_prompt_overrides_enabled': 'false',
    # A key is an orphan candidate only after it has existed (created_at) this
    # many days with last_read_at still NULL — gives newly-seeded keys time to be
    # read before they can be flagged, and self-suppresses on fresh installs.
    'settings_zero_reader_grace_days': '30',
    # Cap the per-finding key list so a big backlog doesn't blow up the Discord
    # embed / audit_log row.
    'settings_zero_reader_max_report': '50',

    # ----- Findings issue labels (content-derived from kind; cite-or-surface) -----
    # Comma-separated labels stamped on the GitHub issue a github_issue-delivery
    # finding opens. Derived from the finding KIND (its content), not a default.
    # Priority/milestone are deliberately omitted — those are the weekly sweep's
    # surfaced judgment axes, never auto-stamped here.
    'findings.quality_regression.labels': 'bug,pipeline',
    'findings.missing_seo.labels': 'bug,pipeline',

    # ----- Prefect stuck-flow queue-backlog detection (#526) -----
    # Distinct from the stuck-run thresholds (seeded in 0000_baseline):
    # page with probe.prefect_queue_backlog_detected when more than this
    # many SCHEDULED runs are overdue (scheduled start in the past) — the
    # backlog symptom of a held concurrency=1 slot.
    'prefect_stuck_flow_queue_depth_threshold': '3',

    # ----- Prefect stuck-flow stale-SCHEDULED reaping (2026-07-14) -----
    # Nothing else ever clears a SCHEDULED run once its scheduled time
    # passes, so overdue_scheduled_count only ratchets upward across
    # incidents (574 backlog pages since 2026-05-31, a 3-day-stale entry
    # still present at investigation time). Force-cancels SCHEDULED runs
    # overdue past this many minutes each probe cycle — cleanup, not a
    # page. Default comfortably exceeds every other threshold in this
    # probe (longest is the 30m flat RUNNING-age check) and the observed
    # healthy backlog self-drain time (~40m).
    'prefect_stuck_flow_queue_reap_minutes': '60',

    # ----- Prefect stuck-flow progress-aware detection -----
    # Minutes a RUNNING content_generation run may go with NO graph-node
    # progress (pipeline_tasks.last_progress_at) before the probe treats it as
    # stuck. Replaces the flat RUNNING-age threshold when a heartbeat exists:
    # a legit media-heavy run that keeps advancing nodes is never crashed, and
    # the same signal suppresses the queue-backlog page while the slot-holder
    # is progressing. Default 20m sits above the longest observed single-node
    # gap (~13m); tune DOWN as confidence builds. NULL heartbeat (pre-migration
    # / write never landed) falls back to prefect_stuck_flow_threshold_minutes.
    'prefect_stuck_flow_progress_stall_minutes': '20',

    # ----- Prefect stuck-flow CANCELLING coverage (2026-07-07 concurrency wedge) -----
    # Minutes a run may sit in CANCELLING before the probe force-CANCELLED it to
    # free the concurrency slot. A run enters CANCELLING when a cancel is
    # requested; the WORKER completes CANCELLING -> CANCELLED after killing the
    # process. If the worker/process is already dead (host/WSL/power event) nobody
    # completes it and the run hangs holding the content-pool concurrency=1 slot
    # forever — the 2026-07-07 wedge (qualified-corgi 62m, inscrutable-gharial
    # 28m; ~73 runs piled up SCHEDULED behind them). A genuine process-worker
    # cancel completes in seconds; 10m of no-terminalize means the holder is dead
    # and the cancel must be forced through. Gated by the same
    # prefect_stuck_flow_auto_crash master switch as the RUNNING/PENDING crash
    # path. Tune DOWN for tighter detection. See project_prefect_concurrency_zombie_stall.
    'prefect_stuck_flow_cancelling_threshold_minutes': '10',

    # ----- Operator-page cooldown (2026-07-01 alert-noise audit) -----
    # Repeat-suppression window for the brain's direct notify_operator()
    # pages: repeats of the same dedup key inside the window skip the
    # external Telegram/Discord sends (critical severity always bypasses;
    # alerts.log keeps the full history). Before this gate three probes
    # re-paged the SAME chronic condition every 5-min cycle — 465 of the
    # 504 operator_paged events in one week. 0 disables the gate.
    'operator_page_cooldown_minutes': '30',

    # ----- Data-feed freshness dead-man's switch (2026-07-01 audit) -----
    # brain/data_freshness_probe.py watches each feed's newest row and emits
    # an edge-triggered `data_feed_stale` finding (warning → Discord via the
    # findings router) when it exceeds threshold_minutes — so a dead producer
    # can't leave dashboards silently serving stale data. table/column are
    # validated as SQL identifiers; a feed with zero rows is not assessed.
    # The corsair_csv sensor feed (iCUE PSU wall-power, #868) was watched here
    # as a filtered feed until 2026-07-28. The iCUE CSV sampler no longer
    # exists — it was the Windows-era path, and on Linux the same HX1500i is
    # read natively by node_exporter's corsairpsu hwmon, so PSU wall-power is
    # still covered via Prometheus (node_hwmon_power_watt{chip=~".*1b1c.*"} +
    # the Shelly psu_total_power_watts) rather than by this probe. Keep the
    # entry out: a feed whose producer is retired can only ever report stale.
    'data_freshness_probe_enabled': 'true',
    'data_freshness_feeds': (
        '[{"name": "cost_logs", "table": "cost_logs", "column": "created_at",'
        ' "threshold_minutes": 180},'
        ' {"name": "gpu_metrics", "table": "gpu_metrics", "column": "timestamp",'
        ' "threshold_minutes": 30},'
        ' {"name": "atom_runs", "table": "atom_runs", "column": "created_at",'
        ' "threshold_minutes": 720},'
        ' {"name": "page_views", "table": "page_views", "column": "created_at",'
        ' "threshold_minutes": 2880}]'
    ),

    # ----- DB wall-clock skew probe (2026-07-08 investigation) -----
    # brain/clock_skew_probe.py compares postgres clock_timestamp() to an
    # external UTC reference (the HTTP Date header from clock_skew_reference_url)
    # and emits an edge-triggered db_clock_skew finding (critical -> Telegram)
    # when |skew| exceeds the threshold. Catches transient WSL2 CLOCK_REALTIME
    # excursions on host sleep/resume that silently poison every DB timestamp.
    # External-absolute (NOT the probe's own clock — it shares the same VM clock
    # and would be blind to a VM-wide jump); degrades to 'unknown' with no page
    # when the reference is unreachable, so it never false-pages on a normal
    # resume. See docs/operations/db-clock-skew.md.
    'clock_skew_probe_enabled': 'true',
    'clock_skew_reference_url': 'https://www.cloudflare.com',
    'clock_skew_threshold_seconds': '120',
    'clock_skew_severity': 'critical',
    'clock_skew_renotify_minutes': '60',
    'clock_skew_sample_retention_days': '30',

    # ----- Content-flow concurrency cap (Glad-Labs/poindexter#578) -----
    # The native Prefect work-pool concurrency limit caps how many
    # content_generation_flow runs execute simultaneously. Each run loads
    # an LLM + image-gen onto the single 5090, so this is a direct VRAM lever:
    # the 2026-05-31 stress test found 3 concurrent flows sit at a stable
    # ~60% VRAM (healthy headroom) while 5 pin the GPU at ~98% and risk
    # OOM. ``scripts/deploy_content_flow.py`` reads ``concurrency`` and
    # fails loud if it exceeds the ``max`` ceiling (no silent VRAM
    # exhaustion); raise the ceiling only on a bigger GPU.
    'prefect_content_flow_concurrency': '3',
    'content_flow_max_concurrency': '3',
    # Minutes after which a pipeline_tasks row stuck in status='in_progress'
    # is treated as orphaned (killed flow / OOM / container restart mid-graph).
    # The reclaim step in content_generation_flow resets orphaned rows to
    # 'pending' (or 'failed' if retry_count >= max_retries) and clears the
    # poisoned LangGraph checkpoint so the retry runs a fresh graph.
    # 30 min = ~15 missed 2-min Prefect polling cycles — clearly orphaned.
    'content_flow_stale_inprogress_minutes': '30',

    # ----- Title originality / SEO -----
    'google_sitemap_ping_url': 'https://www.google.com/ping',
    'indexnow_key': '',
    'indexnow_ping_url': 'https://api.indexnow.org/indexnow',
    'title_originality_cache_ttl_hours': '24',
    'title_originality_external_check_enabled': 'true',
    'title_originality_external_penalty': '-50',

    # ----- Auto-publish gate (dev_diary) -----
    # dev_diary is GRADUATED to live auto-publish (2026-07-04, #2128): the
    # operator is confident enough in the daily dev entries. These defaults now
    # match 0000_baseline.seeds.sql so the armed state is intentional, not an
    # ordering artifact of two seed sources disagreeing (settings_defaults said
    # dry_run=true/threshold=-1 while the baseline seed said false/70). The
    # GLOBAL gate ships OFF (auto_publish_threshold=0, require_human_approval=
    # true); only dev_diary is armed.
    'dev_diary_auto_publish_dry_run': 'false',
    'dev_diary_auto_publish_max_edit_distance': '50',
    'dev_diary_auto_publish_min_clean_runs': '3',
    'dev_diary_auto_publish_threshold': '70',

    # ----- Dev-diary substance bar ("is today worth a diary?") -----
    # The daily job used to skip only when the day was LITERALLY empty, which
    # never fired: release-please, dependabot, and the nightly docs sweep all
    # merge PRs on an otherwise dead day. Measured over 2026-07-08..07-31,
    # four days (07-21/22/25/27) were 100% bot + bookkeeping churn and each
    # still produced a diary about nothing.
    #
    # The gate now scores SUBSTANCE: bot-authored PRs and bookkeeping
    # conventional-commit types don't count, untyped titles DO (real work
    # routinely ships untyped -- see services/topic_sources/dev_diary_source.py
    # SubstancePolicy for the sample that settled that). min_score=1 means
    # "at least one substantive human PR"; on the measured window that skipped
    # exactly the four dead days and wrote on all twenty real ones.
    'dev_diary_min_substance_score': '1',
    'dev_diary_substance_weight_pr': '1.0',
    'dev_diary_substance_weight_commit': '1.0',
    # Posts/audit score 0 by design: the pipeline publishing a post is the
    # machine's routine output, not the operator shipping -- and 2 of the 4
    # thin days DID publish one, so counting them would leave half the problem
    # in place. Both stay in the writer's context bundle regardless. Raise
    # above 0 only if a publish-only day should earn its own diary entry.
    'dev_diary_substance_weight_post': '0',
    'dev_diary_substance_weight_audit': '0',
    # CSV. Blank falls back to the code defaults (app_settings.value is NOT
    # NULL with '' as the unset sentinel), so blanking these RESTORES the
    # filter rather than disabling it.
    'dev_diary_bookkeeping_types': 'chore,docs,ci,test,style,build,deps,revert',
    'dev_diary_bot_author_patterns': 'app/*,*[bot],dependabot*,*-bot',
    # Niche allowlist publish gate (#729). When 'true', publish_service
    # refuses to publish a task whose niche_slug is not a KNOWN niche
    # (no matching ``niches`` row) or is missing -- the manual-approve
    # backstop against orphan/garbage niches reaching readers
    # (auto_publish_gate already blocks the auto path). A known but
    # discovery-inactive niche (e.g. dev_diary -- website-post only, kept
    # out of the topic sweep + media backfill) is still publishable;
    # ``niches.active`` gates discovery/media, not publishability. Set
    # 'false' to disable the gate entirely.
    'enforce_niche_allowlist': 'true',

    # ----- Gitea / external integrations -----
    'publish_quiet_hours': '',

    # ----- Trusted source domains -----
    'trusted_source_domains': '',

    # ----- Worker runtime -----
    # How often (seconds) the worker writes its capability_registry
    # heartbeat. The brain's "worker offline" threshold is already
    # DB-tunable; this is the emit cadence that pairs with it. Was a
    # hardcoded constant in services/worker_service.py.
    'worker_heartbeat_interval_seconds': '30',

    # ----- URL scraper (web research / topic-from-URL fetches) -----
    # Per-request fetch timeout (seconds) and the safety cap (chars) on
    # extracted page text. Were module-level constants in
    # services/url_scraper.py. NOTE: the SSRF redirect cap (MAX_REDIRECTS)
    # stays a code constant on purpose — it is a security guard, not a knob.
    'url_scraper_timeout_seconds': '15',
    'url_scraper_max_content_chars': '50000',

    # ----- Tap ingestion (RAG corpus) -----
    # Max characters per chunk when the tap runner splits a document before
    # embedding (services/taps/_chunking.py). Was a module-level constant.
    'tap_chunk_max_chars': '6000',
    # Per-tap wall-clock budget (seconds) in services/taps/runner.py::run_all.
    # The auto-embed sidecar loops with no outer deadline, so this bounds a
    # tap wedged on a stalled Ollama embed / hung query to one tap instead of
    # freezing the whole hourly run. Generous — catches an infinite hang, not a
    # tight SLA (slowest real tap, claude_code_sessions, is ~50s and growing).
    'tap_run_timeout_seconds': '300',

    # Documents the tap runner buffers before a batched chunk-0 dedup pre-fetch
    # (services/taps/runner.py). The dedup hash lookup runs one query per
    # source_table per batch instead of one SELECT per document (#735), so this
    # only bounds peak memory / round-trip granularity — it does not affect
    # dedup output.
    'tap_dedup_batch_size': '256',

    # ----- Misc -----
    'pexels_api_base': 'https://api.pexels.com/v1',

    # ----- Shared httpx.AsyncClient (lifespan-bound, services/http_client.py) -----
    # The whole worker / coordinator process shares ONE httpx.AsyncClient
    # so the connection pool stays warm across 100+ per-task HTTP calls
    # (Ollama / image-gen / Pexels / Discord / Vercel). Per-call timeouts at
    # the request site override these defaults when a specific caller
    # needs aggressive cutoffs (health checks) or generous ones (LLM gen).
    'shared_http_client_timeout_seconds': '30.0',
    'shared_http_client_max_connections': '100',
    'shared_http_client_max_keepalive': '20',

    # ----- MCP HTTP probe recovery (brain/mcp_http_probe.py) -----
    # Empty = HTTP recovery disabled. Set to http://host.docker.internal:9841/recover
    # once the Recovery Agent Task Scheduler task is running on the host.
    'mcp_http_probe_recovery_url': '',
    # Consecutive probe failures required before paging. Default 3 suppresses
    # transient single-shot misses (fast restart, momentary load) while still
    # catching genuine sustained outages (#1301).
    'mcp_http_probe_min_consecutive_failures': '3',

    # ----- Compose-drift host-routed recovery (brain/compose_drift_probe.py) -----
    # Docker Compose project name the brain pins (COMPOSE_PROJECT_NAME) during
    # compose-drift auto-recover, so the recreate joins the stack's real project
    # instead of inferring one from the brain's /app cwd (orphan networks).
    # 'poindexter' matches the documented clone dir; set it to the basename of
    # the host directory that launched the stack if yours differs. Lockstep with
    # the baseline seed; the operator overlay restores the operator's value.
    'compose_project_name': 'poindexter',
    # On drift, a containerised brain can't `docker compose up` Windows binds
    # itself, so it POSTs {"service":"compose-reapply"} to the host Recovery
    # Agent (the same agent as mcp_http_probe_recovery_url above), which runs
    # start-stack.sh on the host. Default ON — auto-heal — bounded by the cap
    # below so a persistent (unfixable) drift escalates to a page instead of
    # storm-reapplying. SEPARATE from compose_drift_auto_recover_enabled (the
    # brain's own compose-up, which stays off on this Windows host because it
    # mangles C:\ binds). On a fresh install with no agent configured this
    # no-ops (falls through to notify-only).
    'compose_drift_host_recover_enabled': 'true',
    # Max start-stack reapplies per rolling window before escalating to a page.
    'compose_drift_host_recover_cap_per_window': '3',
    'compose_drift_host_recover_window_minutes': '60',
    # Compose `profiles:` the operator activates at `docker compose up` (CSV,
    # e.g. "operator,ci-runner"). A service gated behind a profile NOT listed
    # here is opt-in and legitimately not running, so the drift probe suppresses
    # its container_missing (it still diffs the service if it IS running). Empty
    # default = treat every profiled service as inactive — no false pages out of
    # the box. Incident 2026-06-21: gpu-exporter profiles:[linux-gpu] false-paged
    # CRITICAL every cycle on this Windows host, where the host nvidia-smi
    # exporter (not the profile-gated container) serves GPU metrics. List your
    # active profiles to restore crash-detection for their services.
    'compose_drift_active_profiles': '',

    # ----- Scheduled-tasks probe (brain/health_probes.py::probe_scheduled_tasks) -----
    # The containerised brain can't enumerate the host Windows Task Scheduler, so
    # it asks the host Recovery Agent (GET /tasks — shares mcp_http_probe_recovery_url
    # + _token, same agent) for the status of the host Scheduled Tasks named here
    # (CSV), then pages when one is DISABLED, missing, or its last run failed.
    # Empty default = advisory no-op (fail-open) so an operator without the agent —
    # or on a non-Windows host — never pages. Set it to the host's self-heal task
    # names to enable, e.g.
    # "Poindexter Recovery Agent,Poindexter MCP HTTP,Poindexter-DeployCheckoutSync".
    'scheduled_tasks_probe_watch_tasks': '',

    # ----- Docker port-forward adaptive recovery (brain/docker_port_forward_probe.py) -----
    # The probe detects a stuck Docker Desktop / WSL2 NAT host-port forward
    # (internal-OK + external-FAIL) and recovers it. A `docker restart` fixes a
    # stuck PER-CONTAINER forward (the 2026-04-29 HTTP case) but CANNOT fix a
    # wedge in Docker Desktop's HOST-SIDE port proxy, and restarting a DB severs
    # every consumer's live connection (the 2026-06-29 incident that hung the
    # brain + took the alert plane down ~45 min). So the recovery action is now
    # per-situation:
    #   - DB watch entries (probe_type=postgres) default to alert-only — set via
    #     the `recovery_action` field on docker_port_forward_watch_list entries
    #     ("restart" | "alert_only"; HTTP defaults restart, DB defaults alert_only).
    #   - For ANY entry, after this many CONSECUTIVE failed recoveries the probe
    #     stops restarting and switches to alert-only for the backoff window
    #     below, rather than burning the restart cap on a proven-ineffective
    #     remedy. Default 1 = give up after the very first failed recovery.
    'docker_port_forward_max_failed_recoveries_before_alert_only': '1',
    # 2026-07-14 follow-up — the post-restart recovery check polls at this
    # interval (seconds) up to docker_port_forward_recovery_wait_seconds
    # total, instead of one fixed sleep. Lets a fast recovery exit early
    # rather than always waiting out the full window.
    'docker_port_forward_recovery_poll_interval_seconds': '5',
    # How long (minutes) a container stays alert-only after the give-up trips,
    # before the probe is willing to try one more restart. Bounds the churn
    # without disabling recovery forever.
    'docker_port_forward_alert_only_backoff_minutes': '60',
    # 2026-07-13 follow-up — SCRAM-corruption auth tier. The SSLRequest check
    # above (probe_type=postgres) is a single round trip and cannot see a
    # host-port proxy that passes short exchanges cleanly but corrupts the
    # longer, multi-round-trip SCRAM auth handshake — a real client gets a
    # genuine InvalidPasswordError even though the configured password is
    # correct and unchanged. Confirmed live 2026-07-13: postgres-local/5433
    # sat wedged with zero recovery rows while every HTTP-type watched port
    # around it auto-recovered. Once the SSLRequest tier reports healthy for
    # a postgres entry, this second tier attempts one real asyncpg.connect()
    # against the external host-published port; only InvalidPasswordError
    # escalates (alert-only, same as every other DB wedge signature — never
    # restart). Independently toggleable from the base probe.
    'docker_port_forward_pg_auth_check_enabled': 'true',
    # Timeout (seconds) for the real-auth connection attempt. Slightly above
    # docker_port_forward_probe_timeout_seconds because a real auth handshake
    # is multiple round trips, not one.
    'docker_port_forward_pg_auth_timeout_seconds': '5',

    # ----- API rate limits (slowapi — poindexter#748) -----
    # slowapi limit strings: "<count>/<period>" e.g. "5/minute", "100/hour".
    # Limits are read at request time so operators can tune via app_settings
    # without redeploying. All keys use per-IP keying (get_remote_address).
    'rate_limit_token_per_ip': '10/minute',         # POST /token — OAuth token mint
    'rate_limit_triage_per_ip': '20/minute',        # POST /api/triage — LLM per call
    'rate_limit_remediation_select_per_ip': '30/minute',  # POST /api/remediation/select — firefighter LLM long-tail pick
    'rate_limit_topics_from_url_per_ip': '10/minute',  # POST /api/topics/from-url — outbound fetch
    'rate_limit_podcast_generate_per_ip': '5/minute',  # POST /api/podcast/generate/{id} — GPU
    'rate_limit_video_generate_per_ip': '5/minute',    # POST /api/video/generate/{id} — GPU

    # ----- Experiment / variant selection (#361) -----
    # EWMA damping for the outcome→experiment-variant-weight feedback loop.
    # new_weight = (1 - alpha) * old + alpha * signal (approve=1.0 / reject=0.0).
    'router_feedback_alpha': '0.2',
    # When true, pick_variant allocates proportional to experiment_variants.weight
    # (nudged by the feedback loop) instead of uniform random. Default off.
    'experiment_weighted_selection_enabled': 'false',

    # ----- Pipeline approval gates (#363) -----
    # draft_gate: pause after writer stage for operator review. Default off —
    # prod runs are unaffected until `poindexter gates set draft_gate on`.
    'pipeline_gate_draft_gate': 'off',
    # preview_gate: component-scoped regen gate after the draft is persisted.
    # Default OFF — develop behind the flag; flip to 'on' only after end-to-end
    # verification (plan Task 12). When on it IS the review point (operator can
    # approve / regen_images / regen_text / reject). The node is in the graph_def
    # already (a passthrough no-op while off). regen_*_max_attempts bound the
    # per-component loop; the surface (regen_at_gate) refuses past them.
    # See docs/architecture/2026-06-21-component-scoped-regen-gate.md.
    'pipeline_gate_preview_gate': 'off',
    'regen_images_max_attempts': '3',
    'regen_text_max_attempts': '2',

    # ----- Pipeline event stream (console EVENT STREAM + /pipeline dashboard) -----
    # CSV of audit_log event_types the live-events surfaces show
    # (GET /api/pipeline/events). Empty = the built-in default list in
    # routes/pipeline_events_routes.py::_PIPELINE_EVENT_TYPES — deliberately an
    # empty sentinel, NOT a frozen copy of that list: seeding today's CSV would
    # recreate the exact starvation this key was added to fix (the seeded row
    # wins forever, so event types added in code later would never appear).
    # Set a CSV only to deliberately narrow/widen the feed on this install.
    'pipeline_event_stream_types': '',

    # ----- SEO Harvest Loop (Phase 1) -----
    # The read-only analyzer is safe-on so the opportunity list populates day
    # one. Content-mutating refresh (Phase 2) gates separately on
    # seo.refresh.enabled (default off). See
    # docs/superpowers/specs/2026-06-11-seo-harvest-loop-design.md.
    'seo.harvest.analyzer_enabled': 'true',
    'seo.refresh.enabled': 'false',
    'seo.striking_distance.position_min': '5',
    'seo.striking_distance.position_max': '20',
    # Demand floor for the striking tier — the loop harvests impressions
    # ALREADY earned, so a page-2 post with near-zero impressions is not an
    # opportunity (matches push/low_ctr, which always had a floor). Added after
    # a 2026-07 audit: 105/109 refreshed striking posts had <100 impressions
    # and the corpus earned ~28 clicks total, so meta refreshes had no signal.
    'seo.striking_distance.min_impressions': '100',
    'seo.push_candidate.position_min': '3',
    'seo.push_candidate.position_max': '10',
    'seo.push_candidate.min_impressions': '100',
    'seo.low_ctr.min_impressions': '100',
    'seo.low_ctr.max_ctr': '0.01',
    'seo.opportunity.target_ctr': '0.05',
    # ----- SEO Harvest Loop (Phase 2 — seo_refresh, #763) -----
    # meta_only: re-optimize seo_title + seo_description only; never the body.
    'seo.refresh.scope': 'meta_only',
    # Approval-FIRST: the refresh_gate ships ENABLED, so re-publishing a live
    # post pauses for operator sign-off (unlike draft_gate, which ships off).
    # is_gate_enabled reads pipeline_gate_<gate_name>; gate_name='seo_refresh_gate'.
    'pipeline_gate_seo_refresh_gate': 'true',
    # Lock-2 graduation (wired 2026-07-25 — atoms.approval_gate reads this via
    # the seo_refresh spec's graduation_setting config): once the gate's
    # trailing streak of clean HUMAN approvals (pipeline_gate_history,
    # event_kind='approved', actor='human'; any rejected/dismissed row —
    # operator veto or staleness sweep — resets it) reaches this count, the
    # gate auto-approves (auto_approved history row, actor='graduation') and
    # the refresh republishes without sign-off. 0 disables graduation — the
    # gate always pauses. Revoke earned autonomy: set 0, reject a proposal to
    # reset the streak, then restore.
    'seo.refresh.auto_publish_after_clean_runs': '5',
    # Phase-2 / Task-8 forward-decls (seeded for completeness):
    'seo.query_ingestion.enabled': 'false',
    'seo.refresh.outcome_measure_after_days': '14',
    # Phase 2b (#763) — cap on refresh tasks auto-enqueued per run. Conservative:
    # each refresh still needs operator sign-off at seo_refresh_gate. The job
    # schedules themselves are auto-persisted by PluginScheduler from each job's
    # `schedule` class attribute (plugin.job.<name>), not seeded here.
    'seo.refresh.max_per_run': '3',
    # Spend floor — minimum current impressions for an opportunity to be worth
    # a refresh + operator gate review NOW. Distinct from
    # seo.striking_distance.min_impressions (which governs classification): this
    # governs enqueue, and also screens legacy 'open' rows classified before a
    # floor existed. Raise it to make refreshes more selective without touching
    # what the analyzer classifies or the SEO dashboard shows.
    'seo.refresh.min_impressions': '100',
    # Gate hygiene — seo_refresh runs parked at seo_refresh_gate longer than
    # this are dismissed by ExpireStaleSeoRefreshGatesJob and their
    # opportunity rows reopened for a fresh future proposal. 0 disables.
    'seo.refresh.gate_max_parked_days': '14',
    # Ceiling on one background gate resume (POST /api/gates/pending/…/approve
    # → services.gate_resume). On timeout the graph run is cancelled and the
    # approval rolled back — the task re-parks for review.
    'gate_resume_timeout_seconds': '900',

    # ----- R2 media orphan-reaper (design 2026-07-11) -----
    # Dry-run by default: computes + reports orphans but deletes NOTHING until an
    # operator flips _armed=true after reviewing a dry-run cycle. Keep-set =
    # non-terminal posts + media_assets + feed XML. Grace window protects
    # just-uploaded, not-yet-linked objects; the cap bounds blast radius.
    'media_orphan_sweep_armed': 'false',
    'media_orphan_sweep_grace_days': '14',
    'media_orphan_sweep_max_deletes_per_run': '500',
    'media_orphan_sweep_prefixes': 'images/,video/,podcast/',

    # ----- Cloudflare page-views beacon outage probe -----
    # URL of the Cloudflare Worker that the public-site ViewTracker beacon
    # POSTs page-view pings to (infrastructure/cloudflare/page-views-beacon).
    # ProbeCloudflareBeaconJob health-pings this every 5 min and alerts via
    # the poindexter_cloudflare_beacon_reachable gauge if it stops responding.
    # Re-seeded empty here after the key was dropped as an orphan 2026-06-03
    # (no reader then); the probe job is now the reader. Empty ⇒ probe skips
    # and the gauge stays healthy (an unconfigured beacon must not alert).
    # The production beacon URL is also set in Vercel as NEXT_PUBLIC_BEACON_URL
    # for the browser-side beacon; set this app_setting to the same URL.
    'cloudflare_beacon_url': '',

    # ----- Beacon bot-flag (de-bot the first-party page_views KPI) -----
    # Stealth scrapers present a browser User-Agent and slip the sync job's
    # narrow UA drop-filter, inflating page_views ~10x. FlagBotPageViewsJob
    # (services/jobs/flag_bot_page_views.py) sweeps accumulated rows and flags
    # any (user_agent, path) pair that floods past the cap. Reader-facing
    # surfaces read page_views_human (is_bot=false); liveness signals stay raw.
    # Master switch; 'false' makes the sweep job a no-op.
    'beacon_bot_flag_enabled': 'true',
    # Rolling window (hours) the flood pass groups over.
    'beacon_flood_window_hours': '24',
    # Max hits one (user_agent, path) pair may have in the window before the
    # WHOLE pair is flagged as bot. At current traffic no real pair reaches 20
    # same-UA hits/day; the bot pairs are in the hundreds. Tune up as traffic grows.
    'beacon_flood_cap_per_window': '20',
    # All-history cap for the one-time backfill pass (catches bots that flooded
    # historically but aren't active in the current window).
    'beacon_flood_backfill_cap': '30',
    # Max DISTINCT paths one user_agent may hit in the window before its window
    # rows are flagged (sweep:ua_distinct_paths). Catches full-site crawlers
    # that visit each page once — invisible to the pair cap by construction
    # (147 hits / 145 paths, 2026-07-26, poindexter#973). Window-scoped and
    # backfill-less on purpose: bare UA strings are shared across real humans.
    'beacon_sweep_max_distinct_paths': '25',

    # Social media distribution — Postiz integration
    'social_drafts_enabled': 'false',
    'social_draft_platforms': '',
    'social_reddit_subreddits': '',
    'social_draft_max_retries': '3',
    # Reconciliation lookback for BackfillMissingSocialDraftsJob (poindexter#863)
    # — how many days back to re-check published posts for incomplete draft
    # coverage. Bounds the sweep so it doesn't re-scan the entire archive.
    'social_draft_backfill_lookback_days': '14',
    'postiz_api_url': 'http://postiz:3000',
    'postiz_integration_id_twitter': '',
    'postiz_integration_id_linkedin': '',
    'postiz_integration_id_mastodon': '',
    'postiz_integration_id_bluesky': '',
    'postiz_integration_id_reddit': '',
    'postiz_integration_id_tiktok': '',
    'postiz_integration_id_instagram': '',
    # X exposes a "made with AI" disclosure flag on each post. Glad Labs
    # content is AI-authored, so this defaults true; a future operator can
    # set it false per their content policy or jurisdiction.
    'social_x_made_with_ai': 'true',
    # Operator's own X account — empty on OSS; the operator overlay restores it.
    'social_x_handle': '',
    'social_x_url': '',

}


# Per-key lifecycle metadata for high-risk settings (poindexter#756).
#
# Keys NOT listed here get NULL for owner/value_type and deprecated=FALSE —
# the schema defaults are safe.  Add entries incrementally as keys are
# annotated; there is no requirement that every key in DEFAULTS has an entry.
#
# Fields:
#   owner        (str)  Module/service that is the primary reader.
#   value_type   (str)  One of: string boolean integer float url model csv
#                       json duration.  Matches the CHECK constraint on the
#                       value_type column.
#   deprecated   (bool) True when the key has been renamed/superseded.
#                       SiteConfig.get() emits a once-per-boot WARNING.
#   superseded_by (str) The replacement key to migrate to (with deprecated).
#
# seed_all_defaults() applies these on every boot via UPDATE … WHERE … IS
# DISTINCT FROM so the pass is a no-op on up-to-date deployments.
METADATA: dict[str, dict[str, str | bool | None]] = {
    # ----- Cost guard (incident: spend-limit rename fallthrough 2026-05-27) -----
    'daily_spend_limit_usd': {'owner': 'cost_guard', 'value_type': 'float'},
    'monthly_spend_limit_usd': {'owner': 'cost_guard', 'value_type': 'float'},
    'electricity_measured_min_coverage_pct': {'owner': 'cost_ledger', 'value_type': 'float'},
    'electricity_source_gap_minutes': {'owner': 'cost_ledger', 'value_type': 'integer'},
    'psu_watchdog_degraded_cycles_before_page': {'owner': 'brain_psu_watchdog', 'value_type': 'integer'},
    'cost_throttle_enabled': {'owner': 'spend_throttle', 'value_type': 'boolean'},
    'cost_throttle_count_idle_electricity': {'owner': 'spend_throttle', 'value_type': 'boolean'},
    'cost_throttle_daily_budget_usd': {'owner': 'spend_throttle', 'value_type': 'float'},
    'cost_throttle_monthly_budget_usd': {'owner': 'spend_throttle', 'value_type': 'float'},
    'cost_throttle_resume_buffer_pct': {'owner': 'spend_throttle', 'value_type': 'float'},

    # ----- LLM model selection (writer-flip = canary per feedback_writer_model_canary) -----
    'pipeline_writer_model': {'owner': 'content_router', 'value_type': 'model'},
    # ----- Image direction (writer nominates; local model phrases) -----
    'inline_image_prompt_model': {'owner': 'image_pipeline', 'value_type': 'model'},
    'model_role_image_decision': {'owner': 'image_decision_agent', 'value_type': 'model'},
    'writer_max_inline_images': {'owner': 'plan_image_markers', 'value_type': 'integer'},
    'image_decision_section_body_chars': {'owner': 'image_decision_agent', 'value_type': 'integer'},
    'pipeline_critic_model': {'owner': 'multi_model_qa', 'value_type': 'model'},
    'content_originality_enabled': {'owner': 'multi_model_qa', 'value_type': 'boolean'},
    'content_originality_max_similarity': {'owner': 'multi_model_qa', 'value_type': 'float'},
    'content_originality_chunk_min_chars': {'owner': 'multi_model_qa', 'value_type': 'integer'},
    'content_originality_chunk_max_chars': {'owner': 'multi_model_qa', 'value_type': 'integer'},
    'content_originality_excluded_series': {'owner': 'multi_model_qa', 'value_type': 'string'},
    'qa_title_coherence_enabled': {'owner': 'multi_model_qa', 'value_type': 'boolean'},
    'qa_title_coherence_model': {'owner': 'multi_model_qa', 'value_type': 'model'},
    'qa_title_coherence_digest_chars': {'owner': 'multi_model_qa', 'value_type': 'integer'},
    'title_content_excerpt_chars': {'owner': 'title_generation', 'value_type': 'integer'},
    'video_director_model': {'owner': 'video_director', 'value_type': 'model'},
    'video_director_timeout_seconds': {'owner': 'video_director', 'value_type': 'integer'},
    'video_director_disable_thinking': {'owner': 'video_director', 'value_type': 'boolean'},
    'video_director_max_tokens': {'owner': 'video_director', 'value_type': 'integer'},
    'video_director_max_retries': {'owner': 'video_director', 'value_type': 'integer'},
    'video_shot_qa_enabled': {'owner': 'video', 'value_type': 'boolean'},
    'video_shot_qa_threshold': {'owner': 'video', 'value_type': 'integer'},
    'video_shot_qa_max_retries': {'owner': 'video', 'value_type': 'integer'},
    'video_pexels_video_enabled': {'owner': 'video', 'value_type': 'boolean'},
    'generative_video_model': {'owner': 'video', 'value_type': 'model'},
    'video_hero_shots_max': {'owner': 'video', 'value_type': 'integer'},
    'video_hero_width': {'owner': 'video', 'value_type': 'integer'},
    'video_hero_height': {'owner': 'video', 'value_type': 'integer'},
    'video_hero_fps': {'owner': 'video', 'value_type': 'integer'},
    'video_hero_motion_default': {'owner': 'video', 'value_type': 'string'},
    'video_short_target_seconds': {'owner': 'video', 'value_type': 'integer'},
    'video_short_max_seconds': {'owner': 'video', 'value_type': 'integer'},
    'video_render_min_shot_ratio': {'owner': 'media_render', 'value_type': 'float'},
    'video_fallback_card_enabled': {'owner': 'media_render', 'value_type': 'boolean'},
    'video_render_min_real_source_ratio': {'owner': 'media_render', 'value_type': 'float'},
    'video_narration_fit_enabled': {'owner': 'media_render', 'value_type': 'boolean'},
    'video_short_max_shot_seconds': {'owner': 'media_render', 'value_type': 'float'},
    'video_long_target_seconds': {'owner': 'video', 'value_type': 'integer'},
    'video_long_max_seconds': {'owner': 'video', 'value_type': 'integer'},
    'video_short_min_words': {'owner': 'video', 'value_type': 'integer'},
    'video_long_max_shot_seconds': {'owner': 'media_render', 'value_type': 'float'},
    'video_fit_min_shot_seconds': {'owner': 'media_render', 'value_type': 'float'},
    'video_fit_trailing_hold_seconds': {'owner': 'media_render', 'value_type': 'float'},
    'demo_clip_dir': {'owner': 'demo_clips', 'value_type': 'string'},
    'demo_clip_font_family': {'owner': 'demo_clips', 'value_type': 'string'},
    'demo_clip_font_size': {'owner': 'demo_clips', 'value_type': 'integer'},
    'demo_clip_width': {'owner': 'demo_clips', 'value_type': 'integer'},
    'demo_clip_height': {'owner': 'demo_clips', 'value_type': 'integer'},
    'demo_clip_padding': {'owner': 'demo_clips', 'value_type': 'integer'},
    'demo_clip_framerate': {'owner': 'demo_clips', 'value_type': 'integer'},
    'demo_clip_typing_speed': {'owner': 'demo_clips', 'value_type': 'string'},
    'demo_clip_theme_background': {'owner': 'demo_clips', 'value_type': 'string'},
    'demo_clip_theme_foreground': {'owner': 'demo_clips', 'value_type': 'string'},
    'demo_clip_theme_accent': {'owner': 'demo_clips', 'value_type': 'string'},
    'demo_clip_theme_accent_bright': {'owner': 'demo_clips', 'value_type': 'string'},
    'demo_clip_theme_active': {'owner': 'demo_clips', 'value_type': 'string'},
    'demo_clip_theme_attention': {'owner': 'demo_clips', 'value_type': 'string'},
    'demo_clip_theme_failure': {'owner': 'demo_clips', 'value_type': 'string'},
    'demo_clip_theme_dim': {'owner': 'demo_clips', 'value_type': 'string'},
    'video_caption_engine': {'owner': 'caption_providers', 'value_type': 'string'},
    'plugin.caption_provider.speaches.enabled': {
        'owner': 'caption_providers', 'value_type': 'boolean',
    },
    'plugin.caption_provider.speaches.base_url': {
        'owner': 'caption_providers', 'value_type': 'url',
    },
    'plugin.caption_provider.speaches.model': {
        'owner': 'caption_providers', 'value_type': 'model',
    },
    'plugin.caption_provider.speaches.timeout_seconds': {
        'owner': 'caption_providers', 'value_type': 'integer',
    },
    'plugin.caption_provider.speaches.initial_prompt': {
        'owner': 'caption_providers', 'value_type': 'string',
    },
    'pipeline_fallback_model': {'owner': 'content_router', 'value_type': 'model'},
    'qa_fallback_critic_model': {'owner': 'multi_model_qa', 'value_type': 'model'},
    'qa_fallback_writer_model': {'owner': 'multi_model_qa', 'value_type': 'model'},
    'structured_extraction_model': {'owner': 'content_router', 'value_type': 'model'},
    'embed_model': {'owner': 'rag_engine', 'value_type': 'model'},
    'niche_embedding_model': {'owner': 'topic_discovery', 'value_type': 'model'},
    'qa_vision_model': {'owner': 'multi_model_qa', 'value_type': 'model'},
    'qa_preview_vision_model': {'owner': 'multi_model_qa', 'value_type': 'model'},
    'qa_vision_num_predict': {'owner': 'multi_model_qa', 'value_type': 'integer'},
    'qa_vision_thinking_num_predict': {'owner': 'multi_model_qa', 'value_type': 'integer'},
    'vision_alt_model': {'owner': 'image_service', 'value_type': 'model'},
    'rag_rerank_model': {'owner': 'rag_engine', 'value_type': 'model'},
    'rag_rerank_device': {'owner': 'rag_engine', 'value_type': 'string'},
    'model_eval_promotion_margin': {'owner': 'model_eval', 'value_type': 'float'},
    'model_eval_reranker_golden_size': {'owner': 'model_eval', 'value_type': 'integer'},
    'model_eval_reranker_candidates_per_case': {'owner': 'model_eval', 'value_type': 'integer'},
    'gpu_vram_total_gb': {'owner': 'gpu_scheduler', 'value_type': 'string'},
    'gpu_desktop_reserve_gb': {'owner': 'gpu_scheduler', 'value_type': 'float'},
    'gpu_vram_autodetect_fallback_gb': {'owner': 'gpu_scheduler', 'value_type': 'float'},
    'pipeline_gpu_index': {'owner': 'gpu_scheduler', 'value_type': 'integer'},
    'ollama_kv_cache_type': {'owner': 'vram_budget', 'value_type': 'string'},
    'vram_budget_guard_enabled': {'owner': 'vram_budget', 'value_type': 'boolean'},

    # ----- LLM provider gates (security — paid-API lock) -----
    'plugin.llm_provider.litellm.allow_paid_base_url': {
        'owner': 'litellm_provider', 'value_type': 'boolean',
    },
    'plugin.llm_provider.litellm.disable_aiohttp_transport': {
        'owner': 'litellm_provider', 'value_type': 'boolean',
    },
    'plugin.llm_provider.litellm.anthropic_prompt_caching': {
        'owner': 'litellm_provider', 'value_type': 'boolean',
    },
    'plugin.llm_provider.openai_compat.allow_paid_base_url': {
        'owner': 'openai_compat', 'value_type': 'boolean',
    },

    # ----- QA thresholds -----
    'qa_pass_threshold': {'owner': 'multi_model_qa', 'value_type': 'float'},
    'qa_rewrite_max_attempts': {'owner': 'qa_aggregate', 'value_type': 'integer'},
    'qa_flag_instead_of_reject': {'owner': 'qa_aggregate', 'value_type': 'boolean'},
    'qa_critical_floor': {'owner': 'multi_model_qa', 'value_type': 'float'},
    'deepeval_enabled': {'owner': 'multi_model_qa', 'value_type': 'boolean'},
    'guardrails_enabled': {'owner': 'multi_model_qa', 'value_type': 'boolean'},
    'ragas_enabled': {'owner': 'multi_model_qa', 'value_type': 'boolean'},

    # ----- RAG / retrieval (incident: rag_source_filter empty = corpus pollution 2026-06) -----
    'rag_source_filter': {'owner': 'rag_engine', 'value_type': 'csv'},
    'writer_rag_source_filter': {'owner': 'two_pass_writer', 'value_type': 'csv'},
    'writer_rag_source_caps': {'owner': 'two_pass_writer', 'value_type': 'string'},
    'niche_internal_rag_lookback_days': {
        'owner': 'internal_rag_source', 'value_type': 'integer',
    },
    'niche_internal_rag_kind_weights': {
        'owner': 'internal_rag_source', 'value_type': 'string',
    },
    'niche_external_grounding_enabled': {
        'owner': 'topic_grounding', 'value_type': 'boolean',
    },
    'niche_external_grounding_source_kinds': {
        'owner': 'topic_grounding', 'value_type': 'csv',
    },
    'niche_external_grounding_threshold': {
        'owner': 'topic_grounding', 'value_type': 'float',
    },
    'niche_external_grounding_penalty_factor': {
        'owner': 'topic_grounding', 'value_type': 'float',
    },
    'writer_internal_grounding_enabled': {
        'owner': 'two_pass_writer', 'value_type': 'boolean',
    },
    'rag_hybrid_enabled': {'owner': 'rag_engine', 'value_type': 'boolean'},
    'rag_rerank_enabled': {'owner': 'rag_engine', 'value_type': 'boolean'},
    'rag_engine_enabled': {'owner': 'rag_engine', 'value_type': 'boolean'},
    # Retrieval de-echo (two_pass writer): oversample + near-dup ceiling + MMR.
    'writer_rag_candidate_multiplier': {'owner': 'two_pass_writer', 'value_type': 'integer'},
    'writer_rag_dedup_ceiling': {'owner': 'two_pass_writer', 'value_type': 'float'},
    'writer_rag_mmr_lambda': {'owner': 'two_pass_writer', 'value_type': 'float'},

    # ----- Auto-publish gate (incident: niche-leak 2026-05-26) -----
    'dev_diary_auto_publish_threshold': {'owner': 'auto_publish_gate', 'value_type': 'float'},
    'dev_diary_auto_publish_dry_run': {'owner': 'auto_publish_gate', 'value_type': 'boolean'},
    'dev_diary_auto_publish_max_edit_distance': {'owner': 'auto_publish_gate', 'value_type': 'integer'},
    'dev_diary_auto_publish_min_clean_runs': {'owner': 'auto_publish_gate', 'value_type': 'integer'},
    'enforce_niche_allowlist': {'owner': 'publish_service', 'value_type': 'boolean'},

    # ----- Dev-diary substance bar (thin-day skip) -----
    'dev_diary_min_substance_score': {'owner': 'dev_diary_source', 'value_type': 'float'},
    'dev_diary_substance_weight_pr': {'owner': 'dev_diary_source', 'value_type': 'float'},
    'dev_diary_substance_weight_commit': {'owner': 'dev_diary_source', 'value_type': 'float'},
    'dev_diary_substance_weight_post': {'owner': 'dev_diary_source', 'value_type': 'float'},
    'dev_diary_substance_weight_audit': {'owner': 'dev_diary_source', 'value_type': 'float'},
    'dev_diary_bookkeeping_types': {'owner': 'dev_diary_source', 'value_type': 'string'},
    'dev_diary_bot_author_patterns': {'owner': 'dev_diary_source', 'value_type': 'string'},

    # ----- Pipeline gates -----
    'pipeline_gate_draft_gate': {'owner': 'template_runner', 'value_type': 'string'},
    'pipeline_gate_seo_refresh_gate': {'owner': 'seo_refresh', 'value_type': 'boolean'},
    'seo.refresh.auto_publish_after_clean_runs': {'owner': 'approval_gate', 'value_type': 'integer'},
    'pipeline_gate_preview_gate': {'owner': 'approval_gate', 'value_type': 'string'},
    'regen_images_max_attempts': {'owner': 'regen_at_gate', 'value_type': 'integer'},
    'regen_text_max_attempts': {'owner': 'regen_at_gate', 'value_type': 'integer'},

    # ----- Media pipeline master switches -----
    'media_pipeline_trigger_enabled': {'owner': 'dispatch_media_pipeline', 'value_type': 'boolean'},
    'podcast_pipeline_trigger_enabled': {'owner': 'dispatch_podcast_pipeline', 'value_type': 'boolean'},

    # ----- Render-infra health gate + bounded cap reset (2026-07-03) -----
    'media_infra_healthcheck_enabled': {'owner': 'media_infra_health', 'value_type': 'boolean'},
    'media_infra_health_timeout_seconds': {'owner': 'media_infra_health', 'value_type': 'float'},
    'media_infra_dns_canary_host': {'owner': 'media_infra_health', 'value_type': 'string'},
    'media_redispatch_cap_reset_enabled': {'owner': 'media_reconciliation', 'value_type': 'boolean'},
    'media_redispatch_cap_reset_cooldown_hours': {'owner': 'media_reconciliation', 'value_type': 'integer'},

    # ----- R2 media orphan-reaper (design 2026-07-11) -----
    'media_orphan_sweep_armed': {'owner': 'media_orphan_sweep', 'value_type': 'boolean'},
    'media_orphan_sweep_grace_days': {'owner': 'media_orphan_sweep', 'value_type': 'integer'},
    'media_orphan_sweep_max_deletes_per_run': {'owner': 'media_orphan_sweep', 'value_type': 'integer'},
    'media_orphan_sweep_prefixes': {'owner': 'media_orphan_sweep', 'value_type': 'string'},

    # ----- Content pipeline behaviour -----
    'content_flow_stale_inprogress_minutes': {'owner': 'content_generation_flow', 'value_type': 'integer'},
    'template_runner_use_postgres_checkpointer': {'owner': 'template_runner', 'value_type': 'boolean'},

    # ----- Observability -----
    'enable_tracing': {'owner': 'otel', 'value_type': 'boolean'},
    'enable_pyroscope': {'owner': 'pyroscope', 'value_type': 'boolean'},
    'langfuse_tracing_enabled': {'owner': 'prompt_manager', 'value_type': 'boolean'},

    # ----- Brain / migration drift -----
    'migration_drift_auto_sync_enabled': {'owner': 'brain_migration_drift_probe', 'value_type': 'boolean'},
    'migration_drift_defer_while_inflight': {'owner': 'brain_migration_drift_probe', 'value_type': 'boolean'},

    # ----- Brain / cycle watchdog (2026-06-29 hang hardening) -----
    'brain_cycle_timeout_seconds': {'owner': 'brain_daemon', 'value_type': 'integer'},
    'brain_heartbeat_interval_seconds': {'owner': 'brain_daemon', 'value_type': 'integer'},
    'brain_hang_dump_seconds': {'owner': 'brain_daemon', 'value_type': 'integer'},

    # ----- Deprecated keys — emit warning on read (add new ones here) -----
    # nvidia_exporter_url went dead when PR #1827 moved gpu_scheduler onto
    # Prometheus (gpu_metrics_prometheus_url) for GPU metrics; nothing reads
    # the direct-exporter URL anymore. Kept as a tombstone so SiteConfig.get()
    # warns once-per-boot and points callers at the replacement key.
    'nvidia_exporter_url': {
        'owner': 'gpu_scheduler', 'value_type': 'url',
        'deprecated': True, 'superseded_by': 'gpu_metrics_prometheus_url',
    },
    # Example pattern (uncomment + fill in when retiring a key):
    # 'old_key_name': {
    #     'owner': 'cost_guard', 'value_type': 'float',
    #     'deprecated': True, 'superseded_by': 'new_key_name',
    # },
    # ----- Derived annotations (poindexter#956 follow-up) -----
    #
    # The block above is hand-curated per subsystem; everything below was
    # DERIVED from the tree and mechanically verified, then appended in one
    # pass. Regenerate/extend with `python scripts/suggest_settings_metadata.py`,
    # which documents the rules and re-runs the same checks.
    #
    # owner      = the single module whose source references the key. Emitted
    #              only where exactly ONE non-excluded file does, so an
    #              ambiguous key gets no owner rather than a guessed one.
    #              Seeders and appliers (settings_defaults, operator_overrides,
    #              migrations, seeds) and tests are excluded — they MENTION
    #              every key without reading any. All 430 were verified to
    #              contain the key literal in the named module.
    # value_type = inferred from the key's real default value, never from its
    #              name — the name is not reliable: `allow_paid_base_url` is a
    #              boolean, `affiliate_redirect_base_url` is '/go', several
    #              `*_seconds` are floats, and `nomic-embed-text` is a model
    #              with no ':' or '/'. The ONE proven name signal is `*_enabled`
    #              -> boolean (83/83 of non-empty ones), used to type the
    #              empty-valued ones.
    #
    # Deliberately absent: a key whose default is '' and whose name carries no
    # proven signal gets NO value_type. '' is the unset sentinel across every
    # type, so a guess there would be fabricated data. `csv` appears only for
    # keys whose consumer was individually confirmed to split on ',' — prose
    # that merely contains a comma is a `string`.
    'affiliate_click_bot_ua_pattern': {'owner': 'sync_affiliate_clicks', 'value_type': 'string'},
    'affiliate_disclosure_text': {'owner': 'static_export_service', 'value_type': 'string'},
    'affiliate_import_llm_model': {'owner': 'affiliate_import'},
    'affiliate_import_llm_timeout_seconds': {'owner': 'affiliate_import', 'value_type': 'integer'},
    'affiliate_injection_enabled': {'owner': 'content_inject_affiliate_links', 'value_type': 'boolean'},
    'affiliate_max_links_per_post': {'owner': 'content_inject_affiliate_links', 'value_type': 'integer'},
    'affiliate_redirect_base_url': {'value_type': 'string'},
    'agent_persona_name': {'value_type': 'string'},
    'app_version': {'owner': 'sentry_integration', 'value_type': 'string'},
    'atom_runs_output_preview_max_bytes': {'owner': 'pipeline_architect', 'value_type': 'integer'},
    'audio_gen_engine': {'owner': 'audio_gen_service'},
    'audio_render_timeout_seconds': {'owner': 'stable_audio_open', 'value_type': 'integer'},
    'auto_embed_max_age_hours': {'owner': 'auto_embed_watch', 'value_type': 'integer'},
    'auto_embed_watch_enabled': {'owner': 'auto_embed_watch', 'value_type': 'boolean'},
    'auto_embed_watch_max_retries': {'owner': 'auto_embed_watch', 'value_type': 'integer'},
    'auto_embed_watch_retry_delay_seconds': {'owner': 'auto_embed_watch', 'value_type': 'integer'},
    'beacon_bot_flag_enabled': {'owner': 'flag_bot_page_views', 'value_type': 'boolean'},
    'beacon_flood_backfill_cap': {'owner': 'flag_bot_page_views', 'value_type': 'integer'},
    'beacon_flood_cap_per_window': {'owner': 'flag_bot_page_views', 'value_type': 'integer'},
    'beacon_flood_window_hours': {'owner': 'flag_bot_page_views', 'value_type': 'integer'},
    'beacon_sweep_max_distinct_paths': {'owner': 'flag_bot_page_views', 'value_type': 'integer'},
    'brain_anomaly_baseline_window_days': {'owner': 'detect_anomalies', 'value_type': 'integer'},
    'brain_anomaly_current_window_hours': {'owner': 'detect_anomalies', 'value_type': 'integer'},
    'brain_digest_window_hours': {'owner': 'brain_daemon', 'value_type': 'integer'},
    'branch_drift_min_commits_behind': {'owner': 'branch_drift_probe', 'value_type': 'integer'},
    'cadence_slo_enabled': {'owner': 'health_probes', 'value_type': 'boolean'},
    'cadence_slo_expected_posts_per_day': {'owner': 'health_probes', 'value_type': 'integer'},
    'cadence_slo_shortfall_ratio': {'owner': 'health_probes', 'value_type': 'float'},
    'cadence_slo_window_hours': {'owner': 'health_probes', 'value_type': 'integer'},
    'citation_reconcile_enabled': {'owner': 'content_reconcile_citations', 'value_type': 'boolean'},
    'citation_reconcile_llm_enabled': {'owner': 'content_llm_reconcile_citations', 'value_type': 'boolean'},
    'citation_reconcile_llm_max_content_chars': {'owner': 'content_llm_reconcile_citations', 'value_type': 'integer'},
    'citation_reconcile_llm_model': {'owner': 'content_llm_reconcile_citations'},
    'citation_reconcile_llm_timeout_seconds': {'owner': 'content_llm_reconcile_citations', 'value_type': 'integer'},
    'citation_repoint_enabled': {'owner': 'content_reconcile_citations', 'value_type': 'boolean'},
    'citation_repoint_multitenant_hosts': {'owner': 'content_reconcile_citations'},
    'citation_strip_unlinked_enabled': {'owner': 'content_reconcile_citations', 'value_type': 'boolean'},
    'classify_content_types_enabled': {'owner': 'classify_content_types', 'value_type': 'boolean'},
    'clock_skew_probe_enabled': {'owner': 'clock_skew_probe', 'value_type': 'boolean'},
    'clock_skew_reference_url': {'owner': 'clock_skew_probe', 'value_type': 'url'},
    'clock_skew_renotify_minutes': {'owner': 'clock_skew_probe', 'value_type': 'integer'},
    'clock_skew_sample_retention_days': {'owner': 'clock_skew_probe', 'value_type': 'integer'},
    'clock_skew_severity': {'owner': 'clock_skew_probe', 'value_type': 'string'},
    'clock_skew_threshold_seconds': {'owner': 'clock_skew_probe', 'value_type': 'integer'},
    'cloudflare_beacon_url': {'owner': 'probe_cloudflare_beacon'},
    'community_draft_model': {'owner': 'community_drafts'},
    'community_draft_timeout_seconds': {'owner': 'community_drafts', 'value_type': 'integer'},
    'company_founded_date': {'owner': 'content_validator', 'value_type': 'string'},
    'company_founder_name': {'owner': 'content_validator'},
    'compose_drift_active_profiles': {'owner': 'compose_drift_probe'},
    'compose_drift_host_recover_cap_per_window': {'owner': 'compose_drift_probe', 'value_type': 'integer'},
    'compose_drift_host_recover_enabled': {'owner': 'compose_drift_probe', 'value_type': 'boolean'},
    'compose_drift_host_recover_window_minutes': {'owner': 'compose_drift_probe', 'value_type': 'integer'},
    'compose_project_name': {'owner': 'compose_drift_probe', 'value_type': 'string'},
    'console_chat_brain': {'value_type': 'string'},
    'console_chat_context_recent_turns': {'owner': 'chat_agent', 'value_type': 'integer'},
    'console_chat_daily_token_budget': {'owner': 'chat_agent', 'value_type': 'integer'},
    'console_chat_enabled': {'owner': 'chat_routes', 'value_type': 'boolean'},
    'console_chat_max_tool_calls': {'owner': 'chat_agent', 'value_type': 'integer'},
    'console_chat_model': {'owner': 'chat_agent', 'value_type': 'model'},
    'console_chat_tool_result_max_chars': {'owner': 'chat_agent', 'value_type': 'integer'},
    'console_chat_turn_timeout_s': {'value_type': 'integer'},
    'content_flow_max_concurrency': {'owner': 'deploy_content_flow', 'value_type': 'integer'},
    'content_router_contradiction_review_max_tokens': {'owner': 'self_review', 'value_type': 'integer'},
    'content_router_contradiction_revise_max_tokens': {'owner': 'self_review', 'value_type': 'integer'},
    'content_router_contradiction_timeout_seconds': {'owner': 'self_review', 'value_type': 'integer'},
    'content_router_qa_rewrite_max_tokens': {'value_type': 'integer'},
    'content_router_qa_rewrite_timeout_seconds': {'owner': 'qa_rewrite', 'value_type': 'integer'},
    'content_router_seo_title_max_tokens': {'owner': 'title_generation', 'value_type': 'integer'},
    'content_type_classifier_model': {'owner': 'classify_content_types'},
    'content_type_labels': {'owner': 'classify_content_types', 'value_type': 'string'},
    'content_validator_hallucinated_reference_warning_threshold': {'owner': 'content_validator', 'value_type': 'integer'},
    'content_validator_unlinked_citation_warning_threshold': {'owner': 'content_validator', 'value_type': 'integer'},
    'cost_guard_local_fallback_enabled': {'owner': 'dispatcher', 'value_type': 'boolean'},
    'cost_guard_local_fallback_model': {'owner': 'dispatcher'},
    'create_post_dedup_threshold': {'owner': 'topic_dedup_guard', 'value_type': 'float'},
    'data_fabric_loki_url': {'value_type': 'url'},
    'data_fabric_prometheus_url': {'owner': 'prometheus', 'value_type': 'url'},
    'data_fabric_pyroscope_url': {'owner': 'pyroscope', 'value_type': 'url'},
    'data_fabric_tempo_url': {'owner': 'tempo', 'value_type': 'url'},
    'data_freshness_feeds': {'owner': 'data_freshness_probe', 'value_type': 'json'},
    'data_freshness_probe_enabled': {'owner': 'data_freshness_probe', 'value_type': 'boolean'},
    'default_ollama_model': {'value_type': 'model'},
    'development_mode': {'value_type': 'boolean'},
    'devto_api_base': {'owner': 'devto_service', 'value_type': 'url'},
    'devto_syndicate_content_types': {'owner': 'crosspost_to_devto'},
    'devto_syndicate_min_quality': {'owner': 'crosspost_to_devto', 'value_type': 'integer'},
    'disable_auth_for_dev': {'owner': 'token_validation', 'value_type': 'boolean'},
    'disabled_capabilities_probe_enabled': {'owner': 'probe_disabled_capabilities', 'value_type': 'boolean'},
    'docker_port_forward_alert_only_backoff_minutes': {'owner': 'docker_port_forward_probe', 'value_type': 'integer'},
    'docker_port_forward_max_failed_recoveries_before_alert_only': {'owner': 'docker_port_forward_probe', 'value_type': 'integer'},
    'docker_port_forward_pg_auth_check_enabled': {'owner': 'docker_port_forward_probe', 'value_type': 'boolean'},
    'docker_port_forward_pg_auth_timeout_seconds': {'owner': 'docker_port_forward_probe', 'value_type': 'integer'},
    'docker_port_forward_recovery_poll_interval_seconds': {'owner': 'docker_port_forward_probe', 'value_type': 'integer'},
    'electricity_rate_kwh': {'value_type': 'float'},
    'embed_num_gpu': {'value_type': 'integer'},
    'enable_image_gen_warmup': {'owner': 'startup_manager'},
    'enable_writer_self_review': {'value_type': 'boolean'},
    'environment': {'value_type': 'string'},
    'experiment_weighted_selection_enabled': {'owner': 'experiment_runner', 'value_type': 'boolean'},
    'findings.anomaly.cooldown_minutes': {'value_type': 'integer'},
    'findings.anomaly.delivery': {'value_type': 'string'},
    'findings.anomaly.fallback': {'value_type': 'string'},
    'findings.anomaly.min_severity': {'value_type': 'string'},
    'findings.approval_gate_graduated.cooldown_minutes': {'value_type': 'integer'},
    'findings.approval_gate_graduated.delivery': {'value_type': 'string'},
    'findings.approval_gate_graduated.fallback': {'value_type': 'string'},
    'findings.approval_gate_graduated.min_severity': {'value_type': 'string'},
    'findings.broken_external_link.cooldown_minutes': {'value_type': 'integer'},
    'findings.broken_external_link.delivery': {'value_type': 'string'},
    'findings.broken_external_link.fallback': {'value_type': 'string'},
    'findings.broken_external_link.min_severity': {'value_type': 'string'},
    'findings.broken_external_link_autofixed.delivery': {'value_type': 'string'},
    'findings.broken_internal_link.cooldown_minutes': {'value_type': 'integer'},
    'findings.broken_internal_link.delivery': {'value_type': 'string'},
    'findings.broken_internal_link.fallback': {'value_type': 'string'},
    'findings.broken_internal_link.min_severity': {'value_type': 'string'},
    'findings.broken_internal_link_autofixed.delivery': {'value_type': 'string'},
    'findings.broken_link.cooldown_minutes': {'value_type': 'integer'},
    'findings.broken_link.delivery': {'value_type': 'string'},
    'findings.broken_link.fallback': {'value_type': 'string'},
    'findings.broken_link.min_severity': {'value_type': 'string'},
    'findings.cloud_sync_returned_false.cooldown_minutes': {'value_type': 'integer'},
    'findings.cloud_sync_returned_false.delivery': {'value_type': 'string'},
    'findings.cloud_sync_returned_false.fallback': {'value_type': 'string'},
    'findings.cloud_sync_returned_false.min_severity': {'value_type': 'string'},
    'findings.db_clock_skew.cooldown_minutes': {'value_type': 'integer'},
    'findings.db_clock_skew.delivery': {'value_type': 'string'},
    'findings.db_clock_skew.fallback': {'value_type': 'string'},
    'findings.db_clock_skew.min_severity': {'value_type': 'string'},
    'findings.default.cooldown_minutes': {'value_type': 'integer'},
    'findings.default.delivery': {'value_type': 'string'},
    'findings.default.fallback': {'value_type': 'string'},
    'findings.default.min_severity': {'value_type': 'string'},
    'findings.disabled_capabilities.cooldown_minutes': {'value_type': 'integer'},
    'findings.disabled_capabilities.delivery': {'value_type': 'string'},
    'findings.disabled_capabilities.fallback': {'value_type': 'string'},
    'findings.disabled_capabilities.min_severity': {'value_type': 'string'},
    'findings.duplicate_post.delivery': {'value_type': 'string'},
    'findings.gpu_lock_timeout.cooldown_minutes': {'value_type': 'integer'},
    'findings.gpu_lock_timeout.delivery': {'value_type': 'string'},
    'findings.gpu_lock_timeout.fallback': {'value_type': 'string'},
    'findings.gpu_lock_timeout.min_severity': {'value_type': 'string'},
    'findings.image_gen_downgrade.cooldown_minutes': {'value_type': 'integer'},
    'findings.image_gen_downgrade.delivery': {'value_type': 'string'},
    'findings.image_gen_downgrade.fallback': {'value_type': 'string'},
    'findings.image_gen_downgrade.min_severity': {'value_type': 'string'},
    'findings.image_gen_unreachable.delivery': {'value_type': 'string'},
    'findings.langfuse_prompts_unavailable.cooldown_minutes': {'value_type': 'integer'},
    'findings.langfuse_prompts_unavailable.delivery': {'value_type': 'string'},
    'findings.langfuse_prompts_unavailable.fallback': {'value_type': 'string'},
    'findings.langfuse_prompts_unavailable.min_severity': {'value_type': 'string'},
    'findings.media_drift.delivery': {'value_type': 'string'},
    'findings.media_feed_drift.cooldown_minutes': {'value_type': 'integer'},
    'findings.media_feed_drift.delivery': {'value_type': 'string'},
    'findings.media_feed_drift.fallback': {'value_type': 'string'},
    'findings.media_feed_drift.min_severity': {'value_type': 'string'},
    'findings.media_feed_render_collapse.cooldown_minutes': {'value_type': 'integer'},
    'findings.media_feed_render_collapse.delivery': {'value_type': 'string'},
    'findings.media_feed_render_collapse.fallback': {'value_type': 'string'},
    'findings.media_feed_render_collapse.min_severity': {'value_type': 'string'},
    'findings.media_gpu_busy_skip.cooldown_minutes': {'value_type': 'integer'},
    'findings.media_gpu_busy_skip.delivery': {'value_type': 'string'},
    'findings.media_gpu_busy_skip.min_severity': {'value_type': 'string'},
    'findings.missing_seo.cooldown_minutes': {'value_type': 'integer'},
    'findings.missing_seo.delivery': {'value_type': 'string'},
    'findings.missing_seo.fallback': {'value_type': 'string'},
    'findings.missing_seo.labels': {'value_type': 'string'},
    'findings.missing_seo.min_severity': {'value_type': 'string'},
    'findings.post_verification_failure.cooldown_minutes': {'value_type': 'integer'},
    'findings.post_verification_failure.delivery': {'value_type': 'string'},
    'findings.post_verification_failure.fallback': {'value_type': 'string'},
    'findings.post_verification_failure.min_severity': {'value_type': 'string'},
    'findings.prompt_catalog_drift.cooldown_minutes': {'value_type': 'integer'},
    'findings.prompt_catalog_drift.delivery': {'value_type': 'string'},
    'findings.prompt_catalog_drift.fallback': {'value_type': 'string'},
    'findings.prompt_catalog_drift.min_severity': {'value_type': 'string'},
    'findings.qa_rail_gpu_busy_skip.cooldown_minutes': {'value_type': 'integer'},
    'findings.qa_rail_gpu_busy_skip.delivery': {'value_type': 'string'},
    'findings.qa_rail_gpu_busy_skip.min_severity': {'value_type': 'string'},
    'findings.quality_regression.cooldown_minutes': {'value_type': 'integer'},
    'findings.quality_regression.delivery': {'value_type': 'string'},
    'findings.quality_regression.fallback': {'value_type': 'string'},
    'findings.quality_regression.labels': {'value_type': 'string'},
    'findings.quality_regression.min_severity': {'value_type': 'string'},
    'findings.r2_static_drift.cooldown_minutes': {'value_type': 'integer'},
    'findings.r2_static_drift.delivery': {'value_type': 'string'},
    'findings.r2_static_drift.fallback': {'value_type': 'string'},
    'findings.r2_static_drift.min_severity': {'value_type': 'string'},
    'findings.seo_refresh_gate_expired.cooldown_minutes': {'value_type': 'integer'},
    'findings.seo_refresh_gate_expired.delivery': {'value_type': 'string'},
    'findings.seo_refresh_gate_expired.fallback': {'value_type': 'string'},
    'findings.seo_refresh_gate_expired.min_severity': {'value_type': 'string'},
    'findings.seo_refresh_outcome.cooldown_minutes': {'value_type': 'integer'},
    'findings.seo_refresh_outcome.delivery': {'value_type': 'string'},
    'findings.seo_refresh_outcome.fallback': {'value_type': 'string'},
    'findings.seo_refresh_outcome.min_severity': {'value_type': 'string'},
    'findings.seo_refresh_queued.cooldown_minutes': {'value_type': 'integer'},
    'findings.seo_refresh_queued.delivery': {'value_type': 'string'},
    'findings.seo_refresh_queued.fallback': {'value_type': 'string'},
    'findings.seo_refresh_queued.min_severity': {'value_type': 'string'},
    'findings.settings_zero_reader_keys.cooldown_minutes': {'value_type': 'integer'},
    'findings.settings_zero_reader_keys.delivery': {'value_type': 'string'},
    'findings.settings_zero_reader_keys.fallback': {'value_type': 'string'},
    'findings.settings_zero_reader_keys.min_severity': {'value_type': 'string'},
    'findings.stage_failure_swallowed.cooldown_minutes': {'value_type': 'integer'},
    'findings.stage_failure_swallowed.delivery': {'value_type': 'string'},
    'findings.stage_failure_swallowed.fallback': {'value_type': 'string'},
    'findings.stage_failure_swallowed.min_severity': {'value_type': 'string'},
    'findings.stale_task_reclaimed.cooldown_minutes': {'value_type': 'integer'},
    'findings.stale_task_reclaimed.delivery': {'value_type': 'string'},
    'findings.stale_task_reclaimed.fallback': {'value_type': 'string'},
    'findings.stale_task_reclaimed.min_severity': {'value_type': 'string'},
    'findings.stock_image_regenerated.delivery': {'value_type': 'string'},
    'findings.task_retries_exhausted.cooldown_minutes': {'value_type': 'integer'},
    'findings.task_retries_exhausted.delivery': {'value_type': 'string'},
    'findings.task_retries_exhausted.fallback': {'value_type': 'string'},
    'findings.task_retries_exhausted.min_severity': {'value_type': 'string'},
    'findings.topic_batch_stuck.cooldown_minutes': {'value_type': 'integer'},
    'findings.topic_batch_stuck.delivery': {'value_type': 'string'},
    'findings.topic_batch_stuck.fallback': {'value_type': 'string'},
    'findings.topic_batch_stuck.min_severity': {'value_type': 'string'},
    'findings.topic_gap.cooldown_minutes': {'value_type': 'integer'},
    'findings.topic_gap.delivery': {'value_type': 'string'},
    'findings.topic_gap.fallback': {'value_type': 'string'},
    'findings.topic_rank_degraded.cooldown_minutes': {'value_type': 'integer'},
    'findings.topic_rank_degraded.delivery': {'value_type': 'string'},
    'findings.topic_rank_degraded.fallback': {'value_type': 'string'},
    'findings.topic_rank_degraded.min_severity': {'value_type': 'string'},
    'findings.topic_sanity_rejected.cooldown_minutes': {'value_type': 'integer'},
    'findings.topic_sanity_rejected.delivery': {'value_type': 'string'},
    'findings.topic_sanity_rejected.fallback': {'value_type': 'string'},
    'findings.topic_sanity_rejected.min_severity': {'value_type': 'string'},
    'findings.topic_self_referential.cooldown_minutes': {'value_type': 'integer'},
    'findings.topic_self_referential.delivery': {'value_type': 'string'},
    'findings.topic_self_referential.fallback': {'value_type': 'string'},
    'findings.topic_self_referential.min_severity': {'value_type': 'string'},
    'findings.uncategorized_post_autofixed.delivery': {'value_type': 'string'},
    'findings.vision_scorer_unavailable.cooldown_minutes': {'value_type': 'integer'},
    'findings.vision_scorer_unavailable.delivery': {'value_type': 'string'},
    'findings.vision_scorer_unavailable.fallback': {'value_type': 'string'},
    'findings.vision_scorer_unavailable.min_severity': {'value_type': 'string'},
    'findings.vram_reclaim_ineffective.cooldown_minutes': {'value_type': 'integer'},
    'findings.vram_reclaim_ineffective.delivery': {'value_type': 'string'},
    'findings.vram_reclaim_ineffective.fallback': {'value_type': 'string'},
    'findings.vram_reclaim_ineffective.min_severity': {'value_type': 'string'},
    'findings_daily_digest_enabled': {'owner': 'findings_daily_digest', 'value_type': 'boolean'},
    'findings_daily_digest_lookback_hours': {'owner': 'findings_daily_digest', 'value_type': 'integer'},
    'findings_daily_digest_top_n': {'owner': 'findings_daily_digest', 'value_type': 'integer'},
    'flux_schnell_server_url': {'owner': 'flux_schnell'},
    'gate_resume_timeout_seconds': {'owner': 'gate_resume', 'value_type': 'integer'},
    'glitchtip_triage_org_slug': {'owner': 'glitchtip_triage_probe', 'value_type': 'string'},
    'google_sitemap_ping_url': {'owner': 'publish_service', 'value_type': 'url'},
    'gpu0_headroom_gb': {'owner': 'gpu_scheduler', 'value_type': 'integer'},
    'gpu_evictable_process_pattern': {'owner': 'gpu_registry', 'value_type': 'csv'},
    'gpu_evictable_unattributed_tolerance_gb': {'owner': 'gpu_registry', 'value_type': 'float'},
    'gpu_external_workload_wait_enabled': {'owner': 'gpu_scheduler', 'value_type': 'boolean'},
    'gpu_lock_acquire_timeout_seconds': {'owner': 'gpu_scheduler', 'value_type': 'integer'},
    'gpu_lock_release_timeout_seconds': {'owner': 'gpu_scheduler', 'value_type': 'integer'},
    'gpu_metrics_prometheus_url': {'value_type': 'url'},
    'gpu_model': {'owner': 'startup_manager'},
    'gpu_pinned_endpoint_skips_lock': {'owner': 'dispatcher', 'value_type': 'boolean'},
    'gpu_sched_aging_seconds': {'owner': 'gpu_scheduler', 'value_type': 'integer'},
    'gpu_sched_enabled': {'owner': 'gpu_scheduler', 'value_type': 'boolean'},
    'gpu_sched_eta_fallback_seconds': {'owner': 'gpu_scheduler', 'value_type': 'integer'},
    'gpu_sched_media_max_wait_s': {'owner': 'gpu_scheduler', 'value_type': 'integer'},
    'gpu_sched_qa_rail_max_wait_s': {'owner': 'gpu_scheduler', 'value_type': 'integer'},
    'gpu_serialize_llm_dispatch': {'owner': 'dispatcher', 'value_type': 'boolean'},
    'idle_wsl_reset_cooldown_hours': {'value_type': 'integer'},
    'idle_wsl_reset_enabled': {'value_type': 'boolean'},
    'idle_wsl_reset_inflight_grace_minutes': {'value_type': 'integer'},
    'idle_wsl_reset_min_idle_minutes': {'value_type': 'integer'},
    'idle_wsl_reset_trigger_free_vram_gb': {'value_type': 'integer'},
    'image_aspect_ratio': {'owner': 'image_service', 'value_type': 'string'},
    'image_base_style_prompt': {'owner': '_image_helpers'},
    'image_featured_stage_overhead_seconds': {'owner': 'source_featured_image', 'value_type': 'integer'},
    'image_gen_enabled': {'owner': 'source_featured_image', 'value_type': 'boolean'},
    'image_gen_hard_unload_min_reserved_mb': {'value_type': 'integer'},
    'image_gen_render_attempts': {'value_type': 'integer'},
    'image_gen_retry_backoff_seconds': {'value_type': 'integer'},
    'image_gen_server_url': {'value_type': 'url'},
    'image_model': {'value_type': 'model'},
    'image_negative_prompt': {'value_type': 'string'},
    'image_ocr_gate_enabled': {'value_type': 'boolean'},
    'image_ocr_gate_max_attempts': {'value_type': 'integer'},
    'image_ocr_gate_max_chars': {'value_type': 'integer'},
    'image_ocr_gate_min_confidence': {'value_type': 'float'},
    'image_pexels_fallback_keywords': {'owner': 'image_service'},
    'image_prompt_max_tokens': {'value_type': 'integer'},
    'image_prompt_model': {'owner': 'ai_generation', 'value_type': 'model'},
    'image_prompt_temperature': {'value_type': 'float'},
    'image_prompt_timeout_seconds': {'value_type': 'integer'},
    'image_render_timeout_seconds': {'value_type': 'integer'},
    'image_search_query_model': {'owner': 'pexels', 'value_type': 'model'},
    'image_stock_fallback_enabled': {'value_type': 'boolean'},
    'image_style_dedup_window': {'owner': 'source_featured_image', 'value_type': 'integer'},
    'image_style_history_size': {'value_type': 'integer'},
    'image_style_history_ttl_seconds': {'value_type': 'integer'},
    'image_styles': {'owner': 'source_featured_image', 'value_type': 'json'},
    'indexnow_key': {'owner': 'publish_service'},
    'indexnow_ping_url': {'owner': 'publish_service', 'value_type': 'url'},
    'inline_image_styles': {'owner': '_image_helpers'},
    'langfuse_host': {'value_type': 'url'},
    'langfuse_prompt_mirror_enabled': {'owner': 'sync_prompt_catalog_to_langfuse', 'value_type': 'boolean'},
    'langfuse_prompt_overrides_enabled': {'owner': 'prompt_manager', 'value_type': 'boolean'},
    'langfuse_public_url': {'owner': 'traces_routes', 'value_type': 'url'},
    'link_check_skip_status_codes': {'owner': 'check_published_links', 'value_type': 'string'},
    'live_activity_freshness_seconds': {'owner': 'activity_routes', 'value_type': 'integer'},
    'live_activity_heartbeat_seconds': {'value_type': 'integer'},
    'live_activity_reaper_seconds': {'owner': 'reap_stale_activity', 'value_type': 'integer'},
    'live_activity_recent_limit': {'owner': 'activity_routes', 'value_type': 'integer'},
    'local_llm_api_url': {'value_type': 'url'},
    'max_approval_queue': {'owner': 'pipeline_throttle', 'value_type': 'integer'},
    'max_log_backup_count': {'value_type': 'integer'},
    'max_log_size_mb': {'value_type': 'integer'},
    'mcp_http_probe_min_consecutive_failures': {'owner': 'mcp_http_probe', 'value_type': 'integer'},
    'media.caption.fidelity_min_ratio': {'owner': 'media_transcribe_narration', 'value_type': 'float'},
    'media.cta.podcast': {'value_type': 'string'},
    'media.cta.video': {'value_type': 'string'},
    'media.cta.video_short': {'value_type': 'string'},
    'media.gate2.earned_autonomy_enabled': {'owner': 'media_approval_service', 'value_type': 'boolean'},
    'media.gate2.earned_autonomy_min_dispatches': {'owner': 'media_approval_service', 'value_type': 'integer'},
    'media.layer2.enabled': {'owner': 'media_quality_service', 'value_type': 'boolean'},
    'media.podcast.faithfulness_min': {'owner': 'media_quality_service', 'value_type': 'integer'},
    'media.podcast.faithfulness_model': {'owner': 'media_quality_service'},
    'media.qa.av_sync_tolerance_s': {'owner': 'media_qa', 'value_type': 'float'},
    'media.video.shot_fidelity_min': {'owner': 'media_quality_service', 'value_type': 'integer'},
    'media.video.topic_match_frames': {'owner': 'media_quality_service', 'value_type': 'integer'},
    'media.video.topic_match_min': {'owner': 'media_quality_service', 'value_type': 'integer'},
    'media.video.topic_match_model': {'owner': 'media_quality_service'},
    'media_distribute_max_per_cycle': {'owner': 'media_distribute', 'value_type': 'integer'},
    'media_feed_reconcile_max_shrink': {'owner': 'media_feed_rebuild', 'value_type': 'integer'},
    'media_feed_reconciliation_enabled': {'owner': 'media_feed_reconciliation', 'value_type': 'boolean'},
    'media_pipeline_max_per_cycle': {'owner': 'dispatch_media_pipeline', 'value_type': 'integer'},
    'media_pipeline_redispatch_max': {'owner': 'media_reconciliation', 'value_type': 'integer'},
    'media_qa_frame_detection_enabled': {'owner': 'media_qa', 'value_type': 'boolean'},
    'media_render_min_free_vram_gb': {'owner': 'media_infra_health', 'value_type': 'integer'},
    'media_render_reclaim_cooldown_minutes': {'owner': 'dispatch_media_pipeline', 'value_type': 'integer'},
    'media_render_reclaim_enabled': {'owner': 'dispatch_media_pipeline', 'value_type': 'boolean'},
    'media_render_reclaim_settle_seconds': {'owner': 'dispatch_media_pipeline', 'value_type': 'integer'},
    'media_render_vram_gate_enabled': {'owner': 'media_infra_health', 'value_type': 'boolean'},
    'media_upload_delay_seconds': {'owner': 'publish_service', 'value_type': 'integer'},
    'migration_drift_deploy_checkout_path': {'owner': 'migration_drift_probe', 'value_type': 'string'},
    'migration_drift_max_inflight_defers': {'owner': 'migration_drift_probe', 'value_type': 'integer'},
    'migration_drift_recover_max_attempts': {'owner': 'migration_drift_probe', 'value_type': 'integer'},
    'newsletter_batch_delay_seconds': {'owner': 'newsletter_service', 'value_type': 'integer'},
    'newsletter_batch_size': {'owner': 'newsletter_service', 'value_type': 'integer'},
    'newsletter_enabled': {'value_type': 'boolean'},
    'newsletter_from_name': {'owner': 'newsletter_service'},
    'newsletter_provider': {'owner': 'newsletter_service', 'value_type': 'string'},
    'niche_batch_expires_days': {'owner': 'topic_batch_service', 'value_type': 'integer'},
    'niche_carry_forward_decay_factor': {'owner': 'topic_batch_service', 'value_type': 'float'},
    'niche_goal_descriptions': {'owner': 'topic_ranking', 'value_type': 'json'},
    'niche_internal_rag_batch_share_cap': {'owner': 'topic_batch_service', 'value_type': 'float'},
    'niche_internal_rag_per_kind_limit': {'owner': 'internal_rag_source', 'value_type': 'integer'},
    'niche_internal_rag_snippet_max_chars': {'owner': 'internal_rag_source', 'value_type': 'integer'},
    'niche_ollama_chat_timeout_seconds': {'value_type': 'float'},
    'niche_pool_read_per_source_limit': {'owner': 'topic_batch_service', 'value_type': 'integer'},
    'niche_top_n_per_pool': {'owner': 'topic_batch_service', 'value_type': 'integer'},
    'oauth_issuer_url': {'owner': 'oauth_routes'},
    'offsite_backup_enabled': {'value_type': 'boolean'},
    'offsite_backup_interval': {'value_type': 'string'},
    'offsite_backup_keep_daily': {'value_type': 'integer'},
    'offsite_backup_keep_monthly': {'value_type': 'integer'},
    'offsite_backup_keep_weekly': {'value_type': 'integer'},
    'offsite_backup_max_age_hours': {'owner': 'offsite_backup_watch', 'value_type': 'integer'},
    'offsite_backup_prune_enabled': {'value_type': 'boolean'},
    'offsite_backup_repository': {'owner': 'backup'},
    'offsite_backup_restic_host': {'value_type': 'string'},
    'offsite_backup_restic_image': {'owner': 'backup', 'value_type': 'string'},
    'offsite_backup_s3_region': {'owner': 'backup'},
    'offsite_backup_source_tier': {'owner': 'backup', 'value_type': 'string'},
    'offsite_backup_verify_enabled': {'value_type': 'boolean'},
    'offsite_backup_verify_interval_hours': {'value_type': 'integer'},
    'offsite_backup_verify_read_data_subset_percent': {'owner': 'backup', 'value_type': 'integer'},
    'offsite_backup_watch_enabled': {'owner': 'offsite_backup_watch', 'value_type': 'boolean'},
    'offsite_backup_watch_max_retries': {'owner': 'offsite_backup_watch', 'value_type': 'integer'},
    'offsite_backup_watch_retry_delay_seconds': {'owner': 'offsite_backup_watch', 'value_type': 'integer'},
    'ollama_base_url': {'value_type': 'url'},
    'ollama_model_validation_enabled': {'owner': 'startup_manager', 'value_type': 'boolean'},
    'ollama_model_validation_skip_keys': {'owner': 'startup_manager'},
    'operator_page_cooldown_minutes': {'owner': 'brain_daemon', 'value_type': 'integer'},
    'operator_timezone': {'value_type': 'string'},
    'ops_firefighter_action_allowlist': {'owner': 'rules'},
    'ops_firefighter_enabled': {'value_type': 'boolean'},
    'ops_firefighter_llm_exclude_regex': {'owner': 'rules', 'value_type': 'string'},
    'ops_firefighter_llm_longtail_enabled': {'owner': 'rules', 'value_type': 'boolean'},
    'ops_firefighter_max_actions_per_hour': {'owner': 'rules', 'value_type': 'integer'},
    'ops_firefighter_max_attempts_per_window': {'owner': 'rules', 'value_type': 'integer'},
    'ops_firefighter_min_age_minutes': {'owner': 'rules', 'value_type': 'integer'},
    'ops_firefighter_min_confidence': {'owner': 'rules', 'value_type': 'float'},
    'ops_firefighter_min_repeats': {'owner': 'rules', 'value_type': 'integer'},
    'ops_firefighter_model': {'value_type': 'model'},
    'ops_firefighter_verify_after_seconds': {'owner': 'rules', 'value_type': 'integer'},
    'ops_firefighter_window_minutes': {'owner': 'rules', 'value_type': 'integer'},
    'ops_triage_writer_model': {'owner': 'triage_routes', 'value_type': 'model'},
    'otel_exporter_otlp_endpoint': {'owner': 'telemetry', 'value_type': 'url'},
    'owner_name': {'owner': 'podcast_routes'},
    'pexels_api_base': {'value_type': 'url'},
    'pipeline_architect_model': {'owner': 'pipeline_architect', 'value_type': 'model'},
    'pipeline_architect_timeout_seconds': {'owner': 'pipeline_architect', 'value_type': 'float'},
    'pipeline_event_stream_types': {'owner': 'pipeline_events_routes'},
    'pipeline_streaming_channel': {'owner': 'pipeline_streaming', 'value_type': 'string'},
    'pipeline_streaming_min_edit_interval_s': {'owner': 'pipeline_streaming', 'value_type': 'integer'},
    'pipeline_title_model': {'owner': 'title_generation'},
    'pipeline_writer_unload_before_image_gen': {'owner': 'ollama_unload', 'value_type': 'boolean'},
    'pipeline_writer_unload_confirm_enabled': {'value_type': 'boolean'},
    'pipeline_writer_unload_confirm_timeout_seconds': {'value_type': 'integer'},
    'pipeline_writer_unload_grace_seconds': {'owner': 'ollama_unload', 'value_type': 'integer'},
    'pipeline_writer_unload_poll_interval_seconds': {'value_type': 'float'},
    'plugin.image_provider.flux_schnell.server_url': {'owner': 'flux_schnell'},
    'plugin.job.sync_affiliate_clicks.enabled': {'value_type': 'boolean'},
    'plugin.job.sync_affiliate_clicks.interval_seconds': {'value_type': 'integer'},
    'plugin.llm_provider.gemini.enabled': {'owner': 'gemini', 'value_type': 'boolean'},
    'plugin.tts_provider.chatterbox.atempo': {'owner': 'podcast_service', 'value_type': 'float'},
    'plugin.tts_provider.chatterbox.audio_prompt_path': {'owner': 'podcast_service'},
    'plugin.tts_provider.chatterbox.base_url': {'value_type': 'url'},
    'plugin.tts_provider.chatterbox.cfg_weight': {'owner': 'podcast_service', 'value_type': 'float'},
    'plugin.tts_provider.chatterbox.exaggeration': {'owner': 'podcast_service', 'value_type': 'float'},
    'plugin.tts_provider.chatterbox.model': {'owner': 'podcast_service', 'value_type': 'string'},
    'plugin.tts_provider.chatterbox.timeout_s': {'owner': 'podcast_service', 'value_type': 'integer'},
    'podcast_cover_url': {'owner': 'podcast_routes'},
    'podcast_description': {'owner': 'podcast_routes'},
    'podcast_disable_thinking': {'owner': 'podcast_service', 'value_type': 'boolean'},
    'podcast_distribute_max_per_cycle': {'owner': 'podcast_distribute', 'value_type': 'integer'},
    'podcast_pipeline_max_per_cycle': {'owner': 'dispatch_podcast_pipeline', 'value_type': 'integer'},
    'podcast_redispatch_max': {'owner': 'media_reconciliation', 'value_type': 'integer'},
    'podcast_script_model': {'owner': 'podcast_service', 'value_type': 'model'},
    'podcast_tts_base_url': {'owner': 'tts_service', 'value_type': 'url'},
    'podcast_tts_enabled': {'value_type': 'boolean'},
    'podcast_tts_engine': {'owner': 'podcast_service'},
    'podcast_tts_format': {'value_type': 'string'},
    'podcast_tts_loudnorm_ar': {'value_type': 'integer'},
    'podcast_tts_loudnorm_enabled': {'value_type': 'boolean'},
    'podcast_tts_loudnorm_i': {'value_type': 'integer'},
    'podcast_tts_loudnorm_lra': {'value_type': 'integer'},
    'podcast_tts_loudnorm_tp': {'value_type': 'float'},
    'podcast_tts_model': {'value_type': 'model'},
    'podcast_tts_remux_bitrate': {'value_type': 'string'},
    'podcast_tts_remux_enabled': {'owner': 'tts_service', 'value_type': 'boolean'},
    'podcast_tts_remux_mode': {'owner': 'tts_service', 'value_type': 'string'},
    'podcast_tts_voice': {'value_type': 'string'},
    'post_edit_image_timeout_s': {'owner': 'tasks', 'value_type': 'integer'},
    'post_edit_rebuild_images_timeout_s': {'owner': 'tasks', 'value_type': 'integer'},
    'post_edit_regen_image_timeout_s': {'owner': 'tasks', 'value_type': 'integer'},
    'postiz_api_url': {'value_type': 'url'},
    'postiz_integration_id_bluesky': {'owner': 'social_drafts'},
    'postiz_integration_id_linkedin': {'owner': 'social_drafts'},
    'postiz_integration_id_mastodon': {'owner': 'social_drafts'},
    'postiz_integration_id_reddit': {'owner': 'social_drafts'},
    'postiz_queue_overdue_minutes': {'owner': 'postiz_queue_watch', 'value_type': 'integer'},
    'postiz_queue_watch_enabled': {'owner': 'postiz_queue_watch', 'value_type': 'boolean'},
    'postiz_queue_watch_max_retries': {'owner': 'postiz_queue_watch', 'value_type': 'integer'},
    'postiz_queue_watch_retry_delay_seconds': {'owner': 'postiz_queue_watch', 'value_type': 'integer'},
    'prefect_content_flow_concurrency': {'owner': 'deploy_content_flow', 'value_type': 'integer'},
    'prefect_stuck_flow_cancelling_threshold_minutes': {'owner': 'prefect_stuck_flow_probe', 'value_type': 'integer'},
    'prefect_stuck_flow_progress_stall_minutes': {'owner': 'prefect_stuck_flow_probe', 'value_type': 'integer'},
    'prefect_stuck_flow_queue_depth_threshold': {'owner': 'prefect_stuck_flow_probe', 'value_type': 'integer'},
    'prefect_stuck_flow_queue_reap_minutes': {'owner': 'prefect_stuck_flow_probe', 'value_type': 'integer'},
    'preferred_ollama_model': {'value_type': 'model'},
    'publish_quiet_hours': {'owner': 'scheduling_service'},
    'pyroscope_server_url': {'value_type': 'url'},
    'qa_accuracy_bad_link_max_penalty': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_accuracy_bad_link_penalty': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_accuracy_baseline': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_accuracy_citation_bonus': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_accuracy_first_person_max_penalty': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_accuracy_first_person_penalty': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_accuracy_good_link_bonus': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_accuracy_good_link_max_bonus': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_accuracy_meta_commentary_max_penalty': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_accuracy_meta_commentary_penalty': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_allow_first_person_niches': {'owner': 'validator_config', 'value_type': 'csv'},
    'qa_artifact_penalty_max': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_artifact_penalty_per': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_clarity_good_max_wps': {'owner': 'quality_scorers', 'value_type': 'integer'},
    'qa_clarity_good_min_wps': {'owner': 'quality_scorers', 'value_type': 'integer'},
    'qa_clarity_ideal_max_wps': {'owner': 'quality_scorers', 'value_type': 'integer'},
    'qa_clarity_ideal_min_wps': {'owner': 'quality_scorers', 'value_type': 'integer'},
    'qa_clarity_ok_max_wps': {'owner': 'quality_scorers', 'value_type': 'integer'},
    'qa_clarity_ok_min_wps': {'owner': 'quality_scorers', 'value_type': 'integer'},
    'qa_completeness_heading_bonus': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_completeness_heading_max_bonus': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_completeness_truncation_penalty': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_completeness_word_1000_score': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_completeness_word_1500_score': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_completeness_word_2000_score': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_completeness_word_500_score': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_completeness_word_min_score': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_engagement_baseline': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_fk_target_max': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_fk_target_min': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_llm_buzzword_fail_threshold': {'owner': 'quality_service', 'value_type': 'integer'},
    'qa_llm_buzzword_max_penalty': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_buzzword_penalty_per': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_buzzword_warn_max_penalty': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_buzzword_warn_penalty_per': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_buzzword_warn_threshold': {'owner': 'quality_service', 'value_type': 'integer'},
    'qa_llm_exclamation_max_penalty': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_exclamation_penalty_per': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_exclamation_threshold': {'owner': 'quality_service', 'value_type': 'integer'},
    'qa_llm_filler_fail_threshold': {'owner': 'quality_service', 'value_type': 'integer'},
    'qa_llm_filler_max_penalty': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_filler_penalty_per': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_filler_warn_penalty_per': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_filler_warn_threshold': {'owner': 'quality_service', 'value_type': 'integer'},
    'qa_llm_formulaic_min_avg_words': {'owner': 'quality_service', 'value_type': 'integer'},
    'qa_llm_formulaic_structure_penalty': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_formulaic_variance': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_hedge_penalty': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_hedge_ratio_threshold': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_listicle_title_penalty': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_opener_penalty': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_patterns_enabled': {'owner': 'quality_service', 'value_type': 'boolean'},
    'qa_llm_repetitive_min_count': {'owner': 'quality_service', 'value_type': 'integer'},
    'qa_llm_repetitive_starter_max_penalty': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_repetitive_starter_penalty_per': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_llm_transition_min_count': {'owner': 'quality_service', 'value_type': 'integer'},
    'qa_llm_transition_penalty_per': {'owner': 'quality_service', 'value_type': 'float'},
    'qa_relevance_high_coverage_score': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_relevance_low_coverage_score': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_relevance_med_coverage_score': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_relevance_no_topic_default': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_relevance_none_coverage_score': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_relevance_stuffing_hard_density': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_relevance_stuffing_soft_density': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_rewrite_model': {'owner': 'qa_rewrite'},
    'qa_seo_baseline': {'owner': 'quality_scorers', 'value_type': 'float'},
    'qa_title_originality_enabled': {'owner': 'title_generation', 'value_type': 'boolean'},
    'qa_title_similarity_threshold': {'owner': 'title_generation', 'value_type': 'float'},
    'qa_topic_dedup_hours': {'value_type': 'integer'},
    'qa_validator_warning_penalty': {'owner': 'qa_programmatic', 'value_type': 'float'},
    'qa_web_factcheck_match_ratio': {'owner': 'multi_model_qa', 'value_type': 'float'},
    'qa_web_factcheck_max_claims': {'owner': 'multi_model_qa', 'value_type': 'integer'},
    'qa_web_factcheck_min_term_len': {'owner': 'multi_model_qa', 'value_type': 'integer'},
    'qa_web_factcheck_num_results': {'owner': 'multi_model_qa', 'value_type': 'integer'},
    'qa_web_factcheck_snippet_chars': {'owner': 'multi_model_qa', 'value_type': 'integer'},
    'rag_default_top_k': {'owner': 'rag_engine', 'value_type': 'integer'},
    'rag_embed_retry_attempts': {'owner': 'rag_engine', 'value_type': 'integer'},
    'rag_embed_retry_base_delay_seconds': {'owner': 'rag_engine', 'value_type': 'float'},
    'rag_min_similarity': {'owner': 'rag_engine', 'value_type': 'float'},
    'rag_rrf_k': {'owner': 'rag_engine', 'value_type': 'integer'},
    'ragas_judge_model': {'value_type': 'model'},
    'rate_limit_podcast_generate_per_ip': {'owner': 'podcast_routes', 'value_type': 'string'},
    'rate_limit_remediation_select_per_ip': {'owner': 'remediation_routes', 'value_type': 'string'},
    'rate_limit_token_per_ip': {'value_type': 'string'},
    'rate_limit_topics_from_url_per_ip': {'owner': 'topics_routes', 'value_type': 'string'},
    'rate_limit_triage_per_ip': {'owner': 'triage_routes', 'value_type': 'string'},
    'rate_limit_video_generate_per_ip': {'value_type': 'string'},
    'research_extract_web_content': {'owner': 'research_service', 'value_type': 'boolean'},
    'research_web_content_chars_per_source': {'owner': 'research_service', 'value_type': 'integer'},
    'router_feedback_alpha': {'owner': 'router_outcome_feedback', 'value_type': 'float'},
    'scheduled_publisher_poll_seconds': {'owner': 'scheduled_publisher', 'value_type': 'integer'},
    'scheduled_tasks_probe_watch_tasks': {'owner': 'health_probes'},
    'scheduler_alert_on_job_failure': {'owner': 'scheduler', 'value_type': 'boolean'},
    'scheduler_circular_job_page_threshold': {'owner': 'scheduler', 'value_type': 'integer'},
    'scheduler_job_metrics_capture_enabled': {'owner': 'scheduler', 'value_type': 'boolean'},
    'self_consistency_enabled': {'value_type': 'boolean'},
    'self_consistency_sample_count': {'owner': 'self_consistency_rail', 'value_type': 'integer'},
    'self_consistency_threshold': {'owner': 'self_consistency_rail', 'value_type': 'float'},
    'sentry_enabled': {'owner': 'sentry_integration', 'value_type': 'boolean'},
    'seo.harvest.analyzer_enabled': {'owner': 'run_seo_opportunity_analyzer', 'value_type': 'boolean'},
    'seo.low_ctr.max_ctr': {'owner': 'run_seo_opportunity_analyzer', 'value_type': 'float'},
    'seo.low_ctr.min_impressions': {'owner': 'run_seo_opportunity_analyzer', 'value_type': 'integer'},
    'seo.opportunity.target_ctr': {'owner': 'run_seo_opportunity_analyzer', 'value_type': 'float'},
    'seo.push_candidate.min_impressions': {'owner': 'run_seo_opportunity_analyzer', 'value_type': 'integer'},
    'seo.push_candidate.position_max': {'owner': 'run_seo_opportunity_analyzer', 'value_type': 'integer'},
    'seo.push_candidate.position_min': {'owner': 'run_seo_opportunity_analyzer', 'value_type': 'integer'},
    'seo.query_ingestion.enabled': {'owner': 'gsc_query_gap', 'value_type': 'boolean'},
    'seo.refresh.enabled': {'owner': 'enqueue_seo_refreshes', 'value_type': 'boolean'},
    'seo.refresh.gate_max_parked_days': {'owner': 'expire_stale_seo_refresh_gates', 'value_type': 'integer'},
    'seo.refresh.max_per_run': {'owner': 'enqueue_seo_refreshes', 'value_type': 'integer'},
    'seo.refresh.min_impressions': {'owner': 'enqueue_seo_refreshes', 'value_type': 'integer'},
    'seo.refresh.outcome_measure_after_days': {'owner': 'measure_seo_refresh_outcomes', 'value_type': 'integer'},
    'seo.refresh.scope': {'value_type': 'string'},
    'seo.striking_distance.min_impressions': {'owner': 'run_seo_opportunity_analyzer', 'value_type': 'integer'},
    'seo.striking_distance.position_max': {'owner': 'run_seo_opportunity_analyzer', 'value_type': 'integer'},
    'seo.striking_distance.position_min': {'owner': 'run_seo_opportunity_analyzer', 'value_type': 'integer'},
    'settings_read_telemetry_enabled': {'owner': 'flush_settings_read_telemetry', 'value_type': 'boolean'},
    'settings_read_telemetry_min_restamp_seconds': {'owner': 'flush_settings_read_telemetry', 'value_type': 'integer'},
    'settings_zero_reader_grace_days': {'owner': 'probe_zero_reader_settings', 'value_type': 'integer'},
    'settings_zero_reader_max_report': {'owner': 'probe_zero_reader_settings', 'value_type': 'integer'},
    'settings_zero_reader_probe_enabled': {'owner': 'probe_zero_reader_settings', 'value_type': 'boolean'},
    'shared_http_client_max_connections': {'value_type': 'integer'},
    'shared_http_client_max_keepalive': {'value_type': 'integer'},
    'shared_http_client_timeout_seconds': {'value_type': 'float'},
    'smtp_host': {'owner': 'newsletter_service'},
    'smtp_port': {'owner': 'newsletter_service', 'value_type': 'integer'},
    'smtp_use_tls': {'owner': 'newsletter_service', 'value_type': 'boolean'},
    'social_draft_backfill_lookback_days': {'owner': 'backfill_missing_social_drafts', 'value_type': 'integer'},
    'social_draft_max_retries': {'owner': 'retry_failed_social_drafts', 'value_type': 'integer'},
    'social_draft_platforms': {'owner': 'social_generate_drafts'},
    'social_drafts_enabled': {'value_type': 'boolean'},
    'social_reddit_subreddits': {'owner': 'social_generate_drafts'},
    'social_x_made_with_ai': {'owner': 'social_drafts', 'value_type': 'boolean'},
    'stable_audio_open_server_url': {'owner': 'stable_audio_open'},
    'storage_image_custom_domain': {'owner': 'r2_upload_service'},
    'storage_image_max_height': {'owner': 'r2_upload_service', 'value_type': 'integer'},
    'storage_image_max_width': {'owner': 'r2_upload_service', 'value_type': 'integer'},
    'tap_chunk_max_chars': {'owner': 'runner', 'value_type': 'integer'},
    'tap_dedup_batch_size': {'owner': 'runner', 'value_type': 'integer'},
    'tap_run_timeout_seconds': {'owner': 'runner', 'value_type': 'integer'},
    'template_runner_progress_streaming': {'owner': 'template_runner', 'value_type': 'boolean'},
    'title_junk_regen_max_retries': {'owner': 'title_generation', 'value_type': 'integer'},
    'title_max_length': {'owner': 'title_generation', 'value_type': 'integer'},
    'title_originality_cache_ttl_hours': {'owner': 'title_originality_external', 'value_type': 'integer'},
    'title_originality_external_check_enabled': {'owner': 'title_originality_external', 'value_type': 'boolean'},
    'title_originality_external_penalty': {'owner': 'title_originality_external', 'value_type': 'integer'},
    'topic_batch_reaper_enabled': {'owner': 'reap_stale_topic_batches', 'value_type': 'boolean'},
    'topic_batch_stuck_hours': {'owner': 'reap_stale_topic_batches', 'value_type': 'integer'},
    'topic_dedup_device': {'owner': 'topic_dedup_semantic', 'value_type': 'string'},
    'topic_dedup_engine': {'owner': 'topic_dedup_semantic', 'value_type': 'string'},
    'topic_dedup_existing_threshold': {'owner': 'topic_dedup', 'value_type': 'float'},
    'topic_dedup_existing_threshold_content': {'owner': 'topic_dedup_content', 'value_type': 'float'},
    'topic_dedup_intra_batch_threshold': {'owner': 'topic_dedup', 'value_type': 'float'},
    'topic_discovery_length_distribution': {'owner': 'topic_length'},
    'topic_recent_coverage_enabled': {'owner': 'topic_recent_coverage', 'value_type': 'boolean'},
    'topic_recent_coverage_lookback_days': {'owner': 'topic_recent_coverage', 'value_type': 'integer'},
    'topic_recent_coverage_threshold': {'owner': 'topic_recent_coverage', 'value_type': 'float'},
    'topic_sanity_min_alpha_words': {'owner': 'topic_sanity', 'value_type': 'integer'},
    'topic_source_excluded_domains': {'owner': 'topic_self_reference'},
    'trace_recent_limit': {'owner': 'trace_routes', 'value_type': 'integer'},
    'tts_acronym_replacements': {'owner': 'podcast_service', 'value_type': 'json'},
    'tts_domain_tld_pronunciations': {'owner': 'podcast_service', 'value_type': 'json'},
    'tts_model_name_families': {'owner': 'podcast_service', 'value_type': 'csv'},
    'tts_pronunciations': {'owner': 'podcast_service', 'value_type': 'json'},
    'tts_voice_pool': {'owner': 'podcast_service'},
    'tts_voice_rotation_enabled': {'owner': 'podcast_service', 'value_type': 'boolean'},
    'unlinked_attribution_enabled': {'owner': 'qa_unlinked_attribution', 'value_type': 'boolean'},
    'unlinked_attribution_penalty_per': {'owner': 'qa_unlinked_attribution', 'value_type': 'integer'},
    'unlinked_attribution_score_floor': {'owner': 'qa_unlinked_attribution', 'value_type': 'integer'},
    'url_scraper_max_content_chars': {'owner': 'url_scraper', 'value_type': 'integer'},
    'url_scraper_timeout_seconds': {'owner': 'url_scraper', 'value_type': 'integer'},
    'use_ollama': {'owner': 'ai_content_generator', 'value_type': 'boolean'},
    'video_feed_name': {'owner': 'video_routes'},
    'video_hero_unload_image_gen': {'owner': 'shot_list_renderer', 'value_type': 'boolean'},
    'video_hero_unload_settle_seconds': {'owner': 'shot_list_renderer', 'value_type': 'integer'},
    'video_negative_prompt': {'owner': 'wan2_1', 'value_type': 'string'},
    'voice_agent_brain': {'value_type': 'string'},
    'voice_agent_brain_mode': {'owner': 'voice_agent_livekit', 'value_type': 'string'},
    'voice_agent_default_identity': {'owner': 'voice_routes', 'value_type': 'string'},
    'voice_agent_identity': {'owner': 'voice_agent_livekit', 'value_type': 'string'},
    'voice_agent_livekit_enabled': {'owner': 'voice_agent_livekit', 'value_type': 'boolean'},
    'voice_agent_livekit_url': {'owner': 'voice_pipecat', 'value_type': 'url'},
    'voice_agent_llm_model': {'owner': 'voice_agent', 'value_type': 'model'},
    'voice_agent_ollama_url': {'owner': 'voice_agent', 'value_type': 'url'},
    'voice_agent_public_livekit_url': {'owner': 'voice_routes'},
    'voice_agent_recall_k': {'value_type': 'integer'},
    'voice_agent_recall_min_similarity': {'value_type': 'float'},
    'voice_agent_room_name': {'value_type': 'string'},
    'voice_agent_system_prompt': {'owner': 'voice_agent', 'value_type': 'string'},
    'voice_agent_tts_speed': {'owner': 'voice_agent', 'value_type': 'float'},
    'voice_agent_tts_voice': {'owner': 'voice_agent', 'value_type': 'string'},
    'voice_agent_vad_stop_secs': {'owner': 'voice_agent', 'value_type': 'float'},
    'voice_agent_whisper_model': {'value_type': 'model'},
    'wan_server_url': {'owner': 'wan2_1'},
    'warm_pinned_llm_endpoints_enabled': {'owner': 'warm_pinned_llm_endpoints', 'value_type': 'boolean'},
    'worker_heartbeat_interval_seconds': {'owner': 'worker_service', 'value_type': 'integer'},
    'writer_disable_thinking': {'owner': 'two_pass_writer', 'value_type': 'boolean'},
    'writer_length_expansion_enabled': {'owner': 'two_pass_writer', 'value_type': 'boolean'},
    'writer_min_draft_chars': {'owner': 'writer_core', 'value_type': 'integer'},
    'writer_min_length_ratio': {'owner': 'two_pass_writer', 'value_type': 'float'},
    'writer_min_substance_words': {'owner': 'two_pass_writer', 'value_type': 'integer'},
    'writer_rag_context_snippet_max_chars': {'owner': 'ai_content_generator', 'value_type': 'integer'},
    'writer_rag_research_topic_max_sources': {'owner': 'research_service', 'value_type': 'integer'},
    'writer_rag_two_pass_research_max_sources': {'owner': 'two_pass_writer', 'value_type': 'integer'},
    'writer_rag_two_pass_snippet_limit': {'owner': 'two_pass_writer', 'value_type': 'integer'},
    'writer_self_review_model': {'owner': 'self_review', 'value_type': 'model'},
    'youtube_attribution_enabled': {'owner': 'content_reconcile_citations', 'value_type': 'boolean'},
    'youtube_oembed_timeout_seconds': {'owner': 'content_reconcile_citations', 'value_type': 'integer'},
}


async def seed_all_defaults(pool: Any) -> int:
    """Insert every DEFAULTS entry into app_settings, skipping existing rows.

    Returns the count of rows actually inserted (i.e. fresh-install gap
    closed). On an up-to-date DB this is 0.

    Operator-tuned values survive — the ``ON CONFLICT (key) DO NOTHING``
    clause means an existing row is never overwritten by this seeder.

    A second pass writes lifecycle metadata (owner, value_type, deprecated,
    superseded_by) for keys listed in ``METADATA``.  The UPDATE fires only
    when at least one column differs from the stored value (``IS DISTINCT
    FROM``), so it is a no-op on up-to-date deployments.

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
                    ($1, $2, $3,
                     'Auto-seeded by services.settings_defaults (#379)',
                     FALSE, TRUE, NOW())
                ON CONFLICT (key) DO NOTHING
                """,
                key,
                value,
                resolve_category(key),
            )
            try:
                # Status looks like "INSERT 0 N"
                if status.endswith(" 1"):
                    inserted += 1
            except Exception:
                pass

        # Second pass: write lifecycle metadata where the columns differ.
        # Skips silently if the lifecycle columns don't exist yet (migration
        # hasn't run — e.g. fresh-clone worktree with an older schema).
        try:
            for key, meta in METADATA.items():
                await conn.execute(
                    """
                    UPDATE app_settings SET
                        owner       = $2,
                        value_type  = $3,
                        deprecated  = $4,
                        superseded_by = $5
                    WHERE key = $1
                      AND (
                          owner         IS DISTINCT FROM $2
                       OR value_type    IS DISTINCT FROM $3
                       OR deprecated    IS DISTINCT FROM $4
                       OR superseded_by IS DISTINCT FROM $5
                      )
                    """,
                    key,
                    meta.get('owner'),
                    meta.get('value_type'),
                    meta.get('deprecated', False),
                    meta.get('superseded_by'),
                )
        except Exception:  # silent-ok: lifecycle columns absent (pre-20260618 schema); INSERT pass ran, metadata deferred until migration runs
            pass

        # Category reconcile: converge every row's category onto the resolver's
        # canonical answer. Converges prod's 698-in-'general' pile on the first
        # boot and self-heals any future drift (steady-state writes 0 rows).
        # category is display-only, so overwriting is safe. Single source of
        # truth: services.settings_categories.resolve_category.
        cat_rows = await conn.fetch("SELECT key, category FROM app_settings")
        cat_updates = [
            (resolve_category(r["key"]), r["key"])
            for r in cat_rows
            if r["category"] != resolve_category(r["key"])
        ]
        if cat_updates:
            await conn.executemany(
                "UPDATE app_settings SET category = $1 WHERE key = $2", cat_updates
            )

    return inserted


_OPERATOR_OVERLAY_DESC = (
    "Operator overlay value (services.operator_overrides) — re-applied over the "
    "OSS default on a fresh install or settings reset."
)


# Columns a niche override may rewrite. The column names are interpolated into
# the UPDATE statement (values always travel as bind params), so this allowlist
# is load-bearing — extend it deliberately, never dynamically.
NICHE_OVERRIDE_COLUMNS: tuple[str, ...] = ("slug", "name", "writer_prompt_override")


async def apply_operator_overrides(pool: Any) -> int:
    """Re-apply Glad Labs operator overrides over the public OSS defaults.

    OSS installs have no ``services.operator_overrides`` module, so this is a
    no-op and the public ``DEFAULTS`` stand. The Glad Labs operator install ships
    that module (stripped from the public mirror) with three kinds of override:
    custom local Ollama model tags that aren't on the public registry
    (``OPERATOR_MODEL_PINS``), operator-personal settings that carry identity —
    the voice persona, the exact GPU (``OPERATOR_SETTING_OVERRIDES``) — and the
    operator's branded niches (``OPERATOR_NICHE_OVERRIDES``), whose public seeds
    ship generic slug/name/prompt text.

    A row is overwritten ONLY while it still holds the OSS public default —
    i.e. a freshly-seeded or post-reset row — never a value tuned at runtime.
    For settings the guard is ``app_settings.value = DEFAULTS[key]`` (so a key
    absent from ``DEFAULTS`` is skipped rather than clobbered); for niches it is
    ``writer_prompt_override = <the baseline-seeded prompt>`` (pinned to the
    seeds by ``test_operator_overlay.test_niche_override_expect_matches_baseline_seed``).
    So an operator reset reliably restores the operator's values, while live
    ``poindexter settings set`` tuning and hand-edited niche prompts survive a
    reboot.

    Returns the number of overrides actually applied (0 on OSS installs).
    """
    if pool is None:
        return 0
    try:
        from services.operator_overrides import (
            OPERATOR_MODEL_PINS,
            OPERATOR_SETTING_OVERRIDES,
        )
    except ImportError:
        return 0  # OSS install — no operator overlay present.
    try:
        from services.operator_overrides import OPERATOR_NICHE_OVERRIDES
    except ImportError:  # overlay predates niche overrides
        OPERATOR_NICHE_OVERRIDES = ()
    overrides = {**OPERATOR_MODEL_PINS, **OPERATOR_SETTING_OVERRIDES}
    applied = 0
    async with pool.acquire() as conn:
        for key, operator_value in overrides.items():
            oss_default = DEFAULTS.get(key)
            if oss_default is None:
                continue
            applied_key = await conn.fetchval(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active, updated_at)
                VALUES ($1, $2, $5, $3, FALSE, TRUE, NOW())
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
                    WHERE app_settings.value = $4
                RETURNING key
                """,
                key,
                operator_value,
                _OPERATOR_OVERLAY_DESC,
                oss_default,
                resolve_category(key),
            )
            if applied_key is not None:
                applied += 1

        for entry in OPERATOR_NICHE_OVERRIDES:
            set_map = entry["set"]
            unknown = set(set_map) - set(NICHE_OVERRIDE_COLUMNS)
            if unknown:
                raise ValueError(
                    f"operator niche override for {entry['match_slug']!r} sets "
                    f"non-allowlisted columns {sorted(unknown)} — extend "
                    "NICHE_OVERRIDE_COLUMNS deliberately or drop them"
                )
            assignments = ", ".join(
                f"{col} = ${i}" for i, col in enumerate(set_map, start=1)
            )
            n = len(set_map)
            row_id = await conn.fetchval(
                f"UPDATE niches SET {assignments} "  # nosec B608 - set_map keys are validated against NICHE_OVERRIDE_COLUMNS above; values are bind params
                f"WHERE slug = ${n + 1} AND writer_prompt_override = ${n + 2} "
                "RETURNING id",
                *set_map.values(),
                entry["match_slug"],
                entry["expect_writer_prompt_override"],
            )
            if row_id is not None:
                applied += 1

    # Firefighter remediation_rules are intentionally NOT overlay-seeded — they
    # are operational CRUD state (added / tuned / retired live in the DB). A
    # seed-if-absent re-assert on boot would resurrect a rule deleted at runtime,
    # breaking DELETE. Guard: test_apply_does_not_seed_remediation_rules.
    #
    # subreddit_profiles are seeded by the SEPARATE seed_operator_subreddit_profiles
    # below (seed-if-EMPTY, not the conditional-UPSERT used here) — kept out of
    # this function so the per-call fetchval accounting the overlay tests assert
    # on stays about settings + niches only.
    return applied


async def seed_operator_subreddit_profiles(pool: Any) -> int:
    """Bootstrap the operator's community-draft subreddit profiles on a FRESH
    install / rebuild.

    OSS installs have no ``services.operator_overrides`` module, so this is a
    no-op. The Glad Labs overlay ships ``OPERATOR_SUBREDDIT_PROFILES`` — the
    operator's curated ``subreddit_profiles`` rows (which communities to draft
    for, plus each one's rules / tone / self-promo norms). The public
    ``subreddit_profiles`` table ships EMPTY (migration ``20260713_010000``);
    these restore the operator's targets after a rebuild.

    **Seed-if-EMPTY, not seed-if-absent-per-row.** The profiles are operational
    CRUD state — added / edited / *removed* live via ``poindexter community
    profiles`` — so a per-row re-assert on every boot would resurrect a profile
    the operator deleted at runtime (the same hazard that keeps remediation_rules
    out of :func:`apply_operator_overrides`). Seeding only while the table is
    empty gives rebuild-durability while leaving runtime CRUD authoritative the
    moment any row exists.

    Returns the number of profiles inserted (0 on OSS installs, on a non-empty
    table, or when a concurrent boot already populated it).
    """
    if pool is None:
        return 0
    try:
        from services.operator_overrides import OPERATOR_SUBREDDIT_PROFILES
    except ImportError:
        return 0  # OSS install (or an overlay predating this attribute).
    if not OPERATOR_SUBREDDIT_PROFILES:
        return 0

    from services.community_drafts import SubredditProfile, add_profile

    seeded = 0
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT COUNT(*) FROM subreddit_profiles")
        if existing:
            return 0  # runtime CRUD is authoritative once any profile exists.
        for prof in OPERATOR_SUBREDDIT_PROFILES:
            if await add_profile(conn, SubredditProfile(**prof)):
                seeded += 1
    return seeded


def keys() -> list[str]:
    """Return the sorted list of keys this module knows about.

    Useful for diagnostics (``poindexter setup --check`` could compare
    DEFAULTS.keys() against the live DB to flag drift).
    """
    return sorted(DEFAULTS.keys())


def default_int(key: str) -> int:
    """The declared default for an int-valued key, for use as a code fallback.

    Pass this to ``site_config.get_int(key, default_int(key))`` instead of
    repeating a literal. A repeated literal is a snapshot of whatever the
    default happened to be the day that call site was written, and it goes
    stale silently — ``image_render_timeout_seconds`` accumulated FOUR values
    that way (90 at four call sites, 240 at three, 300 declared here, 240 live
    on prod), each one a fossil of a different month's default.

    Deliberately NOT a blanket rule: a code fallback that differs from the
    declared default is often correct (a fail-safe ``False``, an ``""``
    presence-probe, a ``-1`` sentinel), and ~74 keys diverge on purpose. Use
    this where the call site genuinely means "the normal default", not where it
    means something else.

    Raises ``KeyError`` with a pointed message when the key isn't declared —
    that's a rename that lost its default, and it should fail loudly at import
    of the first call rather than silently supply a zero.
    """
    try:
        raw = DEFAULTS[key]
    except KeyError:
        raise KeyError(
            f"{key!r} has no entry in settings_defaults.DEFAULTS — a call site "
            "asked for its declared default. Add the key or fix the name."
        ) from None
    return int(raw)
