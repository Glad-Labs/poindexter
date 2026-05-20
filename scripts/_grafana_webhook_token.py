"""Decrypt the Grafana webhook JWT for ``scripts/start-stack.sh``.

Reads the encrypted ``app_settings.grafana_webhook_oauth_jwt`` row,
decrypts it with ``POINDEXTER_SECRET_KEY`` (sourced from
``~/.poindexter/bootstrap.toml``), and prints the plaintext JWT to
stdout. ``start-stack.sh`` captures the output and exports it as
``$GRAFANA_WEBHOOK_TOKEN`` for Grafana provisioning env-var
substitution.

This is the host-side decrypt path — the worker container is the
canonical decrypt site (via ``plugins.secrets.get_secret``) but the
worker may not be up yet at the moment ``start-stack.sh`` runs. We
intentionally do NOT depend on the worker being healthy; the only
prerequisites are Postgres + the operator's bootstrap.toml.

## Failure modes (silent — emits empty string)

The script NEVER raises into the calling shell. Failures emit "" to
stdout and a one-line WARNING to stderr. Reasons:

- bootstrap.toml missing or unparseable
- ``POINDEXTER_SECRET_KEY`` or ``database_url`` keys missing
- asyncpg / tomllib missing on the host Python
- DB unreachable (Postgres still booting, wrong DSN, network down)
- ``app_settings.grafana_webhook_oauth_jwt`` row missing (fresh install
  before ``poindexter auth mint-grafana-token --persist`` ran)
- Decryption fails (wrong key, corrupted ciphertext)

Empty output → ``start-stack.sh`` exports ``GRAFANA_WEBHOOK_TOKEN=""``
→ Grafana provisions with an empty Bearer credential → the worker
rejects with 401 and a clear log line. That's the documented
fail-loud path; ``feedback_no_silent_defaults`` says don't paper over
missing config, surface it.

## Why a separate script instead of inlining in start-stack.sh

The decrypt requires asyncpg (pgcrypto's ``pgp_sym_decrypt`` lives
server-side and asyncpg is the easiest cross-platform path to talk
to a local Postgres). Bash can't do that natively. The alternative
— ``docker exec poindexter-worker python -c ...`` — requires the
worker to be running, which it isn't on first boot.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_BOOTSTRAP_PATH = Path.home() / ".poindexter" / "bootstrap.toml"
_KEY = "grafana_webhook_oauth_jwt"
_ENC_PREFIX = "enc:v1:"


def _warn(msg: str) -> None:
    """Single-channel WARN line on stderr — never raises."""
    sys.stderr.write(f"[grafana_webhook_token] {msg}\n")


def _load_bootstrap() -> dict[str, str]:
    """Parse bootstrap.toml. Returns {} on any failure."""
    try:
        if sys.version_info >= (3, 11):
            import tomllib as _tomllib
        else:  # pragma: no cover — tomli only on 3.10
            import tomli as _tomllib  # type: ignore[import-not-found]
    except ImportError:
        _warn("tomllib/tomli unavailable; cannot parse bootstrap.toml")
        return {}
    if not _BOOTSTRAP_PATH.is_file():
        _warn(f"{_BOOTSTRAP_PATH} not found; run `poindexter setup`")
        return {}
    try:
        with _BOOTSTRAP_PATH.open("rb") as f:
            data = _tomllib.load(f)
    except Exception as exc:  # noqa: BLE001
        _warn(f"bootstrap.toml parse failed: {exc}")
        return {}
    # Coerce keys to lowercase strings; values to stripped strings.
    return {
        str(k): str(v).strip()
        for k, v in data.items()
        if isinstance(v, (str, int, float))
    }


async def _decrypt(dsn: str, secret_key: str) -> str:
    """Hit Postgres, decrypt the row, return plaintext or ''.

    Connection is single-use; we tear it down immediately so a slow
    start-stack.sh doesn't hold a connection slot open.
    """
    try:
        import asyncpg
    except ImportError:
        _warn("asyncpg unavailable on host Python; cannot decrypt JWT")
        return ""

    try:
        conn = await asyncpg.connect(dsn, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        # Postgres not up yet, wrong DSN, etc. Fail soft.
        _warn(f"postgres connect failed ({type(exc).__name__}): {exc}")
        return ""

    try:
        row = await conn.fetchrow(
            "SELECT value, is_secret FROM app_settings WHERE key = $1",
            _KEY,
        )
    except Exception as exc:  # noqa: BLE001
        _warn(f"app_settings query failed ({type(exc).__name__}): {exc}")
        await conn.close()
        return ""

    if not row:
        _warn(
            f"app_settings.{_KEY} not set; run "
            "`poindexter auth mint-grafana-token --persist`"
        )
        await conn.close()
        return ""

    value = row["value"]
    if not value:
        _warn(f"app_settings.{_KEY} is empty")
        await conn.close()
        return ""

    # Plaintext rows (shouldn't happen for this key but be safe).
    if not row["is_secret"] or not value.startswith(_ENC_PREFIX):
        await conn.close()
        return value

    try:
        plaintext = await conn.fetchval(
            "SELECT pgp_sym_decrypt(decode($1, 'base64'), $2)::text",
            value[len(_ENC_PREFIX):],
            secret_key,
        )
    except Exception as exc:  # noqa: BLE001
        _warn(f"pgcrypto decrypt failed ({type(exc).__name__}): {exc}")
        await conn.close()
        return ""

    await conn.close()
    return plaintext or ""


def main() -> None:
    cfg = _load_bootstrap()
    if not cfg:
        sys.stdout.write("")
        return

    dsn = cfg.get("database_url") or os.getenv("DATABASE_URL") or ""
    if not dsn:
        _warn("database_url missing from bootstrap.toml")
        sys.stdout.write("")
        return

    secret_key = cfg.get("poindexter_secret_key") or os.getenv(
        "POINDEXTER_SECRET_KEY", "",
    )
    if not secret_key:
        _warn("poindexter_secret_key missing; cannot decrypt JWT")
        sys.stdout.write("")
        return

    token = asyncio.run(_decrypt(dsn, secret_key))
    sys.stdout.write(token)


if __name__ == "__main__":
    main()
