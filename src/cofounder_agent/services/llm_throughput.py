"""Per-model LLM throughput time-series for the operator console.

Buckets ``cost_logs`` inference rows into an epoch-floored ``step_seconds``
grid (``generate_series``, same grid contract as ``services.qa_trend``) and
returns one series per model:

- ``metric="speed"``  → effective output tokens/sec: ``SUM(output_tokens) /
  SUM(duration_ms)`` per bucket. "Effective" because ``duration_ms`` is
  wall-clock call time — prompt processing and any in-call queueing are
  included, so short-output QA verdict calls read slower than raw decode
  speed. That is the throughput the pipeline actually experiences.
- ``metric="volume"`` → output tokens/min generated per bucket (rate, so the
  number is comparable across range presets whose bucket widths differ).

Only rows that can honestly speak to LLM throughput contribute:
``cost_type='inference' AND output_tokens > 0 AND duration_ms > 0 AND
success`` — the output-token filter drops non-LLM cost rows (whisper
captions, ffmpeg renders, YouTube publishes — all zero-token) that share the
table, and failed calls are excluded because their duration includes timeout
waits that poison the average. An empty bucket is ``None`` — a line break, never a fabricated 0
(feedback_no_dummy_data).

Series are capped to the top ``max_models`` models by output-token volume in
the window (DB-tunable via ``llm_throughput_trend_max_models``) so the chart
stays legible; the Grafana Model Throughput row on Cost & Analytics carries
the uncapped breakdown. The ``ollama/`` consumer prefix is stripped for
grouping — ``ollama/phi4:14b`` and ``phi4:14b`` are the same engine reached
through different dispatch paths (see reference_ollama_prefix_is_per_consumer).

SQL lives here, not in the route (adapter-purity ADR).
"""

from __future__ import annotations

import math
from typing import Any

MAX_RANGE_SECONDS = 7 * 86400
MAX_BUCKETS = 1000
MAX_MODELS_CEILING = 20

VALID_METRICS = ("speed", "volume")


def _clamp(range_seconds: int, step_seconds: int) -> tuple[int, int]:
    r = max(60, min(int(range_seconds), MAX_RANGE_SECONDS))
    s = max(15, int(step_seconds))
    if r / s > MAX_BUCKETS:
        s = math.ceil(r / MAX_BUCKETS)
    return r, s


async def get_llm_throughput_trend(
    pool: Any,
    *,
    range_seconds: int,
    step_seconds: int,
    metric: str = "speed",
    max_models: int = 6,
) -> dict[str, Any]:
    if metric not in VALID_METRICS:
        raise ValueError(f"metric must be one of {VALID_METRICS}, got {metric!r}")
    r, s = _clamp(range_seconds, step_seconds)
    n = max(1, min(int(max_models), MAX_MODELS_CEILING))
    rows = await pool.fetch(
        """
        WITH grid AS (
            -- EXTRACT(epoch) is numeric (PG 14+), so start/stop are numeric;
            -- the step must match → generate_series(numeric, numeric, numeric).
            SELECT gs AS bucket FROM generate_series(
                floor(extract(epoch FROM NOW() - ($1 * INTERVAL '1 second')) / $2) * $2,
                floor(extract(epoch FROM NOW()) / $2) * $2,
                $2::numeric
            ) AS gs
        ),
        calls AS (
            SELECT floor(extract(epoch FROM created_at) / $2) * $2 AS bucket,
                   CASE WHEN model LIKE 'ollama/%' THEN substr(model, 8)
                        ELSE model END AS model,
                   output_tokens,
                   duration_ms
            FROM cost_logs
            WHERE created_at > NOW() - ($1 * INTERVAL '1 second')
              AND cost_type = 'inference'
              AND output_tokens > 0
              AND duration_ms > 0
              AND success
        ),
        top_models AS (
            SELECT model, SUM(output_tokens) AS out_tok
            FROM calls
            GROUP BY model
            ORDER BY out_tok DESC, model
            LIMIT $3
        ),
        agg AS (
            SELECT bucket, model,
                   SUM(output_tokens) AS out_tok,
                   SUM(duration_ms) AS dur_ms
            FROM calls
            GROUP BY bucket, model
        )
        SELECT g.bucket AS bucket, tm.model AS model,
               a.out_tok AS out_tok, a.dur_ms AS dur_ms
        FROM grid g
        CROSS JOIN top_models tm
        LEFT JOIN agg a ON a.bucket = g.bucket AND a.model = tm.model
        ORDER BY tm.out_tok DESC, tm.model, g.bucket
        """,
        r,
        s,
        n,
    )
    series: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in rows:
        model = row["model"]
        if current is None or current["label"] != model:
            current = {"label": model, "points": []}
            series.append(current)
        value: float | None = None
        out_tok = row["out_tok"]
        dur_ms = row["dur_ms"]
        if out_tok is not None:
            if metric == "speed":
                if dur_ms:
                    value = round(float(out_tok) / (float(dur_ms) / 1000.0), 1)
            else:  # volume — output tokens per minute
                value = round(float(out_tok) * 60.0 / s, 1)
        current["points"].append([int(row["bucket"]) * 1000, value])
    return {"series": series, "metric": metric}
