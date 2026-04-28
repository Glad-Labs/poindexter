"""Centralized localhost / fallback URLs used across services/.

GH#93 Phase 3 — Scatter-proof config hygiene. Before this module existed,
`"http://localhost:<port>"` literals were duplicated across 10+ service
files as fallback values for `site_config.get()` lookups. Every operator
running Poindexter on non-default ports had to find every one of those
hardcoded strings and override them in `app_settings`, and any new
service quietly forked a new copy of the default.

Owning the canonical values here does two things:

1. **Single source of truth for fallbacks.** If Poindexter ever needs a
   different default (say, SDXL moves to a new port in the shipped
   compose), one line in this module updates every call site.

2. **Grep-ability for auditors.** `grep -rn DEFAULT_OLLAMA_URL
   src/cofounder_agent/` now returns every consumer — useful when
   changing a port or validating "what does the pipeline actually try
   to reach if the DB is unset?"

All of these are FALLBACKS. The DB row in `app_settings` (via
`site_config.get(key, DEFAULT_X)`) is the authoritative value at
runtime — this module only provides the value used when that row
doesn't exist yet (fresh install, migration gap, etc.).

Do not add runtime-config values here. This is only for transport-layer
URLs that an out-of-the-box compose stack exposes. Secrets, feature
flags, thresholds, and anything tunable per-operator belongs in
`app_settings` with `site_config.get()` — no fallback in code.
"""

from __future__ import annotations

# -------- LLM / embedding providers ----------------------------------------

DEFAULT_OLLAMA_URL = "http://localhost:11434"
"""Ollama's default listen port. Set by compose as OLLAMA_BASE_URL;
`site_config.get('ollama_base_url', DEFAULT_OLLAMA_URL)` fallback."""

DEFAULT_SDXL_URL = "http://localhost:9836"
"""SDXL image-generation server (shipped in docker-compose.local.yml as
the `sdxl` service). Fallback for `sdxl_server_url`."""

DEFAULT_WAN_URL = "http://localhost:9840"
"""Wan 2.1 text-to-video server (shipped in docker-compose.local.yml as
the `wan` service). Fallback for `wan_server_url` /
`plugin.video_provider.wan2.1-1.3b.server_url`. Used by the cooperative
sidecar-unload protocol in `gpu_scheduler.request_sidecar_unload` so the
worker can ask Wan to release its idle ~14GB VRAM footprint before
claiming the GPU lock for an Ollama / SDXL workload."""

# -------- Internal service mesh --------------------------------------------

DEFAULT_WORKER_API_URL = "http://localhost:8002"
"""FastAPI worker port inside docker-compose. Fallback for
`internal_api_base_url` used by jobs / publish_service when they call
back into the worker."""

DEFAULT_PUBLIC_SITE_URL = "http://localhost:3000"
"""Next.js dev-mode default for the public site. Fallback for
`next_public_api_base_url` used by revalidation_service."""

# -------- External operator services (self-hosted stack) -------------------

DEFAULT_OPENCLAW_URL = "http://localhost:18789"
"""OpenClaw gateway default. Fallback for `openclaw_gateway_url` used
by task_executor + social_poster to queue outbound webhooks."""

DEFAULT_GITEA_URL = "http://localhost:3001"
"""Self-hosted Gitea default for GiteaIssuesTap. Fallback when
`gitea_url` is unset and `resolve_url()` can't find a migration-time
default."""
