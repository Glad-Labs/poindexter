# Video-render VRAM gate + reclaim + idle WSL reset — Implementation Plan

> **For agentic workers:** Execute inline (this operator runs subagents OFF — see memory `feedback_no_subagent_delegation`). Steps use checkbox (`- [ ]`) syntax for tracking. Every change ships via PR (`feedback_all_changes_via_pr`), with contract tests + doc updates (`feedback_docs_and_tests_default`).

**Goal:** Stop the Wan 2.2 TI2V-5B video render from oversubscribing the RTX 5090 (the display GPU) and freezing the desktop — by (1) gating dispatch on live free VRAM, (2) reclaiming reclaimable VRAM before a render, and (3) an idle-only host-side WSL/Docker reset to clear stale WSL2 GPU retention.

**Architecture:** The render needs ~24 GB resident on the 5090, which also drives the desktop and accumulates stale GPU memory inside the WSL2 Docker VM (`vmwp`/`vmmemWSL`). Root cause (2026-07-12 investigation): with ~15 GB of stale WSL/image-gen VRAM + ~4 GB desktop already resident, the render's 24 GB pushes past 32 GB → CUDA OOM + WDDM shared-memory spill → desktop lockup. The scheduler's existing pre-render eviction only frees Ollama (~0.3 GB). This plan adds a live-VRAM preflight (fail-closed defer, reusing the `media_infra_health` gate + the scheduler's Prometheus query pattern), a real reclaim path (image-gen hard-restart to return its ~7 GB CUDA context; only a process exit returns it — `empty_cache()` doesn't), and a host-side idle reset that clears the stubborn ~8.6 GB WSL retention (needs `wsl --shutdown` + Docker Desktop restart, which the containerized worker cannot do itself).

**Tech Stack:** Python 3.13 / FastAPI backend (`src/cofounder_agent`), the `image-gen` sidecar (`scripts/image-gen-server.py`), Prometheus (`prometheus:9090`, `nvidia_gpu_memory_*_mib`), app_settings DB config, host-side PowerShell + Windows Task Scheduler (`scripts/*.ps1`).

## Global Constraints

- **DB-first config** (`feedback_db_first_config`): every tunable is an `app_settings` key seeded in `src/cofounder_agent/services/settings_defaults.py` (NOT in a migration — `feedback_seed_data_in_baseline_not_new_migrations`). Empty-string is the unset sentinel; values are never NULL (`feedback_app_settings_value_not_null`).
- **No silent defaults** (`feedback_no_silent_defaults`): a required-but-missing setting fails loud; but a gate that can't read VRAM must fail **closed** (defer the render), never fail-open into a lockup.
- **Fail-closed for the gate; fail-open only where a miss cannot cause a lockup.** Deferring is always safe (the piece is retried intact next cycle — same contract as the existing infra gate).
- **Render GPU is `pipeline_gpu_index` (default `0` = the RTX 5090).** Never hardcode; never read the idle 3090.
- **Prometheus metrics:** `nvidia_gpu_memory_total_mib{gpu="<idx>"}`, `nvidia_gpu_memory_used_mib{gpu="<idx>"}`; base URL `gpu_metrics_prometheus_url` (default `http://prometheus:9090`). Free = total − used.
- **Two PRs, both to `origin` (glad-labs-stack), issue-routed to glad-labs-stack** (operator infra, not OSS product — `feedback_check_issue_routing_first`). PR 1 = gate + tier-1 (in-repo). PR 2 = idle WSL reset (host-side).
- **Keep wan-server stopped until PR 1's gate is live** (lane held; no lockup risk during implementation).

---

## PR 1 — VRAM preflight gate + tier-1 image-gen reclaim

### Task 1: `render_gpu_free_vram_gb()` — live free-VRAM read

**Files:**

- Create: `src/cofounder_agent/services/render_vram.py`
- Test: `src/cofounder_agent/tests/unit/services/test_render_vram.py`

**Interfaces:**

- Produces: `async def render_gpu_free_vram_gb(site_config, *, http_client_factory=None) -> float | None` — free VRAM (GB) on `pipeline_gpu_index`, or `None` when Prometheus can't be read (caller decides fail-closed).

**Design notes:**

- Reuse the query shape from `gpu_scheduler._query_prometheus_scalar` (instant query `GET {url}/api/v1/query?query=<expr>`, read `data.result[0].value[1]`), but as a standalone helper (the scheduler's is a bound method with finding side-effects; keep this one pure + testable).
- Expr: `nvidia_gpu_memory_total_mib{gpu="<idx>"} - nvidia_gpu_memory_used_mib{gpu="<idx>"}` → MiB; divide by 1024 for GB.

- [ ] **Step 1 — failing test:** free VRAM parses from a mocked Prometheus instant-query response.

```python
# test_render_vram.py
import pytest
from services.render_vram import render_gpu_free_vram_gb
from tests.unit.support.fake_site_config import FakeSiteConfig  # existing test helper pattern

class _Resp:
    status_code = 200
    def __init__(self, val): self._val = val
    def json(self): return {"status":"success","data":{"resultType":"vector",
        "result":[{"metric":{"gpu":"0"},"value":[0, str(self._val)]}]}}
    def raise_for_status(self): pass

class _Client:
    def __init__(self, val): self._val = val
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url, **kw): return _Resp(self._val)

@pytest.mark.asyncio
async def test_free_vram_parsed_to_gb():
    sc = FakeSiteConfig({"pipeline_gpu_index":"0","gpu_metrics_prometheus_url":"http://prometheus:9090"})
    free = await render_gpu_free_vram_gb(sc, http_client_factory=lambda **kw: _Client(20480))  # 20 GiB in MiB
    assert free == pytest.approx(20.0, abs=0.05)

@pytest.mark.asyncio
async def test_free_vram_none_on_empty_result():
    sc = FakeSiteConfig({"pipeline_gpu_index":"0"})
    class _Empty(_Client):
        async def get(self, url, **kw):
            class R: status_code=200
            R.json = lambda s=None: {"status":"success","data":{"result":[]}}
            R.raise_for_status = lambda s=None: None
            return R()
    assert await render_gpu_free_vram_gb(sc, http_client_factory=lambda **kw: _Empty(0)) is None
```

- [ ] **Step 2 — run, expect FAIL** (`ImportError: render_vram`). Command: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_render_vram.py -q` (in a fresh worktree use the main-checkout venv per `reference_run_worktree_tests`: `<main>/.venv python -m pytest ... -o addopts=""`).

- [ ] **Step 3 — implement `render_vram.py`:**

```python
"""Live free-VRAM read for the render GPU (pipeline_gpu_index) via Prometheus.

Separate from gpu_scheduler's bound _query_prometheus_scalar so the media
dispatch gate can read it without the scheduler's finding side-effects.
"""
from __future__ import annotations
import logging
from typing import Any
import httpx

logger = logging.getLogger(__name__)

def _prom_url(site_config: Any) -> str:
    return (site_config.get("gpu_metrics_prometheus_url", "") or "http://prometheus:9090").rstrip("/")

async def render_gpu_free_vram_gb(site_config: Any, *, http_client_factory: Any = None) -> float | None:
    """Free VRAM (GB) on pipeline_gpu_index, or None if Prometheus is unreadable."""
    if site_config is None:
        return None
    idx = int(site_config.get("pipeline_gpu_index", "0") or "0")
    expr = f'nvidia_gpu_memory_total_mib{{gpu="{idx}"}} - nvidia_gpu_memory_used_mib{{gpu="{idx}"}}'
    factory = http_client_factory or httpx.AsyncClient
    try:
        async with factory(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            resp = await client.get(f"{_prom_url(site_config)}/api/v1/query", params={"query": expr})
            resp.raise_for_status()
            result = resp.json().get("data", {}).get("result", [])
            if not result:
                logger.warning("[render_vram] no series for gpu=%s (no recent scrape)", idx)
                return None
            return float(result[0]["value"][1]) / 1024.0
    except Exception as exc:  # noqa: BLE001 — unreadable IS the signal; caller fails closed
        logger.warning("[render_vram] Prometheus read failed: %s: %s", type(exc).__name__, exc)
        return None
```

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(media): render_gpu_free_vram_gb helper (VRAM gate #<issue>)`

### Task 2: wire the VRAM gate into `check_media_infra_health`

**Files:**

- Modify: `src/cofounder_agent/services/media_infra_health.py` (add a VRAM probe after the DNS canary, before the final healthy return)
- Modify: `src/cofounder_agent/services/settings_defaults.py` (seed keys)
- Test: `src/cofounder_agent/tests/unit/services/test_media_infra_health.py` (extend)

**Interfaces:**

- Consumes: `render_gpu_free_vram_gb` (Task 1).
- The gate stays `MediaInfraHealth(healthy=False, detail=...)` on insufficient/unreadable VRAM → `dispatch_media_pipeline` already defers on unhealthy (no change there).

**Settings (settings_defaults.py):**

```python
    # Video-render VRAM preflight (2026-07-12 desktop-lockup fix). The render
    # loads ~24 GB onto pipeline_gpu_index (the display GPU); dispatch defers
    # unless at least this much is free, so a render can never oversubscribe
    # the card and freeze WDDM. Fail-closed: an unreadable reading defers too.
    'media_render_vram_gate_enabled': 'true',
    'media_render_min_free_vram_gb': '26',   # ~24 GB model + ~2 GB margin
```

- [ ] **Step 1 — failing test:** health is unhealthy when free VRAM < threshold; healthy when ≥; unhealthy (fail-closed) when the read is `None`; skipped when the gate is disabled. (Patch `services.media_infra_health.render_gpu_free_vram_gb` and stub the two HTTP probes to pass.)
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement:** after the DNS-canary block in `check_media_infra_health`, before `return MediaInfraHealth(healthy=True, ...)`:

```python
    if site_config.get_bool("media_render_vram_gate_enabled", True):
        from services.render_vram import render_gpu_free_vram_gb
        min_gb = site_config.get_float("media_render_min_free_vram_gb", 26.0) or 26.0
        free = await render_gpu_free_vram_gb(site_config, http_client_factory=http_client_factory)
        if free is None:
            failures.append(
                f"render-GPU free VRAM unreadable (Prometheus) — deferring to avoid a "
                f"blind render that could oversubscribe the display GPU"
            )
        elif free < min_gb:
            failures.append(
                f"render-GPU free VRAM {free:.1f} GB < {min_gb:.0f} GB required "
                f"(pipeline_gpu_index) — deferring so the render can't freeze the desktop"
            )
```

(Place BEFORE the `if failures:` check so it joins the same detail string.)

- [ ] **Step 4 — run, expect PASS** (extend the existing test file; keep existing tests green).
- [ ] **Step 5 — commit:** `feat(media): defer video render unless render-GPU has free VRAM (#<issue>)`

### Task 3: image-gen hard-unload endpoint (real reclaim)

**Files:**

- Modify: `scripts/image-gen-server.py` (add `POST /unload` `hard` mode → free + `os._exit(0)`; Docker restart policy restarts it)
- Test: `src/cofounder_agent/tests/unit/scripts/test_image_gen_server_unload.py` (unit-test the handler's branch selection with `os._exit` monkeypatched)

**Design notes:**

- `empty_cache()` does NOT return the CUDA context/reserved pool to the host under WSL2 (confirmed 2026-07-12: `/unload` freed 0 GB; a container restart freed ~7 GB). So `hard=true` must exit the process. Docker's `restart: unless-stopped` on `poindexter-image-gen-server` brings it back; it lazy-loads on next `/generate`.
- Keep the default (soft) `/unload` behavior for backward-compat.

- [ ] **Step 1 — failing test:** `POST /unload {"hard": true}` calls `os._exit`; soft `/unload` does not. (Monkeypatch `os._exit`; call the FastAPI handler directly.)
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** the `/unload` route (unload pipeline, `empty_cache`, then if `hard`: log + flush + `os._exit(0)`).
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(image-gen): hard /unload that returns the CUDA context via process exit (#<issue>)`

### Task 4: active reclaim in `dispatch_media_pipeline` before the gate

**Files:**

- Modify: `src/cofounder_agent/services/jobs/dispatch_media_pipeline.py` (when eligible pieces exist AND the VRAM gate would fail, attempt a bounded reclaim, then re-probe once)
- Modify: `src/cofounder_agent/services/gpu_scheduler.py` (`_unload_image_gen` gains a `hard: bool=False` param → posts `{"hard": true}`) — reuse existing method + settings for the URL
- Modify: `settings_defaults.py` (`media_render_reclaim_enabled` default `true`)
- Test: `tests/unit/services/jobs/test_dispatch_media_pipeline.py` (extend)

**Design notes:**

- Flow in `DispatchMediaPipelineJob.run`, when `rows` is non-empty: `health = check_media_infra_health(...)`. If unhealthy **specifically because of the VRAM probe** and `media_render_reclaim_enabled`: call reclaim (evict Ollama — exists; `hard` image-gen unload — Task 3), wait a short settle (`media_render_reclaim_settle_seconds`, default 8), re-probe once. If now healthy → proceed; else defer as normal.
- Keep it idempotent-safe and bounded (one reclaim attempt per cycle). Reclaim only runs when there's work AND VRAM is the blocker (never on a wan/image-gen/DNS outage — restarting image-gen mid-outage is pointless).

- [ ] **Step 1 — failing test:** given eligible rows + a VRAM-only unhealthy first probe, the job calls reclaim then re-probes; on a healthy re-probe it dispatches; on still-unhealthy it defers.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** the reclaim-then-reprobe branch + `_unload_image_gen(hard=True)`.
- [ ] **Step 4 — run, expect PASS; run the full media + gpu_scheduler test files green.**
- [ ] **Step 5 — commit:** `feat(media): reclaim render-GPU VRAM (evict Ollama + hard-unload image-gen) before deferring (#<issue>)`

### Task 5: docs + Grafana visibility + PR

**Files:**

- Modify: `docs/architecture/` (new `video-render-vram-gate.md` — the root cause, the gate, the reclaim, the settings, the `pipeline_gpu_index` note) and link from `project_video_pipeline_workstream` context.
- Modify: `infrastructure/grafana/dashboards/hardware-power.json` (a "Render VRAM gate — free vs required" timeseries on `pipeline_gpu_index`, threshold at `media_render_min_free_vram_gb`) — per `feedback_grafana_everything`.
- Verify: `python scripts/ci/migrations_lint.py` (no new migration — settings are seeded), `poetry run ruff check`, `poetry run pytest tests/unit/services/test_render_vram.py tests/unit/services/test_media_infra_health.py tests/unit/services/jobs/test_dispatch_media_pipeline.py -q`.

- [ ] Write the doc, add the panel, run lint + the three test files green, then open PR 1 to `origin`. After merge + deploy, **restart `poindexter-prefect-worker`/`poindexter-worker`** to load it (`reference_worker_restart_triage`), then `docker start poindexter-wan-server` to un-hold the lane.

---

## PR 2 — Idle-only host-side WSL/Docker reset (the stubborn ~8.6 GB)

**Why host-side:** the ~8.6 GB stuck in `vmwp`/`vmmemWSL` only returns to the host on `wsl --shutdown` + Docker Desktop restart (a container restart doesn't reclaim it; `wsl --shutdown` alone breaks GPU passthrough per `MEMORY_repo_notes`). The worker runs _inside_ WSL and cannot reset it — this must be a Windows host task.

**Files:**

- Create: `scripts/idle-wsl-gpu-reset.ps1` — the reset agent.
- Create: `scripts/register-idle-wsl-reset.ps1` — registers/removes the Windows Scheduled Task (mirrors `scripts/claude-sessions.ps1` conventions).
- Modify: `settings_defaults.py` — DB-config the thresholds (the script reads them via a direct Postgres query, like the brain's bootstrap-DSN pattern).
- Create: `docs/operations/idle-wsl-gpu-reset.md`.

**Reset agent logic (`idle-wsl-gpu-reset.ps1`), runs every N min via Task Scheduler; acts only when ALL hold:**

1. **User away:** `GetLastInputInfo` idle ≥ `idle_wsl_reset_min_idle_minutes` (default `20`).
2. **No active work:** query Postgres — zero `pipeline_tasks` in `in_progress`/`claimed`, and no media/podcast dispatch in flight (`media_pipeline_dispatched_at` set with no terminal media in the last `idle_wsl_reset_inflight_grace_minutes`).
3. **Retention actually high:** 5090 (`pipeline_gpu_index`) free VRAM < `idle_wsl_reset_trigger_free_vram_gb` (default `22`) AND `nvidia_gpu_utilization_percent{gpu="0"}` low (not gaming) — read from Prometheus (host reaches `http://localhost:9091`).
4. **Cooldown:** last reset ≥ `idle_wsl_reset_cooldown_hours` (default `6`) ago (state file under `~/.poindexter/`).

**When all hold:** log + `notify_operator` (Discord, routine — `feedback_telegram_vs_discord`), then `wsl --shutdown`, wait, restart Docker Desktop (`Stop-Process`/`Start-Process "Docker Desktop.exe"` or `Restart-Service com.docker.service` per what the host uses), poll the stack back to health (worker `/health`), stamp the cooldown state file.

**Settings (settings_defaults.py):** `idle_wsl_reset_enabled` (`false` by default — opt-in, it bounces the whole stack), `idle_wsl_reset_min_idle_minutes` (`20`), `idle_wsl_reset_trigger_free_vram_gb` (`22`), `idle_wsl_reset_cooldown_hours` (`6`), `idle_wsl_reset_inflight_grace_minutes` (`15`).

**Tasks (host-side; tested manually + a Pester/unit check where feasible):**

- [ ] Task 6: `idle-wsl-gpu-reset.ps1` with a `-DryRun` mode (evaluates all conditions, prints the decision, does NOT reset). Verify `-DryRun` against the current live state.
- [ ] Task 7: registration script + `docs/operations/idle-wsl-gpu-reset.md`. Register the task **disabled** first; enable only after a successful `-DryRun` and Matt's explicit go (it restarts his stack).
- [ ] Task 8: one supervised live reset (Matt present) to confirm: stack bounces, 5090 returns to ~5 GB used, stack self-heals, cooldown stamped. Then open PR 2 to `origin`.

**Safety:** default OFF; `-DryRun` first; supervised first live run; cooldown + idle-gating; Discord notify on every reset. Never resets while a task or render is in flight.

---

## Self-review notes

- **Spec coverage:** gate (Tasks 1-2), tier-1 reclaim (Tasks 3-4), idle WSL reset (Tasks 6-8), docs+Grafana (Task 5, PR2 doc). ✓
- **Fail-closed** honored in Task 2 (unreadable VRAM ⇒ defer). ✓
- **`pipeline_gpu_index`** used everywhere the render GPU is referenced; never hardcoded 0. ✓
- **No new migration**; all settings seeded in `settings_defaults.py`. ✓
- **Open decision for execution:** Task 4's "VRAM-only unhealthy" detection needs `check_media_infra_health` to distinguish the VRAM failure from wan/image-gen/DNS failures — implement by returning a structured reason (e.g. a `MediaInfraHealth.reasons: list[str]` or a `vram_insufficient: bool` flag) rather than string-sniffing the `detail`. Add that field in Task 2.
