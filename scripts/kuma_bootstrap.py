"""One-shot Kuma bootstrap — restore from-scratch state to working monitoring.

Runs inside the worker container (it has asyncpg + can reach uptime-kuma:3001).
What it does, idempotently:

1. Connect to Kuma over socket.io.
2. needSetup → if True, generate strong admin password and run `setup`.
   Otherwise log in with the password stored in app_settings.uptime_kuma_admin_password.
3. Persist admin credentials to app_settings (`uptime_kuma_admin_username`,
   `uptime_kuma_admin_password` is_secret=true).
4. Create the default monitor set (idempotent — skips by name if already present).
5. Create a "prometheus" API key (idempotent — deletes the existing one with
   that name first to ensure the returned secret is fresh).
6. Persist the API key to app_settings.uptime_kuma_api_key (is_secret=true)
   so brain/prometheus_secret_writer.py picks it up on the next cycle.

Re-running is safe — every step checks current state first.

Created 2026-05-07 to recover from an empty-volume state where the entire
Kuma database was wiped (0 users / 0 monitors / 0 api_keys). See Telegram
thread 9289-9290 for the diagnosis.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from typing import Any

import asyncpg
import socketio


KUMA_URL = "http://uptime-kuma:3001"
ADMIN_USERNAME = "admin"
API_KEY_NAME = "prometheus"

DEFAULT_MONITORS: list[dict[str, Any]] = [
    {
        "name": "gladlabs.io (public)",
        "type": "http",
        "url": "https://www.gladlabs.io",
        "interval": 60,
        "tags": ["public"],
    },
    {
        "name": "gladlabs.mintlify (docs)",
        "type": "http",
        "url": "https://gladlabs.mintlify.app",
        "interval": 300,
        "tags": ["public"],
    },
    {
        "name": "tailnet funnel (voice)",
        "type": "http",
        "url": "https://nightrider.taild4f626.ts.net",
        "interval": 300,
        "tags": ["tailnet"],
    },
    {
        "name": "worker /health",
        "type": "http",
        "url": "http://worker:8002/health",
        "interval": 60,
        "tags": ["internal"],
    },
    {
        "name": "brain-daemon /health",
        "type": "http",
        "url": "http://brain-daemon:8005/health",
        "interval": 60,
        "tags": ["internal"],
    },
    {
        "name": "grafana /api/health",
        "type": "http",
        "url": "http://grafana:3000/api/health",
        "interval": 120,
        "tags": ["internal"],
    },
    {
        "name": "prometheus /-/ready",
        "type": "http",
        "url": "http://prometheus:9090/-/ready",
        "interval": 120,
        "tags": ["internal"],
    },
    {
        "name": "loki /ready",
        "type": "http",
        "url": "http://loki:3100/ready",
        "interval": 120,
        "tags": ["internal"],
    },
    {
        "name": "tempo /ready",
        "type": "http",
        "url": "http://tempo:3200/ready",
        "interval": 120,
        "tags": ["internal"],
    },
    {
        "name": "pyroscope /ready",
        "type": "http",
        "url": "http://pyroscope:4040/ready",
        "interval": 120,
        "tags": ["internal"],
    },
    {
        "name": "langfuse-web /api/public/health",
        "type": "http",
        "url": "http://langfuse-web:3000/api/public/health",
        "interval": 120,
        "tags": ["internal"],
    },
    {
        "name": "prefect-server /api/health",
        "type": "http",
        "url": "http://prefect-server:4200/api/health",
        "interval": 120,
        "tags": ["internal"],
    },
    {
        "name": "glitchtip /api/0/",
        "type": "http",
        "url": "http://glitchtip-web:8000/api/0/",
        "interval": 300,
        "tags": ["internal"],
    },
    {
        "name": "postgres-local (tcp)",
        "type": "port",
        "hostname": "postgres-local",
        "port": 5432,
        "interval": 120,
        "tags": ["internal"],
    },
]

# Defaults Kuma's `add` handler expects on every monitor row. Tuned to this
# Kuma image's schema (database_version=10) — fields like jsonPathOperator,
# snmpVersion, screenshot, remoteBrowser, tlsCheckFreshness exist in newer
# Kuma builds but aren't in the local schema, so passing them causes a
# SQLITE_ERROR on insert. Keep this list aligned with `PRAGMA table_info(monitor)`.
MONITOR_DEFAULTS: dict[str, Any] = {
    "type": "http",
    "method": "GET",
    "interval": 60,
    "retryInterval": 60,
    "resendInterval": 0,
    "maxretries": 0,
    "timeout": 48,
    "expiryNotification": False,
    "ignoreTls": False,
    "upsideDown": False,
    "packetSize": 56,
    "maxredirects": 10,
    "accepted_statuscodes": ["200-299"],
    "dns_resolve_type": "A",
    "dns_resolve_server": "1.1.1.1",
    "port": None,
    "proxyId": None,
    "notificationIDList": {},
    "headers": "",
    "body": "",
    "grpcBody": "",
    "grpcMetadata": "",
    "grpcMethod": "",
    "grpcServiceName": "",
    "grpcEnableTls": False,
    "basic_auth_user": None,
    "basic_auth_pass": None,
    "authMethod": None,
    "authWorkstation": None,
    "authDomain": None,
    "tlsCa": None,
    "tlsCert": None,
    "tlsKey": None,
    "mqttUsername": "",
    "mqttPassword": "",
    "mqttTopic": "",
    "mqttSuccessMessage": "",
    "databaseConnectionString": "",
    "databaseQuery": "",
    "docker_container": "",
    "docker_host": None,
    "radiusUsername": "",
    "radiusPassword": "",
    "radiusCalledStationId": "",
    "radiusCallingStationId": "",
    "radiusSecret": "",
    "game": None,
    "gamedigGivenPortOnly": True,
    "kafkaProducerBrokers": [],
    "kafkaProducerTopic": "",
    "kafkaProducerMessage": "",
    "kafkaProducerAllowAutoTopicCreation": False,
    "kafkaProducerSaslOptions": {"mechanism": "None"},
    "httpBodyEncoding": "json",
    "jsonPath": "",
    "expectedValue": "",
    "kafkaProducerSsl": False,
    "oauth_auth_method": "client_secret_basic",
    "oauth_token_url": "",
    "oauth_client_id": "",
    "oauth_client_secret": "",
    "oauth_scopes": "",
    "invertKeyword": False,
    "description": "",
    "parent": None,
}


def make_monitor_payload(spec: dict[str, Any]) -> dict[str, Any]:
    payload = dict(MONITOR_DEFAULTS)
    payload.update(spec)
    payload.pop("tags", None)  # tags are added in a separate step
    return payload


async def _emit(sio: socketio.AsyncClient, event: str, *args: Any, timeout: float = 30.0) -> Any:
    """Call a Kuma socket event with timeout, return its callback result."""
    return await asyncio.wait_for(sio.call(event, args if len(args) > 1 else (args[0] if args else None)), timeout=timeout)


async def main() -> int:
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    try:
        # SiteConfig handles the pgcrypto envelope for is_secret rows. Reading
        # raw `value` directly returns ciphertext (`enc:v1:...`) — passing that
        # to Kuma's login fails. SiteConfig.get_secret() decrypts on the fly.
        from services.site_config import SiteConfig  # noqa: WPS433 — runtime path

        sc = SiteConfig(initial_config={})
        await sc.load(pool)

        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        await sio.connect(KUMA_URL, transports=["websocket"], wait_timeout=10)
        print(f"connected to {KUMA_URL}", flush=True)

        # ---- 1. Setup or login --------------------------------------------------
        need_setup = await _emit(sio, "needSetup")
        print(f"needSetup={need_setup}", flush=True)

        existing_pw = await sc.get_secret("uptime_kuma_admin_password", "")
        if not existing_pw:
            existing_pw = None

        if need_setup:
            password = existing_pw or secrets.token_urlsafe(24)
            print(f"running setup as {ADMIN_USERNAME!r}", flush=True)
            res = await _emit(sio, "setup", ADMIN_USERNAME, password)
            print(f"setup result: ok={res.get('ok') if isinstance(res, dict) else res}", flush=True)
            if isinstance(res, dict) and not res.get("ok"):
                print(f"setup failed: {res.get('msg')}", flush=True)
                return 2

            async with pool.acquire() as c:
                await c.execute(
                    """
                    INSERT INTO app_settings (key, value, is_secret, category, description, updated_at)
                    VALUES ('uptime_kuma_admin_username', $1, false, 'monitoring',
                            'Kuma admin username (set by scripts/kuma_bootstrap.py)', now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                    """,
                    ADMIN_USERNAME,
                )
                await c.execute(
                    """
                    INSERT INTO app_settings (key, value, is_secret, category, description, updated_at)
                    VALUES ('uptime_kuma_admin_password', $1, true, 'monitoring',
                            'Kuma admin password (set by scripts/kuma_bootstrap.py)', now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, is_secret = true, updated_at = now()
                    """,
                    password,
                )
            login_password = password
        else:
            if not existing_pw:
                print("ERROR: Kuma reports already-setup but no password in app_settings — manual reset needed", flush=True)
                return 3
            login_password = existing_pw

        # ---- 2. Login -----------------------------------------------------------
        login_res = await _emit(sio, "login", {
            "username": ADMIN_USERNAME,
            "password": login_password,
            "token": "",
        })
        if not (isinstance(login_res, dict) and login_res.get("ok")):
            print(f"login failed: {login_res}", flush=True)
            return 4
        print("login ok", flush=True)

        # ---- 3. Existing monitors / api keys -----------------------------------
        # Kuma pushes the monitor list via 'monitorList' event after login.
        # Capture it via a one-shot listener.
        existing_monitors: dict[str, int] = {}
        list_event = asyncio.Event()

        @sio.on("monitorList")
        def _on_monitor_list(data: dict[str, Any]) -> None:
            existing_monitors.clear()
            for mid, m in (data or {}).items():
                existing_monitors[m.get("name", "")] = int(mid)
            list_event.set()

        try:
            await asyncio.wait_for(list_event.wait(), timeout=8)
        except asyncio.TimeoutError:
            print("warning: no monitorList push received in 8s — assuming empty", flush=True)
        print(f"existing monitors: {len(existing_monitors)}", flush=True)

        api_keys = await _emit(sio, "getAPIKeyList")
        existing_keys: list[dict[str, Any]] = []
        if isinstance(api_keys, dict) and api_keys.get("ok"):
            existing_keys = api_keys.get("keyList") or []
        print(f"existing API keys: {len(existing_keys)}", flush=True)

        # ---- 4. Create monitors -------------------------------------------------
        created = 0
        skipped = 0
        for spec in DEFAULT_MONITORS:
            if spec["name"] in existing_monitors:
                skipped += 1
                continue
            payload = make_monitor_payload(spec)
            res = await _emit(sio, "add", payload)
            if isinstance(res, dict) and res.get("ok"):
                created += 1
                print(f"  + {spec['name']} (id={res.get('monitorID')})", flush=True)
            else:
                print(f"  ! {spec['name']}: {res}", flush=True)
        print(f"monitors: created={created} skipped={skipped}", flush=True)

        # ---- 5. API key (delete-and-recreate so we always know the secret) -----
        for k in existing_keys:
            if k.get("name") == API_KEY_NAME:
                kid = k.get("id")
                print(f"  removing stale API key id={kid} so we can mint a fresh one", flush=True)
                await _emit(sio, "deleteAPIKey", kid)

        key_payload = {
            "name": API_KEY_NAME,
            "expires": None,
            "active": True,
        }
        res = await _emit(sio, "addAPIKey", key_payload)
        if not (isinstance(res, dict) and res.get("ok")):
            print(f"addAPIKey failed: {res}", flush=True)
            return 5
        clear_key = res.get("key") or res.get("clearKey")
        if not clear_key:
            print(f"addAPIKey returned no clear-text key: {res}", flush=True)
            return 6
        print(f"new API key minted (length={len(clear_key)}, prefix={clear_key[:4]}...)", flush=True)

        # ---- 6. Persist key to app_settings ------------------------------------
        async with pool.acquire() as c:
            await c.execute(
                """
                INSERT INTO app_settings (key, value, is_secret, category, description, updated_at)
                VALUES ('uptime_kuma_api_key', $1, true, 'monitoring',
                        'Kuma metrics-scrape API key (set by scripts/kuma_bootstrap.py)', now())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, is_secret = true, updated_at = now()
                """,
                clear_key,
            )
        print("app_settings.uptime_kuma_api_key updated", flush=True)

        await sio.disconnect()
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
