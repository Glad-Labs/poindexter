"""Cross-process JWT cache for the poindexter CLI.

``OAuthClient`` caches a minted JWT in memory until ~30 s before expiry,
but every ``poindexter <cmd>`` invocation is a *fresh* process, so that
cache never survives. The result is a DB credential read (host->5433) plus
a token mint (host->8002) on **every** command. On Windows + Docker Desktop
both hops cross the flaky host port-proxy — the source of the recurring
``WinError 64`` / "No CLI OAuth credentials" wedges.

This module persists the minted JWT to ``~/.poindexter/cli_token_cache.json``
so a still-fresh token is reused across invocations, skipping *both* the DB
read and the mint on the hot path. It is consumed by ``OAuthClient`` through
the :class:`CliTokenStore` adapter (an optional ``token_store`` seam), so the
mint / cache / 401-retry logic stays in one place.

Design rules (this runs at CLI bootstrap, before the settings DB is
reachable — the same exemption as the other bootstrap-direct paths):

* **Never raise.** A cache read or write failing must never break the
  command it exists to speed up. Every entry point degrades to a miss.
* **Never hand out a bad token.** Stale, near-expiry, or undecodable
  entries read as a miss (freshness via ``oauth_client.token_is_fresh``,
  so the disk and in-memory caches agree on "fresh").
* **Owner-only on disk.** The file holds a bearer credential; it's written
  0600 next to ``bootstrap.toml`` (same directory, same threat model).
* **Kill-switch.** ``POINDEXTER_CLI_TOKEN_CACHE=0`` (also ``false`` / ``no``
  / ``off``) disables it entirely — the constant lives in an env var, not
  app_settings, because the whole point is to avoid touching the DB.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

# app_settings can't configure a cache whose job is to avoid the DB, so the
# path override + kill-switch are env vars (bootstrap tier, like
# POINDEXTER_API_URL).
_CACHE_DIR_ENV = "POINDEXTER_TOKEN_CACHE_DIR"
_CACHE_DISABLE_ENV = "POINDEXTER_CLI_TOKEN_CACHE"
_CACHE_FILENAME = "cli_token_cache.json"
_CACHE_VERSION = 1
_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})


def _cache_enabled() -> bool:
    raw = os.getenv(_CACHE_DISABLE_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


def _cache_dir() -> Path:
    override = os.getenv(_CACHE_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".poindexter"


def _cache_path() -> Path:
    return _cache_dir() / _CACHE_FILENAME


def _normalise(base_url: str) -> str:
    """Match ``WorkerClient``/``OAuthClient`` URL normalisation so a stored
    token keys the same with or without a trailing slash."""
    return base_url.rstrip("/")


def _read_all() -> dict[str, str]:
    """Return the ``{base_url: token}`` map, or ``{}`` on any problem.

    Tolerant of a missing file, non-JSON bytes, or an unexpected shape —
    all read as an empty cache (a miss), never an exception.
    """
    try:
        raw = _cache_path().read_bytes()
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return {}
    return {
        k: v for k, v in tokens.items() if isinstance(k, str) and isinstance(v, str)
    }


def _write_all(tokens: dict[str, str]) -> None:
    """Atomically persist the token map, owner-only. Best-effort — a write
    failure (unwritable dir, races, full disk) is swallowed."""
    path = _cache_path()
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": _CACHE_VERSION, "tokens": tokens})
        # Temp file in the same dir + atomic replace, so a reader never sees a
        # half-written file. ``mkstemp`` creates 0600 on POSIX; the explicit
        # ``chmod`` reaffirms it (a no-op on Windows, harmless).
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), prefix=".tok-", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        tmp = None  # consumed by replace
    except OSError:
        return
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)


def load_token(base_url: str) -> str | None:
    """Return a persisted, still-fresh token for ``base_url``, else ``None``.

    Never raises. Returns ``None`` when the cache is disabled, the entry is
    absent, or the token is stale / near-expiry / undecodable.
    """
    if not _cache_enabled():
        return None
    token = _read_all().get(_normalise(base_url))
    if not token:
        return None
    # Lazy import: keeps CLI cold-start light and avoids an import cycle at
    # module load (oauth_client pulls httpx + logger_config).
    from services.auth.oauth_client import token_is_fresh

    if not token_is_fresh(token):
        return None
    return token


def save_token(base_url: str, token: str) -> None:
    """Persist ``token`` for ``base_url``. No-op when disabled or ``token``
    is empty. Never raises."""
    if not _cache_enabled() or not token:
        return
    tokens = _read_all()
    tokens[_normalise(base_url)] = token
    _write_all(tokens)


def clear_token(base_url: str) -> None:
    """Drop the cached token for ``base_url`` (e.g. after a 401). Never
    raises; a missing entry is a no-op. Ignores the kill-switch so a stale
    file can always be cleaned up."""
    tokens = _read_all()
    if tokens.pop(_normalise(base_url), None) is not None:
        _write_all(tokens)


class CliTokenStore:
    """``TokenStore`` adapter binding the module functions to one base_url.

    This is the seam ``OAuthClient`` consumes: it consults ``load()`` on a
    cache miss before minting and calls ``save()`` after a mint, so the CLI
    reuses a token across invocations without the client knowing where it's
    persisted.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    async def load(self) -> str | None:
        return load_token(self._base_url)

    async def save(self, token: str) -> None:
        save_token(self._base_url, token)

    async def clear(self) -> None:
        clear_token(self._base_url)


__all__ = [
    "CliTokenStore",
    "load_token",
    "save_token",
    "clear_token",
]
