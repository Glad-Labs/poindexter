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
cold load. The warm request must use the context the real calls will use.
``dispatch_complete`` runs every call routed to a pinned endpoint at
``pinned_llm_endpoint_num_ctx``, so this job warms at that size, read through
the same ``pinned_endpoint_num_ctx`` helper, rather than letting Ollama pick.

**A resident model at the wrong size is reported, not re-warmed (2026-09-25).**
``/api/ps`` names each resident model's ``context_length``. When a pinned tag is
resident at any other size, something outside the dispatcher loaded it: a
request with no ``num_ctx`` gets the instance's ``OLLAMA_CONTEXT_LENGTH``, and a
direct call with its own ``options.num_ctx`` sets it outright. The next rail call
reloads it either way. The job raises ``pinned_endpoint_context_mismatch`` and
leaves the model alone, because re-warming would fight that caller. The judge
was reloaded 80 times on 2026-09-24/25, most of them a 10-minute poller at
32768 trading places with the rails at 16384.

Scope is deliberately narrow: only endpoints that ``model_api_base_overrides``
declares, and only when the model is genuinely absent. The default endpoint is
left alone — it serves many models under ``OLLAMA_MAX_LOADED_MODELS=1``, so
warming one there would just evict whatever the pipeline is using.

**The shared-slot trap (2026-09-17).** A pinned instance is usually a one-slot
instance too (``OLLAMA_MAX_LOADED_MODELS=1`` on the operator's :11435). When the
override map routes TWO tags to it — measured: ``qwen3-vl:30b`` and
``qwen3-vl:30b-a3b-instruct``, both to the 3090 — every fire warmed the first,
evicting the second, then warmed the second, evicting the first: two 50-second
loads and ~40 GB of PCIe traffic every five minutes, the judge cold for a real
call half the time, and each ``pinned_endpoint_cold`` finding deduped by tag so
nothing looked wrong. The job therefore treats each endpoint as holding at most
``warm_pinned_llm_max_models_per_endpoint`` (default 1) of its own override
tags: once that many are resident or just warmed, the remaining tags are left
alone and a ``pinned_endpoint_overcommitted`` finding names them, because a
warm that evicts a pinned sibling is not a warm.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

# app_settings keys (seeded in settings_defaults.py).
_ENABLED_KEY = "warm_pinned_llm_endpoints_enabled"
_MAX_PER_ENDPOINT_KEY = "warm_pinned_llm_max_models_per_endpoint"
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


def _resident_contexts(payload: Any) -> dict[str, int]:
    """``{model name: loaded context_length}`` from an ``/api/ps`` body.

    A model whose entry carries no usable ``context_length`` (an Ollama too old
    to report it) is left out: an unknown size is not a mismatch.
    """
    if not isinstance(payload, dict):
        return {}
    out: dict[str, int] = {}
    for entry in payload.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("model")
        ctx = entry.get("context_length")
        if name and isinstance(ctx, int) and not isinstance(ctx, bool) and ctx > 0:
            out[str(name)] = ctx
    return out


def _max_models_per_endpoint(site_config: Any) -> int:
    """How many of its own pinned tags one endpoint may hold at once (>= 1)."""
    raw = str(site_config.get(_MAX_PER_ENDPOINT_KEY, "1") or "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(
            "[warm_pinned] %s=%r is not an integer; using 1", _MAX_PER_ENDPOINT_KEY, raw,
        )
        return 1


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

        from poindexter.services.llm_providers.dispatcher import (
            PINNED_NUM_CTX_KEY,
            get_provider_config,
            pinned_endpoint_num_ctx,
        )
        from poindexter.services.llm_providers.litellm_provider import _coerce_override_map
        from poindexter.services.ollama_client import resolve_num_ctx
        from poindexter.utils.findings import emit_finding

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

        max_per_endpoint = _max_models_per_endpoint(site_config)

        # The size every dispatch routed to a pinned endpoint runs at. With the
        # enforcement opted out (0), calls resolve per phase and the warm falls
        # back to the fleet default it always used.
        pinned_ctx = await pinned_endpoint_num_ctx(pool, site_config=site_config)
        warm_ctx = (
            pinned_ctx if pinned_ctx is not None
            else resolve_num_ctx(None, site_config=site_config)
        )

        warmed: list[str] = []
        already: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []
        mismatched: list[str] = []

        # Group the map by endpoint first: the ``ollama/`` and ``ollama_chat/``
        # spellings of one tag are the same model, and two DIFFERENT tags on
        # one endpoint are the shared-slot trap the module docstring describes.
        by_endpoint: dict[str, list[str]] = {}
        for model, endpoint in overrides.items():
            endpoint = str(endpoint or "").strip().rstrip("/")
            # An override pointing at the default endpoint is the SAME
            # server — warming there would evict live work, not protect it.
            if not endpoint or endpoint == default_base:
                continue
            tag = str(model).split("/", 1)[-1]
            tags = by_endpoint.setdefault(endpoint, [])
            if tag not in tags:
                tags.append(tag)

        async with httpx.AsyncClient() as client:
            for endpoint, tags in by_endpoint.items():
                try:
                    resp = await client.get(f"{endpoint}/api/ps", timeout=10)
                    resp.raise_for_status()
                    ps_body = resp.json()
                    resident = _resident_models(ps_body)
                    contexts = _resident_contexts(ps_body)
                except Exception as exc:
                    logger.warning(
                        "[warm_pinned] %s unreachable at %s: %s",
                        ", ".join(tags), endpoint, describe_exception(exc),
                    )
                    failed.extend(tags)
                    continue

                # Slots this endpoint already spends on its OWN pinned tags.
                # A foreign model resident there is not ours to count — the
                # warm below evicts it, which is the documented intent.
                held = [t for t in tags if t in resident]
                already.extend(held)
                overcommitted: list[str] = []

                # A pinned tag resident at a size no dispatch will ask for is a
                # reload waiting for the next rail call. Name it; don't reload it
                # (see the module docstring for why re-warming makes it worse).
                for tag in held:
                    loaded = contexts.get(tag)
                    if pinned_ctx is None or loaded is None or loaded == pinned_ctx:
                        continue
                    mismatched.append(tag)
                    logger.warning(
                        "[warm_pinned] %s is resident at %s with num_ctx=%d, but "
                        "pinned calls run at %s=%d; the next one will reload it",
                        tag, endpoint, loaded, PINNED_NUM_CTX_KEY, pinned_ctx,
                    )
                    emit_finding(
                        source="warm_pinned_llm_endpoints",
                        kind="pinned_endpoint_context_mismatch",
                        severity="warn",
                        title=(
                            f"pinned endpoint holds {tag} at num_ctx={loaded}, "
                            f"not {pinned_ctx}"
                        ),
                        body=(
                            f"{tag} is resident at {endpoint} with a {loaded}-token "
                            f"context, but every Poindexter call routed there runs at "
                            f"{PINNED_NUM_CTX_KEY}={pinned_ctx}, so the next one "
                            "reloads it (10-40 s for a ~20 GB judge). Something "
                            "outside the dispatcher loaded it at another size. A "
                            "request that sends no num_ctx gets the instance's "
                            "OLLAMA_CONTEXT_LENGTH (scripts/linux/ollama-vision.sh "
                            f"on the reference install; keep it equal to "
                            f"{PINNED_NUM_CTX_KEY}), and a direct /api/generate or "
                            "/api/chat with its own options.num_ctx sets the size "
                            "outright. The instance's serve log names every load's "
                            "size ('starting llama-server ... -c <num_ctx>'). This "
                            "job leaves the model loaded: re-warming would fight "
                            "that caller and double the reloads."
                        ),
                        dedup_key=f"pinned_endpoint_context_mismatch_{endpoint}",
                    )

                for tag in tags:
                    if tag in resident:
                        continue
                    if len(held) >= max_per_endpoint:
                        # Warming this tag would evict a pinned sibling, and the
                        # next fire would warm the sibling back: the ping-pong.
                        overcommitted.append(tag)
                        continue
                    if not await self._warm(
                        client, endpoint=endpoint, tag=tag, num_ctx=warm_ctx,
                        failed=failed,
                    ):
                        continue
                    warmed.append(tag)
                    held.append(tag)

                if overcommitted:
                    skipped.extend(overcommitted)
                    logger.warning(
                        "[warm_pinned] %s holds %d pinned model(s) but the override "
                        "map routes %d tags there; not warming %s (it would evict "
                        "%s) — trim model_api_base_overrides or raise %s",
                        endpoint, len(held), len(tags), ", ".join(overcommitted),
                        ", ".join(held), _MAX_PER_ENDPOINT_KEY,
                    )
                    emit_finding(
                        source="warm_pinned_llm_endpoints",
                        kind="pinned_endpoint_overcommitted",
                        severity="warn",
                        title=f"pinned endpoint overcommitted — {len(tags)} tags for {max_per_endpoint} slot(s)",
                        body=(
                            f"{endpoint} is routed {len(tags)} model tag(s) "
                            f"({', '.join(tags)}) but can hold {max_per_endpoint} "
                            f"(warm_pinned_llm_max_models_per_endpoint). Holding "
                            f"{', '.join(held)}; NOT warming {', '.join(overcommitted)} "
                            "because that would evict the resident pin and the next "
                            "fire would evict it back. Remove the unused tag(s) from "
                            "model_api_base_overrides, or raise the cap if the "
                            "instance really runs OLLAMA_MAX_LOADED_MODELS>1."
                        ),
                        dedup_key=f"pinned_endpoint_overcommitted_{endpoint}",
                    )

        detail = (
            f"warmed={len(warmed)} already_resident={len(already)} "
            f"unreachable={len(failed)} skipped_shared_slot={len(skipped)} "
            f"context_mismatch={len(mismatched)}"
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
                "skipped_shared_slot": len(skipped),
                "context_mismatch": len(mismatched),
                "pinned_endpoints": len(by_endpoint),
                "warm_num_ctx": warm_ctx,
            },
        )

    @staticmethod
    async def _warm(
        client: Any, *, endpoint: str, tag: str, num_ctx: int, failed: list[str],
    ) -> bool:
        """Load ``tag`` on ``endpoint`` never-evict at ``num_ctx``; False (and
        recorded) on failure. ``num_ctx`` must be the size real calls use, or
        Ollama reloads on first use and the warm was wasted (module docs)."""
        from poindexter.utils.findings import emit_finding

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
                "[warm_pinned] failed to warm %s at %s: %s", tag, endpoint, describe_exception(exc),
            )
            failed.append(tag)
            return False

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
        return True

