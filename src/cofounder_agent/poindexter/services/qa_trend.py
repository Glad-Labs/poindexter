"""QA pass-rate time-series for the operator console (sub-project C).

Buckets ``audit_log`` rows where ``event_type='qa_pass_completed'`` (one row per
terminal QA gate decision; ``details->>'approved'`` is pass/fail,
``details->>'terminal'`` marks the gate decision vs a deferred rescue pass) into
an epoch-floored ``step_seconds`` grid via ``generate_series``. Returns the
console's canonical series shape. An empty bucket (no reviews) is ``None`` — the
rate is undefined, rendered as a line break, never a fabricated 0
(feedback_no_dummy_data). SQL lives here, not in the route (adapter-purity ADR).
"""

from __future__ import annotations

import math
from typing import Any

MAX_RANGE_SECONDS = 7 * 86400
MAX_BUCKETS = 1000


def _clamp(range_seconds: int, step_seconds: int) -> tuple[int, int]:
    r = max(60, min(int(range_seconds), MAX_RANGE_SECONDS))
    s = max(15, int(step_seconds))
    if r / s > MAX_BUCKETS:
        s = math.ceil(r / MAX_BUCKETS)
    return r, s


async def get_qa_pass_trend(pool: Any, *, range_seconds: int, step_seconds: int) -> dict[str, Any]:
    r, s = _clamp(range_seconds, step_seconds)
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
        agg AS (
            SELECT floor(extract(epoch FROM timestamp) / $2) * $2 AS bucket,
                   COUNT(*) FILTER (WHERE details->>'approved' = 'true') AS pass,
                   COUNT(*) AS total
            FROM audit_log
            WHERE event_type = 'qa_pass_completed'
              AND COALESCE(details->>'terminal', 'true') = 'true'
              AND timestamp > NOW() - ($1 * INTERVAL '1 second')
            GROUP BY 1
        )
        SELECT g.bucket AS bucket,
               CASE WHEN a.total > 0
                    THEN round(a.pass::numeric / a.total * 100, 1) END AS pct
        FROM grid g LEFT JOIN agg a USING (bucket)
        ORDER BY g.bucket
        """,
        r,
        s,
    )
    points = [
        [int(row["bucket"]) * 1000, float(row["pct"]) if row["pct"] is not None else None]
        for row in rows
    ]
    return {"series": [{"label": "pass %", "points": points}]}
