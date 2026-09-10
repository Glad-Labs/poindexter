"""``finance`` business module — bank integrations + accounting surface.

Phase F1 (2026-05-13). First non-content business module — exercises
the Module v1 pattern (Phase 3-lite/4-lite) end-to-end against a
real use case. Integrates Matt's Mercury business banking via
Mercury's read-only API token (scope: read-only, no money-movement).

What ships in F1:
- ``FinanceModule`` class — manifest + migrate() pointing at this
  package's migrations/ directory
- ``MercuryClient`` — async HTTP client for the Mercury Banking API
- CLI: ``poindexter finance balance`` — pulls live account balances
- App-settings seam: ``mercury_api_token`` (secret),
  ``mercury_enabled`` (boolean, default false). Names match the
  existing unprefixed convention (``sentry_dsn``,
  ``telegram_bot_token``, etc.) and Matt's hand-seeded
  ``mercury_api_token`` row.

What's deferred to F2:
- DB tables (``finance_accounts``, ``finance_transactions``)
- Hourly polling job pulling fresh data via the plugin scheduler
- Brain knowledge entries so the system can reference balance/runway
  in natural-language operator chat
- Telegram daily digest with income/expense summary

Visibility: private. Mercury credentials + account data are Matt's
operator overlay. When Phase 5 ships the visibility-aware sync
filter, this module will not flow to the public OSS mirror.
"""

from .finance_module import FinanceModule

__all__ = ["FinanceModule"]
