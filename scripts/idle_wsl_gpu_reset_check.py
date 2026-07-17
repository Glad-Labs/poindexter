"""Decision logic for the idle-only WSL/Docker GPU reset (2026-07-12, PR 2).

Host-side companion to ``scripts/idle-wsl-gpu-reset.ps1``. PowerShell owns the
genuinely host-native pieces (measuring user idle time via GetLastInputInfo,
running ``wsl --shutdown``, restarting Docker Desktop); this script owns
everything that means reading Postgres/Prometheus/app_settings, which Python
already has a proven connection story for (mirrors ``scripts/gpu-scraper.py``
and ``scripts/image-gen-server.py``'s ``_resolve_db_url()``).

Why host-side at all: the ~8.6 GB stuck in WSL2's ``vmwp``/``vmmemWSL`` only
returns to the host on ``wsl --shutdown`` + a Docker Desktop restart — a
container restart doesn't touch it, and the containerized worker runs
*inside* WSL and cannot reset the VM it's running in.

Modes (mutually exclusive, picked by CLI flag):

  --decide --idle-minutes N   Evaluate all reset conditions, print JSON to
                               stdout, exit 0 if the reset should proceed,
                               1 if not, 2 on error (DB/Prometheus unreachable
                               — fails closed, same as "should not reset").
  --stamp-cooldown            Record now (UTC) as the last-reset time.
  --notify TEXT                Post TEXT to the operator Discord webhook
                               directly (see notify() below for why this
                               doesn't go through notify_operator()).

All four condition thresholds are DB-configurable via app_settings (see
settings_defaults.py): idle_wsl_reset_enabled, idle_wsl_reset_min_idle_minutes,
idle_wsl_reset_trigger_free_vram_gb, idle_wsl_reset_cooldown_hours,
idle_wsl_reset_inflight_grace_minutes. GPU-busy detection reuses the existing
gpu_busy_threshold_percent key (gpu_scheduler's gaming-detection threshold) —
one signal, not two competing ones.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx

STATE_FILE = Path(os.path.expanduser("~")) / ".poindexter" / "idle_wsl_reset_state.json"


def _resolve_db_url() -> str:
    """Mirrors scripts/gpu-scraper.py / scripts/image-gen-server.py: force
    IPv4 (127.0.0.1, not "localhost") — on Windows "localhost" can resolve to
    ::1 first and Docker Desktop's IPv6 port-proxy silently drops the
    connection from this host process."""
    for env_key in ("POINDEXTER_BRAIN_URL", "GLADLABS_BRAIN_URL", "DATABASE_URL"):
        val = os.getenv(env_key)
        if val:
            return val.replace("@localhost:", "@127.0.0.1:")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from brain.bootstrap import resolve_database_url  # type: ignore

        dsn = resolve_database_url()
    except Exception as exc:  # bootstrap is best-effort on the host
        print(f"[dsn] bootstrap resolution failed ({exc}); using default", file=sys.stderr)
        dsn = None
    default = "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain"
    return (dsn or default).replace("@localhost:", "@127.0.0.1:")


async def _get_settings(conn: asyncpg.Connection, keys: list[str]) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])", keys,
    )
    return {row["key"]: row["value"] for row in rows if row["value"] not in (None, "")}


def _as_bool(val: str | None, default: bool) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


def _as_float(val: str | None, default: float) -> float:
    try:
        return float(val) if val not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _as_int(val: str | None, default: int) -> int:
    try:
        return int(val) if val not in (None, "") else default
    except (TypeError, ValueError):
        return default


async def _query_prometheus_scalar(base_url: str, expr: str) -> float | None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/v1/query", params={"query": expr})
            resp.raise_for_status()
            result = resp.json().get("data", {}).get("result", [])
            if not result:
                return None
            return float(result[0]["value"][1])
    except Exception:
        return None


def _read_cooldown_state() -> datetime | None:
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return datetime.fromisoformat(data["last_reset_utc"])
    except Exception:
        return None


def _write_cooldown_state() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"last_reset_utc": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )


_SETTINGS_KEYS = [
    "idle_wsl_reset_enabled",
    "idle_wsl_reset_min_idle_minutes",
    "idle_wsl_reset_trigger_free_vram_gb",
    "idle_wsl_reset_cooldown_hours",
    "idle_wsl_reset_inflight_grace_minutes",
    # The render floor the reclaim exists to protect — read so the trigger can
    # be clamped to never sit below it (poindexter#881; see decide()).
    "media_render_min_free_vram_gb",
    "gpu_busy_threshold_percent",
    "pipeline_gpu_index",
    "gpu_metrics_prometheus_url",
]


async def decide(idle_minutes: float) -> dict[str, Any]:
    """Evaluate every reset condition. Returns a JSON-serializable dict with
    ``should_reset`` and a ``reasons`` list explaining every check (not just
    the first failure) — so a -DryRun run is genuinely informative."""
    reasons: list[str] = []
    checks: dict[str, Any] = {"idle_minutes": idle_minutes}

    conn = await asyncpg.connect(_resolve_db_url(), timeout=10)
    try:
        settings = await _get_settings(conn, _SETTINGS_KEYS)

        enabled = _as_bool(settings.get("idle_wsl_reset_enabled"), False)
        checks["enabled"] = enabled
        if not enabled:
            reasons.append("idle_wsl_reset_enabled=false")

        min_idle = _as_float(settings.get("idle_wsl_reset_min_idle_minutes"), 20.0)
        checks["min_idle_minutes"] = min_idle
        if idle_minutes < min_idle:
            reasons.append(
                f"user active — idle {idle_minutes:.1f} min < {min_idle:.0f} min required"
            )

        in_progress = await conn.fetchval(
            "SELECT COUNT(*) FROM pipeline_tasks WHERE status = 'in_progress'"
        )
        checks["pipeline_tasks_in_progress"] = int(in_progress)
        if in_progress:
            reasons.append(f"{in_progress} pipeline task(s) in_progress")

        grace_min = _as_float(settings.get("idle_wsl_reset_inflight_grace_minutes"), 15.0)
        checks["inflight_grace_minutes"] = grace_min
        inflight = await conn.fetchval(
            """
            SELECT COUNT(*) FROM pipeline_tasks
             WHERE media_pipeline_dispatched_at IS NOT NULL
               AND media_pipeline_dispatched_at > NOW() - make_interval(mins => $1::int)
            """,
            int(grace_min),
        )
        checks["media_dispatch_inflight"] = int(inflight)
        if inflight:
            reasons.append(f"{inflight} media dispatch(es) in flight (last {grace_min:.0f} min)")

        cooldown_hours = _as_float(settings.get("idle_wsl_reset_cooldown_hours"), 6.0)
        checks["cooldown_hours"] = cooldown_hours
        last_reset = _read_cooldown_state()
        checks["last_reset_utc"] = last_reset.isoformat() if last_reset else None
        if last_reset is not None:
            elapsed_hours = (datetime.now(UTC) - last_reset).total_seconds() / 3600.0
            checks["hours_since_last_reset"] = round(elapsed_hours, 2)
            if elapsed_hours < cooldown_hours:
                reasons.append(
                    f"cooldown active — last reset {elapsed_hours:.1f}h ago "
                    f"< {cooldown_hours:.0f}h"
                )

        idx = _as_int(settings.get("pipeline_gpu_index"), 0)
        prom_url = settings.get("gpu_metrics_prometheus_url") or "http://localhost:9091"
        configured_trigger_gb = _as_float(
            settings.get("idle_wsl_reset_trigger_free_vram_gb"), 28.0,
        )
        # The reclaim exists to lift the render GPU back over the render floor
        # (media_render_min_free_vram_gb), so a trigger BELOW that floor is
        # incoherent: it leaves a dead band where a render defers (free < floor)
        # yet the reclaim that would fix it never fires (free >= trigger). Clamp
        # the effective trigger up to the floor so the two thresholds can't
        # silently drift into that gap (poindexter#881). The default (28)
        # already sits above the floor (25); the clamp only bites a
        # misconfiguration.
        render_floor_gb = _as_float(settings.get("media_render_min_free_vram_gb"), 25.0)
        trigger_free_gb = max(configured_trigger_gb, render_floor_gb)
        busy_threshold = _as_float(settings.get("gpu_busy_threshold_percent"), 30.0)
        checks["configured_trigger_free_vram_gb"] = configured_trigger_gb
        checks["render_floor_gb"] = render_floor_gb
        checks["trigger_free_vram_gb"] = trigger_free_gb
        if trigger_free_gb > configured_trigger_gb:
            checks["trigger_clamped_to_floor"] = True
        checks["gpu_busy_threshold_percent"] = busy_threshold

        total_mib = await _query_prometheus_scalar(
            prom_url, f'nvidia_gpu_memory_total_mib{{gpu="{idx}"}}',
        )
        used_mib = await _query_prometheus_scalar(
            prom_url, f'nvidia_gpu_memory_used_mib{{gpu="{idx}"}}',
        )
        util_pct = await _query_prometheus_scalar(
            prom_url, f'nvidia_gpu_utilization_percent{{gpu="{idx}"}}',
        )
        free_gb = (total_mib - used_mib) / 1024.0 if (total_mib is not None and used_mib is not None) else None
        checks["free_vram_gb"] = free_gb
        checks["gpu_utilization_percent"] = util_pct

        if free_gb is None or util_pct is None:
            reasons.append("Prometheus VRAM/utilization unreadable — failing closed")
        else:
            if free_gb >= trigger_free_gb:
                reasons.append(
                    f"retention not high — {free_gb:.1f} GB free >= {trigger_free_gb:.0f} GB trigger"
                )
            if util_pct >= busy_threshold:
                reasons.append(
                    f"GPU busy — {util_pct:.0f}% util >= {busy_threshold:.0f}% threshold "
                    "(external workload or an unowned pipeline run)"
                )
    finally:
        await conn.close()

    should_reset = not reasons
    if should_reset:
        reasons.append("all conditions satisfied")
    return {"should_reset": should_reset, "reasons": reasons, "checks": checks}


async def notify(text: str) -> None:
    """Post ``text`` to the operator Discord webhook.

    Deliberately bypasses ``notify_operator()`` / ``outbound_dispatcher``:
    both the framework path and its own legacy-Discord fallback resolve their
    DB service / SiteConfig through ``services.integrations.shared_context``
    module-level singletons that only the app lifespan (worker/brain) wires —
    a bare ``python scripts/...`` invocation never populates them, so the
    call would silently no-op. This uses the same underlying mechanism the
    fallback itself uses (``SiteConfig.get_secret("discord_ops_webhook_url")``
    + a direct POST) without the two layers of lifespan-only indirection.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "cofounder_agent"))
    from services.site_config import SiteConfig  # type: ignore

    pool = await asyncpg.create_pool(_resolve_db_url(), min_size=1, max_size=2)
    try:
        site_config = SiteConfig(pool=pool)
        await site_config.load(pool)
        webhook_url = await site_config.get_secret("discord_ops_webhook_url", "")
        if not webhook_url:
            print("[notify] discord_ops_webhook_url not configured — skipping", file=sys.stderr)
            return
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(webhook_url, json={"content": text})
    finally:
        await pool.close()


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--decide", action="store_true", help="evaluate reset conditions")
    mode.add_argument("--stamp-cooldown", action="store_true", help="record a reset just happened")
    mode.add_argument("--notify", metavar="TEXT", help="post TEXT to the operator Discord channel")
    parser.add_argument("--idle-minutes", type=float, default=None, help="required with --decide")
    args = parser.parse_args()

    if args.decide:
        if args.idle_minutes is None:
            parser.error("--decide requires --idle-minutes")
        try:
            result = await decide(args.idle_minutes)
        except Exception as exc:  # noqa: BLE001 — unreachable DB/Prometheus IS the signal
            print(json.dumps({"should_reset": False, "error": f"{type(exc).__name__}: {exc}"}))
            return 2
        print(json.dumps(result))
        return 0 if result["should_reset"] else 1

    if args.stamp_cooldown:
        _write_cooldown_state()
        print(json.dumps({"ok": True, "stamped_utc": datetime.now(UTC).isoformat()}))
        return 0

    if args.notify:
        try:
            await notify(args.notify)
        except Exception as exc:  # noqa: BLE001 — notify is best-effort, never blocks the reset
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
            return 2
        print(json.dumps({"ok": True}))
        return 0

    return 2  # unreachable — mutually_exclusive_group(required=True)


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(_main()))
