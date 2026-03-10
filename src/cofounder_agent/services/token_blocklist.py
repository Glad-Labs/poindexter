"""
Token Blocklist — in-memory revocation store for JWT logout.

Tokens added via add_token() are rejected by is_revoked() until their
natural expiry time passes, after which they are pruned automatically.

Limitations:
- Not persistent across server restarts (revoked tokens become valid again after restart)
- Not shared across multiple process workers (use Redis for multi-worker deployments)

For production multi-instance deployments, replace with a Redis-backed implementation.
"""

import hashlib
import logging
import time
from typing import Dict

logger = logging.getLogger(__name__)

# {token_sha256_hex: expiry_epoch_float}
_revoked: Dict[str, float] = {}


def add_token(token: str, exp: float) -> None:
    """
    Mark a token as revoked until its expiry time.

    Args:
        token: Raw JWT string
        exp: Token expiry as a Unix timestamp (seconds)
    """
    _prune()
    key = _key(token)
    _revoked[key] = exp
    logger.info("[token_blocklist] Token revoked (expires at %s)", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp)))


def is_revoked(token: str) -> bool:
    """Return True if the token has been explicitly revoked and has not yet expired."""
    _prune()
    key = _key(token)
    return key in _revoked


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _prune() -> None:
    """Remove entries whose natural expiry has already passed (they can no longer be used anyway)."""
    now = time.time()
    expired = [k for k, exp in _revoked.items() if exp <= now]
    for k in expired:
        del _revoked[k]
