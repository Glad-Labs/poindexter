"""
Token Blocklist — JWT revocation store backed by Redis with in-memory fallback.

When Redis is available (REDIS_URL set, REDIS_ENABLED=true), revoked tokens
survive server restarts and are shared across all worker processes.

When Redis is unavailable, the module falls back to an in-memory dict.
Revoked tokens are then lost on restart — acceptable for development but not
recommended for production (fix #162).
"""

import hashlib
import logging
import os
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ── in-memory fallback ────────────────────────────────────────────────────────
# {token_sha256_hex: expiry_epoch_float}
_revoked: Dict[str, float] = {}

# ── Redis connection (lazy-initialised, module-level) ─────────────────────────
_redis: Optional[object] = None  # type: ignore[type-arg]
_redis_checked: bool = False

_KEY_PREFIX = "token_blocklist:"


async def _get_redis() -> Optional[object]:  # type: ignore[return]
    """Lazy-initialise a Redis connection.  Returns None when Redis is unavailable."""
    global _redis, _redis_checked
    if _redis_checked:
        return _redis

    _redis_checked = True
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_enabled = os.getenv("REDIS_ENABLED", "true").lower() in ("true", "1", "yes")

    if not redis_enabled:
        logger.info("[token_blocklist] Redis disabled — using in-memory fallback")
        return None

    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]

        conn = await aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
        )
        await conn.ping()
        _redis = conn
        logger.info("[token_blocklist] Redis connected — tokens will persist across restarts")
    except Exception as exc:
        logger.warning(
            "[token_blocklist] Redis unavailable (%s) — using in-memory fallback", exc
        )
        _redis = None

    return _redis


# ── public API ─────────────────────────────────────────────────────────────────


async def add_token(token: str, exp: float) -> None:
    """
    Mark a token as revoked until its natural expiry time (exp).

    Args:
        token: Raw JWT string
        exp: Token expiry as a Unix timestamp (seconds since epoch)
    """
    key = _hash(token)
    ttl_seconds = max(1, int(exp - time.time()))

    r = await _get_redis()
    if r is not None:
        try:
            await r.setex(f"{_KEY_PREFIX}{key}", ttl_seconds, "1")  # type: ignore[union-attr]
            logger.info("[token_blocklist] Token revoked in Redis (TTL %ds)", ttl_seconds)
            return
        except Exception as exc:
            logger.warning("[token_blocklist] Redis write failed (%s) — falling back to memory", exc)

    # in-memory fallback
    _prune()
    _revoked[key] = exp
    logger.info("[token_blocklist] Token revoked in memory (expires at %s)", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp)))


async def is_revoked(token: str) -> bool:
    """Return True if the token has been explicitly revoked and has not yet expired."""
    key = _hash(token)

    r = await _get_redis()
    if r is not None:
        try:
            result = await r.exists(f"{_KEY_PREFIX}{key}")  # type: ignore[union-attr]
            return bool(result)
        except Exception as exc:
            logger.warning("[token_blocklist] Redis read failed (%s) — falling back to memory", exc)

    # in-memory fallback
    _prune()
    return key in _revoked


# ── helpers ────────────────────────────────────────────────────────────────────


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _prune() -> None:
    """Remove expired entries from the in-memory fallback dict."""
    now = time.time()
    expired = [k for k, exp in _revoked.items() if exp <= now]
    for k in expired:
        del _revoked[k]
