# Host-Routed Compose-Drift Self-Heal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make compose drift _self-heal_ end-to-end on the Windows host, not just page — by routing the recovery action through the existing host Recovery Agent (which can run `start-stack` correctly), since the Linux brain cannot `docker compose up` Windows `C:\` binds.

**Architecture:** The brain's `compose_drift_probe` already _detects_ drift (it reads the auto-synced clone spec via the `/host-deploy` mount and compares running containers). Today, on drift it only pages — the brain-side auto-recover (`compose_drift_auto_recover_enabled`) is OFF and must stay OFF, because a Linux container running `docker compose up` mangles Windows binds to `/app/C:\...`. The fix is the **detector/actor split**: the brain POSTs `{"service":"compose-reapply"}` to the host **Recovery Agent** (port 9841, already live — it recovers `mcp-http` today), and the agent runs `start-stack.sh up -d --no-build` from the clone _on the host_, where compose resolves the binds correctly. Bounded by a restart cap so a persistent drift can't storm-reapply.

**Tech Stack:** Python stdlib (`http.server`) for the agent; `brain/compose_drift_probe.py` (asyncpg + stdlib); `app_settings` for tunables; the existing `poindexter_recovery_token` Bearer auth; Git Bash to invoke `start-stack.sh` on the host.

## Global Constraints

- `compose_drift_auto_recover_enabled` (the brain's _own_ `docker compose up`) MUST stay **false** — the Linux brain can't deploy Windows binds. This PR does NOT touch that; it adds a _separate_ host-routed path.
- Public-mirror safe: no operator identity (`gladlabs.io`, "Matt", `C:\Users\mattm`, `glad-labs-website` dir name) in any repo file. Genericize the `.cmd` (`%USERPROFILE%` + relative paths only).
- Every tunable is an `app_settings` row (DB-first config).
- All changes via PR; flag this PR for explicit review (deploy-infra + self-heal). Do not auto-merge.
- Brain is image-baked → deploy = `docker compose build brain-daemon` + recreate. Recovery agent is host-local → deploy = copy to `~/.poindexter/scripts/` + restart its Task.

---

## File Map

| File                                                               | Action                                           | Purpose                                                                                 |
| ------------------------------------------------------------------ | ------------------------------------------------ | --------------------------------------------------------------------------------------- |
| `scripts/recovery-agent.py`                                        | Create (import from `~/.poindexter/scripts/`)    | Canonical, version-controlled copy of the host agent; generalize to action-kinds        |
| `scripts/recovery-agent.cmd`                                       | Create (genericized)                             | Windowless launcher (no hardcoded operator dir)                                         |
| `src/cofounder_agent/tests/unit/test_recovery_agent.py`            | Create                                           | Unit tests: auth, healthz, task-restart, compose-reapply, unknown-service               |
| `brain/compose_drift_probe.py`                                     | Modify                                           | Add host-recover path: on drift + flag + under cap → POST `/recover`                    |
| `src/cofounder_agent/tests/unit/brain/test_compose_drift_probe.py` | Modify                                           | Tests for the host-recover trigger + cap + flag-off                                     |
| `src/cofounder_agent/services/settings_defaults.py`                | Modify                                           | `compose_drift_host_recover_enabled` (true), `compose_drift_recovery_url`, cap settings |
| `brain/Dockerfile`                                                 | Modify (if compose_drift gains a new module dep) | — likely none; compose_drift_probe already baked                                        |
| `docs/operations/self-healing.md`                                  | Create                                           | Document the detect→host-recover loop + the agent's action model (PR4 may fold this in) |

---

## Task 1: Bring the Recovery Agent into the repo (no behavior change)

The agent currently lives ONLY at `~/.poindexter/scripts/recovery-agent.py` — not version-controlled, not tested, not synced. Establish the canonical repo copy first.

- [ ] Copy the live `recovery-agent.py` to `scripts/recovery-agent.py` verbatim (it's already generic — `Path.home()`, env vars, stdlib).
- [ ] Create `scripts/recovery-agent.cmd` genericized: drop the `cd /d "%USERPROFILE%\glad-labs-website\mcp-server"` (vestigial — the agent is stdlib-only), keep the `%USERPROFILE%` log path + `python recovery-agent.py`.
- [ ] Add `test_recovery_agent.py`: token-required (401 no/wrong token), `/healthz` 200, known-service task-restart (mock `subprocess.run`), unknown-service 400, bad-JSON 400. Inject the subprocess seam.
- [ ] Verify tests pass.

## Task 2: Generalize the agent to action-kinds

- [ ] Change `SERVICES` from `name → task_name` to `name → {"kind": "task"|"compose", ...}`:
  - `kind="task"` → `Start-ScheduledTask` (existing `mcp-http` behavior, unchanged).
  - `kind="compose"` → run `start-stack.sh up -d --no-build` from the clone via **Git Bash** (resolve Git Bash like `docker-watchdog.ps1`'s `Resolve-GitBash`; never the PATH `bash` = WSL). Working dir = the clone (`POINDEXTER_DEPLOY_ROOT` ?? default clone path).
  - Add `"compose-reapply": {"kind": "compose"}`.
- [ ] Cap/log: the agent logs each action to `recovery-agent.log` (already does). The _cap_ lives on the brain side (Task 3) so the policy is one place.
- [ ] Tests for the compose action (mock the Git Bash subprocess; assert it shells `start-stack.sh up -d --no-build`).

## Task 3: Wire the brain compose_drift_probe to host-recover

- [ ] Add settings reads: `compose_drift_host_recover_enabled` (default **true**), `compose_drift_recovery_url` (default `http://host.docker.internal:9841/recover`), reuse the recovery token key. Cap: reuse/define `compose_drift_recover_cap_per_window` (default 3) + `_window_minutes` (default 60).
- [ ] On detected drift (the existing detection path), if enabled + under cap: `POST {"service":"compose-reapply"}` with Bearer token; record the attempt timestamp (module-level rolling window, like `docker_port_forward_probe`); audit_log the dispatch.
- [ ] Keep the existing paging/finding. The brain-side `compose_drift_auto_recover_enabled` path is untouched (stays the no-op-on-Windows it is).
- [ ] Tests: drift + enabled + under-cap → one POST (mock the HTTP fn); flag-off → no POST; over-cap → no POST + cap audit; POST failure → logged, not raised.

## Task 4: Settings + defaults

- [ ] `settings_defaults.py`: add the keys above (all non-secret; the token already exists as a secret row). Place near the existing `compose_*` / brain-probe settings.

## Task 5: Deploy + E2E verify (operator, post-merge)

- [ ] Copy `scripts/recovery-agent.py` + `.cmd` → `~/.poindexter/scripts/`; restart the "Poindexter Recovery Agent" Task; confirm `/healthz` 200.
- [ ] `docker compose build brain-daemon` + recreate (image-baked).
- [ ] Set `compose_drift_recovery_url` in app_settings; confirm `compose_drift_host_recover_enabled=true`.
- [ ] Induce a benign drift (e.g., add a label to a non-critical container out-of-band), wait a brain cycle, confirm: brain detects → POSTs → agent runs start-stack → drift clears. Check `recovery-agent.log` + brain logs + `audit_log`.
- [ ] Confirm the cap holds (a persistent drift stops re-POSTing after N).

---

## Design decision — RESOLVED

**`compose_drift_host_recover_enabled` defaults to `true`** (auto-heal on every detected drift, capped at 3/hour then escalate). Confirmed by the operator 2026-06-21 ("auto-heal on drift"). The cutover already removed the historical false-positive source (the brain now reads the _synced clone_ spec via `/host-deploy`, so "drift" means genuine divergence). The cap is the circuit-breaker that makes the default safe.

---

## Concrete design (locked after reading all source — these supersede any vaguer wording above)

**1. Reuse the existing agent URL/token — do NOT add new secrets.** `mcp_http_probe.py` already reads `mcp_http_probe_recovery_url` + `mcp_http_probe_recovery_token` and they are live on prod (the agent recovered `mcp-http` at 00:40 on 2026-06-21). The compose-drift host path reads the **same two keys** — it's the same physical agent on `:9841`. (Renaming both consumers to neutral `recovery_agent_url` / `recovery_agent_token` with a backcompat shim is deferred to PR4's audit, so PR3 needs zero new provisioning.)

**2. Isolated branch — do NOT refactor the existing 5b path.** `test_compose_drift_probe.py` only covers on-demand suppression; the 5b recreate→recovered→persists path is **untested**, so refactoring it has no safety net. PR3 adds a new, self-contained host-recover branch and leaves 5a/5b byte-for-byte unchanged. Precedence in `run_compose_drift_probe` after the per-service audit (section 4):

```
if host_recover_enabled and recovery_url and recovery_token:   # NEW 5-host branch
    ... cap check → POST → sleep_fn → re-probe → recovered/persists/cap-escalated ...
    return
elif auto_recover_enabled:        # existing 5b (brain-side compose up) — untouched
    ...
else:                             # existing 5a (notify-only) — untouched
    ...
```

When `host_recover_enabled` is true but URL/token are blank (fresh install), log and **fall through** to 5a (notify-only) — never a silent no-op.

**3. The POST helper** mirrors `mcp_http_probe._try_http_recovery` exactly, only the service name differs:

```python
async def _try_host_recover(url: str, token: str) -> tuple[bool, str]:
    """POST {"service":"compose-reapply"} to the host Recovery Agent.
    Mirror of brain/mcp_http_probe.py::_try_http_recovery — same agent, same auth."""
    # httpx None-guard; POST json={"service":"compose-reapply"} +
    # headers={"Authorization": f"Bearer {token}"}; 2xx → (True, msg) else (False, msg)
```

Injectable as `host_recover_fn` on `run_compose_drift_probe` for tests (default `_try_host_recover`).

**4. The cap** copies the mcp_http_probe rolling-window pattern: module-level `_host_recover_attempts: list[float]`, prune to `now - window_s`, fire only if `len < cap`, else page "cap reached — escalating". Add `_reset_host_recover_state()` test hook.

**5. The agent's `compose` action** runs `bash <start-stack.sh> up -d --no-build` (NOT `--force-recreate` — that would tear down healthy containers incl. postgres/brain; plain `up -d` recreates only the config-hash-changed = drifted services). start-stack.sh path resolution, public-mirror-safe (no repo-name literal):

- `POINDEXTER_START_STACK` env (absolute path) → else glob `~/.poindexter/deploy/*/scripts/start-stack.sh` (single match) → else `~/.poindexter/scripts/start-stack.sh` → else error.
- Git Bash resolution mirrors `docker-watchdog.ps1::Resolve-GitBash`: `GIT_BASH` env → `C:\Program Files\Git\bin\bash.exe` → `C:\Program Files\Git\usr\bin\bash.exe` → `%LOCALAPPDATA%\Programs\Git\bin\bash.exe` → bare `bash` (never the PATH `bash` if it resolves to WSL).

**6. New settings (all non-secret, in `settings_defaults.py`):** `compose_drift_host_recover_enabled='true'`, `compose_drift_host_recover_cap_per_window='3'`, `compose_drift_host_recover_window_minutes='60'`.

**7. Deploy (Task 5):** copy `scripts/recovery-agent.py` + `.cmd` → host; re-point the "Poindexter Recovery Agent" Task at the **clone** copy (`~/.poindexter/deploy/<clone>/scripts/recovery-agent.py`) so it auto-syncs like start-stack/docker-watchdog did in the cutover; restart the Task; `docker compose build brain-daemon` + recreate; confirm settings; induce a benign drift and watch detect→POST→reapply→clear.
