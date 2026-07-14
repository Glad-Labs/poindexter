"""One-off: add the performance_report_custom (page+query) stream to the
gsc_main tap row so GSC query-dimension data starts landing in
external_metrics.

Part of Glad-Labs/poindexter#764. Idempotent -- safe to re-run (it computes
the target config and upserts the full row each time). Does NOT flip
``seo.query_ingestion.enabled`` -- that master switch is a separate, explicit
step once the ingested data has been spot-checked.

Run once, against prod, after this change is deployed:

    poetry run python scripts/enable_gsc_query_dimension.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "cofounder_agent"))

from services import declarative_config_service as dcs  # noqa: E402

_SURFACE = "taps"
_TAP_NAME = "gsc_main"
_STREAM = "performance_report_custom"


def _resolve_db_url() -> str:
    """Mirrors scripts/check_taps.py -- bootstrap.toml is canonical (#198),
    force IPv4 so Windows doesn't resolve localhost to the IPv6 proxy."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        try:
            from brain.bootstrap import resolve_database_url  # type: ignore

            dsn = resolve_database_url()
        except Exception as exc:  # bootstrap is best-effort on the host
            print(f"[dsn] bootstrap resolution failed ({exc}); using default", file=sys.stderr)
            dsn = None
    if not dsn:
        dsn = "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain"
    return dsn.replace("@localhost:", "@127.0.0.1:")


async def main() -> None:
    pool = await asyncpg.create_pool(_resolve_db_url())
    try:
        row = await dcs.get_row(pool, _SURFACE, _TAP_NAME)
        if row is None:
            raise SystemExit(f"no tap named {_TAP_NAME!r} -- nothing to update")

        config = dict(row.get("config") or {})

        streams = list(config.get("streams") or [])
        if _STREAM not in streams:
            streams.append(_STREAM)
        config["streams"] = streams

        mappings = dict(config.get("metrics_mapping") or {})
        mappings[_STREAM] = {
            "source": "google_search_console",
            "date_field": "date",
            "post_field": "page",
            "metric_fields": ["impressions", "clicks", "ctr", "position"],
            "dimension_fields": ["query", "site_url", "search_type"],
        }
        config["metrics_mapping"] = mappings

        field_selection = dict(config.get("field_selection") or {})
        field_selection[_STREAM] = ["page", "query"]
        config["field_selection"] = field_selection

        updated = await dcs.upsert_row(pool, _SURFACE, {**row, "config": config})
        print(f"{_TAP_NAME}: streams={updated['config']['streams']}")
        print(f"{_TAP_NAME}: field_selection={updated['config']['field_selection']}")
        print(
            "Next: spot-check one tap run (`poindexter taps run gsc_main`), "
            "confirm external_metrics rows with dimensions ? 'query' look sane, "
            "then set seo.query_ingestion.enabled=true when ready."
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
