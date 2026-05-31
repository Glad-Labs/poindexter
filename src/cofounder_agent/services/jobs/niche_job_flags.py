"""Per-niche scheduler-job enable/disable flags (Glad-Labs/poindexter#521).

The backfill jobs (``backfill_podcasts`` / ``backfill_videos``) generate
derived media for already-published posts on a fixed cadence. Operators
want to turn a *single* job off for a *single* niche — e.g. stop
generating podcasts for ``dev_diary`` while videos keep flowing, and
without touching the global master switch (``plugin.job.<job>.enabled``)
or editing every post's ``media_to_generate`` array.

This module owns the one app_settings key convention that makes that
possible::

    niche.<slug>.jobs.<job_name>.enabled   (bool, default ``true``)

Read it through :func:`niche_job_enabled`. It is DB-first config (lives
in ``app_settings`` via the injected ``SiteConfig``), not an env var,
and it is **fail-safe**: an absent row defaults to enabled (``true``) so
a niche keeps producing media until the operator explicitly opts it out.
The default is read *explicitly* (passed to ``get_bool``) rather than
relying on a silent magic value — see ``feedback_no_silent_defaults``.
"""

from __future__ import annotations

from typing import Any

# The app_settings key template. Exposed so tests + operators can build
# the exact key without re-deriving the format string by hand.
NICHE_JOB_ENABLED_KEY = "niche.{slug}.jobs.{job_name}.enabled"


def niche_job_key(niche_slug: str, job_name: str) -> str:
    """Return the app_settings key for a niche's per-job enable flag.

    ``niche.<slug>.jobs.<job_name>.enabled`` — the single row an
    operator toggles to disable one scheduler job for one niche.
    """
    return NICHE_JOB_ENABLED_KEY.format(slug=niche_slug, job_name=job_name)


def niche_job_enabled(
    site_config: Any,
    niche_slug: str,
    job_name: str,
    *,
    default: bool = True,
) -> bool:
    """Is ``job_name`` enabled for ``niche_slug``?

    Reads ``niche.<slug>.jobs.<job_name>.enabled`` from the injected
    ``SiteConfig`` (DB-backed app_settings, in-memory cache). Fail-safe:
    when the row is absent the niche stays **enabled** (``default=True``)
    so media generation never silently stops on a fresh install.

    ``site_config`` is the run-bound instance the scheduler seeds into
    ``config["_site_config"]``. When it is ``None`` (early boot / a
    misconfigured caller) we honour the same fail-safe default rather
    than crash the whole sweep.
    """
    if site_config is None:
        return default
    return bool(
        site_config.get_bool(niche_job_key(niche_slug, job_name), default)
    )
