"""Migration 20260507_042600: seed sentry_dsn from GlitchTip.

ISSUE: Glad-Labs/poindexter#408

Background — ``services/sentry_integration.py`` reads ``sentry_dsn`` from
``app_settings`` and bails (no SDK init) when the row is empty. Migration
0058 seeds the row with an empty default, and nothing else fills it in.
Result: the local GlitchTip stack (4 containers, ~400MB RAM) runs and the
brain ``glitchtip_triage_probe`` is fully wired — but ``sentry_sdk.init()``
inside the worker is a no-op, so GlitchTip never sees a single event and
the triage probe triages nothing real.

This migration resolves a usable DSN for the local GlitchTip in the
following priority order, then upserts ``app_settings.sentry_dsn`` with
the result. We only overwrite when the existing value is empty (the
``sentry_dsn`` row already exists from migration 0058 with ``value=''``)
— operators who set their own DSN keep it.

Resolution order:

1. ``bootstrap.toml.sentry_dsn`` — explicit operator override. Canonical
   when present; never second-guessed.
2. ``bootstrap.toml.glitchtip_dsn`` — alias kept for clarity since the
   self-hosted backend is GlitchTip; treated identically to
   ``sentry_dsn`` (Sentry-compatible protocol either way).
3. **GlitchTip API mint** using ``glitchtip_admin_email`` +
   ``glitchtip_admin_password`` from bootstrap.toml. We log into the
   running container at ``http://localhost:8080`` (or
   ``GLITCHTIP_BASE_URL`` env var if set), pull the first project's
   first ``publicKey``, and assemble
   ``http://glitchtip-web:8000/<project_id>``. The host is rewritten to
   the compose-internal service name so the worker (which runs in the
   docker network) can reach it.
4. **Fail loud, leave empty.** If none of the above work the migration
   logs a WARNING with the resolution chain that was tried and leaves
   the row at ``''`` — which keeps the existing ``sentry_integration``
   "skip init" behavior. Per ``feedback_no_silent_defaults`` we don't
   pin a placeholder DSN; per ``feedback_app_settings_value_not_null``
   we never write NULL.

CI / fresh-DB safety: every external call (TOML parse, HTTP) is wrapped
in a generous try/except + short timeout. CI Postgres has no GlitchTip
neighbour, no bootstrap.toml, and no network egress — so the migration
falls through to step 4 and finishes cleanly. ``migrations_smoke.py``
asserts only that the schema_migrations row was recorded, which we do.

Idempotent: only writes when the existing ``sentry_dsn`` value is empty.
Safe to re-run after operator override.
"""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


_BOOTSTRAP_FILE = Path.home() / ".poindexter" / "bootstrap.toml"

# GlitchTip's compose-internal hostname/port (matches docker-compose.local.yml).
# Workers reach GlitchTip via the docker network; the host port mapping
# (8080) is only used by the migration during DSN minting on the host.
_GLITCHTIP_INTERNAL_HOST = "glitchtip-web"
_GLITCHTIP_INTERNAL_PORT = 8000

# Where the migration script reaches GlitchTip from the host while
# running. Falls back to in-cluster URL if the host port isn't bound.
_GLITCHTIP_HOST_BASE_URL = "http://localhost:8080"

# Per-call HTTP timeout — keep short so a hung GlitchTip doesn't block
# migration runs forever. Total budget for the mint flow is roughly
# 4 × _HTTP_TIMEOUT_SECONDS.
_HTTP_TIMEOUT_SECONDS = 5.0


# ---------------------------------------------------------------------------
# bootstrap.toml reader (stdlib-only — keep migration deps minimal).
# ---------------------------------------------------------------------------


def _read_bootstrap_toml() -> dict[str, Any]:
    """Return parsed bootstrap.toml, or {} on any failure."""
    if not _BOOTSTRAP_FILE.is_file():
        return {}
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover — older runtimes
        return {}
    try:
        with _BOOTSTRAP_FILE.open("rb") as f:
            return tomllib.load(f)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Migration 20260507_042600: bootstrap.toml parse failed: %r", exc
        )
        return {}


def _bootstrap_get(values: dict[str, Any], key: str) -> str:
    raw = values.get(key)
    if raw is None:
        return ""
    return str(raw).strip()


# ---------------------------------------------------------------------------
# GlitchTip DSN mint (stdlib urllib — no extra deps for the migration).
# ---------------------------------------------------------------------------


def _mint_dsn_from_glitchtip(
    base_url: str,
    email: str,
    password: str,
) -> str:
    """Best-effort: log into GlitchTip and assemble a DSN for the first project.

    Returns the DSN string on success, or "" on any failure (network,
    auth, no project, schema drift). Never raises — the migration treats
    a "" return as "fall back to next step in the chain".

    The resulting DSN is rewritten to use the compose-internal hostname
    (``glitchtip-web:8000``) so worker containers in the same docker
    network can reach it.
    """
    if not (base_url and email and password):
        return ""

    # Local imports — keep stdlib usage local to this function so the
    # migration module's top-level imports stay light.
    import json
    import urllib.error
    import urllib.request
    from http.cookiejar import CookieJar

    base_url = base_url.rstrip("/")

    cookie_jar = CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar),
    )
    opener.addheaders = [
        ("User-Agent", "poindexter-migration-20260507_042600/1.0"),
        ("Accept", "application/json"),
    ]

    def _open(req: urllib.request.Request) -> tuple[int, bytes, dict[str, str]]:
        try:
            with opener.open(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
                body = resp.read()
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, body, hdrs
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read()
            except Exception:  # noqa: BLE001
                body = b""
            return exc.code, body, {k.lower(): v for k, v in exc.headers.items()}
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            logger.debug("Migration 20260507_042600: GlitchTip request failed: %r", exc)
            return 0, b"", {}

    # 1) Prime CSRF cookie via the allauth session endpoint.
    status, _, _ = _open(
        urllib.request.Request(f"{base_url}/_allauth/browser/v1/auth/session")
    )
    if status not in (200, 401, 410):
        logger.warning(
            "Migration 20260507_042600: GlitchTip session probe returned %s — "
            "skipping mint",
            status,
        )
        return ""

    csrf = ""
    for cookie in cookie_jar:
        if cookie.name == "csrftoken":
            csrf = cookie.value or ""
            break
    if not csrf:
        logger.debug("Migration 20260507_042600: no csrftoken cookie set; mint will likely fail")

    # 2) Login.
    login_req = urllib.request.Request(
        f"{base_url}/_allauth/browser/v1/auth/login",
        data=json.dumps({"email": email, "password": password}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": f"{base_url}/",
            "Origin": base_url,
            "X-CSRFToken": csrf,
        },
        method="POST",
    )
    status, body, _ = _open(login_req)
    if status >= 400:
        logger.warning(
            "Migration 20260507_042600: GlitchTip login returned %s (%s) — "
            "skipping mint",
            status, body[:200],
        )
        return ""

    # 3) List orgs → take the first.
    status, body, _ = _open(urllib.request.Request(f"{base_url}/api/0/organizations/"))
    if status != 200:
        logger.warning(
            "Migration 20260507_042600: /organizations/ returned %s", status
        )
        return ""
    try:
        orgs = json.loads(body or b"[]")
    except json.JSONDecodeError:
        return ""
    if not isinstance(orgs, list) or not orgs:
        logger.warning(
            "Migration 20260507_042600: GlitchTip has no organizations — "
            "operator must create one before sentry_dsn can be auto-seeded"
        )
        return ""
    org_slug = (orgs[0].get("slug") or "").strip()
    if not org_slug:
        return ""

    # 4) List projects under the org → take the first.
    status, body, _ = _open(
        urllib.request.Request(f"{base_url}/api/0/organizations/{org_slug}/projects/")
    )
    if status != 200:
        logger.warning(
            "Migration 20260507_042600: /organizations/%s/projects/ returned %s",
            org_slug, status,
        )
        return ""
    try:
        projects = json.loads(body or b"[]")
    except json.JSONDecodeError:
        return ""
    if not isinstance(projects, list) or not projects:
        logger.warning(
            "Migration 20260507_042600: org %s has no projects — operator must "
            "create one before sentry_dsn can be auto-seeded",
            org_slug,
        )
        return ""
    project = projects[0]
    project_slug = (project.get("slug") or "").strip()
    if not project_slug:
        return ""

    # 5) Fetch the project's keys → use the first dsn.public if present;
    #    otherwise build the DSN from publicKey + project id ourselves.
    status, body, _ = _open(
        urllib.request.Request(f"{base_url}/api/0/projects/{org_slug}/{project_slug}/keys/")
    )
    if status != 200:
        logger.warning(
            "Migration 20260507_042600: /projects/%s/%s/keys/ returned %s",
            org_slug, project_slug, status,
        )
        return ""
    try:
        keys = json.loads(body or b"[]")
    except json.JSONDecodeError:
        return ""
    if not isinstance(keys, list) or not keys:
        logger.warning(
            "Migration 20260507_042600: project %s/%s has no keys — operator "
            "must create one before sentry_dsn can be auto-seeded",
            org_slug, project_slug,
        )
        return ""
    key = keys[0]

    dsn_block = key.get("dsn") if isinstance(key, dict) else None
    raw_dsn = ""
    if isinstance(dsn_block, dict):
        raw_dsn = (dsn_block.get("public") or "").strip()
    if not raw_dsn:
        public_key = (key.get("public") or "").strip() if isinstance(key, dict) else ""
        project_id = key.get("projectId") if isinstance(key, dict) else None
        if public_key and project_id:
            raw_dsn = (
                f"http://{public_key}@{_GLITCHTIP_INTERNAL_HOST}:"
                f"{_GLITCHTIP_INTERNAL_PORT}/{project_id}"
            )

    if not raw_dsn:
        logger.warning(
            "Migration 20260507_042600: GlitchTip key payload had no DSN material: %s",
            str(key)[:200],
        )
        return ""

    return _rewrite_dsn_for_internal_network(raw_dsn)


def _rewrite_dsn_for_internal_network(dsn: str) -> str:
    """Rewrite localhost DSNs to the compose-internal service hostname.

    GlitchTip's API returns DSNs using ``GLITCHTIP_DOMAIN`` (typically
    ``http://localhost:8080`` on first boot). The worker runs inside
    the docker network and can't reach the host's localhost — it talks
    to the GlitchTip web container via ``http://glitchtip-web:8000``.

    Anything that's not a localhost / 127.0.0.1 / 0.0.0.0 host is left
    alone (operator pointed at a real domain, we trust them).
    """
    try:
        parsed = urlparse(dsn)
    except Exception:  # noqa: BLE001
        return dsn
    if not parsed.hostname:
        return dsn
    host = parsed.hostname.lower()
    if host not in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return dsn

    netloc_user = ""
    if parsed.username:
        netloc_user = parsed.username
        if parsed.password:
            netloc_user += f":{parsed.password}"
        netloc_user += "@"

    new_netloc = f"{netloc_user}{_GLITCHTIP_INTERNAL_HOST}:{_GLITCHTIP_INTERNAL_PORT}"
    rewritten = parsed._replace(netloc=new_netloc).geturl()
    return rewritten


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def _read_app_setting(conn, key: str, default: str = "") -> str:
    """Plain (non-decrypted) read of an app_settings row."""
    try:
        val = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Migration 20260507_042600: read app_settings.%s failed: %r",
            key, exc,
        )
        return default
    if val is None or str(val).strip() == "":
        return default
    return str(val)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


async def _resolve_dsn(conn) -> tuple[str, str]:
    """Return (dsn, source). Empty dsn means "could not resolve"."""
    bootstrap = _read_bootstrap_toml()

    # Step 1 + 2: explicit operator-supplied keys in bootstrap.toml.
    for key in ("sentry_dsn", "glitchtip_dsn"):
        v = _bootstrap_get(bootstrap, key)
        if v:
            return _rewrite_dsn_for_internal_network(v), f"bootstrap.toml:{key}"

    # Step 3: GlitchTip API mint.
    email = _bootstrap_get(bootstrap, "glitchtip_admin_email")
    password = _bootstrap_get(bootstrap, "glitchtip_admin_password")
    base_url = (
        os.environ.get("GLITCHTIP_BASE_URL", "").strip()
        or _GLITCHTIP_HOST_BASE_URL
    )
    # If the configured URL points at the in-network hostname (which the
    # migration host can't resolve), swap in the host-mapped URL.
    if "glitchtip-web" in base_url:
        base_url = _GLITCHTIP_HOST_BASE_URL

    if email and password:
        minted = _mint_dsn_from_glitchtip(base_url, email, password)
        if minted:
            return minted, "glitchtip-api"

    return "", "unresolved"


# ---------------------------------------------------------------------------
# Migration entry points
# ---------------------------------------------------------------------------


async def up(pool) -> None:
    """Apply the migration: resolve a DSN and seed sentry_dsn if currently empty."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing — skipping migration "
                "20260507_042600 (sentry_dsn seed)"
            )
            return

        existing = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'sentry_dsn'",
        )

        if existing is not None and str(existing).strip():
            logger.info(
                "Migration 20260507_042600: app_settings.sentry_dsn already set "
                "(value preserved); operator override wins"
            )
            return

        dsn, source = await _resolve_dsn(conn)

        if not dsn:
            logger.warning(
                "Migration 20260507_042600: could not resolve sentry_dsn from "
                "bootstrap.toml.{sentry_dsn,glitchtip_dsn} or the GlitchTip "
                "API. Leaving app_settings.sentry_dsn = '' (worker Sentry SDK "
                "will stay un-initialised). Fix: set bootstrap.toml.sentry_dsn "
                "or create a GlitchTip org+project then re-run the migration "
                "(or upsert the row directly with `poindexter set sentry_dsn "
                "<dsn> --secret`)."
            )
            # NOT NULL contract: ensure the row stays at '' (already is in
            # most cases — defensive insert preserves any existing value).
            await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, TRUE, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                "sentry_dsn",
                "",
                "api_keys",
                "Sentry-compatible DSN — workers send exceptions here. The "
                "self-hosted GlitchTip stack speaks the Sentry protocol; "
                "DSNs of the form http://<key>@glitchtip-web:8000/<project_id> "
                "route into the local stack. Empty value disables the SDK at "
                "boot (services/sentry_integration.py:96-108).",
            )
            return

        # Seed the resolved DSN. UPDATE first (the row exists from 0058);
        # follow with INSERT-ON-CONFLICT-DO-NOTHING as a defensive net
        # for fresh DBs where 0058 hasn't run yet.
        await conn.execute(
            """
            UPDATE app_settings
               SET value = $1,
                   is_secret = TRUE,
                   updated_at = NOW()
             WHERE key = 'sentry_dsn'
               AND (value IS NULL OR value = '')
            """,
            dsn,
        )
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, $3, $4, TRUE, TRUE)
            ON CONFLICT (key) DO NOTHING
            """,
            "sentry_dsn",
            dsn,
            "api_keys",
            "Sentry-compatible DSN — workers send exceptions to GlitchTip "
            "via this endpoint. Auto-seeded from "
            "bootstrap.toml/glitchtip-api on first boot.",
        )

        logger.info(
            "Migration 20260507_042600: seeded sentry_dsn from %s "
            "(host rewritten to %s if necessary)",
            source, _GLITCHTIP_INTERNAL_HOST,
        )


async def down(pool) -> None:
    """Revert the migration.

    Clears the value back to '' rather than dropping the row — the row
    itself was created by migration 0058 and is depended on by
    sentry_integration.py.
    """
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        await conn.execute(
            "UPDATE app_settings SET value = '' WHERE key = 'sentry_dsn'"
        )
        logger.info("Migration 20260507_042600 rolled back: cleared sentry_dsn")
