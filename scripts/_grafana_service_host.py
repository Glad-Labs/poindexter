"""Resolve ``app_settings.operator_service_host`` for ``scripts/start-stack.sh``.

Prints the hostname the operator's BROWSER can reach the sibling service UIs
on (Prefect :4200, Langfuse :3010, GlitchTip :8080, pgAdmin :18443, Prometheus
:9091, worker API :8002) to stdout. ``start-stack.sh`` writes it into
``.poindexter-grafana.env`` as ``POINDEXTER_SERVICE_HOST``; the Grafana
entrypoint substitutes it for the ``__POINDEXTER_SERVICE_HOST__`` placeholder
in every dashboard JSON at container start.

## Why this exists at all

Grafana does not interpolate environment variables inside dashboard JSON —
``${__env.X}`` comes back literal (verified on grafana-oss 13.0.1). So the
only way a dashboard link's host can be configuration rather than a hardcoded
literal is to rewrite the file before Grafana reads it. Hardcoding was the bug:
``localhost`` resolves only for a browser ON the Docker host, which is why the
Prefect/Langfuse/GlitchTip links were dead from a phone.

This mirrors ``_grafana_webhook_token.py`` — same host-side, pre-worker,
bootstrap.toml-driven DB read — but the value is NOT a secret, so there is no
pgcrypto decrypt step.

## Failure modes (all soft — emits empty string)

bootstrap.toml missing/unparseable, ``database_url`` absent, asyncpg missing,
Postgres unreachable, or the row unset on a fresh install. Empty output means
``start-stack.sh`` leaves ``POINDEXTER_SERVICE_HOST`` unset, and the Grafana
entrypoint falls back to ``localhost`` — i.e. exactly the historical behaviour.
That is a safe default rather than a silent wrong one: it is correct for a
browser on the host, and it is what a fresh install wants. It is NOT
``feedback_no_silent_defaults`` territory, because nothing is broken by it that
was not already broken before this setting existed.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

_BOOTSTRAP_PATH = Path.home() / ".poindexter" / "bootstrap.toml"
_KEY = "operator_service_host"

# A hostname only. Anything with a scheme, port, path, or whitespace is a
# misconfiguration that would be substituted straight into every dashboard URL
# and produce a silently broken link, so refuse it loudly and fall back.
_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _warn(msg: str) -> None:
    sys.stderr.write(f"[grafana_service_host] {msg}\n")


def _load_bootstrap() -> dict[str, str]:
    try:
        if sys.version_info >= (3, 11):
            import tomllib as _tomllib
        else:  # pragma: no cover — tomli only on 3.10
            import tomli as _tomllib  # type: ignore[import-not-found]
    except ImportError:
        _warn("tomllib/tomli unavailable; cannot parse bootstrap.toml")
        return {}
    if not _BOOTSTRAP_PATH.is_file():
        return {}
    try:
        with _BOOTSTRAP_PATH.open("rb") as f:
            data = _tomllib.load(f)
    except Exception as exc:  # noqa: BLE001
        _warn(f"bootstrap.toml parse failed: {exc}")
        return {}
    return {
        str(k): str(v).strip()
        for k, v in data.items()
        if isinstance(v, (str, int, float))
    }


async def _fetch(dsn: str) -> str:
    try:
        import asyncpg
    except ImportError:
        _warn("asyncpg unavailable on host Python")
        return ""
    try:
        conn = await asyncpg.connect(dsn, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        _warn(f"postgres connect failed ({type(exc).__name__}): {exc}")
        return ""
    try:
        value = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", _KEY,
        )
    except Exception as exc:  # noqa: BLE001
        _warn(f"app_settings query failed ({type(exc).__name__}): {exc}")
        return ""
    finally:
        await conn.close()
    return (value or "").strip()


def main() -> None:
    cfg = _load_bootstrap()
    dsn = cfg.get("database_url") or os.getenv("DATABASE_URL") or ""
    if not dsn:
        sys.stdout.write("")
        return
    host = asyncio.run(_fetch(dsn))
    if host and not _HOST_RE.match(host):
        _warn(
            f"app_settings.{_KEY}={host!r} is not a bare hostname "
            "(no scheme/port/path allowed); ignoring",
        )
        host = ""
    sys.stdout.write(host)


if __name__ == "__main__":
    main()
