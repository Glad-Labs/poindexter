"""Handler: ``tap.corsair_csv`` — ingest Corsair iCUE LINK CSV exports.

Watches the operator's iCUE log directory (default
``C:\\Users\\mattm\\sensor_logs``), parses curated columns from the
newest CSV, and INSERTs one ``sensor_samples`` row per (timestamp,
metric) cell. Idempotent via ``(source, sampled_at, metric_name)``
uniqueness — re-running on already-processed rows is a no-op.

## Why a tap (not a node-exporter)

The data Corsair surfaces (rail-level PSU current + efficiency,
coolant-loop temps, per-fan RPMs) isn't available from
windows_exporter or nvidia-smi-exporter. iCUE LINK has the firmware
relationship with the hardware; we read what it exports.

CSV ingestion (vs. a custom Prometheus exporter) is the
fastest-to-ship option that gives Grafana panels without a new daemon.
Trade-off: data arrives in batches at iCUE's logging cadence, not on
Prometheus's scrape cadence. For the operator-monitoring use case
that's fine (Matt watches Grafana for *trends*, not millisecond
ground truth).

## State cursor

Per-tap state lives in ``external_taps.state`` as JSON:

::

    {
      "current_file": "corsair_cue_20260511_19_05_19.csv",
      "byte_offset":  908880,
      "rows_processed_total": 1663,
      "last_sample_at": "2026-05-12T08:56:48+00:00"
    }

When the operator restarts iCUE a new CSV appears in the directory.
The handler detects this by sorting the glob match by mtime; the
newest file wins. If the cursor's ``current_file`` no longer matches,
state resets ``byte_offset`` to 0 and starts fresh on the new file.

## Per-tap config

Lives under ``external_taps.config``. Required keys:

- ``directory``: path on the worker host
- ``filename_glob``: glob pattern, default ``corsair_cue_*.csv``
- ``poll_interval_minutes``: tap is a no-op until N minutes since
  last successful run. Default 5.
- ``max_rows_per_run``: safety cap so a 100k-row backlog can't pin
  the worker indefinitely on first run. Default 10000.
- ``metrics``: dict mapping CSV column name → ``{name, unit}``.
  Only mapped columns get extracted; the other ~60 are ignored.

## Failure posture

- Directory missing → ``{"records": 0}`` + log warning. The tap will
  retry on the next cadence; operator notified via brain probes if
  this persists.
- CSV file unreadable mid-parse → log + commit what was parsed; the
  byte_offset still advances so we don't retry the bad rows forever.
- Cell value unparseable (e.g. "—" placeholder) → skipped silently;
  bad data on individual cells shouldn't drop the whole row.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)


# Corsair surrounds numeric values with their unit string:
#   "53.75°C"  "1.34V"  "10%"  "0RPM"  "234W"  "19.50A"
# Strip the suffix to a float. Anything that doesn't match is None.
_VALUE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(°[A-Z]|V|A|W|RPM|%|°C)?\s*$")


# iCUE's CSV uses Windows locale date-times. Format examples:
#   "11/5/2026 19:05:19 PM"
#   "12/5/2026 08:56:48 AM"
# That's d/m/yyyy with a redundant AM/PM tag on the 24h clock.
# We parse via two attempts because operator locales vary.
_TIMESTAMP_FORMATS = [
    "%d/%m/%Y %H:%M:%S %p",
    "%m/%d/%Y %H:%M:%S %p",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
]


def _parse_value(raw: str) -> float | None:
    """Strip Corsair's unit suffix and return a float, or None on parse fail.

    Examples
    --------
    >>> _parse_value("53.75°C")
    53.75
    >>> _parse_value("0RPM")
    0.0
    >>> _parse_value("12V")
    12.0
    >>> _parse_value("—") is None
    True
    """
    if raw is None:
        return None
    m = _VALUE_RE.match(raw)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _parse_timestamp(
    raw: str, *, local_offset_hours: float = 0.0,
) -> datetime | None:
    """Parse a Corsair timestamp string and convert to true UTC.

    Corsair doesn't emit a timezone — the value is the operator's
    Windows wall-clock at the moment iCUE wrote the row. To land
    the data on a UTC axis Grafana's ``$__timeFilter()`` will match
    against ``now()`` correctly, we subtract the operator's local
    UTC offset.

    Args:
        raw: timestamp string from the CSV.
        local_offset_hours: operator's UTC offset (e.g. -4 for EDT,
            -5 for EST). Default 0 = treat string as UTC (only correct
            for operators in Europe/Reykjavik). Set via
            ``external_taps.config.local_timezone_offset_hours``.

    Returns:
        UTC-aware datetime, or None on parse failure.
    """
    if not raw:
        return None
    raw = raw.strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            # The CSV wall-clock is the operator's local time. Subtract
            # the offset (EDT -4 means local was 4h behind UTC, so add
            # 4h to reach UTC).
            utc_dt = dt - _local_offset_delta(local_offset_hours)
            return utc_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _local_offset_delta(hours: float):
    """Convert a float-hours offset (e.g. -4.0 for EDT) to a timedelta."""
    from datetime import timedelta

    return timedelta(hours=hours)


def _derive_offset_hours_from_mtime(path: Path) -> float | None:
    """Auto-derive the operator's UTC offset from the CSV file's mtime.

    poindexter#484: iCUE writes naive wall-clock timestamps. The OS
    records mtime in true UTC. So:

        offset_hours ≈ (mtime_utc - last_row_local_naive) / 1 hour

    Rounded to the nearest 15-minute boundary because every real
    timezone is quarter-hour aligned. The sign is negated to match the
    existing ``local_timezone_offset_hours`` convention (-4 for EDT,
    -5 for EST, +9 for JST etc.).

    Returns ``None`` on any parse failure / unreadable file; callers
    fall back to the explicit config knob.
    """
    try:
        st = path.stat()
        file_mtime_utc = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        # Read the tail of the file so we sample a RECENT row, not the
        # first row written by a long-lived session. 8 KB is plenty for
        # one CSV row even with wide metric maps.
        size = st.st_size
        if size == 0:
            return None
        with path.open("rb") as fh:
            fh.seek(max(0, size - 8192))
            tail_bytes = fh.read()
        tail = tail_bytes.decode("utf-8-sig", errors="replace")
        # The last non-empty line is the most recent CSV row. Strip
        # whitespace so a trailing newline doesn't mask a real row.
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        if not lines:
            return None
        last_line = lines[-1]
        first_col = last_line.split(",", 1)[0].strip()
        naive_dt: datetime | None = None
        for fmt in _TIMESTAMP_FORMATS:
            try:
                naive_dt = datetime.strptime(first_col, fmt)
                break
            except ValueError:
                continue
        if naive_dt is None:
            return None
        # mtime is wall-clock UTC; naive_dt is the operator's local
        # wall-clock. Their difference is how far AHEAD UTC is of local.
        # Negate to land on the config convention (negative for west of
        # UTC, positive for east).
        delta_seconds = (
            file_mtime_utc.replace(tzinfo=None) - naive_dt
        ).total_seconds()
        offset_hours = -round(delta_seconds / 900) * 0.25
        # Sanity clamp — real timezones are in [-12, +14]. A value past
        # this range means the file mtime and the last row clock have
        # drifted by something other than a TZ flip (clock skew, manual
        # backdating, etc.). Don't apply a nonsense offset.
        if not (-12.0 <= offset_hours <= 14.0):
            return None
        return offset_hours
    except Exception as exc:  # noqa: BLE001 — derivation is best-effort
        logger.warning(
            "[tap.corsair_csv] mtime-based TZ derivation failed: %s", exc,
        )
        return None


def _find_latest_csv(directory: Path, glob: str) -> Path | None:
    """Return the newest matching CSV in ``directory``, or None when empty."""
    if not directory.is_dir():
        return None
    candidates = sorted(
        directory.glob(glob),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _is_due(state: dict[str, Any], poll_interval_minutes: int) -> bool:
    """Tap-side cadence gate so the row stays the source of truth.

    The tap_runner runs the whole row set every N min, but each row
    decides whether it's actually due. This lets operators tune
    individual taps without touching the runner's schedule.
    """
    if poll_interval_minutes <= 0:
        return True  # 0 / negative = always run, for tests + manual triggers
    last = state.get("last_sample_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60.0
    return age_minutes >= poll_interval_minutes


@register_handler("tap", "corsair_csv")
async def corsair_csv(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Parse new rows from the active Corsair CSV and INSERT into sensor_samples.

    The handler signature is set by the integrations dispatcher;
    ``payload`` is unused here (taps don't have an inbound payload —
    they pull, not push). ``site_config`` is unused because everything
    we need lives in ``row.config`` / ``row.state``.

    Returns ``{"records": <int>, "file": <name>, "bytes_advanced": <int>}``
    so the runner can sum totals across taps.
    """
    # asyncpg returns jsonb as a str on some pool configs; parse defensively
    # the same way tap_external_metrics_writer + tap_singer_subprocess do.
    import json as _json

    config = row.get("config") or {}
    if isinstance(config, str):
        try:
            config = _json.loads(config)
        except _json.JSONDecodeError:
            config = {}
    raw_state = row.get("state") or {}
    if isinstance(raw_state, str):
        try:
            raw_state = _json.loads(raw_state)
        except _json.JSONDecodeError:
            raw_state = {}
    state = dict(raw_state)

    directory = Path(config.get("directory", "")).expanduser()
    glob = config.get("filename_glob", "corsair_cue_*.csv")
    poll_interval_minutes = int(config.get("poll_interval_minutes", 5))
    max_rows_per_run = int(config.get("max_rows_per_run", 10000))
    metrics_map: dict[str, dict[str, str]] = config.get("metrics") or {}
    # Operator's local UTC offset for the CSV's naive timestamps.
    # Default 0 = "treat as UTC"; only correct in zero-offset zones.
    # Matt's PC is EDT (-4) in summer / EST (-5) in winter.
    config_local_offset_hours = float(
        config.get("local_timezone_offset_hours", 0.0)
    )
    auto_derive_tz = bool(config.get("auto_derive_timezone_offset", True))
    # Default before the rotation/derivation block runs; overwritten
    # below once `latest` is known. Sustains the early-return paths
    # (no files / not due) which never reach the derivation block.
    local_offset_hours = config_local_offset_hours

    if not directory or str(directory) == ".":
        raise ValueError("tap.corsair_csv: row.config.directory is required")

    if not _is_due(state, poll_interval_minutes):
        return {"records": 0, "file": state.get("current_file"), "reason": "not due"}

    latest = _find_latest_csv(directory, glob)
    if latest is None:
        logger.warning(
            "[tap.corsair_csv] no files matching %s in %s — nothing to ingest",
            glob, directory,
        )
        return {"records": 0, "file": None, "reason": "no files found"}

    # File rotation: iCUE restarts produce a new dated CSV. Detect it
    # by name change and reset the byte cursor + derived-offset cache.
    prior_file = state.get("current_file")
    byte_offset = int(state.get("byte_offset", 0))
    offset_cache: dict[str, float] = dict(state.get("derived_offset_hours_cache") or {})
    current_file: str = str(latest.name)
    if prior_file != current_file:
        logger.info(
            "[tap.corsair_csv] file rotated %s → %s — resetting byte_offset + offset cache",
            prior_file, current_file,
        )
        byte_offset = 0
        # Drop the prior file's cached offset on rotation — the new
        # file may have been written under a DST flip.
        offset_cache.pop(current_file, None)

    # poindexter#484 — auto-derive TZ offset from file mtime so the
    # operator doesn't have to flip local_timezone_offset_hours twice
    # a year at DST. Cache per-filename so we only derive once per
    # rotation. Fall back to the explicit config knob on any failure.
    if auto_derive_tz:
        cached = offset_cache.get(current_file)
        if cached is not None:
            local_offset_hours = float(cached)
        else:
            derived = _derive_offset_hours_from_mtime(latest)
            if derived is not None:
                logger.info(
                    "[tap.corsair_csv] auto-derived TZ offset %.2fh for %s "
                    "(file mtime vs last-row wall-clock)",
                    derived, current_file,
                )
                offset_cache[current_file] = derived
                local_offset_hours = derived
            else:
                local_offset_hours = config_local_offset_hours
    else:
        local_offset_hours = config_local_offset_hours

    file_size = latest.stat().st_size
    if file_size <= byte_offset:
        # File hasn't grown since last poll, or somehow shrank
        # (truncate?). Either way, nothing to do.
        return {"records": 0, "file": current_file, "reason": "no new bytes"}

    # Read the new bytes only. The header line still gets read on the
    # first run (byte_offset=0) but is skipped after that by tracking
    # whether the cursor sits exactly at row-start vs. mid-file.
    with latest.open("rb") as fh:
        fh.seek(byte_offset)
        new_bytes = fh.read(min(file_size - byte_offset, 8 * 1024 * 1024))
    # iCUE CSV has a UTF-8 BOM only on the very first line; strip it
    # defensively in case we're reading from offset 0.
    text = new_bytes.decode("utf-8-sig", errors="replace")

    # On a continuation run (byte_offset > 0) the first chunk almost
    # always starts mid-row. csv.reader handles partial rows by simply
    # dropping the malformed initial line on the next call — but we
    # want to preserve byte-exact resume. Compromise: advance the
    # offset to the first newline before parsing.
    if byte_offset > 0:
        nl_idx = text.find("\n")
        if nl_idx == -1:
            # Less than a single line of new data — nothing committable
            # yet. Don't advance the cursor; we'll re-read these bytes
            # next cycle when more has accumulated.
            return {"records": 0, "file": current_file, "reason": "partial row"}
        text = text[nl_idx + 1:]
        byte_offset += nl_idx + 1  # Move past the dropped trailing slice

    # Headers — re-read from disk on every run since we only have a
    # body slice. The first row of the file is authoritative.
    with latest.open("r", encoding="utf-8-sig", newline="") as fh:
        header_reader = csv.reader(fh)
        try:
            headers = next(header_reader)
        except StopIteration:
            return {"records": 0, "file": current_file, "reason": "empty file"}

    # Map header position → mapped metric metadata. Done once per run.
    header_to_metric: dict[int, dict[str, str]] = {}
    for idx, header in enumerate(headers):
        meta = metrics_map.get(header)
        if meta:
            header_to_metric[idx] = meta
    if not header_to_metric:
        logger.warning(
            "[tap.corsair_csv] no headers from row.config.metrics matched "
            "the CSV — first 5 headers: %s",
            headers[:5],
        )
        # Still advance the cursor so we don't re-read the same bytes
        # forever. The operator likely needs to fix the metrics map.
        new_state = {
            **state,
            "current_file": current_file,
            "byte_offset": file_size,
            "derived_offset_hours_cache": offset_cache,
        }
        await _save_state(pool, row["id"], new_state)
        return {"records": 0, "file": current_file, "reason": "no metrics matched"}

    rows_inserted = 0
    rows_parsed = 0  # data rows that yielded samples; excludes header + blanks
    cap_hit = False
    last_sample_at: datetime | None = None
    reader = csv.reader(io.StringIO(text))
    samples_to_insert: list[tuple] = []

    for csv_row in reader:
        if not csv_row:
            continue
        ts = _parse_timestamp(csv_row[0], local_offset_hours=local_offset_hours)
        if ts is None:
            # Header row (byte_offset=0) or blank — skip without counting
            # against the per-run cap. The cap is about real ingestion
            # work, not header parsing.
            continue
        if rows_parsed >= max_rows_per_run:
            logger.info(
                "[tap.corsair_csv] hit max_rows_per_run=%d cap; remaining "
                "rows deferred to next cycle",
                max_rows_per_run,
            )
            cap_hit = True
            break
        rows_parsed += 1
        last_sample_at = ts
        for idx, meta in header_to_metric.items():
            if idx >= len(csv_row):
                continue
            value = _parse_value(csv_row[idx])
            if value is None:
                continue
            samples_to_insert.append(
                (
                    "corsair_csv",
                    meta["name"],
                    value,
                    meta.get("unit"),
                    ts,
                )
            )

    if samples_to_insert:
        async with pool.acquire() as conn:
            # COPY would be faster but copy_records_to_table requires
            # the table OID lookup which mocks struggle with. INSERT
            # ... ON CONFLICT keeps the idempotency guarantee from the
            # unique index intact and runs fast enough at this volume
            # (~75 cells × 1600 rows × poll-frequency = ~24 inserts/sec
            # steady-state).
            await conn.executemany(
                """
                INSERT INTO sensor_samples
                    (source, metric_name, metric_value, unit, sampled_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (source, sampled_at, metric_name) DO NOTHING
                """,
                samples_to_insert,
            )
        rows_inserted = len(samples_to_insert)

    # On cap-hit we keep byte_offset where it was so next run picks up
    # exactly where we left off. On normal completion we jump to EOF.
    new_byte_offset = byte_offset if cap_hit else file_size
    bytes_advanced = new_byte_offset - byte_offset

    new_state = {
        **state,
        "current_file": current_file,
        "byte_offset": new_byte_offset,
        "rows_processed_total": int(state.get("rows_processed_total", 0))
        + rows_parsed,
        "last_sample_at": (
            last_sample_at.isoformat()
            if last_sample_at is not None
            else state.get("last_sample_at")
        ),
        # poindexter#484 — persist the derived TZ offset cache so the
        # next cycle skips re-derivation until the file rotates.
        "derived_offset_hours_cache": offset_cache,
    }
    await _save_state(pool, row["id"], new_state)

    logger.info(
        "[tap.corsair_csv] %s: parsed %d row(s), inserted %d sample(s), "
        "byte_offset %d → %d",
        current_file, rows_parsed, rows_inserted, byte_offset, new_byte_offset,
    )
    return {
        "records": rows_inserted,
        "file": current_file,
        "bytes_advanced": bytes_advanced,
        "rows_parsed": rows_parsed,
    }


async def _save_state(pool: Any, row_id: Any, state: dict[str, Any]) -> None:
    """Persist the cursor back onto the external_taps row."""
    import json

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE external_taps SET state = $1::jsonb WHERE id = $2",
            json.dumps(state),
            row_id,
        )
