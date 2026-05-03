"""Brain-local secret reader for ``app_settings`` rows.

Single source of truth for the brain daemon's ``app_settings`` reads.
Mirrors ``services.plugins.secrets.get_secret`` (worker-side) and
``scripts/_oauth_helper.read_app_setting`` (CLI-side) so all three
helpers stay behaviourally identical, but ships ZERO imports from the
worker codebase — the brain container's pyproject pins only
``asyncpg + httpx + pyyaml`` (see ``brain/pyproject.toml`` for the
rationale on the slim dep closure).

Plaintext rows (``is_secret=false`` OR pre-migration legacy
``is_secret=true`` rows that were never re-encrypted) come back
verbatim. Encrypted rows (the modern ``enc:v1:<base64>`` envelope)
get pgcrypto-decrypted with ``POINDEXTER_SECRET_KEY`` from the
container env.

Why this lives in its own module:

Before this file, the same ~30-line decrypt helper was copy-pasted
into ``brain/oauth_client.py``, ``brain/glitchtip_triage_probe.py``,
and an inline ``_BrainSecretReader`` class in ``brain/brain_daemon.py``
(the prometheus secret writer's ad-hoc adapter). When
Glad-Labs/poindexter#342 traced the alert-dispatch outage to the brain
``notify`` path reading the encrypted ``telegram_bot_token`` value
verbatim and shoving ``enc:v1:...`` into the URL, it was clear the
brain notify path needed the same decrypt logic — and that adding a
fourth copy was not the answer.

Failure modes (all return ``default`` so callers can degrade gracefully):

- Row missing or empty value
- ``is_secret=true`` but ``POINDEXTER_SECRET_KEY`` env var unset (logged
  at WARN — without the bootstrap key the brain can't decrypt anything,
  so we want this surfaced)
- ``pgp_sym_decrypt`` raised (corrupt envelope, wrong key, etc.) —
  logged at WARN with the key name so the operator can rotate

Callers should treat ``default`` as "not configured" and skip whatever
they were trying to do. Do NOT log the returned value — it may be a
plaintext secret.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("brain.secrets")

# Modern encryption envelope written by ``services.plugins.secrets.set_secret``.
# Anything starting with this prefix is a base64-encoded pgcrypto blob;
# anything else is plaintext (or an older legacy row that the migration
# script hasn't re-encrypted yet — both are returned as-is).
_ENC_PREFIX = "enc:v1:"


async def read_app_setting(pool, key: str, default: str = "") -> str:
    """Fetch one ``app_settings`` value, decrypting if it's marked secret.

    Args:
        pool: asyncpg-style pool with ``.fetchrow()`` / ``.fetchval()``.
            Production callers pass the daemon's main pool; tests pass
            a ``MagicMock`` with ``AsyncMock`` methods.
        key: The ``app_settings.key`` to look up.
        default: Returned when the row is missing, the value is empty,
            decryption fails, or ``POINDEXTER_SECRET_KEY`` is unset for
            an encrypted row.

    Returns:
        The decrypted plaintext value (for ``is_secret=true``,
        ``enc:v1:...`` rows), the raw value (for plaintext rows), or
        ``default`` (for any failure mode).

    Never raises — the brain daemon is best-effort by design and the
    caller would just log the same exception anyway. If you need to
    distinguish "row missing" from "decrypt failed", use a sentinel
    default and check for it.
    """
    try:
        row = await pool.fetchrow(
            "SELECT value, is_secret FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("[BRAIN.SECRETS] read %s failed: %s", key, exc)
        return default

    if not row:
        return default
    val = row["value"]
    if not val:
        return default

    # Plaintext rows (is_secret=false, OR is_secret=true but never
    # encrypted by the rotation script) come back verbatim.
    if not row["is_secret"] or not val.startswith(_ENC_PREFIX):
        return val

    pkey = os.getenv("POINDEXTER_SECRET_KEY")
    if not pkey:
        logger.warning(
            "[BRAIN.SECRETS] POINDEXTER_SECRET_KEY unset — cannot decrypt %s",
            key,
        )
        return default

    try:
        decrypted = await pool.fetchval(
            "SELECT pgp_sym_decrypt(decode($1, 'base64'), $2)::text",
            val[len(_ENC_PREFIX):],
            pkey,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("[BRAIN.SECRETS] decrypt %s failed: %s", key, exc)
        return default

    if decrypted is None:
        return default
    return decrypted
