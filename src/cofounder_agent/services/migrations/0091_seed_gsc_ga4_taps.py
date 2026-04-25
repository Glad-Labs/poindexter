"""Migration 0091: Seed external_taps rows for Google Search Console + GA4.

Both rows ship with ``enabled=false``. Operator follows
``docs/integrations/setup-gsc-and-ga4.md`` to:

1. Create a Google Cloud OAuth client (one screen).
2. Run ``scripts/google-oauth-setup.py`` to get a refresh_token.
3. Paste the OAuth credentials into Poindexter as encrypted settings.
4. Update the tap_config jsonb on each row with the OAuth values
   plus their site_urls / property_id specifics.
5. Flip ``enabled=true`` and run ``poindexter taps run <name>``.

Both rows reference ``record_handler='external_metrics_writer'``, so
records land in ``external_metrics`` automatically — no per-tap
record-handling code.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


# GSC stream metadata: tap-google-search-console emits one stream per
# property + report type. The "performance_report_date" stream is the
# core one — daily aggregates per (date, country, device, query) tuple.
_GSC_TAP_CONFIG = {
    # placeholders the operator fills in:
    "client_id": "",
    "client_secret": "",
    "refresh_token": "",
    # one or more verified property URLs the OAuth account has access to:
    "site_urls": ["https://www.gladlabs.io"],
    # how far back to fetch on first run; tap state advances this on subsequent runs:
    "start_date": "2026-04-01T00:00:00Z",
    "user_agent": "poindexter-singer-tap/1.0 (https://gladlabs.io)",
}

_GSC_METRICS_MAPPING = {
    "performance_report_date": {
        "source": "google_search_console",
        "date_field": "date",
        "post_field": "page",  # tap emits "page" URL; record_handler maps URL->slug if you add a transform
        "metric_fields": ["impressions", "clicks", "ctr", "position"],
        "dimension_fields": ["country", "device", "query"],
    }
}


# tap-ga4 needs a property_id (the numeric GA4 property identifier).
# The operator fills this in after creating the property in GA admin.
# `dimensions`/`metrics` lists shape what the API call requests.
_GA4_TAP_CONFIG = {
    "client_id": "",
    "client_secret": "",
    "refresh_token": "",
    "property_id": "",  # e.g. "123456789"
    "start_date": "2026-04-01T00:00:00Z",
    "user_agent": "poindexter-singer-tap/1.0 (https://gladlabs.io)",
    # tap-ga4 reads which reports to run from this list — example shape;
    # operator can edit per their reporting needs:
    "report_definitions": [
        {
            "name": "page_metrics",
            "dimensions": ["date", "pagePath", "country", "deviceCategory"],
            "metrics": ["sessions", "screenPageViews", "engagementRate", "userEngagementDuration"],
        }
    ],
}

_GA4_METRICS_MAPPING = {
    "page_metrics": {
        "source": "ga4",
        "date_field": "date",
        "post_field": "pagePath",  # the tap emits the URL path; not the slug directly
        "metric_fields": ["sessions", "screenPageViews", "engagementRate", "userEngagementDuration"],
        "dimension_fields": ["country", "deviceCategory"],
    }
}


async def up(pool) -> None:
    import json

    async with pool.acquire() as conn:
        # GSC
        await conn.execute(
            """
            INSERT INTO external_taps
                (name, handler_name, tap_type, target_table, record_handler,
                 schedule, config, state, enabled, metadata)
            VALUES ($1, 'singer_subprocess', 'tap-google-search-console',
                    'external_metrics', 'external_metrics_writer',
                    'every 6 hours', $2::jsonb, '{}'::jsonb, FALSE, $3::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            "gsc_main",
            json.dumps({
                "command": "tap-google-search-console",
                "tap_config": _GSC_TAP_CONFIG,
                "streams": list(_GSC_METRICS_MAPPING.keys()),
                "metrics_mapping": _GSC_METRICS_MAPPING,
                "max_records": 50000,
                "timeout_seconds": 1800,
            }),
            json.dumps({
                "description": "Google Search Console performance metrics",
                "setup_doc": "docs/integrations/setup-gsc-and-ga4.md",
                "operator_action_required": [
                    "fill tap_config.client_id / client_secret / refresh_token",
                    "verify tap_config.site_urls matches GSC properties",
                    "flip enabled=true",
                ],
            }),
        )

        # GA4
        await conn.execute(
            """
            INSERT INTO external_taps
                (name, handler_name, tap_type, target_table, record_handler,
                 schedule, config, state, enabled, metadata)
            VALUES ($1, 'singer_subprocess', 'tap-ga4',
                    'external_metrics', 'external_metrics_writer',
                    'every 6 hours', $2::jsonb, '{}'::jsonb, FALSE, $3::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            "ga4_main",
            json.dumps({
                "command": "tap-ga4",
                "tap_config": _GA4_TAP_CONFIG,
                "streams": list(_GA4_METRICS_MAPPING.keys()),
                "metrics_mapping": _GA4_METRICS_MAPPING,
                "max_records": 50000,
                "timeout_seconds": 1800,
            }),
            json.dumps({
                "description": "GA4 page metrics (sessions, page views, engagement)",
                "setup_doc": "docs/integrations/setup-gsc-and-ga4.md",
                "operator_action_required": [
                    "fill tap_config.client_id / client_secret / refresh_token",
                    "fill tap_config.property_id (numeric GA4 property ID)",
                    "flip enabled=true",
                ],
            }),
        )

        logger.info("0091: seeded gsc_main + ga4_main external_taps rows (disabled)")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM external_taps WHERE name IN ('gsc_main', 'ga4_main')"
        )
        logger.info("0091: removed gsc_main + ga4_main external_taps rows")
