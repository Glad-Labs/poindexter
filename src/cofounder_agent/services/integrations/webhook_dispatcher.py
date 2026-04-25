"""Inbound webhook dispatcher.

Backs the catch-all ``POST /api/webhooks/{name}`` route. Looks up the
``webhook_endpoints`` row by name, verifies the request signature
according to ``signing_algorithm``, dispatches to the registered
handler, and updates the row's observability counters.

Fail-closed: if the row doesn't exist, is disabled, or signature
verification fails, the request is rejected and no handler runs.

Signature algorithms:

- ``none``        — no verification, no secret required
- ``hmac-sha256`` — header ``X-Signature`` carries raw hex digest
- ``svix``        — header ``Svix-Signature`` (Resend format); handles
  the ``v1,<hex>`` prefix and space-separated alternates
- ``bearer``      — header ``Authorization: Bearer <token>``; compares
  token directly, not a hashed payload
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import HTTPException, Request

from services.integrations import registry, secret_resolver

logger = logging.getLogger(__name__)


async def dispatch_inbound(
    name: str,
    request: Request,
    *,
    db_service: Any,
    site_config: Any,
) -> dict[str, Any]:
    """Look up the endpoint row, verify signature, run the handler.

    Returns the handler's result as a dict suitable for JSON response.
    Raises :class:`HTTPException` on any rejection path.
    """
    row = await _load_row(db_service, name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown webhook: {name}")
    if not row["enabled"]:
        raise HTTPException(status_code=404, detail=f"webhook disabled: {name}")
    if row["direction"] != "inbound":
        raise HTTPException(
            status_code=400,
            detail=f"webhook is outbound, not receivable: {name}",
        )

    body = await request.body()

    # Signature verification. Resolves the secret through the single
    # audited path so the GH-107 raw-get bug class cannot reappear.
    algorithm = row["signing_algorithm"]
    if algorithm != "none":
        secret = await secret_resolver.resolve_secret(dict(row), site_config)
        if not secret:
            await _record_failure(db_service, row["id"], "secret not configured")
            raise HTTPException(
                status_code=503,
                detail=f"signing secret not configured for webhook: {name}",
            )
        verified = _verify_signature(algorithm, body, request.headers, secret)
        if not verified:
            await _record_failure(db_service, row["id"], "signature mismatch")
            raise HTTPException(status_code=401, detail="invalid signature")

    # Parse body. Handlers receive a dict where possible; raw bytes when
    # the content isn't JSON (unusual for webhooks we accept today).
    try:
        payload: Any = json.loads(body.decode("utf-8")) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = body  # hand to the handler raw; handler decides

    try:
        result = await registry.dispatch(
            "webhook",
            row["handler_name"],
            payload,
            site_config=site_config,
            row=dict(row),
            pool=db_service.pool,
        )
    except registry.HandlerRegistrationError as exc:
        await _record_failure(db_service, row["id"], f"unknown handler: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"handler not registered: {row['handler_name']}",
        ) from exc
    except Exception as exc:
        # Handlers should catch their own expected errors and return a
        # structured result. An exception here means something unexpected
        # — log + surface as 500 so Matt notices, record the failure.
        logger.exception(
            "[webhook-dispatch] handler %r raised for %s",
            row["handler_name"], name,
        )
        await _record_failure(db_service, row["id"], f"handler exception: {exc}")
        raise HTTPException(status_code=500, detail="handler failed") from exc

    await _record_success(db_service, row["id"])

    out: dict[str, Any] = {"ok": True, "name": name}
    if isinstance(result, dict):
        out.update(result)
    return out


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def _verify_signature(
    algorithm: str,
    body: bytes,
    headers: Any,
    secret: str,
) -> bool:
    if algorithm == "hmac-sha256":
        provided = headers.get("x-signature")
        if not provided:
            return False
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, provided)

    if algorithm == "svix":
        # Svix protocol — used by Resend, Clerk, others. The signing
        # input is "<msg_id>.<timestamp>.<body>" (NOT just the body),
        # the secret carries a "whsec_" prefix whose remainder is
        # base64-encoded raw key bytes, and the expected signature is
        # base64 (NOT hex). Header carries multiple "v1,<base64>" pairs
        # space-separated for key rotation. See
        # https://docs.svix.com/receiving/verifying-payloads/how-manual
        import base64
        sig_header = headers.get("svix-signature")
        msg_id = headers.get("svix-id")
        timestamp = headers.get("svix-timestamp")
        if not sig_header or not msg_id or not timestamp:
            return False
        key_b64 = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
        try:
            key_bytes = base64.b64decode(key_b64)
        except Exception:
            return False
        signed = f"{msg_id}.{timestamp}.{body.decode('utf-8', errors='replace')}".encode("utf-8")
        expected = base64.b64encode(
            hmac.new(key_bytes, signed, hashlib.sha256).digest()
        ).decode("utf-8")
        for pair in sig_header.split(" "):
            pair = pair.strip()
            if "," not in pair:
                continue
            version, sig = pair.split(",", 1)
            if version == "v1" and hmac.compare_digest(expected, sig):
                return True
        return False

    if algorithm == "bearer":
        provided = headers.get("authorization", "")
        if not provided.startswith("Bearer "):
            return False
        token = provided[len("Bearer ") :]
        return hmac.compare_digest(secret, token)

    logger.warning("[webhook-dispatch] unknown signing_algorithm: %r", algorithm)
    return False


# ---------------------------------------------------------------------------
# Row load + counter updates
# ---------------------------------------------------------------------------


async def _load_row(db_service: Any, name: str) -> dict[str, Any] | None:
    if db_service.pool is None:
        raise HTTPException(status_code=503, detail="database pool unavailable")
    row = await db_service.pool.fetchrow(
        """
        SELECT id, name, direction, handler_name, path, url, signing_algorithm,
               secret_key_ref, event_filter, enabled, config, metadata
          FROM webhook_endpoints
         WHERE name = $1
        """,
        name,
    )
    return dict(row) if row else None


async def _record_success(db_service: Any, row_id: Any) -> None:
    if db_service.pool is None:
        return
    await db_service.pool.execute(
        """
        UPDATE webhook_endpoints
           SET last_success_at = now(),
               total_success = total_success + 1,
               last_error = NULL
         WHERE id = $1
        """,
        row_id,
    )


async def _record_failure(db_service: Any, row_id: Any, error: str) -> None:
    if db_service.pool is None:
        return
    await db_service.pool.execute(
        """
        UPDATE webhook_endpoints
           SET last_failure_at = now(),
               total_failure = total_failure + 1,
               last_error = $2
         WHERE id = $1
        """,
        row_id,
        error,
    )
