# MCP HTTP Recovery Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a host-side HTTP recovery agent (port 9841) that the containerized brain daemon can POST to in order to restart the "Poindexter MCP HTTP" Windows Task Scheduler task, then wire it into the existing `mcp_http_probe` auto-recovery path.

**Architecture:** A pure-stdlib Python HTTP server runs on the Windows host at `0.0.0.0:9841`. When the brain probe detects `http://host.docker.internal:8004/healthz` is down, it POSTs `{"service":"mcp-http"}` with a Bearer token to `http://host.docker.internal:9841/recover`. The agent verifies the token against `bootstrap.toml` and runs `Start-ScheduledTask`. The probe gains two new app_settings keys (`mcp_http_probe_recovery_url`, `mcp_http_probe_recovery_token`) and a `recovery_fn` injection point for tests, symmetric to the existing `launcher_fn`.

**Tech Stack:** Python stdlib (`http.server`, `subprocess`, `json`), asyncpg, existing `brain.mcp_http_probe` module, Windows Task Scheduler via PowerShell.

---

## File Map

| File                                                                                            | Action | Purpose                                                                                                                            |
| ----------------------------------------------------------------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `~/.poindexter/scripts/recovery-agent.py`                                                       | Create | Host-side HTTP server that receives recovery POST and runs `Start-ScheduledTask`                                                   |
| `~/.poindexter/scripts/recovery-agent.cmd`                                                      | Create | Windowless launcher script (mirrors `poindexter-mcp-http.cmd`)                                                                     |
| `brain/mcp_http_probe.py`                                                                       | Modify | Add `RECOVERY_URL_KEY`, `RECOVERY_TOKEN_KEY`, `_try_http_recovery()`, update `_handle_failure` and `run_mcp_http_probe` signatures |
| `src/cofounder_agent/tests/unit/brain/test_mcp_http_probe.py`                                   | Modify | Add tests for HTTP recovery path and its restart cap integration                                                                   |
| `src/cofounder_agent/services/settings_defaults.py`                                             | Modify | Add `mcp_http_probe_recovery_url: ""` to DEFAULTS                                                                                  |
| `src/cofounder_agent/services/migrations/20260608_HHMMSS_seed_mcp_http_probe_recovery_token.py` | Create | Seed `mcp_http_probe_recovery_token` row with `is_secret=TRUE, value=''`                                                           |

---

## Task 1: Create the recovery agent script

**Files:**

- Create: `~/.poindexter/scripts/recovery-agent.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Poindexter Recovery Agent — host-side HTTP endpoint for container-initiated
service recovery.

Runs on the Windows host at 0.0.0.0:9841 (bound to all interfaces so
Docker containers can reach it via host.docker.internal). Accepts
authenticated POST /recover requests and executes recovery actions that
require host-level access (Task Scheduler, etc.).

Authentication: Bearer <token> where token is read from
~/.poindexter/bootstrap.toml key ``poindexter_recovery_token``.

Usage:
    uv run recovery-agent.py            # from mcp-server/ dir, or
    python recovery-agent.py            # if sys.path includes brain/

The token must also be stored as a secret in app_settings under the key
``mcp_http_probe_recovery_token`` so the brain probe can read it.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("poindexter-recovery-agent")

# Map of service names (sent by the brain probe) → Task Scheduler task names.
SERVICES: dict[str, str] = {
    "mcp-http": "Poindexter MCP HTTP",
}

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9841


def _load_token() -> str:
    """Read the recovery token from bootstrap.toml or POINDEXTER_RECOVERY_TOKEN env var."""
    token = os.environ.get("POINDEXTER_RECOVERY_TOKEN", "").strip()
    if token:
        return token

    # Walk up from this file's location to find brain/bootstrap.py.
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "brain" / "bootstrap.py").is_file():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            break

    try:
        from brain.bootstrap import get_bootstrap_value  # type: ignore[import-not-found]
        token = (get_bootstrap_value("poindexter_recovery_token", "") or "").strip()
    except Exception as exc:
        logger.error("Failed to load bootstrap token: %s", exc)

    return token


class RecoveryHandler(BaseHTTPRequestHandler):
    """HTTP handler — only POST /recover is accepted."""

    # Set at startup; empty string means auth is disabled (dev only).
    _token: str = ""

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/recover":
            self.send_error(404, "Not found")
            return

        # --- Auth ---
        auth = self.headers.get("Authorization", "")
        if self._token:
            if not auth.startswith("Bearer "):
                self._send_json(401, {"ok": False, "error": "missing bearer token"})
                return
            if auth[len("Bearer "):].strip() != self._token:
                self._send_json(401, {"ok": False, "error": "invalid token"})
                return

        # --- Parse body ---
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body: dict = json.loads(self.rfile.read(length)) if length else {}
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid JSON"})
            return

        service = body.get("service", "")
        if service not in SERVICES:
            self._send_json(400, {"ok": False, "error": f"unknown service: {service!r}"})
            return

        task_name = SERVICES[service]
        logger.info("Recovery request: service=%r → task=%r", service, task_name)

        try:
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive",
                    "-Command", f"Start-ScheduledTask -TaskName '{task_name}'",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            logger.error("Subprocess error: %s", exc)
            self._send_json(500, {"ok": False, "error": str(exc)})
            return

        if result.returncode == 0:
            logger.info("Recovery succeeded: %s", task_name)
            self._send_json(200, {"ok": True, "service": service, "task": task_name})
        else:
            err = (result.stderr or result.stdout or "unknown error").strip()
            logger.warning("Recovery failed (rc=%d): %s", result.returncode, err)
            self._send_json(500, {"ok": False, "error": err})

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/healthz", "/health"}:
            self._send_json(200, {"ok": True, "service": "poindexter-recovery-agent"})
        else:
            self.send_error(404)

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info(fmt, *args)


def main() -> None:
    token = _load_token()
    if not token:
        logger.error(
            "poindexter_recovery_token not set in bootstrap.toml or "
            "POINDEXTER_RECOVERY_TOKEN env var — requests will be rejected. "
            "Set the token and restart."
        )
        # Don't exit — still start the server so the brain probe gets a 401
        # (server reachable) rather than a ConnectionError (server absent).

    RecoveryHandler._token = token

    host = os.environ.get("POINDEXTER_RECOVERY_HOST", DEFAULT_HOST)
    port = int(os.environ.get("POINDEXTER_RECOVERY_PORT", str(DEFAULT_PORT)))

    logger.info("Recovery agent starting on %s:%d", host, port)
    server = ThreadingHTTPServer((host, port), RecoveryHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test it locally (without auth)**

```powershell
$env:POINDEXTER_RECOVERY_TOKEN = "testtoken"
$env:POINDEXTER_RECOVERY_HOST = "127.0.0.1"
Start-Process python -ArgumentList "C:\Users\mattm\.poindexter\scripts\recovery-agent.py" -NoNewWindow

Start-Sleep 2
Invoke-WebRequest -Uri "http://127.0.0.1:9841/healthz" -UseBasicParsing | Select-Object StatusCode, Content
# Expected: StatusCode=200, Content={"ok":true,...}

# CTRL+C the process after verifying
```

- [ ] **Step 3: Commit — no git repo for `~/.poindexter/scripts/`, just confirm file exists**

```powershell
Test-Path "C:\Users\mattm\.poindexter\scripts\recovery-agent.py"
# Expected: True
```

---

## Task 2: Create the Windows launcher script

**Files:**

- Create: `~/.poindexter/scripts/recovery-agent.cmd`

- [ ] **Step 1: Write the launcher**

```cmd
@echo off
:: Poindexter Recovery Agent (port 9841) — receives POST /recover from the
:: brain daemon (Docker container) and restarts host Task Scheduler tasks.
:: Started at logon by Task Scheduler "Poindexter Recovery Agent" (windowless).

set "LOG=%USERPROFILE%\.poindexter\logs\recovery-agent.log"
if not exist "%USERPROFILE%\.poindexter\logs" mkdir "%USERPROFILE%\.poindexter\logs"

cd /d "%USERPROFILE%\glad-labs-website\mcp-server"
echo %date% %time% Starting Poindexter Recovery Agent >> "%LOG%"
python "%USERPROFILE%\.poindexter\scripts\recovery-agent.py" >> "%LOG%" 2>&1
```

Note: Uses `python` directly (not `uv run`) because the recovery agent uses only stdlib — no virtualenv needed. If the system Python isn't on PATH, use the full path (e.g., `C:\Users\mattm\AppData\Local\Programs\Python\Python313\python.exe`).

- [ ] **Step 2: Verify the file is in place**

```powershell
Test-Path "C:\Users\mattm\.poindexter\scripts\recovery-agent.cmd"
# Expected: True
```

---

## Task 3: Register the Task Scheduler task

- [ ] **Step 1: Register the task**

```powershell
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument '/c "C:\Users\mattm\.poindexter\scripts\recovery-agent.cmd"'

$trigger = New-ScheduledTaskTrigger -AtLogon

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "Poindexter Recovery Agent" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force
```

- [ ] **Step 2: Start it immediately and verify**

```powershell
Start-ScheduledTask -TaskName "Poindexter Recovery Agent"
Start-Sleep 4
Get-NetTCPConnection -LocalPort 9841 -ErrorAction SilentlyContinue | Select-Object State, OwningProcess
# Expected: State=Listen
```

---

## Task 4: Generate and configure the shared token

- [ ] **Step 1: Generate a token**

```powershell
$token = python -c "import secrets; print(secrets.token_hex(32))"
Write-Host "Token: $token"
```

- [ ] **Step 2: Add it to bootstrap.toml**

Open `C:\Users\mattm\.poindexter\bootstrap.toml` and add:

```toml
poindexter_recovery_token = "<token-from-step-1>"
```

- [ ] **Step 3: Restart the recovery agent to pick up the token**

```powershell
Stop-ScheduledTask -TaskName "Poindexter Recovery Agent" -ErrorAction SilentlyContinue
Start-Sleep 2
Start-ScheduledTask -TaskName "Poindexter Recovery Agent"
Start-Sleep 3
```

- [ ] **Step 4: Verify the token is required**

```powershell
# Without token → 401
try {
    Invoke-WebRequest -Uri "http://localhost:9841/recover" -Method POST `
        -ContentType "application/json" -Body '{"service":"mcp-http"}' -UseBasicParsing
} catch { $_.Exception.Response.StatusCode }
# Expected: 401

# With wrong token → 401
try {
    Invoke-WebRequest -Uri "http://localhost:9841/recover" -Method POST `
        -Headers @{Authorization="Bearer wrongtoken"} `
        -ContentType "application/json" -Body '{"service":"mcp-http"}' -UseBasicParsing
} catch { $_.Exception.Response.StatusCode }
# Expected: 401
```

---

## Task 5: Update the brain probe (TDD)

**Files:**

- Modify: `brain/mcp_http_probe.py`
- Modify: `src/cofounder_agent/tests/unit/brain/test_mcp_http_probe.py`

### Step 5a — Tests first

- [ ] **Step 1: Add HTTP recovery tests to `test_mcp_http_probe.py`**

Add to `_default_settings()`:

```python
mhp.RECOVERY_URL_KEY: "",
mhp.RECOVERY_TOKEN_KEY: "",
```

Add the following test class after `TestMcpHttpProbe`:

```python
class TestMcpHttpProbeHttpRecovery:
    """HTTP recovery path (recovery_fn injection, no subprocess)."""

    @pytest.mark.asyncio
    async def test_http_recovery_invoked_on_failure(self):
        pool = _make_pool(
            setting_values={
                mhp.RECOVERY_URL_KEY: "http://host.docker.internal:9841/recover",
                mhp.RECOVERY_TOKEN_KEY: "tok",
            }
        )
        recovery_calls: list[tuple[str, str]] = []

        async def fake_recovery(url: str, token: str) -> tuple[bool, str]:
            recovery_calls.append((url, token))
            return True, "recovery dispatched"

        result = await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(status_code=503),
            recovery_fn=fake_recovery,
            now_fn=lambda: 1000.0,
        )
        assert result["ok"] is False
        assert recovery_calls == [
            ("http://host.docker.internal:9841/recover", "tok")
        ]
        assert "recovery dispatched" in result.get("recovery_detail", "")

    @pytest.mark.asyncio
    async def test_http_recovery_not_invoked_when_url_empty(self):
        """Empty recovery_url → no attempt, no recovery_detail."""
        pool = _make_pool(
            setting_values={
                mhp.RECOVERY_URL_KEY: "",
                mhp.RECOVERY_TOKEN_KEY: "tok",
            }
        )
        recovery_calls: list = []

        async def fake_recovery(url: str, token: str) -> tuple[bool, str]:
            recovery_calls.append((url, token))
            return True, "should not be called"

        result = await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(status_code=503),
            recovery_fn=fake_recovery,
            now_fn=lambda: 1000.0,
        )
        assert recovery_calls == []
        assert "recovery_detail" not in result

    @pytest.mark.asyncio
    async def test_http_recovery_restart_cap_enforced(self):
        """HTTP recovery obeys the same restart_cap/window as the subprocess path."""
        pool = _make_pool(
            setting_values={
                mhp.RECOVERY_URL_KEY: "http://host.docker.internal:9841/recover",
                mhp.RECOVERY_TOKEN_KEY: "tok",
                mhp.RESTART_CAP_KEY: "2",
                mhp.RESTART_WINDOW_MINUTES_KEY: "60",
            }
        )
        recovery_calls: list = []

        async def fake_recovery(url: str, token: str) -> tuple[bool, str]:
            recovery_calls.append((url, token))
            return True, "dispatched"

        clock = [1000.0]
        factory = _make_http_factory(status_code=503)
        for offset in (0, 6 * 60, 12 * 60, 18 * 60):
            clock[0] = 1000.0 + offset
            await mhp.run_mcp_http_probe(
                pool,
                http_client_factory=factory,
                recovery_fn=fake_recovery,
                now_fn=lambda: clock[0],
            )
        # Cap=2 → only first 2 cycles invoke recovery.
        assert len(recovery_calls) == 2

    @pytest.mark.asyncio
    async def test_launcher_takes_priority_over_http_recovery(self):
        """If launcher_path is set, it is preferred over recovery_url
        (host-process deployments shouldn't change behaviour)."""
        pool = _make_pool(
            setting_values={
                mhp.LAUNCHER_PATH_KEY: "C:\\fake\\launcher.cmd",
                mhp.RECOVERY_URL_KEY: "http://host.docker.internal:9841/recover",
                mhp.RECOVERY_TOKEN_KEY: "tok",
            }
        )
        launcher_calls: list = []
        recovery_calls: list = []

        def fake_launcher(path: str) -> tuple[bool, str]:
            launcher_calls.append(path)
            return True, f"dispatched {path}"

        async def fake_recovery(url: str, token: str) -> tuple[bool, str]:
            recovery_calls.append((url, token))
            return True, "should not be called"

        await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(status_code=503),
            launcher_fn=fake_launcher,
            recovery_fn=fake_recovery,
            now_fn=lambda: 1000.0,
        )
        assert launcher_calls == ["C:\\fake\\launcher.cmd"]
        assert recovery_calls == []
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```bash
cd src/cofounder_agent
poetry run pytest tests/unit/brain/test_mcp_http_probe.py::TestMcpHttpProbeHttpRecovery -v
```

Expected: 4 `ERRORS` — `AttributeError: module 'brain.mcp_http_probe' has no attribute 'RECOVERY_URL_KEY'`

### Step 5b — Implement

- [ ] **Step 3: Update `brain/mcp_http_probe.py`**

**Add constants** (after `RESTART_WINDOW_MINUTES_KEY`):

```python
RECOVERY_URL_KEY = "mcp_http_probe_recovery_url"
RECOVERY_TOKEN_KEY = "mcp_http_probe_recovery_token"
```

**Add `_try_http_recovery` function** (after `_try_launcher`):

```python
async def _try_http_recovery(url: str, token: str) -> tuple[bool, str]:
    """POST to the host-side recovery agent to restart the MCP HTTP server.

    This is the container-safe recovery path — the recovery agent runs on
    the Windows host and is reachable via host.docker.internal even from
    inside a Docker container, unlike the ``_try_launcher`` subprocess path
    which requires the brain to run directly on the host OS.
    """
    if not url or not token:
        return False, "recovery_url or recovery_token not configured"

    if httpx is None:
        return False, "httpx not available"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                url,
                json={"service": "mcp-http"},
                headers={"Authorization": f"Bearer {token}"},
            )
            if 200 <= response.status_code < 300:
                return True, f"recovery agent responded HTTP {response.status_code}"
            return False, f"recovery agent returned HTTP {response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"recovery request failed: {type(exc).__name__}: {exc}"
```

**Update `run_mcp_http_probe` signature** — add `recovery_fn` param:

```python
async def run_mcp_http_probe(
    pool,
    *,
    now_fn: Callable[[], float] | None = None,
    http_client_factory: Callable[..., Any] | None = None,
    launcher_fn: Callable[[str], tuple[bool, str]] | None = None,
    recovery_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
```

And thread it through to `_handle_failure` — find the two calls to `_handle_failure` in `run_mcp_http_probe` and add `recovery_fn=recovery_fn` to each:

```python
    # network error path:
    return await _handle_failure(
        pool,
        now=now,
        url=probe_url,
        fingerprint_suffix="network",
        title="MCP HTTP server unreachable",
        body=f"GET {probe_url} raised {type(exc).__name__}: {exc}",
        launcher_fn=launcher_fn,
        recovery_fn=recovery_fn,
    )

    # non-2xx path:
    return await _handle_failure(
        pool,
        now=now,
        url=probe_url,
        fingerprint_suffix=f"http_{status_code}",
        title=f"MCP HTTP server returned HTTP {status_code}",
        body=f"GET {probe_url} returned HTTP {status_code}",
        status_code=status_code,
        launcher_fn=launcher_fn,
        recovery_fn=recovery_fn,
    )
```

**Update `_handle_failure` signature** — add `recovery_fn` param:

```python
async def _handle_failure(
    pool,
    *,
    now: float,
    url: str,
    fingerprint_suffix: str,
    title: str,
    body: str,
    status_code: int | None = None,
    launcher_fn: Callable[[str], tuple[bool, str]] | None = None,
    recovery_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
```

**Replace the auto-recovery block** in `_handle_failure` (the section starting with `# Auto-recovery — bounded by the rolling restart cap`). Replace the entire block with:

```python
    # Auto-recovery — bounded by the rolling restart cap so we don't
    # busy-loop the launcher when the underlying problem is persistent.
    #
    # Priority: subprocess launcher (host-process deployments) >
    #           HTTP recovery agent (containerised brain deployments).
    launcher_path = (await _read_app_setting(pool, LAUNCHER_PATH_KEY, "")).strip()
    recovery_url = (await _read_app_setting(pool, RECOVERY_URL_KEY, "")).strip()
    recovery_token = (await _read_app_setting(pool, RECOVERY_TOKEN_KEY, "")).strip()

    recovery_detail = ""
    if launcher_path or recovery_url:
        restart_cap = await _read_int(pool, RESTART_CAP_KEY, DEFAULT_RESTART_CAP)
        window_min = await _read_int(pool, RESTART_WINDOW_MINUTES_KEY, DEFAULT_RESTART_WINDOW_MINUTES)
        window_s = max(1, window_min) * 60
        cutoff = now - window_s
        _restart_attempts[:] = [t for t in _restart_attempts if t >= cutoff]
        if len(_restart_attempts) < max(1, restart_cap):
            if launcher_path:
                ok, detail = (launcher_fn or _try_launcher)(launcher_path)
            else:
                ok, detail = await (recovery_fn or _try_http_recovery)(
                    recovery_url, recovery_token,
                )
            recovery_detail = detail
            if ok:
                _restart_attempts.append(now)
                logger.info("[MCP_HTTP_PROBE] auto-recovery: %s", detail)
            else:
                logger.warning("[MCP_HTTP_PROBE] auto-recovery skipped: %s", detail)
        else:
            recovery_detail = (
                f"restart cap reached ({len(_restart_attempts)}/{restart_cap} in "
                f"{window_min}m)"
            )
            logger.warning("[MCP_HTTP_PROBE] %s", recovery_detail)
```

- [ ] **Step 4: Run all probe tests**

```bash
cd src/cofounder_agent
poetry run pytest tests/unit/brain/test_mcp_http_probe.py -v
```

Expected: All green. Confirm the four new `TestMcpHttpProbeHttpRecovery` tests pass and the existing `TestMcpHttpProbe` tests are unchanged.

- [ ] **Step 5: Commit**

```bash
git add brain/mcp_http_probe.py \
        src/cofounder_agent/tests/unit/brain/test_mcp_http_probe.py
git commit -m "feat(brain): add HTTP recovery path to mcp_http_probe

The brain daemon runs in a Docker container. The previous subprocess
launcher path (mcp_http_probe_launcher_path) targeted a Windows host
.cmd file invisible to the container — auto-recovery was silently a
no-op since containerisation.

Add RECOVERY_URL_KEY / RECOVERY_TOKEN_KEY + _try_http_recovery(): the
probe POSTs {service: 'mcp-http'} to a host-side recovery agent
(port 9841) reachable via host.docker.internal. The subprocess launcher
path is retained for host-process deployments; if both are configured,
launcher_path takes priority.

recovery_fn injection mirrors launcher_fn for unit-testability.
Restart cap and window apply to both paths identically."
```

---

## Task 6: Add app_settings defaults and migration

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py`
- Create: migration (generate filename with the script)

### Step 6a — settings_defaults.py

- [ ] **Step 1: Add `mcp_http_probe_recovery_url` to DEFAULTS**

In `settings_defaults.py`, find the block that contains other `mcp_http_probe_*` keys. If none exist in DEFAULTS, add the new key near the end of the dict before the closing `}`:

```python
    # ----- MCP HTTP probe recovery (brain/mcp_http_probe.py) -----
    # Empty = HTTP recovery disabled. Set to http://host.docker.internal:9841/recover
    # once the Recovery Agent Task Scheduler task is running on the host.
    'mcp_http_probe_recovery_url': '',
```

`mcp_http_probe_recovery_token` is a secret — it goes in the migration (next step), NOT here.

### Step 6b — migration for the secret token row

- [ ] **Step 2: Generate the migration file**

```bash
cd C:/Users/mattm/glad-labs-website
python scripts/new-migration.py "seed mcp http probe recovery token secret"
```

Note the generated filename (e.g., `20260608_HHMMSS_seed_mcp_http_probe_recovery_token_secret.py`).

- [ ] **Step 3: Write the migration body**

Open the generated file and replace its body (after the docstring and imports) with:

```python
"""Seed the ``mcp_http_probe_recovery_token`` secret app_settings row.

This is the shared Bearer token used by:
- The brain probe (brain/mcp_http_probe.py) when POSTing to the
  host-side recovery agent at mcp_http_probe_recovery_url.
- The recovery agent (~/.poindexter/scripts/recovery-agent.py) when
  verifying inbound requests.

Seeds an EMPTY value (the sentinel for "unset") with is_secret=TRUE
so the auto-encrypt trigger treats it correctly. The operator sets the
actual value via:

    python -c "import secrets; print(secrets.token_hex(32))"
    # Then in psql or docker exec:
    UPDATE app_settings SET value='<token>' WHERE key='mcp_http_probe_recovery_token';
"""
from __future__ import annotations


async def upgrade(pool) -> None:
    await pool.execute(
        """
        INSERT INTO app_settings (key, value, category, description, is_secret, is_active, updated_at)
        VALUES (
            'mcp_http_probe_recovery_token',
            '',
            'integrations',
            'Bearer token shared between brain probe and host recovery agent (port 9841). '
            'Set to output of: python -c "import secrets; print(secrets.token_hex(32))"',
            TRUE,
            TRUE,
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
        """,
    )


async def downgrade(pool) -> None:
    await pool.execute(
        "DELETE FROM app_settings WHERE key = 'mcp_http_probe_recovery_token'",
    )
```

- [ ] **Step 4: Smoke-test the migration**

```bash
cd src/cofounder_agent
python scripts/ci/migrations_smoke.py
```

Expected: passes with no errors.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py \
        src/cofounder_agent/services/migrations/<generated-filename>.py
git commit -m "feat: seed mcp_http_probe_recovery_url default + recovery_token secret row"
```

---

## Task 7: Configure and activate in production

- [ ] **Step 1: Apply the migration**

```bash
docker exec poindexter-worker python -m scripts.migrate
```

Expected: migration runs and the `mcp_http_probe_recovery_token` row appears in `app_settings`.

- [ ] **Step 2: Set the token value in the DB**

```powershell
$token = python -c "import secrets; print(secrets.token_hex(32))"
Write-Host "Save this token to bootstrap.toml AND the DB: $token"

docker exec poindexter-worker python3 -c "
import asyncio, asyncpg, os

async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    await conn.execute(
        \"UPDATE app_settings SET value = \$1 WHERE key = 'mcp_http_probe_recovery_token'\",
        '$token',
    )
    print('Token set.')
    await conn.close()

asyncio.run(main())
"
```

Also add to `~/.poindexter/bootstrap.toml`:

```toml
poindexter_recovery_token = "<same token>"
```

- [ ] **Step 3: Restart the recovery agent to pick up the new token from bootstrap.toml**

```powershell
Stop-ScheduledTask -TaskName "Poindexter Recovery Agent" -ErrorAction SilentlyContinue
Start-Sleep 2
Start-ScheduledTask -TaskName "Poindexter Recovery Agent"
Start-Sleep 3
Get-NetTCPConnection -LocalPort 9841 | Select-Object State
# Expected: Listen
```

- [ ] **Step 4: Set `mcp_http_probe_recovery_url` in app_settings**

```bash
docker exec poindexter-worker python3 -c "
import asyncio, asyncpg, os

async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    await conn.execute(
        \"UPDATE app_settings SET value = 'http://host.docker.internal:9841/recover' WHERE key = 'mcp_http_probe_recovery_url'\",
    )
    print('Recovery URL set.')
    await conn.close()

asyncio.run(main())
"
```

---

## Task 8: End-to-end verification

- [ ] **Step 1: Stop the MCP HTTP server**

```powershell
Stop-ScheduledTask -TaskName "Poindexter MCP HTTP"
Start-Sleep 2
Get-NetTCPConnection -LocalPort 8004 -ErrorAction SilentlyContinue
# Expected: no output (port not listening)
```

- [ ] **Step 2: Trigger a probe cycle via the brain daemon and confirm recovery fires**

Wait up to 5 minutes for the brain probe cycle, then check:

```bash
docker logs poindexter-brain-daemon --tail 20 2>&1 | grep -i "mcp\|recovery"
```

Expected log lines:

```
[MCP_HTTP_PROBE] http://host.docker.internal:8004/healthz raised ConnectError: ...
[MCP_HTTP_PROBE] auto-recovery: recovery agent responded HTTP 200
```

- [ ] **Step 3: Confirm the MCP HTTP server came back**

```powershell
Start-Sleep 5
Get-NetTCPConnection -LocalPort 8004 | Select-Object State
# Expected: Listen
```

```bash
docker logs poindexter-brain-daemon --tail 5 2>&1 | grep "mcp_http"
# Expected: [MCP_HTTP_PROBE] http://host.docker.internal:8004/healthz ok (HTTP 200)
```

- [ ] **Step 4: Confirm the alert clears within the next 5-minute cycle**

```bash
docker exec poindexter-worker python3 -c "
import asyncio, asyncpg, os

async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    rows = await conn.fetch(
        \"SELECT created_at, alertname, fingerprint FROM alert_events \
         WHERE alertname = 'mcp_http_server_unreachable' \
         ORDER BY created_at DESC LIMIT 3\"
    )
    for r in rows:
        print(r)
    await conn.close()

asyncio.run(main())
"
# Expected: no new rows after the recovery succeeded
```

---

## Self-Review Checklist

- [x] Recovery agent: starts, serves `/healthz`, verifies Bearer token, invokes `Start-ScheduledTask`
- [x] Probe: `recovery_fn` injection for tests, restart cap applies to HTTP path, launcher_path takes priority
- [x] Tests: 4 new tests covering invocation, empty URL guard, restart cap, launcher priority
- [x] settings_defaults: non-secret URL key seeded as empty string
- [x] Migration: secret token row with `is_secret=TRUE`
- [x] End-to-end: stop MCP → brain fires → recovery agent restarts → probe clears
- [x] No placeholder text in any step
- [x] Type names consistent: `RECOVERY_URL_KEY`, `RECOVERY_TOKEN_KEY`, `recovery_fn`, `_try_http_recovery`
