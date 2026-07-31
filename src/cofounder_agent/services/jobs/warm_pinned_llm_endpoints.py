"""WarmPinnedLlmEndpointsJob — keep GPU-pinned Ollama instances resident.

Closes the gap that made glad-labs-stack#2051 ("pin qwen3-vl **warm** on GPU 1")
ship only half its title. The *pinning* works: a second Ollama runs with
``CUDA_VISIBLE_DEVICES`` bound to the 3090 and ``model_api_base_overrides`` routes
the vision model to it. The *warm* never existed.

``OLLAMA_KEEP_ALIVE=-1`` means "never evict **once loaded**" — it loads nothing.
Nothing warmed the instance at boot, so after every restart the pinned GPU sat
empty until the first vision call cold-loaded an 18 GB model mid-pipeline, timed
out, and the rail passed open. Observed 2026-07-31: the instance had been up
16h48m with ``/api/ps`` returning no models, while ``vision_scorer_unavailable``
and ``qa_rail_degraded`` findings kept firing.

**The num_ctx trap — read before changing the warm request.** Ollama keys a
loaded model by its context size and *reloads* when a request asks for a
different one (measured 2026-07-30: requesting phi4 at 8192 then 16384 leaves ONE
resident instance at 16384, not two). So warming at the wrong ``num_ctx`` is
worse than not warming: it burns VRAM and the first real call still pays a full
cold load. The warm request must use the context the real calls will use, which
is why this job resolves ``num_ctx`` the same way ``dispatch_complete`` does
rather than letting Ollama pick a default.

Scope is deliberately narrow: only endpoints that ``model_api_base_overrides``
declares, and only when the model is genuinely absent. The default endpoint is
left alone — it serves many models under ``OLLAMA_MAX_LOADED_MODELS=1``, so
warming one there would just evict whatever the pipeline is using.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)

# app_settings keys (seeded in settings_defaults.py).
_ENABLED_KEY = "warm_pinned_llm_endpoints_enabled"
_PROVIDER = "litellm"
_WARM_TIMEOUT_SECONDS = 300  # a cold 18GB load is slow; well under the 5m period


def _resident_models(payload: Any) -> set[str]:
    """Model names currently loaded, from an ``/api/ps`` body."""
    if not isinstance(payload, dict):
        return set()
    out: set[str] = set()
    for entry in payload.get("models") or []:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("model")
            if name:
                out.add(str(name))
    return out


class WarmPinnedLlmEndpointsJob:
    """Load each pinned model on its own endpoint so the GPU is never cold."""

    name = "warm_pinned_llm_endpoints"
    description = (
        "Keep GPU-pinned Ollama endpoints resident (model_api_base_overrides) "
        "so a vision/QA rail never cold-loads mid-pipeline (stack#2051/#2938)"
    )
    schedule = "every 5 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config = config.get("_site_config")
        if site_config is None:
            return JobResult(
                ok=False, detail="no site_config in config", changes_made=0,
            )
        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)
        if not site_config.get_bool(_ENABLED_KEY, True):
            return JobResult(ok=True, detail="disabled", changes_made=0)

        import httpx

        from services.llm_providers.dispatcher import get_provider_config
        from services.llm_providers.litellm_provider import _coerce_override_map
        from services.ollama_client import resolve_num_ctx
        from utils.findings import emit_finding

        provider_config = await get_provider_config(pool, _PROVIDER)
        overrides = _coerce_override_map(
            provider_config.get("model_api_base_overrides"),
        )
        if not overrides:
            # The OSS path: no second instance configured, nothing to warm.
            return JobResult(
                ok=True, detail="no pinned endpoints configured", changes_made=0,
            )
        default_base = str(provider_config.get("api_base") or "").strip()

        warmed: list[str] = []
        already: list[str] = []
        failed: list[str] = []

        async with httpx.AsyncClient() as client:
            for model, endpoint in overrides.items():
                endpoint = str(endpoint or "").strip().rstrip("/")
                # An override pointing at the default endpoint is the SAME
                # server — warming there would evict live work, not protect it.
                if not endpoint or endpoint == default_base:
                    continue
                tag = str(model).split("/", 1)[-1]
                try:
                    resp = await client.get(f"{endpoint}/api/ps", timeout=10)
                    resp.raise_for_status()
                    resident = _resident_models(resp.json())
                except Exception as exc:
                    logger.warning(
                        "[warm_pinned] %s unreachable at %s: %s", tag, endpoint, exc,
                    )
                    failed.append(tag)
                    continue

                if tag in resident:
                    already.append(tag)
                    continue

                # Context must match what real calls will request or Ollama
                # reloads on first use and the warm was wasted — see module docs.
                num_ctx = resolve_num_ctx(None, site_config=site_config)
                try:
                    warm = await client.post(
                        f"{endpoint}/api/generate",
                        json={
                            "model": tag,
                            "prompt": "warm",
                            "stream": False,
                            # -1 = never evict; the pin only pays off if it stays.
                            "keep_alive": -1,
                            "options": {"num_ctx": num_ctx},
                        },
                        timeout=_WARM_TIMEOUT_SECONDS,
                    )
                    warm.raise_for_status()
                except Exception as exc:
                    logger.warning(
                        "[warm_pinned] failed to warm %s at %s: %s", tag, endpoint, exc,
                    )
                    failed.append(tag)
                    continue

                warmed.append(tag)
                # A warm that actually fired means the endpoint WAS cold — either
                # a restart or an unexpected eviction. Surface it: silently
                # re-warming would hide exactly the condition #2051 was about.
                # Called bare, like every other job: emit_finding is documented
                # fire-and-forget and never raises, so wrapping it would only
                # add a swallow the silent-except ratchet rightly rejects.
                emit_finding(
                    source="warm_pinned_llm_endpoints",
                    kind="pinned_endpoint_cold",
                    severity="warn",
                    title=f"pinned endpoint was cold — warmed {tag}",
                    body=(
                        f"{tag} was not resident at {endpoint} (num_ctx="
                        f"{num_ctx}). Expected after an Ollama restart; "
                        "recurring outside restarts means something is "
                        "evicting a model that should never be evicted."
                    ),
                    dedup_key=f"pinned_endpoint_cold_{tag}",
                )

        detail = (
            f"warmed={len(warmed)} already_resident={len(already)} "
            f"unreachable={len(failed)}"
        )
        return JobResult(
            # Unreachable endpoints are the operator's signal, but this job is
            # advisory housekeeping — a down sidecar must not mark the run failed
            # and spam the scheduler's failure path.
            ok=True,
            detail=detail,
            changes_made=len(warmed),
            metrics={
                "warmed": len(warmed),
                "already_resident": len(already),
                "unreachable": len(failed),
                "pinned_endpoints": len(overrides),
            },
        )
