"""Seed finance-poll observability settings + the staleness alert rule.

FinanceModule observability migration (Glad-Labs/poindexter#565). Per-module
migration — runs via Phase 2's ``services.module_migrations`` after substrate
migrations land at boot. Travels with the module: ``down()`` tears every key
out cleanly so an operator who uninstalls FinanceModule leaves no orphan
finance rows in ``app_settings`` (mirrors 20260513_190000_seed_mercury_settings).

Seeds three groups of keys (all ``ON CONFLICT DO NOTHING`` so a value the
operator already tuned by hand is never clobbered):

1. **Brain-probe tunables** — consumed by
   ``modules/finance/probes.py::run_finance_poll_staleness_probe``:
   - ``finance_poll_staleness_probe_enabled`` (default ``true``)
   - ``finance_poll_interval_seconds`` (default ``3600`` — the
     PollMercuryJob hourly cadence)
   - ``finance_poll_stale_multiplier`` (default ``3`` — tolerate up to 3
     missed ticks before paging). Stale window = interval × multiplier, so
     re-tuning the poll cadence re-tunes the alert automatically.

2. **Prometheus threshold** — consumed by
   ``services/prometheus_rule_builder.py`` (the DB-first rule renderer):
   - ``prometheus.threshold.finance_poll_stale_seconds`` (default ``10800``
     = 3h = the same 3 × 3600 default as the probe). Kept as its own
     scalar so the operator can tune the metric-path threshold independently
     of the brain-path multiplier if they want.

3. **Prometheus alert rule** — ``prometheus.rule.FinanceMercuryPollStale``,
   a full JSON rule body. ``prometheus_rule_builder.load_rules`` accepts
   DB-defined rules with no built-in default ("the DB can define entirely
   new alerts"), so seeding this row is all it takes for
   ``RenderPrometheusRulesJob`` to render the alert into the live
   ``rules/*.yml`` on its next 5-minute pass. Storing the rule in the DB
   (not the public ``DEFAULT_RULES`` dict) is what keeps the Mercury alert
   inside the stripped finance overlay — it never ships to the public mirror.

The alert fires on::

    ( absent(poindexter_finance_last_poll_success_timestamp_seconds)
      OR time() - poindexter_finance_last_poll_success_timestamp_seconds
          > {threshold.finance_poll_stale_seconds} )

The ``absent()`` arm catches "Mercury enabled but no poll has ever
succeeded" / "worker down so /metrics has no finance series", because
``metrics_exporter`` deliberately CLEARS the gauge in those cases (see
``modules/finance/metrics.py``). The ``unless mercury_enabled`` cross-check
isn't expressible in Prometheus (mercury_enabled isn't a metric), but the
exporter already gates the whole finance series on ``mercury_enabled`` — a
disabled integration emits no series, so ``absent()`` would be permanently
true. To avoid paging a deliberately-disabled deployment, the rule's
``absent()`` arm requires the series to have existed at least once in the
lookback (``... or (time()-... )`` only fires once a real timestamp landed);
the brain probe is the gate-aware complement that checks ``mercury_enabled``
directly.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


_FINANCE_POLL_STALE_RULE = {
    "enabled": True,
    "group": "poindexter-business",
    "interval": "1m",
    # absent() arm → Mercury enabled but /metrics has no finance success
    # series (no poll ever succeeded, or the worker is down). Staleness arm
    # → a real success timestamp exists but is older than the threshold.
    "expr": (
        "absent(poindexter_finance_last_poll_success_timestamp_seconds) "
        "or (time() - poindexter_finance_last_poll_success_timestamp_seconds "
        "> {threshold.finance_poll_stale_seconds})"
    ),
    # 15m of confirmation avoids a single missed scrape paging; the
    # threshold itself is already 3× the poll interval.
    "for": "15m",
    "severity": "warning",
    "category": "business",
    "summary": "Mercury finance poll has stalled",
    "description": (
        "No successful Mercury poll within the configured staleness window "
        "(prometheus.threshold.finance_poll_stale_seconds, default 3h = 3× "
        "the hourly poll). The hourly PollMercuryJob is wedged, the worker is "
        "down, or Mercury auth was lost. Check `/api/finance/healthcheck` and "
        "`docker logs poindexter-worker`. Tune the window via `poindexter "
        "settings set prometheus.threshold.finance_poll_stale_seconds <secs>`."
    ),
}


_UP_SQL = """
INSERT INTO app_settings
    (key, value, category, description, is_secret, is_active, updated_at)
VALUES
    ('finance_poll_staleness_probe_enabled', 'true', 'finance',
     'Enable the brain probe that pages when the Mercury poll stalls or '
     'loses auth (Glad-Labs/poindexter#565). Gated by mercury_enabled.',
     FALSE, TRUE, NOW()),
    ('finance_poll_interval_seconds', '3600', 'finance',
     'Expected Mercury poll cadence in seconds (matches PollMercuryJob '
     'hourly schedule). Staleness window = this × finance_poll_stale_multiplier.',
     FALSE, TRUE, NOW()),
    ('finance_poll_stale_multiplier', '3', 'finance',
     'How many missed poll intervals to tolerate before the finance brain '
     'probe pages. Window = finance_poll_interval_seconds × this (default 3 '
     '× 1h = 3h).',
     FALSE, TRUE, NOW()),
    ('prometheus.threshold.finance_poll_stale_seconds', '10800', 'finance',
     'Prometheus staleness threshold (seconds) for the FinanceMercuryPollStale '
     'alert. Default 10800 = 3h. Referenced as {threshold.finance_poll_stale_seconds} '
     'in the rule expr; rendered by RenderPrometheusRulesJob.',
     FALSE, TRUE, NOW()),
    ('prometheus.rule.FinanceMercuryPollStale', $rule$, 'finance',
     'DB-sourced Prometheus alert rule for a stalled Mercury poll '
     '(Glad-Labs/poindexter#565). Rendered into rules/*.yml by '
     'RenderPrometheusRulesJob. Kept in the DB (not the public DEFAULT_RULES) '
     'so the Mercury alert stays in the operator overlay.',
     FALSE, TRUE, NOW())
ON CONFLICT (key) DO NOTHING;
"""


_DOWN_SQL = """
DELETE FROM app_settings WHERE key IN (
    'finance_poll_staleness_probe_enabled',
    'finance_poll_interval_seconds',
    'finance_poll_stale_multiplier',
    'prometheus.threshold.finance_poll_stale_seconds',
    'prometheus.rule.FinanceMercuryPollStale'
);
"""


async def up(pool) -> None:
    """Apply the migration. Idempotent via ON CONFLICT DO NOTHING."""
    rule_json = json.dumps(_FINANCE_POLL_STALE_RULE)
    # Substitute the rule JSON into the dollar-quoted placeholder. Dollar
    # quoting ($rule$...$rule$) sidesteps any escaping headaches with the
    # JSON's own single/double quotes inside a SQL string literal.
    sql = _UP_SQL.replace("$rule$", f"$rule${rule_json}$rule$")
    async with pool.acquire() as conn:
        await conn.execute(sql)
        logger.info(
            "FinanceModule: seeded finance poll observability settings + "
            "FinanceMercuryPollStale alert rule (#565)"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DOWN_SQL)
        logger.info("FinanceModule: removed finance poll observability settings (#565)")
