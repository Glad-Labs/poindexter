"""Write Prometheus scrape secrets to disk so password_file directives work.

Background — Prometheus has no native interpolation for env vars or
config-store lookups in its YAML. To scrape Uptime Kuma (and other
auth'd targets), Prometheus needs the API key as plaintext at scrape
time. We have two bad options and one good one:

* **Inline in prometheus.yml**: places the secret in git. Did this
  once (PR #102), got it leaked.
* **Env var into Prometheus container**: violates Matt's "no env vars"
  rule (see feedback_no_env_vars.md). Also still requires render-at-boot.
* **Use Prometheus's `password_file:` directive**: Prometheus reads the
  file at every scrape, so rotation is just "rewrite the file." No
  Prometheus restart, no template substitution, no env vars.

This module implements the third option for one secret. The pattern
generalizes — add another `(setting_key, password_file_path)` tuple
to ``_SECRETS`` to keep more secrets in lockstep.

## Operator workflow

1. Operator stores the secret in ``app_settings`` (``is_secret=true``)
   via ``poindexter set`` or the admin UI.
2. On every brain cycle (5 min), this module decrypts each secret via
   ``SiteConfig.get_secret`` and writes it to the corresponding
   bind-mounted file under ``/host-prometheus/secrets/``.
3. Prometheus reads the file on next scrape. No restart needed.
4. If the secret rotates, the next brain cycle overwrites the file.

## Bootstrap

If the password_file doesn't exist when Prometheus starts, the scrape
fails with "no such file" until brain runs its first cycle (≤5 min).
That's a graceful degradation — uptime-kuma metrics are temporarily
absent rather than the whole stack being broken.

## Idempotent

Re-runs are no-op when the on-disk content already matches the
app_settings value. Avoids spurious file mtime updates.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("brain.prometheus_secret_writer")

# Path inside the brain container (bind-mounted from
# ./infrastructure/prometheus/secrets on the host). The same host path
# is mounted into the prometheus container at
# /etc/prometheus/secrets/, which is what `password_file:` references
# in prometheus.yml.
SECRETS_DIR = Path(os.environ.get("PROMETHEUS_SECRETS_DIR", "/host-prometheus/secrets"))

# (app_settings key, on-disk filename). Files are written into
# ``SECRETS_DIR`` with mode 0o600 (operator-readable only).
_SECRETS: tuple[tuple[str, str], ...] = (
    ("uptime_kuma_api_key", "uptime-kuma-api-key"),
)


async def write_prometheus_secrets(site_config) -> dict[str, str]:
    """Write each tracked Prometheus scrape secret to its password_file.

    Args:
        site_config: SiteConfig instance with ``get_secret`` available.
            Brain wires this through from the cycle-level state.

    Returns a dict ``{filename: status}`` for each secret, where status
    is one of:
      * ``"written"``  — file existed but content changed; overwritten
      * ``"created"``  — file didn't exist; created with the value
      * ``"unchanged"`` — file existed and content already matches
      * ``"skipped"``   — app_setting empty (operator hasn't set the key)
      * ``"error: …"`` — write failed (e.g. permission denied, dir missing)

    Never raises — surface errors via the return dict so a single bad
    secret doesn't crash the brain cycle.
    """
    return await _write_all(site_config, _SECRETS, SECRETS_DIR)


async def _write_all(
    site_config,
    secrets: Iterable[tuple[str, str]],
    secrets_dir: Path,
) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        secrets_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning(
            "[PROMETHEUS_SECRETS] Could not create secrets dir %s: %s",
            secrets_dir, exc,
        )
        # Still try each write — mkdir might fail because the dir is
        # bind-mounted with restrictive perms but writes still succeed.

    for app_settings_key, filename in secrets:
        target = secrets_dir / filename
        try:
            value = await site_config.get_secret(app_settings_key, "")
        except Exception as exc:
            out[filename] = f"error: get_secret failed: {type(exc).__name__}: {exc}"
            logger.warning(
                "[PROMETHEUS_SECRETS] %s: get_secret failed: %s",
                app_settings_key, exc,
            )
            continue

        if not value:
            out[filename] = "skipped"
            logger.debug(
                "[PROMETHEUS_SECRETS] %s empty — skipping write of %s",
                app_settings_key, target,
            )
            continue

        try:
            existed = target.exists()
            current = target.read_text() if existed else ""
            if current == value:
                out[filename] = "unchanged"
                continue
            target.write_text(value)
            try:
                target.chmod(0o600)
            except Exception:
                # Non-fatal: bind-mount may not allow chmod.
                pass
            status = "written" if existed else "created"
            out[filename] = status
            logger.info(
                "[PROMETHEUS_SECRETS] %s %s from app_settings.%s",
                status, target, app_settings_key,
            )
        except Exception as exc:
            out[filename] = f"error: write failed: {type(exc).__name__}: {exc}"
            logger.warning(
                "[PROMETHEUS_SECRETS] write to %s failed: %s",
                target, exc,
            )

    return out
