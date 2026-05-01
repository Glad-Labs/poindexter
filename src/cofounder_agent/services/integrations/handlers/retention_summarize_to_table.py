"""Handler: ``retention.summarize_to_table``.

Generic per-day compression for append-only tables. For every day-bucket
older than the policy's TTL window, the handler:

1. Selects the raw rows in the bucket.
2. Aggregates them — total count, per-column counts, optional average
   confidence, optional error/decision excerpts.
3. Asks the local LLM (via :func:`build_summary_text_via_llm`) to write
   a short paragraph describing what happened that day.
4. Inside a single transaction, INSERTs one summary row into the
   target table and DELETEs the raw rows the summary replaces.

The summary table schema is operator-defined. The handler discovers
column names from ``row.config`` (e.g. ``"event_type_counts"`` for
audit_log, ``"outcome_counts"`` for brain_decisions) and refuses
unknown identifiers via the same regex whitelist used by
``retention.ttl_prune``.

## Config (``row.config`` JSONB)

- ``bucket`` (str, required): currently ``"day"`` only. Anything else
  is rejected at validation time so a typo can't silently change the
  granularity.
- ``summary_table`` (str, required): destination table for the
  per-bucket summary rows. Validated against ``_IDENT_RE``.
- ``text_columns`` (list[str], required): columns extracted from each
  raw row to feed the LLM prompt.
- ``count_columns`` (list[str], required): columns whose values are
  GROUP BY'd into a JSONB ``{value: count}`` map per bucket. The
  destination column is named ``"<col>_counts"``.
- ``confidence_column`` (str, optional): if set, AVG over this column
  is stored as ``avg_confidence``. Used by ``brain_decisions``.
- ``top_source_column`` (str, optional): if set, top 5 distinct values
  of this column with their counts land in the ``top_sources`` column.
  Used by ``audit_log``.
- ``excerpts_column`` (str, optional): destination JSONB column name
  for the excerpts list (e.g. ``"error_excerpts"``). Validated.
- ``excerpts_filter`` (str, optional): WHERE fragment that filters
  rows down to the ones that should appear in the excerpts column.
  Operator-controlled migration seed (same trust model as
  ``retention.ttl_prune.filter_sql``).
- ``excerpts_text_columns`` (list[str], optional): columns to extract
  for each excerpt row. Defaults to ``text_columns``.
- ``dry_run`` (bool, default false): count buckets without writing.

## App settings consumed

- ``memory_compression_summary_model`` — Ollama model name
- ``memory_compression_summary_timeout_seconds`` — LLM call timeout
- ``memory_compression_excerpts_per_bucket`` — how many sample rows
  feed the LLM prompt per bucket

## Security

Same trust model as ``retention.ttl_prune`` — every identifier
sourced from operator-controlled migration seeds is validated through
``_IDENT_RE`` so a typo can't turn into a SQL injection. The
``# nosec B608`` annotations match the existing handlers' style.

## LLM resilience

If the LLM is unreachable (network, timeout, container down) the
handler does NOT abort the retention pass — it falls back to a
joined-preview summary and tags the bucket ``summary_method =
'joined_preview'``. The next pass picks up where this one left off.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from services.integrations.registry import register_handler
from services.jobs.collapse_old_embeddings import (
    build_summary_text,
    build_summary_text_via_llm,
)

logger = logging.getLogger(__name__)


# Conservative identifier whitelist (same as retention.ttl_prune).
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")

_VALID_BUCKETS = {"day"}

_DEFAULT_SUMMARY_MODEL = "gemma3:27b-it-qat"
_DEFAULT_SUMMARY_TIMEOUT_S = 60
_DEFAULT_EXCERPTS_PER_BUCKET = 12


def _validate_identifier(value: str, field_name: str) -> str:
    if not value or not _IDENT_RE.match(value):
        raise ValueError(
            f"retention.summarize_to_table: invalid {field_name}={value!r} — "
            f"must match {_IDENT_RE.pattern}"
        )
    return value


def _validate_identifier_list(values: Sequence[str], field_name: str) -> list[str]:
    if not values or not isinstance(values, (list, tuple)):
        raise ValueError(
            f"retention.summarize_to_table: {field_name} must be a non-empty list"
        )
    out: list[str] = []
    for v in values:
        if not isinstance(v, str):
            raise ValueError(
                f"retention.summarize_to_table: {field_name} must contain strings"
            )
        out.append(_validate_identifier(v, f"{field_name}.item"))
    return out


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

async def _get_setting(pool: Any, key: str, default: str) -> str:
    """Read a string value from app_settings with a fallback (same shape
    as services.jobs.collapse_old_embeddings._get_setting)."""
    try:
        row = await pool.fetchrow(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
        if row and row["value"] is not None:
            return str(row["value"])
    except Exception as exc:  # noqa: BLE001 — never crash retention pass
        logger.debug(
            "[retention.summarize_to_table] setting read failed for %s: %s",
            key, exc,
        )
    return default


def _parse_int(raw: str, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Bucket math
# ---------------------------------------------------------------------------

def _day_bucket(ts: datetime) -> tuple[datetime, datetime]:
    """Return ``(bucket_start, bucket_end)`` for the calendar day of ``ts`` (UTC)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    start = datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start, end


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SUMMARY_PROMPT = (
    "You are compressing one calendar day of {source_table} rows so the "
    "system remembers the gist without storing every row. Below are "
    "{n} representative rows from {bucket_start_iso}, each separated by "
    "'---'.\n\n"
    "Write a single paragraph (3-6 sentences) summarizing what happened "
    "that day. Preserve specific event types, sources, severity, "
    "decisions, and outcomes. Drop boilerplate and repetition. The "
    "summary will be stored as a single row replacing all {row_count} "
    "raw rows for the day, so dense factual content beats prose.\n\n"
    "Rows:\n{joined}\n\n"
    "Summary:"
)


def _row_to_excerpt(row: dict[str, Any], cols: Sequence[str]) -> str:
    parts: list[str] = []
    for c in cols:
        v = row.get(c)
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            try:
                v = json.dumps(v, default=str, separators=(",", ":"))
            except (TypeError, ValueError):
                v = str(v)
        s = str(v).strip()
        if not s:
            continue
        parts.append(f"{c}={s}")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# The handler
# ---------------------------------------------------------------------------

@register_handler("retention", "summarize_to_table")
async def summarize_to_table(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Compress old rows into per-day summary rows + LLM paragraph."""
    if pool is None:
        raise RuntimeError("retention.summarize_to_table: pool unavailable")

    # ---- core retention_policies fields
    ttl_days = row.get("ttl_days")
    if ttl_days is None:
        raise ValueError(
            "retention.summarize_to_table: ttl_days is required (rows older "
            "than this many days are eligible for compression)"
        )
    try:
        ttl_days = int(ttl_days)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"retention.summarize_to_table: ttl_days must be int, "
            f"got {ttl_days!r}"
        ) from exc
    if ttl_days < 0:
        raise ValueError(
            f"retention.summarize_to_table: ttl_days must be >= 0, got {ttl_days}"
        )

    table_name = _validate_identifier(
        row.get("table_name") or "", "table_name",
    )
    age_column = _validate_identifier(
        row.get("age_column") or "created_at", "age_column",
    )

    # ---- handler-specific config
    config = row.get("config") or {}
    if isinstance(config, str):
        # asyncpg sometimes hands JSONB back as a string depending on
        # codec setup. Be defensive — parse if needed, but never crash.
        try:
            config = json.loads(config)
        except (TypeError, ValueError):
            config = {}
    if not isinstance(config, dict):
        config = {}

    bucket = str(config.get("bucket") or "").lower()
    if bucket not in _VALID_BUCKETS:
        raise ValueError(
            f"retention.summarize_to_table: bucket={bucket!r} not supported "
            f"(valid: {sorted(_VALID_BUCKETS)})"
        )

    summary_table = _validate_identifier(
        config.get("summary_table") or "", "summary_table",
    )
    text_columns = _validate_identifier_list(
        config.get("text_columns") or [], "text_columns",
    )
    count_columns = _validate_identifier_list(
        config.get("count_columns") or [], "count_columns",
    )

    confidence_column: str | None = None
    if config.get("confidence_column"):
        confidence_column = _validate_identifier(
            config["confidence_column"], "confidence_column",
        )

    top_source_column: str | None = None
    if config.get("top_source_column"):
        top_source_column = _validate_identifier(
            config["top_source_column"], "top_source_column",
        )

    excerpts_column: str | None = None
    if config.get("excerpts_column"):
        excerpts_column = _validate_identifier(
            config["excerpts_column"], "excerpts_column",
        )
    excerpts_filter = config.get("excerpts_filter") or ""
    excerpts_text_columns = config.get("excerpts_text_columns") or text_columns
    if excerpts_text_columns:
        excerpts_text_columns = _validate_identifier_list(
            excerpts_text_columns, "excerpts_text_columns",
        )

    dry_run = bool(config.get("dry_run", False))

    # ---- LLM settings
    summary_model = (
        await _get_setting(
            pool, "memory_compression_summary_model", _DEFAULT_SUMMARY_MODEL,
        )
    ).strip()
    summary_timeout_s = _parse_int(
        await _get_setting(
            pool, "memory_compression_summary_timeout_seconds",
            str(_DEFAULT_SUMMARY_TIMEOUT_S),
        ),
        _DEFAULT_SUMMARY_TIMEOUT_S,
    )
    excerpts_per_bucket = _parse_int(
        await _get_setting(
            pool, "memory_compression_excerpts_per_bucket",
            str(_DEFAULT_EXCERPTS_PER_BUCKET),
        ),
        _DEFAULT_EXCERPTS_PER_BUCKET,
    )

    # ---- Identify eligible day buckets
    select_columns = sorted(
        set(text_columns)
        | set(count_columns)
        | ({top_source_column} if top_source_column else set())
        | ({confidence_column} if confidence_column else set())
        | set(excerpts_text_columns or [])
        | {age_column}
    )
    select_cols_sql = ", ".join(select_columns)

    cutoff_sql = (
        f"WHERE {age_column} < now() - make_interval(days => $1)"
    )

    summarized = 0
    deleted = 0
    bucket_count = 0
    skipped: list[str] = []

    async with pool.acquire() as conn:
        # Discover the distinct day-buckets older than the cutoff. We
        # do this in one query (cheap, indexed if age_column has the
        # standard index) rather than walking every day individually.
        bucket_rows = await conn.fetch(
            f"""
            SELECT date_trunc('day', {age_column}) AS bucket_start,
                   count(*) AS row_count
              FROM {table_name}
              {cutoff_sql}
             GROUP BY 1
             ORDER BY 1 ASC
            """,  # nosec B608  # table_name+age_column validated by _validate_identifier (regex whitelist); see module docstring for the operator-controlled trust model.
            ttl_days,
        )

        for br in bucket_rows:
            bucket_start = br["bucket_start"]
            row_count = int(br["row_count"] or 0)
            if row_count == 0:
                continue
            bucket_count += 1

            if dry_run:
                # Don't fetch / aggregate / call LLM in dry mode — just
                # report the candidate buckets and their sizes.
                continue

            bucket_start_dt, bucket_end_dt = _day_bucket(bucket_start)

            # Pull every row in this bucket. We deliberately don't LIMIT
            # because we need the exact set to delete; for huge daily
            # volumes this would warrant batching, but compression is a
            # background job and the audit_log/brain_decisions tables
            # are bounded by per-day row counts well under 100K.
            raw_rows = await conn.fetch(
                f"""
                SELECT id, {select_cols_sql}
                  FROM {table_name}
                 WHERE {age_column} >= $1
                   AND {age_column} <  $2
                """,  # nosec B608  # identifiers validated above
                bucket_start_dt, bucket_end_dt,
            )

            if not raw_rows:
                continue

            row_count = len(raw_rows)

            # ---- Aggregate
            aggregates = _aggregate(
                raw_rows,
                count_columns=count_columns,
                top_source_column=top_source_column,
                confidence_column=confidence_column,
                excerpts_filter=excerpts_filter,
                excerpts_text_columns=excerpts_text_columns or text_columns,
                excerpts_per_bucket=excerpts_per_bucket,
            )

            # ---- LLM summary (with safe fallback)
            sample_rows = raw_rows[:excerpts_per_bucket]
            previews = [
                _row_to_excerpt(dict(r), text_columns) for r in sample_rows
            ]
            previews = [p for p in previews if p]

            summary_method = "joined_preview"
            summary_text: str | None = None
            if previews:
                prompt_template = _SUMMARY_PROMPT.replace(
                    "{bucket_start_iso}",
                    bucket_start_dt.date().isoformat(),
                ).replace("{row_count}", str(row_count))
                # build_summary_text_via_llm formats {n}/{source_table}/
                # {joined}; remaining placeholders need to be done first.
                summary_text = await build_summary_text_via_llm(
                    previews,
                    source_table=table_name,
                    model=summary_model,
                    timeout_s=summary_timeout_s,
                    prompt_template=prompt_template,
                )
                if summary_text:
                    summary_method = "ollama"
            if not summary_text:
                summary_text = build_summary_text(previews) or (
                    f"{row_count} {table_name} rows on "
                    f"{bucket_start_dt.date().isoformat()} "
                    f"(no LLM summary available)"
                )

            # ---- Write summary + delete originals atomically
            try:
                await _write_summary_and_delete(
                    conn,
                    summary_table=summary_table,
                    table_name=table_name,
                    bucket_start=bucket_start_dt,
                    bucket_end=bucket_end_dt,
                    row_count=row_count,
                    aggregates=aggregates,
                    count_columns=count_columns,
                    top_source_column=top_source_column,
                    confidence_column=confidence_column,
                    excerpts_column=excerpts_column,
                    summary_text=summary_text,
                    summary_method=summary_method,
                    raw_row_ids=[r["id"] for r in raw_rows],
                )
            except Exception as exc:  # noqa: BLE001 — never let one bad bucket kill the pass
                logger.exception(
                    "[retention.summarize_to_table] %s: bucket %s write failed: %s",
                    row.get("name"), bucket_start_dt.date(), exc,
                )
                skipped.append(
                    f"{bucket_start_dt.date().isoformat()}: {type(exc).__name__}"
                )
                continue

            summarized += 1
            deleted += row_count

            logger.info(
                "[retention.summarize_to_table] %s: bucket %s → 1 summary "
                "row, %d raw rows deleted (method=%s)",
                row.get("name"), bucket_start_dt.date(),
                row_count, summary_method,
            )

    if dry_run:
        logger.info(
            "[retention.summarize_to_table] %s: DRY RUN — would compress "
            "%d buckets older than %d days",
            row.get("name"), bucket_count, ttl_days,
        )
        return {
            "dry_run": True,
            "buckets": bucket_count,
            "summarized": 0,
            "deleted": 0,
        }

    return {
        "summarized": summarized,
        "deleted": deleted,
        "buckets": bucket_count,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _aggregate(
    raw_rows: Sequence[dict[str, Any]],
    *,
    count_columns: Sequence[str],
    top_source_column: str | None,
    confidence_column: str | None,
    excerpts_filter: str,
    excerpts_text_columns: Sequence[str],
    excerpts_per_bucket: int,
) -> dict[str, Any]:
    """Compute per-bucket aggregates from a batch of raw rows."""
    # Per-column value counts
    counts: dict[str, dict[str, int]] = {col: {} for col in count_columns}
    for r in raw_rows:
        for col in count_columns:
            v = r.get(col)
            key = "" if v is None else str(v)
            counts[col][key] = counts[col].get(key, 0) + 1

    top_sources: list[dict[str, Any]] = []
    if top_source_column:
        src_counts: dict[str, int] = {}
        for r in raw_rows:
            v = r.get(top_source_column)
            key = "" if v is None else str(v)
            src_counts[key] = src_counts.get(key, 0) + 1
        top_sources = [
            {"value": k, "count": v}
            for k, v in sorted(src_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ]

    avg_confidence: float | None = None
    if confidence_column:
        vals = [
            float(r.get(confidence_column))
            for r in raw_rows
            if r.get(confidence_column) is not None
        ]
        if vals:
            avg_confidence = sum(vals) / len(vals)

    # Excerpts: select rows matching the filter and serialize them.
    # excerpts_filter is a tiny DSL: only "<col> = '<literal>'" forms
    # are interpreted in-Python (we don't ship the filter to SQL — it's
    # already passed as part of bucket selection above; here we just
    # pick which rows from the in-memory batch land in the JSONB column).
    # Anything more complex than a single equality just passes through
    # as "no filter" — operators get the full batch sample.
    excerpt_rows = list(raw_rows)
    fparsed = _parse_simple_eq_filter(excerpts_filter)
    if fparsed is not None:
        col, val = fparsed
        excerpt_rows = [r for r in raw_rows if str(r.get(col)) == val]
    excerpts: list[dict[str, Any]] = []
    for r in excerpt_rows[:excerpts_per_bucket]:
        snippet: dict[str, Any] = {}
        for c in excerpts_text_columns:
            if c in r and r[c] is not None:
                v = r[c]
                if isinstance(v, (dict, list)):
                    snippet[c] = v
                else:
                    snippet[c] = str(v)
        if snippet:
            excerpts.append(snippet)

    return {
        "counts": counts,
        "top_sources": top_sources,
        "avg_confidence": avg_confidence,
        "excerpts": excerpts,
    }


_SIMPLE_EQ_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*'([^']*)'\s*$"
)


def _parse_simple_eq_filter(expr: str) -> tuple[str, str] | None:
    """Parse ``col = 'value'`` filters used to scope error_excerpts.

    Returns ``(col, value)`` if parseable, ``None`` otherwise. We use
    string matching against the in-memory row batch rather than
    re-running the filter as SQL — keeps the handler portable across
    DBs and avoids round-trips.
    """
    if not expr:
        return None
    m = _SIMPLE_EQ_RE.match(expr)
    if not m:
        return None
    return m.group(1), m.group(2)


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

async def _write_summary_and_delete(
    conn: Any,
    *,
    summary_table: str,
    table_name: str,
    bucket_start: datetime,
    bucket_end: datetime,
    row_count: int,
    aggregates: dict[str, Any],
    count_columns: Sequence[str],
    top_source_column: str | None,
    confidence_column: str | None,
    excerpts_column: str | None,
    summary_text: str,
    summary_method: str,
    raw_row_ids: Sequence[Any],
) -> None:
    """Write one summary row + delete N raw rows inside a transaction.

    Either both writes succeed and the transaction commits, or any
    failure rolls the whole bucket back. On rollback the raw rows
    survive and the next pass picks them up.
    """
    columns = ["bucket_start", "bucket_end", "row_count"]
    values: list[Any] = [bucket_start, bucket_end, row_count]

    # Per-count-column JSONB columns: <col>_counts
    for col in count_columns:
        dst = _validate_identifier(f"{col}_counts", "count column")
        columns.append(dst)
        values.append(json.dumps(aggregates["counts"].get(col, {})))

    if top_source_column:
        columns.append("top_sources")
        values.append(json.dumps(aggregates["top_sources"]))

    if confidence_column:
        columns.append("avg_confidence")
        values.append(aggregates["avg_confidence"])

    if excerpts_column:
        columns.append(excerpts_column)
        values.append(json.dumps(aggregates["excerpts"]))

    columns.extend(["summary_text", "summary_method"])
    values.extend([summary_text, summary_method])

    cols_sql = ", ".join(columns)

    # Cast JSONB columns explicitly so asyncpg doesn't fail on str→jsonb
    # coercion. The trailing ::jsonb is on the placeholders we already
    # know are JSON-serialized strings.
    casts = []
    for c in columns:
        if c == "summary_text" or c == "summary_method":
            casts.append("text")
        elif c.endswith("_counts") or c == "top_sources" or c == excerpts_column:
            casts.append("jsonb")
        elif c == "avg_confidence":
            casts.append("double precision")
        elif c == "row_count":
            casts.append("integer")
        elif c == "bucket_start" or c == "bucket_end":
            casts.append("timestamptz")
        else:
            casts.append(None)
    cast_placeholders = ", ".join(
        f"${i + 1}::{cast}" if cast else f"${i + 1}"
        for i, cast in enumerate(casts)
    )

    insert_sql = (
        f"INSERT INTO {summary_table} ({cols_sql}) "  # nosec B608  # summary_table validated by _validate_identifier; column names derived from validated count_columns/excerpts_column or fixed literals.
        f"VALUES ({cast_placeholders}) "
        f"RETURNING id"
    )

    delete_sql = (
        f"DELETE FROM {table_name} WHERE id = ANY($1::bigint[])"  # nosec B608  # table_name validated by _validate_identifier.
    )

    # The delete needs an int[] for asyncpg's typing on a BIGSERIAL/SERIAL
    # column. Coerce defensively in case the SELECT returned numeric/None.
    raw_ids_int = [int(rid) for rid in raw_row_ids if rid is not None]

    async with conn.transaction():
        inserted = await conn.fetchrow(insert_sql, *values)
        if inserted is None or inserted.get("id") is None:
            raise RuntimeError(
                f"retention.summarize_to_table: INSERT into {summary_table} "
                "returned no row — refusing to delete originals"
            )

        del_result = await conn.execute(delete_sql, raw_ids_int)
        try:
            actually_deleted = int(str(del_result).rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            actually_deleted = 0
        if actually_deleted != len(raw_ids_int):
            logger.warning(
                "[retention.summarize_to_table] %s: expected to delete %d "
                "rows, deleted %d (commit will proceed; missing rows may "
                "have been deleted by a concurrent pass)",
                table_name, len(raw_ids_int), actually_deleted,
            )


# ---------------------------------------------------------------------------
# Re-exported symbols (kept here so tests can import without reaching deep)
# ---------------------------------------------------------------------------

__all__ = [
    "summarize_to_table",
    "_aggregate",
    "_day_bucket",
    "_parse_simple_eq_filter",
    "_validate_identifier",
    "_validate_identifier_list",
    "_write_summary_and_delete",
]
