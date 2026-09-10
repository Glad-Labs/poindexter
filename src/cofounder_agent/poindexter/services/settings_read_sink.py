"""Process-level read sink for ``SettingsService.get`` (Glad-Labs/poindexter#756).

``SiteConfig.get`` records every key it is asked for into its own instance set,
which ``FlushSettingsReadTelemetryJob`` drains once a minute to stamp
``app_settings.last_read_at``. ``SettingsService`` is the other read path — but
it is constructed ad-hoc in several places (``main.py`` lifespan,
``content_router_service``, ``multi_model_qa``) with only a pool, so there is no
single instance for the flush job to drain. Those reads land here instead: one
shared, process-wide buffer that every ``SettingsService`` records into, which
the flush job unions with the ``SiteConfig`` drain.

This is a write-then-drain telemetry buffer — the same shape as a metrics
counter — behind a two-function seam (``record_read`` / ``drain_read_keys``), so
it stays disposable and hides no dependency. It is deliberately NOT the retired
``set_site_config`` ambient-config singleton: it carries no config state and no
service reaches through it to resolve a value; it only accumulates key names.

Concurrency: ``set.add`` and the snapshot-then-clear in ``drain_read_keys`` each
run with no ``await`` between them, so they are atomic under asyncio — a
``record_read`` racing a drain lands in this batch or the next, never lost.
"""

from __future__ import annotations

_read_keys: set[str] = set()


def record_read(key: str) -> None:
    """Record that *key* was read via ``SettingsService.get``.

    Records the ask regardless of where the value resolves (DB / env / default):
    the flush job's UPDATE filters down to real ``app_settings`` rows, so
    recording a key with no row is a harmless no-op there.
    """
    _read_keys.add(key)


def drain_read_keys() -> list[str]:
    """Return the keys recorded since the last drain, then clear the buffer."""
    keys = list(_read_keys)
    _read_keys.clear()
    return keys
