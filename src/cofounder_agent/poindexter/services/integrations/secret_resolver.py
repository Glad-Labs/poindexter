"""Single audited path for resolving an integration row's secret.

Every integration row that needs a signing key, auth token, or any
other encrypted value stores a reference in ``secret_key_ref``
(or an equivalent column) pointing at an ``app_settings`` key. This
module is the ONE place code reaches into ``site_config`` to turn that
reference into a plaintext value.

## Why centralize

Three separate incidents on 2026-04-23 (Alertmanager dispatch, Vercel
ISR, auto-Telegram post-publish) hit the same bug: the caller used
``site_config.get(key)`` instead of ``await site_config.get_secret(key)``,
and got ciphertext back. Tracked in GH-107.

Fixing every call site is whack-a-mole. Instead, the integrations
framework requires every surface to route through :func:`resolve_secret`,
and a CI lint can flag any ``site_config.get("*_secret"|"*_token")``
outside this module.

## Usage

.. code:: python

    secret = await resolve_secret(row, site_config)
    if secret is None:
        raise HTTPException(503, "secret not configured")
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Columns that an integration row might use to reference a secret.
# Most surfaces use ``secret_key_ref``; retention/taps may use different
# names. Listed in priority order — the first non-empty wins.
_REF_COLUMNS = (
    "secret_key_ref",
    "credentials_ref",
    "auth_ref",
)


async def resolve_secret(row: dict[str, Any], site_config: Any) -> str | None:
    """Return the plaintext secret referenced by ``row``, or ``None``.

    ``row`` is the integration row as a mapping. The function looks at
    the reference columns in :data:`_REF_COLUMNS` (first non-empty wins)
    and calls ``site_config.get_secret(<that key>)`` to decrypt.

    Returns ``None`` if no reference column is set on the row — callers
    interpret this as "this row doesn't have a signing secret", which
    is a legitimate case (e.g. a webhook handler that doesn't require a
    signature, or an outbound call to an unauthenticated URL).

    Returns an empty string if the reference column points at a key
    that exists in ``app_settings`` but is empty. Callers should treat
    this as "configured but not set" — typically a 503-equivalent.

    Raises whatever ``site_config.get_secret`` raises on decryption
    failure (key mismatch, corrupt ciphertext) — these are operator
    errors that the caller should surface, not swallow.
    """
    ref: str | None = None
    for col in _REF_COLUMNS:
        val = row.get(col)
        if val:
            ref = val
            break

    if ref is None:
        return None

    # Callers passing ``site_config=None`` (e.g. ``notify_operator`` from
    # a module-level startup check that doesn't have one in scope) get a
    # clean None instead of an AttributeError. The handler raises a
    # tidy "secret not configured" error, the dispatcher records the
    # failure on the row, and the operator sees one log line instead of
    # a stack trace.
    if site_config is None:
        logger.warning(
            "resolve_secret: row %r references %r but no site_config in scope — "
            "treating as unconfigured",
            row.get("name"),
            ref,
        )
        return None

    secret = await site_config.get_secret(ref)
    if secret is None:
        logger.warning(
            "resolve_secret: row %r references app_settings key %r which does not exist",
            row.get("name"),
            ref,
        )
        return ""
    return secret
