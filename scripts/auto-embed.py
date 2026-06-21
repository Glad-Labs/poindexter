"""Auto-Embed — thin runner over the plugin registry.

Runs as a Docker sidecar (see ``docker-compose.local.yml`` → ``auto-embed``
service) or ad-hoc from the host. Iterates every registered Tap, fetches
Documents, stores embeddings in pgvector. The actual per-source logic
lives under ``services/taps/`` as plugin implementations.

Pre-Phase-B history: this file used to be 1157 lines with 6 hardcoded
phases. Phase B (GitHub #66) collapsed it onto the plugin framework.
See ``docs/architecture/plugin-architecture.md`` for the design.

## Running

    # From the repo root (poetry env):
    poetry run python scripts/auto-embed.py

    # From the Docker sidecar (runs hourly):
    docker compose -f docker-compose.local.yml up -d auto-embed

## Environment

Only ``DATABASE_URL`` + optional ``OLLAMA_URL`` / ``IN_DOCKER``. Every
other knob — enable/disable per Tap, per-Tap config — lives in
``app_settings`` under ``plugin.tap.<name>``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx

# ---------------------------------------------------------------------------
# Make `poindexter` + `plugins` + `services` importable from the worker
# repo layout. Docker image COPYs them into /app.
# ---------------------------------------------------------------------------

_ALERT_COOLDOWN_FILE = Path.home() / ".gladlabs" / "auto-embed-last-alert.txt"
_ALERT_COOLDOWN_SECONDS = 6 * 3600

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _candidate in (
    _REPO_ROOT / "src" / "cofounder_agent",
    Path("/app"),
):
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

try:
    from poindexter.memory import MemoryClient
    _HAS_MEMORY_CLIENT = True
except ImportError as _imp_err:
    MemoryClient = None  # type: ignore[assignment,misc]
    _HAS_MEMORY_CLIENT = False
    _MEMORY_CLIENT_IMPORT_ERR = _imp_err

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Resolve DB URL via bootstrap.toml first, env vars as fallback.
_dsn = os.getenv("LOCAL_DATABASE_URL") or os.getenv("DATABASE_URL", "")
if not _dsn:
    try:
        from pathlib import Path as _Path
        for _p in _Path(__file__).resolve().parents:
            if (_p / "brain" / "bootstrap.py").is_file():
                if str(_p) not in sys.path:
                    sys.path.insert(0, str(_p))
                break
        from brain.bootstrap import resolve_database_url
        _dsn = resolve_database_url() or ""
    except Exception:
        pass
LOCAL_DSN = _dsn or "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain"

# URL localization — same pattern used by brain.docker_utils and the
# plugins ecosystem. When IN_DOCKER=true, rewrites localhost URLs to
# host.docker.internal so DB values that are correct from the host
# also work from inside the container.
IN_DOCKER = os.getenv("IN_DOCKER", "").lower() in ("1", "true", "yes") \
    or Path("/.dockerenv").exists()


def _localize_url(url: str) -> str:
    if not url or not IN_DOCKER:
        return url
    return (
        url.replace("://localhost:", "://host.docker.internal:")
           .replace("://127.0.0.1:", "://host.docker.internal:")
    )


OLLAMA_URL = _localize_url(os.getenv("OLLAMA_URL") or "http://127.0.0.1:11434")
EMBED_MODEL = "nomic-embed-text"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path.home() / ".gladlabs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "auto-embed.log"

logger = logging.getLogger("auto-embed")
logger.setLevel(logging.INFO)

_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
logger.addHandler(_file_handler)

# Always log to stdout so ``docker logs poindexter-auto-embed`` is useful.
# (Previously gated on ``isatty()``, which made the container silent because
# Docker's captured stdout is not a TTY. Still writes to the file handler
# above regardless.)
_stdout = logging.StreamHandler(sys.stdout)
_stdout.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_stdout)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------


def _read_discord_webhook() -> str:
    """Read discord_ops_webhook_url from bootstrap.toml without touching the DB."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return ""
    bootstrap = Path.home() / ".poindexter" / "bootstrap.toml"
    if not bootstrap.exists():
        return ""
    try:
        with bootstrap.open("rb") as fh:
            data = tomllib.load(fh)
        return data.get("discord_ops_webhook_url", "")
    except Exception:
        return ""


def _notify_db_failure(error: str) -> None:
    """POST a Discord alert if outside the cooldown window."""
    import time
    now = time.time()
    if _ALERT_COOLDOWN_FILE.exists():
        try:
            last = float(_ALERT_COOLDOWN_FILE.read_text().strip())
            if now - last < _ALERT_COOLDOWN_SECONDS:
                return
        except Exception:
            pass

    webhook = _read_discord_webhook()
    if not webhook:
        return

    import urllib.request, json as _json
    payload = _json.dumps({"content": f":warning: **auto-embed DB failure** — `{error}`\nCheck `~/.gladlabs/auto-embed.log` and `docker ps` for postgres-local."}).encode()
    try:
        req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        _ALERT_COOLDOWN_FILE.write_text(str(now))
    except Exception as e:
        logger.warning("Could not send Discord alert: %s", e)


async def check_ollama(client: httpx.AsyncClient) -> bool:
    """Return True if Ollama is reachable and the embed model is present."""
    try:
        resp = await client.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        found = any(EMBED_MODEL in m for m in models)
        if not found:
            logger.warning(
                "Ollama is up but model %r not found. Available: %s",
                EMBED_MODEL, models,
            )
        return found
    except Exception as e:
        logger.warning("Ollama not reachable: %s", e)
        return False


# ---------------------------------------------------------------------------
# Main — thin dispatcher over the plugin registry
# ---------------------------------------------------------------------------


async def main() -> None:
    """Run every registered Tap + the legacy OpenClaw SQLite phase.

    Every Tap lives under ``services/taps/`` and registers via
    ``importlib.metadata.entry_points``. Adding a new data source is
    ``pip install poindexter-tap-X`` + flipping the enabled flag in
    ``app_settings`` — no edits here.
    """
    start = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("Auto-Embed run started (plugin runner)")
    logger.info("=" * 60)

    http = httpx.AsyncClient()
    if not await check_ollama(http):
        logger.warning("Ollama down or model missing — skipping this run.")
        await http.aclose()
        return

    try:
        pool = await asyncpg.create_pool(LOCAL_DSN, min_size=1, max_size=4)
    except Exception as e:
        logger.error("Could not connect to local pgvector DB: %s", e)
        _notify_db_failure(str(e))
        await http.aclose()
        return

    local_conn = None
    try:
        from services.taps.runner import run_all

        if not _HAS_MEMORY_CLIENT or MemoryClient is None:
            logger.error(
                "poindexter.memory.MemoryClient unavailable (%s). "
                "Cannot run the Tap registry; exiting.",
                _MEMORY_CLIENT_IMPORT_ERR,
            )
            return

        async with MemoryClient(dsn=LOCAL_DSN, ollama_url=OLLAMA_URL) as mem:
            summary = await run_all(pool, mem)

        logger.info("--- Tap runner summary ---")
        for ts in summary.taps:
            status = "disabled" if not ts.enabled else "ok"
            logger.info(
                "  %-20s %s %d embedded, %d skipped, %d failed (%.2fs)",
                ts.name, status, ts.embedded, ts.skipped, ts.failed, ts.duration_s,
            )
            # Surface per-tap failure detail into auto-embed.log. The runner
            # logs the underlying exception on its own logger
            # (services.taps.runner), which does NOT propagate to this
            # script's "auto-embed" file handler — so without this the log
            # only ever showed the failure COUNT, never the cause (which hid
            # a NUL-byte INSERT crash on 3 sessions every run). A non-zero
            # failed count must come with its reason — feedback_no_silent_defaults.
            if ts.error:
                logger.warning("    %-20s extract error: %s", ts.name, ts.error)
            for failure in ts.failures:
                logger.warning("    %-20s store failure: %s", ts.name, failure)

        # OpenClaw SQLite is now a proper Tap (services/taps/openclaw_sqlite.py,
        # registered via poindexter.taps entry_points) — it appears above
        # with every other Tap. GitHub #79 follow-up: the precomputed
        # embeddings ride through via Document.precomputed_embedding so
        # Ollama isn't called twice.

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()

        logger.info("")
        logger.info(
            "  TOTAL: %d embedded, %d failed across %d tap(s) in %.1fs",
            summary.total_embedded, summary.total_failed,
            len(summary.taps), elapsed,
        )
        logger.info("Auto-Embed run complete.\n")

        # Liveness heartbeat — brain/auto_embed_watch.py reads the newest
        # auto_embed_succeeded audit_log row to detect a wedged/dead embedder
        # (a hung Tap, a stuck Ollama call, a crashed container). Stamped on
        # every completed run regardless of embedded count: "the embedder
        # cycled" is the signal, not "there was new content to embed".
        # Best-effort — a heartbeat-write failure must never fail the run.
        try:
            await pool.execute(
                "INSERT INTO audit_log (event_type, source, details, severity)"
                " VALUES ($1, $2, $3::jsonb, $4)",
                "auto_embed_succeeded",
                "auto-embed",
                json.dumps({
                    "embedded": summary.total_embedded,
                    "failed": summary.total_failed,
                    "taps": len(summary.taps),
                    "elapsed_s": round(elapsed, 1),
                }),
                "info",
            )
        except Exception as hb_err:
            logger.warning("Could not write auto_embed heartbeat: %s", hb_err)

    except Exception as e:
        logger.exception("Fatal error: %s", e)
    finally:
        if local_conn:
            await local_conn.close()
        await pool.close()
        await http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
