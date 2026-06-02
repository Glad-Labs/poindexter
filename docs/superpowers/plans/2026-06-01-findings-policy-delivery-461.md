# Findings Policy Delivery (#461 core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the per-kind `findings.<kind>.*` policies actually drive delivery in `findings_alert_router` — wire the `auto_fix` and `github_issue` channels and per-kind `min_severity` gating, which are seeded in `app_settings` today but inert.

**Architecture:** The canonical findings-delivery path is `emit_finding → audit_log → findings_alert_router (60s Job) → alert_events → brain/alert_dispatcher → Telegram/Discord`. A separate `brain/findings_dispatcher.py` + `findings_dispatch_state` table was built **and reverted on 2026-05-31** (migration `20260531_150000_drop_findings_dispatch_state_duplicate.py`) as a duplicate path — **do not rebuild it.** All new policy behavior lands _inside_ `findings_alert_router`, the declared canonical worker. Today the router only honors `delivery='log_only'` (suppress) vs route-everything-else; this plan adds the two action channels and severity gating, all in one file plus its test.

**Tech Stack:** Python 3.13, asyncpg, the `plugins.job.Job` protocol (`run(self, pool, config) -> JobResult`), pytest (async). Auto-fix jobs (`FixBrokenExternalLinksJob`, etc.) already exist and share the `run(self, pool, config)` signature. `github_issue` delivery shells out to the `gh` CLI.

---

## Background the executing engineer MUST read first

Read these before touching code — they encode decisions you can silently violate:

- `src/cofounder_agent/services/jobs/findings_alert_router.py` — the whole file. Note `_load_log_only_kinds`, `_should_route`, `_normalize_severity`, `_finding_kind`, `_insert_alert_event`, and the `FindingsAlertRouterJob.run` loop. **Note especially:** unconfigured kinds intentionally **route** (stay loud) — the router deliberately ignores `findings.default.*` so a brand-new critical finding-kind can never be silently swallowed. Preserve that invariant.
- `src/cofounder_agent/services/migrations/20260531_150000_drop_findings_dispatch_state_duplicate.py` — why the separate dispatcher was dropped and why policies are wired _here_.
- `src/cofounder_agent/services/migrations/20260531_130000_findings_dispatcher_state_and_policies.py` — the seeded `_POLICIES` (the `findings.<kind>.{delivery,fallback,cooldown_minutes,min_severity}` rows you're now honoring).
- `src/cofounder_agent/plugins/job.py` — the `Job` protocol + `JobResult(ok, detail, changes_made, metrics)`. Jobs must NOT raise on routine failure — return `JobResult(ok=False, ...)`.
- `src/cofounder_agent/utils/findings.py` — `emit_finding(*, source, kind, title, body, severity='info', dedup_key=None, extra=None) -> None`, fire-and-forget.

**Scope boundary:** This plan is the policy-delivery core only. Two pieces are intentionally OUT of scope (separate plans — see "Deferred" at the end): #461 Phase-4 triage surfaces (Discord digest + MCP `findings_list` tool + Grafana panel) and #181's anticipation-interface generalization. Task 2 here (`auto_fix`) is the _first concrete consumer_ that #181 later generalizes — build the caller first, abstract later.

## File Structure

- **Modify:** `src/cofounder_agent/services/jobs/findings_alert_router.py` — replace the log-only-set policy loader with a full per-kind policy loader; add a `_delivery_for` decision helper; add `auto_fix` + `github_issue` dispatch helpers; rewire the `run` loop to branch on delivery. Stop discarding `config` (auto-fix jobs need `config["_site_config"]`).
- **Modify/Create test:** `src/cofounder_agent/tests/unit/services/jobs/test_findings_alert_router.py` — if it exists, add to it; otherwise create it. Most new tests target pure helpers (`_delivery_for`, the kind→job map) and need no DB.

---

### Task 1: Per-kind policy loading + `min_severity` gating

**Files:**

- Modify: `src/cofounder_agent/services/jobs/findings_alert_router.py`
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_findings_alert_router.py`

- [ ] **Step 1: Write the failing tests for the decision helper**

Add to the test file:

```python
from services.jobs.findings_alert_router import _delivery_for, _SEV_RANK

# policies dict shape mirrors what _load_policies returns:
# {kind: {field: value}}. Absence of a kind => route (stay loud).
_POL = {
    "media_drift": {"delivery": "log_only"},
    "anomaly": {"delivery": "telegram", "min_severity": "critical"},
    "broken_external_link": {"delivery": "auto_fix", "fallback": "discord", "min_severity": "warn"},
    "quality_regression": {"delivery": "github_issue", "min_severity": "warn"},
}


def test_unconfigured_kind_routes_even_when_default_is_log_only():
    # Invariant: a kind with no findings.<kind>.* policy must stay loud.
    assert _delivery_for("brand_new_kind", "warning", _POL) == "route"


def test_log_only_kind_is_suppressed():
    assert _delivery_for("media_drift", "warning", _POL) == "log_only"


def test_critical_log_only_is_refused_and_routes():
    # A critical marked log_only is a misconfig — fail loud, never drop.
    assert _delivery_for("media_drift", "critical", _POL) == "route"


def test_min_severity_gates_below_threshold():
    # anomaly min_severity=critical: a warn-level anomaly is below threshold.
    assert _delivery_for("anomaly", "warning", _POL) == "log_only"


def test_min_severity_passes_at_threshold():
    assert _delivery_for("anomaly", "critical", _POL) == "telegram"


def test_auto_fix_delivery_passthrough():
    assert _delivery_for("broken_external_link", "warning", _POL) == "auto_fix"


def test_github_issue_delivery_passthrough():
    assert _delivery_for("quality_regression", "warning", _POL) == "github_issue"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_findings_alert_router.py -k "delivery_for or min_severity or unconfigured or log_only_kind or critical_log_only or auto_fix_delivery or github_issue_delivery" -v`
Expected: FAIL — `ImportError: cannot import name '_delivery_for'`.

- [ ] **Step 3: Implement `_SEV_RANK`, `_load_policies`, and `_delivery_for`**

In `findings_alert_router.py`, add near the top (after `_ROUTABLE_SEVERITIES`):

```python
# Severity ordering for per-kind min_severity gating. emit_finding uses
# 'warn'; alert_events/Prometheus use 'warning' — _normalize_severity
# collapses them, but rank both so a raw value never KeyErrors.
_SEV_RANK = {"info": 0, "warn": 1, "warning": 1, "critical": 2}
```

Replace `_load_log_only_kinds` with a full-policy loader (keep the name out of the public surface — `run` will call the new one):

```python
async def _load_policies(pool: Any) -> dict[str, dict[str, str]]:
    """Load every findings.<kind>.<field> app_setting into {kind: {field: value}}.

    The router consults per-kind policies ONLY — it deliberately does NOT
    fold in findings.default.* (an unlisted kind must stay loud, never be
    silently suppressed by a default of log_only; that is exactly the
    silent-drop the alert audit eliminated)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value FROM app_settings WHERE key LIKE 'findings.%.%'"
        )
    policies: dict[str, dict[str, str]] = {}
    for r in rows:
        parts = r["key"].split(".")
        # findings.<kind>.<field>
        if len(parts) != 3:
            continue
        _, kind, field = parts
        if kind == "default":
            continue  # intentionally ignored by the router (see docstring)
        policies.setdefault(kind, {})[field] = r["value"]
    return policies
```

Add the decision helper:

```python
def _delivery_for(kind: str, severity: str, policies: dict[str, dict[str, str]]) -> str:
    """Resolve the delivery action for one finding.

    Returns one of: 'route' (insert alert_events -> dispatcher picks
    channel by severity), 'log_only' (suppress paging), 'auto_fix',
    'github_issue'. Telegram/discord per-kind values collapse to 'route'
    (the dispatcher's severity matrix already routes channel).

    Invariants preserved from the prior _should_route:
    - A kind with NO policy => 'route' (stay loud).
    - A 'critical' finding NEVER resolves to log_only (misconfig => route).
    - Otherwise gate on per-kind min_severity (default 'warning')."""
    pol = policies.get(kind)
    if pol is None:
        return "route"

    sev = _normalize_severity(severity)
    delivery = pol.get("delivery", "route")
    # Channel-by-severity values are handled downstream; treat as route.
    if delivery in ("discord", "telegram"):
        delivery = "route"

    if sev == "critical":
        return "route" if delivery == "log_only" else delivery

    min_sev = _normalize_severity(pol.get("min_severity", "warning"))
    if _SEV_RANK.get(sev, 0) < _SEV_RANK.get(min_sev, 0):
        return "log_only"
    return delivery
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_findings_alert_router.py -k "delivery_for or min_severity or unconfigured or log_only_kind or critical_log_only or auto_fix_delivery or github_issue_delivery" -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/jobs/findings_alert_router.py src/cofounder_agent/tests/unit/services/jobs/test_findings_alert_router.py
git commit -m "feat(findings): per-kind policy loader + min_severity gating in router (#461)"
```

---

### Task 2: `auto_fix` delivery — trigger the matching job out-of-cycle

**Files:**

- Modify: `src/cofounder_agent/services/jobs/findings_alert_router.py`
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_findings_alert_router.py`

**Design:** When a finding resolves to `auto_fix`, instantiate the mapped Job and `await job.run(pool, config)` in-process (the router already runs inside the worker that owns these jobs — no brain round-trip needed). Record the outcome as a `<kind>_autofixed` follow-up finding (`severity='info'`, which the seeded policy maps to `log_only`, so it never pages). On job failure or missing mapping, fall back to the policy's `fallback` channel (route to alert_events). This requires the router to stop discarding `config` — auto-fix jobs read `config["_site_config"]`.

- [ ] **Step 1: Write the failing test for the kind→job map**

```python
from services.jobs.findings_alert_router import _AUTOFIX_JOBS
from services.jobs.fix_broken_external_links import FixBrokenExternalLinksJob
from services.jobs.fix_broken_internal_links import FixBrokenInternalLinksJob
from services.jobs.fix_uncategorized_posts import FixUncategorizedPostsJob


def test_autofix_map_covers_seeded_auto_fix_kinds():
    # Every kind whose seeded policy delivery='auto_fix' must have a job,
    # else the router would silently fall back forever.
    assert _AUTOFIX_JOBS["broken_external_link"] is FixBrokenExternalLinksJob
    assert _AUTOFIX_JOBS["broken_internal_link"] is FixBrokenInternalLinksJob
    assert _AUTOFIX_JOBS["uncategorized_post"] is FixUncategorizedPostsJob
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_findings_alert_router.py -k autofix_map -v`
Expected: FAIL — `ImportError: cannot import name '_AUTOFIX_JOBS'`.

- [ ] **Step 3: Implement the map + dispatch helper**

Add imports at the top of `findings_alert_router.py`:

```python
from services.jobs.fix_broken_external_links import FixBrokenExternalLinksJob
from services.jobs.fix_broken_internal_links import FixBrokenInternalLinksJob
from services.jobs.fix_uncategorized_posts import FixUncategorizedPostsJob
from utils.findings import emit_finding
```

> NOTE: `missing_seo` policy is `auto_fix` with `fallback=github_issue`, but `flag_missing_seo` only _flags_ today (no auto-fill). Deliberately omit `missing_seo` from `_AUTOFIX_JOBS` so it falls through to its `github_issue` fallback until the auto-fill expansion lands (issue #461 Phase 3 note). Document this in the map comment.

```python
# kind -> auto-fix Job class. Only kinds with a REAL fix job belong here.
# A kind with delivery='auto_fix' but no entry falls back to its
# `fallback` channel (route) — never silently dropped. missing_seo is
# intentionally absent (flag-only today; falls back to github_issue).
_AUTOFIX_JOBS: dict[str, Any] = {
    "broken_external_link": FixBrokenExternalLinksJob,
    "broken_internal_link": FixBrokenInternalLinksJob,
    "uncategorized_post": FixUncategorizedPostsJob,
}


async def _dispatch_auto_fix(
    pool: Any, config: dict[str, Any], finding: dict[str, Any], kind: str
) -> bool:
    """Run the mapped auto-fix job for an auto_fix finding.

    Returns True if the fix job ran (regardless of changes_made) — the
    finding is considered handled. Returns False if no job is mapped or
    the job raised/returned not-ok, so the caller can apply the fallback
    channel. Emits an info-level ``<kind>_autofixed`` follow-up finding
    recording the outcome (seeded policy maps it to log_only)."""
    job_cls = _AUTOFIX_JOBS.get(kind)
    if job_cls is None:
        return False
    try:
        result = await job_cls().run(pool, config)
    except Exception as exc:  # job protocol says don't raise, but be safe
        logger.warning(
            "[findings_alert_router] auto_fix job %s raised for "
            "audit_log.id=%s: %s", kind, finding.get("id"), exc,
        )
        return False
    emit_finding(
        source="findings_alert_router",
        kind=f"{kind}_autofixed",
        title=f"auto-fix ran for {kind}",
        body=(
            f"Triggered {job_cls.__name__} from finding "
            f"audit_log.id={finding.get('id')}: ok={result.ok} "
            f"changes_made={result.changes_made} — {result.detail}"
        ),
        severity="info",
        dedup_key=f"autofixed:{kind}:{finding.get('id')}",
    )
    return result.ok
```

- [ ] **Step 4: Run to verify the map test passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_findings_alert_router.py -k autofix_map -v`
Expected: PASS.

- [ ] **Step 5: Rewire `run` to branch on delivery (and keep `config`)**

In `FindingsAlertRouterJob.run`, delete the `del config` line and replace the policy load + loop body. The full new `run` body:

```python
    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        watermark = await _read_watermark(pool)
        rows = await _fetch_unrouted_findings(pool, watermark)
        if not rows:
            return JobResult(
                ok=True,
                detail=f"no new findings above watermark {watermark}",
                changes_made=0,
            )

        policies = await _load_policies(pool)
        routed = autofixed = filed = suppressed = errors = 0
        max_id = watermark

        for r in rows:
            kind = _finding_kind(r)
            severity = r.get("severity") or "info"
            delivery = _delivery_for(kind, severity, policies)
            fallback = (policies.get(kind) or {}).get("fallback", "route")

            try:
                if delivery == "log_only":
                    suppressed += 1
                elif delivery == "auto_fix":
                    if await _dispatch_auto_fix(pool, config, r, kind):
                        autofixed += 1
                    else:
                        await _deliver_fallback(pool, r, kind, fallback)
                        routed += 1
                elif delivery == "github_issue":
                    if await _dispatch_github_issue(r, kind):
                        filed += 1
                    else:
                        await _deliver_fallback(pool, r, kind, fallback)
                        routed += 1
                else:  # 'route'
                    await _insert_alert_event(pool, r)
                    routed += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    "[findings_alert_router] delivery=%s failed for "
                    "audit_log.id=%s: %s", delivery, r.get("id"), exc,
                )
                continue  # do NOT advance watermark — retry next cycle
            max_id = max(max_id, int(r["id"]))

        if max_id > watermark:
            await _write_watermark(pool, max_id)

        return JobResult(
            ok=errors == 0,
            detail=(
                f"routed {routed}, auto-fixed {autofixed}, filed {filed}, "
                f"suppressed {suppressed}, {errors} error(s); "
                f"watermark {watermark} -> {max_id}"
            ),
            changes_made=routed + autofixed + filed,
        )
```

Add the fallback helper (a fallback that is `log_only` means "fix failed but don't page" — still record nothing extra; a fallback of anything else routes):

```python
async def _deliver_fallback(
    pool: Any, finding: dict[str, Any], kind: str, fallback: str
) -> None:
    """Apply a policy's fallback when the primary channel could not act.
    Any fallback other than log_only routes to alert_events so the
    operator still sees it. log_only fallback is a no-op (recorded in
    audit_log already)."""
    if fallback == "log_only":
        logger.info(
            "[findings_alert_router] %s primary failed; fallback=log_only "
            "for audit_log.id=%s", kind, finding.get("id"),
        )
        return
    await _insert_alert_event(pool, finding)
```

- [ ] **Step 6: Run the full router test module**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_findings_alert_router.py -v`
Expected: PASS (existing tests + new). If existing `run`-level tests asserted on the old `detail` string, update those assertions to the new wording.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/jobs/findings_alert_router.py src/cofounder_agent/tests/unit/services/jobs/test_findings_alert_router.py
git commit -m "feat(findings): auto_fix delivery triggers matching fix job + fallback (#461)"
```

---

### Task 3: `github_issue` delivery — file an issue via `gh`

**Files:**

- Modify: `src/cofounder_agent/services/jobs/findings_alert_router.py`
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_findings_alert_router.py`

**Design:** `quality_regression` (and `missing_seo`'s fallback) want a GitHub issue, not a page. No Python helper exists, so shell `gh issue create` against the **private** repo `Glad-Labs/glad-labs-stack` (operator findings stay private — matches the #461 spec). Dedup by title: skip if an open issue with the same title already exists (`gh issue list --search`). `gh` auth is a real operational dependency in the worker container — call it out in the docstring; on `gh` missing/unauth, return False so the finding falls back to a page (never silently dropped).

- [ ] **Step 1: Write the failing test (subprocess mocked)**

```python
import services.jobs.findings_alert_router as router_mod


class _FakeProc:
    def __init__(self, returncode, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._out = stdout
        self._err = stderr

    async def communicate(self):
        return self._out, self._err


def test_github_issue_skips_when_duplicate_open_issue_exists(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        # First call is the dedup `gh issue list` — return one match.
        if "list" in args:
            return _FakeProc(0, stdout=b'[{"title":"quality regression: foo"}]')
        return _FakeProc(0)

    monkeypatch.setattr(router_mod.asyncio, "create_subprocess_exec", fake_exec)
    finding = {"id": 1, "source": "audit_published_quality",
               "details": {"kind": "quality_regression",
                           "title": "quality regression: foo", "body": "b"}}
    import asyncio as _aio
    ok = _aio.run(router_mod._dispatch_github_issue(finding, "quality_regression"))
    assert ok is True                      # handled (dedup is success)
    assert not any("create" in c for c in calls)   # never created a dup
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_findings_alert_router.py -k github_issue_skips -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_dispatch_github_issue'`.

- [ ] **Step 3: Implement `_dispatch_github_issue`**

Add `import asyncio` and `import shutil` at the top if not present, then:

```python
_FINDINGS_ISSUE_REPO = "Glad-Labs/glad-labs-stack"  # private — operator findings


async def _dispatch_github_issue(finding: dict[str, Any], kind: str) -> bool:
    """File a GitHub issue for a finding via the `gh` CLI.

    Returns True if the issue was created OR a same-title open issue
    already exists (dedup). Returns False if `gh` is unavailable/unauth
    or the create failed, so the caller applies the fallback channel.

    Operational dependency: the worker container needs `gh` on PATH and
    authenticated. Absent that, findings fall back to a page — loud, not
    silent."""
    if shutil.which("gh") is None:
        logger.warning("[findings_alert_router] `gh` not on PATH; cannot file %s", kind)
        return False

    details = finding.get("details") or {}
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            details = {}
    title = details.get("title") or f"{kind} finding"
    body = (details.get("body") or "")[:60000]

    # Dedup: is there already an open issue with this exact title?
    list_proc = await asyncio.create_subprocess_exec(
        "gh", "issue", "list", "--repo", _FINDINGS_ISSUE_REPO,
        "--state", "open", "--search", title, "--json", "title",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await list_proc.communicate()
    if list_proc.returncode == 0:
        try:
            existing = json.loads(out or b"[]")
            if any(i.get("title") == title for i in existing):
                logger.info("[findings_alert_router] dup issue exists: %r", title)
                return True
        except json.JSONDecodeError:
            pass  # fall through to create

    create_proc = await asyncio.create_subprocess_exec(
        "gh", "issue", "create", "--repo", _FINDINGS_ISSUE_REPO,
        "--title", title, "--body", body, "--label", "finding",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await create_proc.communicate()
    if create_proc.returncode != 0:
        logger.warning(
            "[findings_alert_router] `gh issue create` failed for %s: %s",
            kind, (err or b"").decode(errors="replace")[:500],
        )
        return False
    return True
```

- [ ] **Step 4: Run to verify the test passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_findings_alert_router.py -k github_issue_skips -v`
Expected: PASS.

- [ ] **Step 5: Run the full module + lint**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_findings_alert_router.py -v && poetry run ruff check services/jobs/findings_alert_router.py`
Expected: all PASS, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/jobs/findings_alert_router.py src/cofounder_agent/tests/unit/services/jobs/test_findings_alert_router.py
git commit -m "feat(findings): github_issue delivery via gh CLI with title dedup (#461)"
```

---

### Task 4: Reconcile the #461 issue text with reality

**Files:** none (GitHub only).

- [ ] **Step 1: Post a status comment on [poindexter#461](https://github.com/Glad-Labs/poindexter/issues/461)** recording: (a) the separate `brain/findings_dispatcher.py` + `findings_dispatch_state` approach was built and reverted 2026-05-31 — canonical path is `findings_alert_router` + `alert_dispatcher`; (b) Phase 1/2 are effectively done (routing + per-kind policies + `min_severity` gating + `auto_fix` + `github_issue` now wired); (c) remaining = Phase 4 triage surfaces (digest/MCP/Grafana) + the `missing_seo` auto-fill expansion, tracked separately.

```bash
gh issue comment 461 --repo Glad-Labs/poindexter --body-file <path-to-status-note>
```

- [ ] **Step 2: Commit** — n/a (no code).

---

## Deferred (separate plans — do NOT fold in here)

- **#461 Phase 4 — triage surfaces.** Discord daily findings digest, MCP `findings_list` tool (in `mcp-server/`), Grafana "unprocessed findings by kind/severity" panel (in `infrastructure/grafana/provisioning/`). Different subsystems; own plan.
- **#181 — anticipation interface.** Task 2's `_dispatch_auto_fix` is the first concrete consumer of "trigger opportunistic work in response to a signal." #181 generalizes that into the brain watchdog's formal opportunistic-work surface (idle-cycle work: pending auto_fix findings, fading-post refreshes). **Brainstorm the interface shape FIRST** (`superpowers:brainstorming`) — the `findings_dispatch_state` revert is the cautionary tale of generalizing before a caller exists. Now there's a caller.
- **#340 close + refile B/C** — handled as a standalone action outside this plan (drafts pending operator go-ahead).

## Self-Review

- **Spec coverage:** #461 Phase 1 (dispatch logic) ✅ already canonical via router; Phase 2 (per-kind policies + min_severity) → Task 1; Phase 3 (auto-fix wiring) → Task 2; the `github_issue` channel named in the seeded policies → Task 3. Phase 4 explicitly deferred. ✅
- **Placeholder scan:** every code step has complete code; no TODO/TBD. ✅
- **Type consistency:** `_delivery_for(kind, severity, policies)` returns the literal set `{route, log_only, auto_fix, github_issue}` consumed verbatim in `run`'s branches; `_load_policies` returns `dict[str, dict[str, str]]` matching `_POL` test shape and `_delivery_for`'s param; `_dispatch_auto_fix` / `_dispatch_github_issue` / `_deliver_fallback` signatures match their call sites in `run`; `JobResult` fields (`ok`, `detail`, `changes_made`) match `plugins/job.py`. ✅
- **Invariant preserved:** unconfigured kinds route; critical never log_only; `findings.default.*` ignored by the router. ✅

## Execution Handoff
