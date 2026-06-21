# Postgres Host-Port-Wedge Self-Heal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the brain's existing `docker_port_forward_probe` to detect and auto-recover the WSL2 NAT host-port-proxy wedge on Postgres (port 5433), so it self-heals like the HTTP sidecars already do.

**Architecture:** Extend `brain/docker_port_forward_probe.py` with a credential-free Postgres `SSLRequest` reachability check and a `probe_type` dispatch (`"http"` default | `"postgres"`). Add a Postgres entry to the seeded watch list. All existing detect → restart → cap → audit → alert logic is reused unchanged.

**Tech Stack:** Python 3.13, stdlib `socket`/`struct` (no new deps — brain is stdlib + asyncpg only), pytest.

## Global Constraints

- Brain modules use **stdlib + asyncpg only** — no new dependencies. `_pg_probe` is pure `socket`/`struct`.
- **Backward compatible:** the 12 existing HTTP watch entries keep working with no edit; `probe_type` defaults to `"http"`.
- **Seeds live in `0000_baseline.seeds.sql`**, never a new migration file.
- **No schema change, no new app_settings key** — reuse the existing 3/60min restart cap and all current settings.
- Detection requires **internal-OK + external-FAIL** before any restart (never restarts a genuinely-down DB).
- All changes via PR; TDD; frequent commits.

---

### Task 1: `_pg_probe` — credential-free Postgres reachability check

**Files:**

- Modify: `brain/docker_port_forward_probe.py` (add stdlib imports + `_pg_probe`)
- Test: `src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py`

**Interfaces:**

- Produces: `_pg_probe(host: str, port: int, timeout_seconds: float) -> bool` and module constant `_PG_SSL_REQUEST: bytes`.

- [ ] **Step 1: Write the failing tests**

Append to the test file:

```python
# ---------------------------------------------------------------------------
# Postgres reachability probe (_pg_probe) — SSLRequest framing
# ---------------------------------------------------------------------------


class _FakeSock:
    """Minimal context-manager socket stub for _pg_probe tests."""

    def __init__(self, reply: bytes = b"", *, raise_on: str | None = None):
        self._reply = reply
        self._raise_on = raise_on
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def settimeout(self, _t):
        pass

    def sendall(self, data):
        if self._raise_on == "sendall":
            raise ConnectionResetError("reset on send")
        self.sent += data

    def recv(self, _n):
        if self._raise_on == "recv":
            raise ConnectionResetError("reset on recv")
        return self._reply


@pytest.mark.unit
class TestPgProbe:
    def test_sends_ssl_request_and_true_on_S(self, monkeypatch):
        sock = _FakeSock(reply=b"S")
        monkeypatch.setattr(
            pf.socket, "create_connection", lambda addr, timeout: sock
        )
        assert pf._pg_probe("postgres-local", 5432, 3.0) is True
        # Mirrors asyncpg's first wire step exactly.
        assert sock.sent == pf._PG_SSL_REQUEST

    def test_true_on_N(self, monkeypatch):
        monkeypatch.setattr(
            pf.socket, "create_connection",
            lambda addr, timeout: _FakeSock(reply=b"N"),
        )
        assert pf._pg_probe("host.docker.internal", 5433, 3.0) is True

    def test_false_on_empty_reply_wedge(self, monkeypatch):
        # The wedge: TCP accepted, then dropped on first byte → empty recv.
        monkeypatch.setattr(
            pf.socket, "create_connection",
            lambda addr, timeout: _FakeSock(reply=b""),
        )
        assert pf._pg_probe("host.docker.internal", 5433, 3.0) is False

    def test_false_on_reset(self, monkeypatch):
        monkeypatch.setattr(
            pf.socket, "create_connection",
            lambda addr, timeout: _FakeSock(raise_on="recv"),
        )
        assert pf._pg_probe("host.docker.internal", 5433, 3.0) is False

    def test_false_on_connect_error(self, monkeypatch):
        def _boom(addr, timeout):
            raise OSError("connection refused")
        monkeypatch.setattr(pf.socket, "create_connection", _boom)
        assert pf._pg_probe("nope", 5432, 3.0) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry -C src/cofounder_agent run pytest src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py::TestPgProbe -v`
Expected: FAIL — `module 'brain.docker_port_forward_probe' has no attribute 'socket'` / `_pg_probe`.

- [ ] **Step 3: Implement `_pg_probe`**

In `brain/docker_port_forward_probe.py`, add to the stdlib imports near the top (after `import os`):

```python
import socket
import struct
```

Then add this helper just above the `_http_probe` definition:

```python
# ---------------------------------------------------------------------------
# Postgres reachability probe — for non-HTTP watch-list entries
# (probe_type="postgres"). Detects the WSL2 NAT host-port wedge that drops
# the connection mid-handshake (the failure that surfaces as asyncpg
# "unexpected connection_lost() call"). Credential-free: we send the libpq
# SSLRequest and read the single negotiation byte, exactly as asyncpg's
# first wire step does, then close. A healthy Postgres answers 'S'/'N'; a
# wedged proxy accepts the TCP connection then drops it (empty recv /
# reset). Stdlib-only, matching the probe's "reachability, not auth"
# philosophy.
# ---------------------------------------------------------------------------

_PG_SSL_REQUEST = struct.pack("!ii", 8, 80877103)  # length=8, code=80877103


def _pg_probe(host: str, port: int, timeout_seconds: float) -> bool:
    """True iff Postgres answers the SSLRequest handshake within timeout."""
    try:
        with socket.create_connection(
            (host, port), timeout=timeout_seconds
        ) as sock:
            sock.settimeout(timeout_seconds)
            sock.sendall(_PG_SSL_REQUEST)
            reply = sock.recv(1)
            return reply in (b"S", b"N")
    except Exception as exc:  # noqa: BLE001 — any failure = not reachable
        logger.debug("[PORT_FORWARD] pg_probe %s:%s failed: %s", host, port, exc)
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry -C src/cofounder_agent run pytest src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py::TestPgProbe -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add brain/docker_port_forward_probe.py src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py
git commit -m "feat(brain): add credential-free Postgres SSLRequest reachability probe"
```

---

### Task 2: `probe_type` parsing in `_parse_watch_list`

**Files:**

- Modify: `brain/docker_port_forward_probe.py` (`_parse_watch_list`)
- Test: `src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py`

**Interfaces:**

- Produces: every parsed watch entry dict now carries `"probe_type": "http" | "postgres"`.

- [ ] **Step 1: Update the existing exact-equality test + add new tests**

In `TestWatchListParsing`, the existing `test_strips_poindexter_prefix_for_internal_hostname` asserts an exact dict — add `"probe_type": "http"`:

```python
    def test_strips_poindexter_prefix_for_internal_hostname(self):
        out = pf._parse_watch_list(json.dumps([
            {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
        ]))
        assert out == [{
            "container": "poindexter-pyroscope",
            "port": 4040,
            "host_port": 4040,
            "path": "/",
            "internal_hostname": "pyroscope",
            "probe_type": "http",
        }]
```

Then add new cases to `TestWatchListParsing`:

```python
    def test_probe_type_defaults_to_http(self):
        out = pf._parse_watch_list(json.dumps([
            {"container": "poindexter-grafana", "port": 3000, "path": "/"},
        ]))
        assert out[0]["probe_type"] == "http"

    def test_probe_type_postgres_preserved(self):
        out = pf._parse_watch_list(json.dumps([
            {
                "container": "poindexter-postgres-local",
                "internal_hostname": "postgres-local",
                "port": 5432,
                "host_port": 5433,
                "probe_type": "postgres",
            },
        ]))
        assert out[0]["probe_type"] == "postgres"
        assert out[0]["host_port"] == 5433

    def test_unknown_probe_type_falls_back_to_http(self):
        out = pf._parse_watch_list(json.dumps([
            {"container": "poindexter-x", "port": 1, "probe_type": "carrier-pigeon"},
        ]))
        assert out[0]["probe_type"] == "http"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry -C src/cofounder_agent run pytest src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py::TestWatchListParsing -v`
Expected: FAIL — parsed dict has no `probe_type` key.

- [ ] **Step 3: Implement `probe_type` parsing**

In `_parse_watch_list`, just before `out.append({...})`, add:

```python
        probe_type = str(entry.get("probe_type") or "http").strip().lower()
        if probe_type not in ("http", "postgres"):
            logger.warning(
                "[PORT_FORWARD] %s entry has unknown probe_type=%r — "
                "defaulting to 'http'", container, entry.get("probe_type"),
            )
            probe_type = "http"
```

and add `"probe_type": probe_type,` to the appended dict.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry -C src/cofounder_agent run pytest src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py::TestWatchListParsing -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add brain/docker_port_forward_probe.py src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py
git commit -m "feat(brain): parse optional probe_type in port-forward watch list"
```

---

### Task 3: Dispatch on `probe_type` + thread `pg_probe_fn`

**Files:**

- Modify: `brain/docker_port_forward_probe.py` (`_check_one_service`, `run_docker_port_forward_probe`)
- Test: `src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py`

**Interfaces:**

- Consumes: `_pg_probe` (Task 1), `probe_type` (Task 2).
- Produces: `run_docker_port_forward_probe(..., pg_probe_fn=None)` and `_check_one_service(..., pg_probe_fn=...)`; postgres entries set `internal_url`/`external_url` to `postgres://host:port` descriptors.

- [ ] **Step 1: Write the failing tests**

Append to the test file:

```python
# ---------------------------------------------------------------------------
# Postgres watch entry — wedge detection + recovery via pg_probe_fn
# ---------------------------------------------------------------------------


_PG_ENTRY = {
    "container": "poindexter-postgres-local",
    "internal_hostname": "postgres-local",
    "port": 5432,
    "host_port": 5433,
    "probe_type": "postgres",
}


@pytest.mark.unit
class TestPostgresWatchEntry:
    @pytest.mark.asyncio
    async def test_pg_wedge_internal_ok_external_fail_restarts(self):
        pool = _make_pool(setting_values={pf.WATCH_LIST_KEY: json.dumps([_PG_ENTRY])})
        external = iter([False, True])  # fail, then recover post-restart

        def fake_pg(host, _port, _timeout):
            if host == "host.docker.internal":
                return next(external)
            return True  # internal always ok

        restart_calls: list[str] = []

        summary = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=lambda u, t: (_ for _ in ()).throw(
                AssertionError("http probe must not run for postgres entry")
            ),
            pg_probe_fn=fake_pg,
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (restart_calls.append(c) or (True, "ok")),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        svc = summary["services"]["poindexter-postgres-local"]
        assert svc["status"] == "recovered"
        assert restart_calls == ["poindexter-postgres-local"]
        assert "docker_port_forward_recovered" in _executed_audit_events(pool)

    @pytest.mark.asyncio
    async def test_pg_both_down_does_not_restart(self):
        pool = _make_pool(setting_values={pf.WATCH_LIST_KEY: json.dumps([_PG_ENTRY])})
        restart_calls: list[str] = []

        summary = await pf.run_docker_port_forward_probe(
            pool,
            pg_probe_fn=lambda h, p, t: False,  # both down
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (restart_calls.append(c) or (True, "ok")),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        assert summary["services"]["poindexter-postgres-local"]["status"] == "service_down"
        assert restart_calls == []

    @pytest.mark.asyncio
    async def test_pg_both_ok_no_restart(self):
        pool = _make_pool(setting_values={pf.WATCH_LIST_KEY: json.dumps([_PG_ENTRY])})
        restart_calls: list[str] = []

        summary = await pf.run_docker_port_forward_probe(
            pool,
            pg_probe_fn=lambda h, p, t: True,
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (restart_calls.append(c) or (True, "ok")),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        svc = summary["services"]["poindexter-postgres-local"]
        assert svc["status"] == "ok"
        assert svc["external_url"] == "postgres://host.docker.internal:5433"
        assert restart_calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry -C src/cofounder_agent run pytest src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py::TestPostgresWatchEntry -v`
Expected: FAIL — `run_docker_port_forward_probe() got an unexpected keyword argument 'pg_probe_fn'`.

- [ ] **Step 3: Implement dispatch + injection**

In `run_docker_port_forward_probe`, add the parameter and default (next to `http_probe_fn`):

```python
    pg_probe_fn: Callable[[str, int, float], bool] | None = None,
```

and in the body where defaults are filled:

```python
    pg_probe_fn = pg_probe_fn or _pg_probe
```

Pass it into the `_check_one_service(...)` call:

```python
                pg_probe_fn=pg_probe_fn,
```

In `_check_one_service`, add the parameter to the signature:

```python
    pg_probe_fn: Callable[[str, int, float], bool],
```

Then replace the URL-build + probe block (currently builds `internal_url`/`external_url` and calls `http_probe_fn` twice) with:

```python
    host_port = service.get("host_port", port)
    probe_type = service.get("probe_type", "http")
    if probe_type == "postgres":
        internal_url = f"postgres://{internal_hostname}:{port}"
        external_url = f"postgres://host.docker.internal:{host_port}"
        ok_internal = pg_probe_fn(internal_hostname, port, timeout)
        ok_external = pg_probe_fn("host.docker.internal", host_port, timeout)
    else:
        internal_url = f"http://{internal_hostname}:{port}{path}"
        external_url = f"http://host.docker.internal:{host_port}{path}"
        ok_internal = http_probe_fn(internal_url, timeout)
        ok_external = http_probe_fn(external_url, timeout)
```

Everything downstream (`ok_internal`/`ok_external`/`internal_url`/`external_url`) is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry -C src/cofounder_agent run pytest src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py::TestPostgresWatchEntry -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add brain/docker_port_forward_probe.py src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py
git commit -m "feat(brain): dispatch postgres watch entries to the pg reachability probe"
```

---

### Task 4: Seed the Postgres watch entry + update docs

**Files:**

- Modify: `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql:153`
- Modify: `docs/operations/self-healing.md`
- Modify: `docs/reference/app-settings.md`

- [ ] **Step 1: Append the Postgres entry to the seeded watch list**

In `0000_baseline.seeds.sql`, the `docker_port_forward_watch_list` value is a JSON array. Add this object immediately before the closing `]'` of that array (after the `poindexter-pyroscope-grafana-frontend` entry):

```
, {"container": "poindexter-postgres-local", "internal_hostname": "postgres-local", "port": 5432, "host_port": 5433, "probe_type": "postgres"}
```

- [ ] **Step 2: Update `docs/operations/self-healing.md`**

Add a sentence to the docker-port-forward section noting that the probe now also covers the Postgres host-port wedge (`probe_type=postgres`, credential-free SSLRequest check) and auto-recovers it via `docker restart poindexter-postgres-local`, replacing the manual lever.

- [ ] **Step 3: Update `docs/reference/app-settings.md`**

Document the optional `probe_type` field on `docker_port_forward_watch_list` entries (`"http"` default, `"postgres"` for the DB) and note the seeded Postgres entry.

- [ ] **Step 4: Verify seed JSON parses**

Run: `poetry -C src/cofounder_agent run python -c "import re,json,pathlib; s=pathlib.Path('src/cofounder_agent/services/migrations/0000_baseline.seeds.sql').read_text(); m=re.search(r\"docker_port_forward_watch_list', '(\[.*?\])'\", s); arr=json.loads(m.group(1)); assert any(e.get('probe_type')=='postgres' and e['container']=='poindexter-postgres-local' for e in arr), arr; print('OK', len(arr), 'entries')"`
Expected: `OK 13 entries`

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/migrations/0000_baseline.seeds.sql docs/operations/self-healing.md docs/reference/app-settings.md
git commit -m "feat(brain): seed postgres host-port watch entry + docs"
```

---

### Task 5: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full probe test file**

Run: `poetry -C src/cofounder_agent run pytest src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py -v`
Expected: ALL PASS (existing scenarios + new TestPgProbe/TestPostgresWatchEntry + updated parsing tests).

- [ ] **Step 2: Lint the changed brain module**

Run: `poetry -C src/cofounder_agent run ruff check brain/docker_port_forward_probe.py`
Expected: no errors (the broad-except carries `# noqa: BLE001`).

- [ ] **Step 3: Final state check**

Run: `git -C . log --oneline -6`
Expected: the four feature commits + the design-doc commit present.

---

## Self-Review

- **Spec coverage:** `_pg_probe` (Task 1) ✓; `probe_type` dispatch (Tasks 2–3) ✓; Postgres watch entry seed (Task 4) ✓; reuse of cap/audit/alerts (Task 3, unchanged downstream) ✓; docs (Task 4) ✓; tests incl. both-down guard + framing (Tasks 1–3) ✓; recovery_wait already 45s (no change) ✓.
- **Placeholder scan:** none — every step has concrete code/commands.
- **Type consistency:** `_pg_probe(host, port, timeout) -> bool` and `pg_probe_fn` signature match across Tasks 1/3; `probe_type` string values (`"http"`/`"postgres"`) consistent across Tasks 2–4.
- **Live-prod rollout** (`poindexter settings set` of the watch list, after the brain image redeploys) is operational, tracked in the spec's Rollout section — out of band from these code tasks.
