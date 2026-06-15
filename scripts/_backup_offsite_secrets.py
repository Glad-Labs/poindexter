"""Decrypt the offsite-backup secrets for ``scripts/start-stack.sh``.

Reads the three encrypted ``app_settings`` rows
(``offsite_backup_restic_password`` / ``offsite_backup_s3_access_key_id`` /
``offsite_backup_s3_secret_access_key``), decrypts each with
``POINDEXTER_SECRET_KEY`` (from ``~/.poindexter/bootstrap.toml``), and prints
an env-file body to stdout that the ``backup-offsite`` compose service loads:

    RESTIC_PASSWORD=...
    AWS_ACCESS_KEY_ID=...
    AWS_SECRET_ACCESS_KEY=...

Mirrors ``scripts/_grafana_webhook_token.py`` exactly (same pgcrypto decrypt,
same bootstrap.toml read, same fail-soft posture). Never raises into the
calling shell: any failure emits an explicit empty assignment for the missing
key (loud-inert — the runner then idles) and a one-line WARNING to stderr.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_BOOTSTRAP_PATH = Path.home() / ".poindexter" / "bootstrap.toml"
_ENC_PREFIX = "enc:v1:"

# app_settings key → env var the runner/restic expects.
_KEYS: dict[str, str] = {
    "offsite_backup_restic_password": "RESTIC_PASSWORD",
    "offsite_backup_s3_access_key_id": "AWS_ACCESS_KEY_ID",
    "offsite_backup_s3_secret_access_key": "AWS_SECRET_ACCESS_KEY",
}


def _warn(msg: str) -> None:
    sys.stderr.write(f"[backup_offsite_secrets] {msg}\n")


def _load_bootstrap() -> dict[str, str]:
    try:
        if sys.version_info >= (3, 11):
            import tomllib as _tomllib
        else:  # pragma: no cover
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
    return {
        str(k): str(v).strip()
        for k, v in data.items()
        if isinstance(v, (str, int, float))
    }


async def _decrypt_all(dsn: str, secret_key: str) -> dict[str, str]:
    """Return {app_settings_key: plaintext} for whatever resolves; missing
    keys are simply absent (caller renders them as empty assignments)."""
    out: dict[str, str] = {}
    try:
        import asyncpg
    except ImportError:
        _warn("asyncpg unavailable on host Python; cannot decrypt secrets")
        return out
    try:
        conn = await asyncpg.connect(dsn, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        _warn(f"postgres connect failed ({type(exc).__name__}): {exc}")
        return out
    try:
        for setting_key in _KEYS:
            try:
                row = await conn.fetchrow(
                    "SELECT value, is_secret FROM app_settings WHERE key = $1",
                    setting_key,
                )
            except Exception as exc:  # noqa: BLE001
                _warn(f"query failed for {setting_key} ({type(exc).__name__})")
                continue
            if not row or not row["value"]:
                continue
            value = row["value"]
            if not row["is_secret"] or not value.startswith(_ENC_PREFIX):
                out[setting_key] = value
                continue
            try:
                plaintext = await conn.fetchval(
                    "SELECT pgp_sym_decrypt(decode($1, 'base64'), $2)::text",
                    value[len(_ENC_PREFIX):],
                    secret_key,
                )
            except Exception as exc:  # noqa: BLE001
                _warn(f"decrypt failed for {setting_key} ({type(exc).__name__})")
                continue
            if plaintext:
                out[setting_key] = plaintext
    finally:
        await conn.close()
    return out


def _render_env(resolved: dict[str, str]) -> str:
    """Render the env-file body. Missing keys → explicit empty assignment."""
    lines = [
        "# Auto-managed by scripts/start-stack.sh — DO NOT EDIT BY HAND.",
        "# Regenerated every start-stack invocation from encrypted app_settings",
        "# (offsite_backup_restic_password / _s3_access_key_id / _s3_secret_access_key).",
    ]
    for setting_key, env_var in _KEYS.items():
        lines.append(f"{env_var}={resolved.get(setting_key, '')}")
    return "\n".join(lines) + "\n"


def main() -> None:
    cfg = _load_bootstrap()
    if not cfg:
        sys.stdout.write(_render_env({}))
        return
    dsn = cfg.get("database_url") or os.getenv("DATABASE_URL") or ""
    secret_key = cfg.get("poindexter_secret_key") or os.getenv(
        "POINDEXTER_SECRET_KEY", ""
    )
    if not dsn or not secret_key:
        _warn("database_url or poindexter_secret_key missing; emitting empties")
        sys.stdout.write(_render_env({}))
        return
    resolved = asyncio.run(_decrypt_all(dsn, secret_key))
    sys.stdout.write(_render_env(resolved))


if __name__ == "__main__":
    main()
