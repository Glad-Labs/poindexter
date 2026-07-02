"""brain/data_freshness_probe.py — dead-man's switch for DATA feeds.

2026-07-01 observability audit: the stack alerts when a *service* dies
but not when a *data feed* does — a dashboard showing a number and a
dashboard showing a stale number look identical, which is exactly how
trust in the whole observability surface erodes. The one feed that had
a freshness watchdog (``corsair_feed_probe``, #868) is the pattern this
probe generalizes: per-feed ``max(timestamp)`` age vs an app_settings
threshold, finding emitted on the fresh→stale EDGE only (state in
``brain_knowledge``), routed via the worker's ``findings_alert_router``
(warning → Discord) with the brain's ``alert_dispatcher`` deduping.

Feeds are declarative JSON in ``app_settings.data_freshness_feeds``:

.. code-block:: json

    [{"name": "cost_logs", "table": "cost_logs", "column": "created_at",
      "threshold_minutes": 180},
     {"name": "gpu_metrics", "table": "gpu_metrics", "column": "timestamp",
      "threshold_minutes": 30,
      "filter_column": null, "filter_value": null}]

- ``table`` / ``column`` / ``filter_column`` are validated as SQL
  identifiers (``^[a-z_][a-z0-9_]*$``) before interpolation; the
  optional ``filter_value`` is always bound as a query parameter.
- A feed with zero rows is **not assessed** (mirrors corsair: an
  operator who never enabled the producer gets no false alarms).
- Findings use kind ``data_feed_stale`` (dot-free per #756 so a
  per-kind ``findings.data_feed_stale.delivery`` policy can attach) and
  a stable per-feed ``dedup_key``.

The ``corsair_csv`` sensor feed keeps its dedicated probe
(``corsair_feed_probe``) — it predates this one and its threshold key
(``corsair_feed_stale_threshold_minutes``) is already operator-tuned;
the default feed list here deliberately excludes ``sensor_samples`` to
avoid double findings. Folding it in is a follow-up once this probe has
a track record.

Standalone — stdlib only (asyncpg pool is injected by the daemon).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("brain.data_freshness_probe")

ENABLED_SETTING_KEY = "data_freshness_probe_enabled"
FEEDS_SETTING_KEY = "data_freshness_feeds"

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_STATE_ENTITY_PREFIX = "data_freshness_watchdog"

# In-code fallback mirroring the settings_defaults.py seed (drift-guarded
# by test_data_freshness_probe.test_seeded_default_matches_in_code_fallback)
# — used when the app_settings row is missing/unparseable so a fresh brain
# still watches the core feeds. Thresholds are deliberately generous
# (page on DEAD, not on slow): cost_logs is written every 5-min brain
# cycle (electricity cost) so 3h = many missed cycles; gpu_metrics comes
# from the always-on host gpu-scraper daemon; atom_runs goes quiet only
# when the content pipeline is dark for half a day; page_views can be
# legitimately quiet, so only a 2-day silence is worth a look.
DEFAULT_FEEDS: list[dict[str, Any]] = [
    {"name": "cost_logs", "table": "cost_logs", "column": "created_at",
     "threshold_minutes": 180},
    {"name": "gpu_metrics", "table": "gpu_metrics", "column": "timestamp",
     "threshold_minutes": 30},
    {"name": "atom_runs", "table": "atom_runs", "column": "created_at",
     "threshold_minutes": 720},
    {"name": "page_views", "table": "page_views", "column": "created_at",
     "threshold_minutes": 2880},
]


async def _read_setting(pool: Any, key: str, default: str = "") -> str:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001 — probe must never crash the cycle
        logger.warning("[data_freshness] setting read %s failed: %s", key, exc)
        return default
    return str(val) if val else default


def _parse_feeds(raw: str) -> list[dict[str, Any]]:
    """Parse + validate the feeds JSON; invalid entries are dropped loudly."""
    if not raw.strip():
        return _validate_entries(DEFAULT_FEEDS)
    try:
        parsed = json.loads(raw)
        assert isinstance(parsed, list)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[data_freshness] %s is not a JSON list — using in-code defaults",
            FEEDS_SETTING_KEY,
        )
        return _validate_entries(DEFAULT_FEEDS)
    return _validate_entries(parsed)


def _validate_entries(parsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize + identifier-validate feed entries; drop bad ones loudly."""
    feeds: list[dict[str, Any]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        table = str(entry.get("table") or "").strip()
        column = str(entry.get("column") or "").strip()
        filter_column = str(entry.get("filter_column") or "").strip()
        try:
            threshold = int(entry.get("threshold_minutes", 0))
        except (TypeError, ValueError):
            threshold = 0
        ok = (
            bool(name)
            and _IDENTIFIER_RE.match(table)
            and _IDENTIFIER_RE.match(column)
            and (not filter_column or _IDENTIFIER_RE.match(filter_column))
            and threshold > 0
        )
        if not ok:
            logger.warning(
                "[data_freshness] dropping invalid feed entry %r "
                "(name/table/column must be sql identifiers, "
                "threshold_minutes > 0)", entry,
            )
            continue
        feeds.append({
            "name": name, "table": table, "column": column,
            "threshold_minutes": threshold,
            "filter_column": filter_column or None,
            "filter_value": entry.get("filter_value"),
        })
    return feeds


async def _feed_age_minutes(pool: Any, feed: dict[str, Any]) -> float | None:
    """Minutes since the feed's newest row, or None (no rows / query error).

    Identifiers were regex-validated in ``_parse_feeds``; the optional
    filter VALUE is bound as a parameter.
    """
    where = ""
    args: list[Any] = []
    if feed["filter_column"] and feed["filter_value"] is not None:
        where = f"WHERE {feed['filter_column']} = $1"
        args.append(str(feed["filter_value"]))
    sql = (
        f"SELECT EXTRACT(EPOCH FROM (now() - max({feed['column']}))) / 60.0 "
        f"AS age_min FROM {feed['table']} {where}"
    )
    try:
        row = await pool.fetchrow(sql, *args)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[data_freshness] query for feed %s failed: %s",
            feed["name"], exc,
        )
        return None
    if row is None or row["age_min"] is None:
        return None
    return float(row["age_min"])


async def _read_prev_state(pool: Any, feed_name: str) -> str | None:
    try:
        row = await pool.fetchrow(
            "SELECT value FROM brain_knowledge "
            "WHERE entity = $1 AND attribute = 'last_state'",
            f"{_STATE_ENTITY_PREFIX}:{feed_name}",
        )
    except Exception as exc:  # noqa: BLE001
        # Treat unknown as no-prior — worst case is one duplicate finding —
        # but say so loudly: a persistently failing state read would
        # otherwise silently re-emit every cycle.
        logger.warning(
            "[data_freshness] prev-state read for %s failed (%s) — "
            "treating as no-prior", feed_name, exc,
        )
        return None
    return row["value"] if row else None


async def _write_state(pool: Any, feed_name: str, state: str) -> None:
    try:
        await pool.execute(
            """
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
            VALUES ($1, 'last_state', $2, 1.0, 'data_freshness_probe')
            ON CONFLICT (entity, attribute)
              DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            f"{_STATE_ENTITY_PREFIX}:{feed_name}", state,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[data_freshness] state write for %s failed: %s — edge "
            "detection may re-emit next cycle", feed_name, exc,
        )


async def _emit_stale_finding(
    pool: Any, feed: dict[str, Any], age_min: float,
) -> None:
    details = {
        "kind": "data_feed_stale",
        "title": (
            f"Data feed stale: {feed['name']} "
            f"({age_min:.0f}m old, threshold {feed['threshold_minutes']}m)"
        ),
        "body": (
            f"No new {feed['table']}.{feed['column']} row in "
            f"{age_min:.0f} minutes (threshold "
            f"{feed['threshold_minutes']}m). Every dashboard panel reading "
            f"this table is now showing stale data WITHOUT looking broken — "
            f"check the producer for this feed and restart it. Tune or "
            f"remove the feed via app_settings.{FEEDS_SETTING_KEY}."
        ),
        "dedup_key": f"data_feed_stale:{feed['name']}",
        "extra": {
            "feed": feed["name"],
            "age_minutes": round(age_min, 1),
            "threshold_minutes": feed["threshold_minutes"],
        },
    }
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ('finding', 'data_freshness_probe', $1::jsonb, 'warning')",
            json.dumps(details),
        )
        logger.warning(
            "[data_freshness] feed %s STALE (%.0fm > %dm) — finding emitted",
            feed["name"], age_min, feed["threshold_minutes"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[data_freshness] finding insert failed: %s", exc)


async def run_data_freshness_probe(pool: Any) -> dict[str, Any]:
    """One pass over every configured feed. Never raises.

    Returns ``{ok, detail, feeds: {name: {age_minutes, threshold, state}}}``
    for the brain's ``run_cycle`` aggregation — ``ok=False`` when at least
    one assessed feed is stale.
    """
    enabled = (await _read_setting(pool, ENABLED_SETTING_KEY, "true")).lower()
    if enabled in ("false", "0", "no", "off"):
        return {"ok": True, "detail": "disabled", "feeds": {}}

    feeds = _parse_feeds(await _read_setting(pool, FEEDS_SETTING_KEY, ""))
    results: dict[str, dict[str, Any]] = {}
    stale_feeds: list[str] = []

    for feed in feeds:
        age_min = await _feed_age_minutes(pool, feed)
        if age_min is None:
            # Zero rows ever (producer not enabled for this operator) or a
            # query error — not an alert condition, mirrors corsair_feed.
            results[feed["name"]] = {"state": "not_assessed"}
            continue

        is_stale = age_min > feed["threshold_minutes"]
        new_state = "stale" if is_stale else "fresh"
        prev_state = await _read_prev_state(pool, feed["name"])

        # Edge-triggered: one finding per stale episode, not per cycle.
        # prev=None + stale still emits so a feed that is already dead at
        # brain boot gets surfaced.
        if is_stale and prev_state != "stale":
            await _emit_stale_finding(pool, feed, age_min)
        elif not is_stale and prev_state == "stale":
            logger.info(
                "[data_freshness] feed %s recovered (%.0fm old)",
                feed["name"], age_min,
            )
        if new_state != prev_state:
            await _write_state(pool, feed["name"], new_state)

        results[feed["name"]] = {
            "age_minutes": round(age_min, 1),
            "threshold_minutes": feed["threshold_minutes"],
            "state": new_state,
        }
        if is_stale:
            stale_feeds.append(feed["name"])

    detail = (
        f"{len(results)} feed(s) checked; "
        + (f"STALE: {', '.join(stale_feeds)}" if stale_feeds else "all fresh")
    )
    return {"ok": not stale_feeds, "detail": detail, "feeds": results}
