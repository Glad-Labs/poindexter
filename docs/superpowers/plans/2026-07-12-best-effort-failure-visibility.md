# Best-Effort Failure Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close `lint_silent_excepts.py`'s three detection blind spots and make best-effort exception swallows emit a visible-but-non-paging signal (`emit_finding(info)` / `warning+`) instead of vanishing below the operator's alerting bar.

**Architecture:** Extend the linter's AST matcher from single-statement to whole-body (a broad handler is silent iff _every_ statement is low-visibility); adopt the existing `emit_finding` as the blessed best-effort signal at `info` severity; convert a first exemplar batch; re-baseline the ratchet. `info` findings are intrinsically non-paging — the findings router's fetch floor (`_ROUTABLE_SEVERITIES = ("warn","warning","critical")`) never selects them, so they land on the Findings dashboard but never route. No settings changes.

**Tech Stack:** Python 3 (stdlib `ast` for the linter), pytest, the existing `utils/findings.emit_finding` → `audit_log` → `findings_alert_router` path.

## Global Constraints

- **All changes ship on branch `claude/silent-excepts-lint-140129`, PR #2372** (draft). One commit per task; linear history (no merge commits) per `feedback_linear_history_no_merge_commits`.
- **Non-blocking invariant (every converted handler):** never start raising or blocking. `emit_finding` is fire-and-forget (never raises). Preserve the existing swallow + sentinel-return / `continue` behavior exactly — only add an observability side-effect.
- **Visibility bar:** a broad handler is acceptable (not silent) iff it calls `emit_finding(...)`, logs at `warning`/`error`/`exception`/`critical`, or re-raises. `debug`/`info`/`pass`/`...`/`print`/sentinel-return/`continue` alone = silent.
- **Best-effort findings emit at `severity="info"`** — non-paging by construction (the router's `_ROUTABLE_SEVERITIES` floor never fetches `info`). No `findings.<kind>` policy seed is added; an inert `info` policy would only trip the zero-reader-settings probe.
- **Brain tree (`brain/`) cannot import `services.audit_log`** → uses `logger.warning(...)`, not `emit_finding`.
- **Loops aggregate:** count failures, emit ONE finding after the loop when count > 0. Never `emit_finding` per iteration.
- **Running tests in this worktree** (`reference_run_worktree_tests`): a fresh worktree has no venv. The **linter test is pure stdlib** — any Python runs it. The **service/brain tests need the backend env** — run them with the main checkout's poetry env, e.g. from `C:\Users\mattm\glad-labs-website\src\cofounder_agent`: `poetry run pytest "<worktree>\src\cofounder_agent\tests\...\test_x.py" -q -o addopts=""` (the `-o addopts=""` drops the repo's `--forked` default). Below, `PYTEST` = that command and `<worktree>` = this worktree's root.
- **Linter re-baseline command** (pure stdlib, run from the worktree root): `python scripts/ci/lint_silent_excepts.py --update-baseline`, then verify green with `python scripts/ci/lint_silent_excepts.py`.

---

### Task 1: Whole-body linter matcher + convention docs + re-baseline

Rewrite the matcher so multi-statement / control-flow / print swallows are seen, treat `emit_finding`/`warning+`/`raise` as visible, document the convention, and re-baseline the ~76 newly-visible sites.

**Files:**

- Modify: `scripts/ci/lint_silent_excepts.py` (matcher + module docstring)
- Modify: `src/cofounder_agent/utils/findings.py` (module docstring — convention pointer)
- Modify: `scripts/ci/silent_excepts_baseline.json` (regenerated)
- Test: `src/cofounder_agent/tests/unit/scripts/test_lint_silent_excepts.py`

**Interfaces:**

- Consumes: existing `_is_broad_except`, `_is_low_visibility_log_call`, `_is_sentinel_return`, `scan_file`, `compute_counts`, `load_baseline` (unchanged signatures).
- Produces: `_is_ellipsis_expr(stmt) -> bool`, `_is_print_call(stmt) -> bool`, `_stmt_is_low_visibility(stmt, *, broad: bool) -> bool`; rewritten `_handler_is_silent(handler) -> bool` (same signature). Later tasks rely on the new matcher counting `debug`+sentinel-return, `continue`/`break`, `...`, and `print` as silent, and treating `emit_finding`/`warning+`/`raise` as visible.

- [ ] **Step 1: Write the failing tests**

Append a new test class to `src/cofounder_agent/tests/unit/scripts/test_lint_silent_excepts.py`:

```python
class TestMultiStatementAndControlFlowSilence:
    """The linter's original matcher only saw single-statement handlers. A broad
    handler whose whole body is low-visibility is the same swallow spread over
    multiple statements, or hidden behind loop control flow."""

    def test_broad_except_debug_then_return_none_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception as e:\n"
            "        logger.debug('x: %s', e)\n"
            "        return None\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_info_then_return_empty_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception as e:\n"
            "        logger.info('x: %s', e)\n"
            "        return []\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_continue_is_silent(self, tmp_path):
        src = (
            "def f(items):\n"
            "    for i in items:\n"
            "        try:\n            work(i)\n"
            "        except Exception:\n            continue\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_debug_then_continue_is_silent(self, tmp_path):
        src = (
            "def f(items):\n"
            "    for i in items:\n"
            "        try:\n            work(i)\n"
            "        except Exception as e:\n"
            "            logger.debug('skip %s: %s', i, e)\n"
            "            continue\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_break_is_silent(self, tmp_path):
        src = (
            "def f(items):\n"
            "    for i in items:\n"
            "        try:\n            work(i)\n"
            "        except Exception:\n            break\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_ellipsis_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception:\n        ...\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_print_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception as e:\n        print('broke', e)\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_narrow_except_continue_is_not_silent(self, tmp_path):
        src = (
            "def f(items):\n"
            "    for i in items:\n"
            "        try:\n            work(i)\n"
            "        except ValueError:\n            continue\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_debug_then_warning_is_not_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception as e:\n"
            "        logger.debug('d: %s', e)\n"
            "        logger.warning('w: %s', e)\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_debug_then_emit_finding_then_return_none_is_not_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception as e:\n"
            "        logger.debug('d: %s', e)\n"
            "        emit_finding(source='s', kind='k', title='t', body='b')\n"
            "        return None\n"
        )
        assert _scan_src(tmp_path, src) == 0
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `PYTEST "<worktree>\src\cofounder_agent\tests\unit\scripts\test_lint_silent_excepts.py::TestMultiStatementAndControlFlowSilence" -v`
Expected: the `*_is_silent` cases FAIL (currently return 0, expect 1); the `*_not_silent` cases already pass.

- [ ] **Step 3: Add the new helpers and rewrite the matcher**

In `scripts/ci/lint_silent_excepts.py`, add after `_is_low_visibility_log_call` (before `_is_broad_except`):

```python
def _is_ellipsis_expr(stmt: ast.stmt) -> bool:
    """True if stmt is a bare ``...`` (Ellipsis) — a pass-equivalent no-op body."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    )


def _is_print_call(stmt: ast.stmt) -> bool:
    """True if stmt is a bare ``print(...)`` call — stdout only, never a logging
    event, so it reaches neither GlitchTip nor a surfaced board."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "print"
    )


def _stmt_is_low_visibility(stmt: ast.stmt, *, broad: bool) -> bool:
    """True if one handler-body statement is below the operator's alerting bar.

    Always low-visibility (any breadth): ``pass``, ``...``, a
    ``<logger>.debug/info(...)`` call, a ``print(...)``.
    Low-visibility ONLY under a broad except (a narrow except names its
    exception, making these deliberate control flow): ``continue`` / ``break``
    and a sentinel ``return`` (bare / constant / empty collection).
    Anything else — a ``warning``+/``error``/``exception``/``critical`` log, an
    ``emit_finding(...)`` call, a ``raise``, an assignment, any real call — is a
    visibility signal.
    """
    if isinstance(stmt, ast.Pass) or _is_ellipsis_expr(stmt):
        return True
    if _is_low_visibility_log_call(stmt) or _is_print_call(stmt):
        return True
    if broad and isinstance(stmt, (ast.Continue, ast.Break)):
        return True
    if broad and _is_sentinel_return(stmt):
        return True
    return False
```

Replace the existing `_handler_is_silent` body with:

```python
def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    """True if EVERY statement in the handler body is below operator visibility —
    the failure is recorded nowhere an operator will see it (no finding, no
    warning+, no re-raise).

    Covers the single ``pass`` / ``<logger>.debug(...)`` handler AND its
    multi-statement forms — a broad ``except`` whose whole body is
    ``<logger>.debug(...)`` then ``return None`` (or ``continue`` in a loop) is
    the same swallow. Any visibility signal anywhere in the body — an
    ``emit_finding(...)`` call, a ``warning``+ log, a ``raise`` — makes it NOT
    silent.
    """
    broad = _is_broad_except(handler)
    return all(_stmt_is_low_visibility(s, broad=broad) for s in handler.body)
```

- [ ] **Step 4: Run the whole linter test file to verify all pass (except the ratchet test)**

Run: `PYTEST "<worktree>\src\cofounder_agent\tests\unit\scripts\test_lint_silent_excepts.py" -v --deselect "<worktree>\src\cofounder_agent\tests\unit\scripts\test_lint_silent_excepts.py::TestBaselineRatchet::test_real_tree_matches_baseline"`
Expected: PASS (new class + all pre-existing classes — the superset matcher preserves every prior count). The ratchet test is deselected because the tree won't match the old baseline until Step 7.

- [ ] **Step 5: Update the module docstring (convention) in the linter**

In `scripts/ci/lint_silent_excepts.py`, insert this paragraph into the module docstring, immediately before the `…or when a whole block is wrapped in broad` line:

```
Two-statement and control-flow swallows count too — a *broad* handler whose
whole body is low-visibility is silent even when spread across statements:

  - ``<logger>.debug(...)`` then ``return <sentinel>`` — the two-statement
    form of the same swallow
  - ``continue`` / ``break`` in a loop (skips the failure with no record)
  - a bare ``...`` (pass-equivalent) or a ``print(...)`` (stdout only, never
    a logging event — reaches neither GlitchTip nor a board)

A handler is VISIBLE (not counted) the moment any statement is a real signal:
an ``emit_finding(...)`` call, a ``warning``+/``error``/``exception``/
``critical`` log, or a ``raise``. That is the bar — surface a best-effort
failure as an info-severity finding (Findings dashboard, deduped, non-paging)
or a warning+; ``debug``/``info`` alone is below it. In a loop, count the
failures and emit ONE finding after the loop, never one per iteration.
```

- [ ] **Step 6: Add the convention pointer to `utils/findings.py`**

In `src/cofounder_agent/utils/findings.py`, append this paragraph to the module docstring (after the "Per-kind delivery policy…" bullet, before the closing `"""`):

```
Best-effort exception swallows use this too: when a broad ``except`` swallows a
non-fatal failure and returns a fallback, record it here —
``emit_finding(severity="info", dedup_key=...)`` — so the failure is visible on
the Findings dashboard without paging or blocking. ``info`` never pages: the
findings router's fetch floor (``_ROUTABLE_SEVERITIES`` = warn/warning/critical)
never selects ``info`` rows, so an ``info`` finding is dashboard-visible but
never routed. In a loop, count failures and emit ONE finding after the loop
(never one per iteration). This is the convention enforced by
``scripts/ci/lint_silent_excepts.py``.
```

- [ ] **Step 7: Regenerate the baseline and verify the ratchet is green**

Run (from worktree root):

```bash
python scripts/ci/lint_silent_excepts.py --update-baseline
python scripts/ci/lint_silent_excepts.py
```

Expected: first prints `baseline written — N files, M silent handlers grandfathered` (M grows by ~76 vs the prior 287); second prints `clean — no new silent handlers`.

- [ ] **Step 8: Run the full linter test file including the ratchet test**

Run: `PYTEST "<worktree>\src\cofounder_agent\tests\unit\scripts\test_lint_silent_excepts.py" -v`
Expected: PASS (including `TestBaselineRatchet::test_real_tree_matches_baseline`, now that the baseline matches the new matcher).

- [ ] **Step 9: Commit**

```bash
git add scripts/ci/lint_silent_excepts.py scripts/ci/silent_excepts_baseline.json src/cofounder_agent/utils/findings.py src/cofounder_agent/tests/unit/scripts/test_lint_silent_excepts.py
git commit -m "feat(ci): whole-body silent-except matcher + best-effort visibility convention"
```

---

### Task 2: Exemplar — R2 WebP conversion fallback (simple debug+return)

Convert the pure `_convert_to_webp` best-effort swallow to emit an info finding.

**Files:**

- Modify: `src/cofounder_agent/services/r2_upload_service.py:73-75`
- Test: `src/cofounder_agent/tests/unit/services/test_r2_upload_service.py` (**append** — exists)

**Interfaces:**

- Consumes: `_stmt_is_low_visibility` treating `emit_finding` as visible (Task 1).
- Produces: nothing downstream; `_convert_to_webp` still returns `None` on failure.

- [ ] **Step 1: Write the failing test**

Append to `src/cofounder_agent/tests/unit/services/test_r2_upload_service.py`:

```python
def test_convert_to_webp_emits_finding_on_failure(tmp_path, monkeypatch):
    from services import r2_upload_service

    calls = []
    monkeypatch.setattr("utils.findings.emit_finding", lambda **kw: calls.append(kw))
    bad = tmp_path / "not-an-image.txt"
    bad.write_text("garbage", encoding="utf-8")

    result = r2_upload_service._convert_to_webp(bad)

    assert result is None  # non-blocking: caller falls back to the original
    assert calls, "expected an emit_finding call on conversion failure"
    assert calls[0]["kind"] == "webp_conversion_fallback"
    assert calls[0]["severity"] == "info"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTEST "<worktree>\src\cofounder_agent\tests\unit\services\test_r2_upload_service.py::test_convert_to_webp_emits_finding_on_failure" -v`
Expected: FAIL (`assert calls` — no emit yet).

- [ ] **Step 3: Convert the handler**

In `src/cofounder_agent/services/r2_upload_service.py`, replace the `_convert_to_webp` except block (currently lines 73-75):

```python
    except Exception as exc:  # noqa: BLE001 — conversion is best-effort
        from utils.findings import emit_finding

        logger.debug("[STORAGE] WebP conversion failed for %s: %s", path.name, exc)
        emit_finding(
            source="r2_upload_service",
            kind="webp_conversion_fallback",
            title=f"WebP conversion fell back to original for {path.name}",
            body=(
                f"Pillow WebP conversion failed for {path.name}: {exc}. Uploaded "
                "the original file instead — non-blocking, but a systematic "
                "failure means every image ships un-optimised."
            ),
            severity="info",
            dedup_key=f"webp_conversion_fallback:{path.suffix}",
        )
        return None
```

(The local `from utils.findings import emit_finding` inside the handler avoids any import-time cycle and matches the `scheduler.py` precedent; the test patches `utils.findings.emit_finding`, which this local import resolves at call time.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTEST "<worktree>\src\cofounder_agent\tests\unit\services\test_r2_upload_service.py::test_convert_to_webp_emits_finding_on_failure" -v`
Expected: PASS.

- [ ] **Step 5: Re-baseline (this site drops out) and verify green**

Run (from worktree root):

```bash
python scripts/ci/lint_silent_excepts.py --update-baseline
python scripts/ci/lint_silent_excepts.py
```

Expected: `clean`. The `r2_upload_service.py` count drops by 1 in the baseline diff.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/r2_upload_service.py src/cofounder_agent/tests/unit/services/test_r2_upload_service.py scripts/ci/silent_excepts_baseline.json
git commit -m "feat(storage): surface WebP-conversion fallback as an info finding"
```

---

### Task 3: Exemplars — analytics + affiliate sync loops (aggregate-count findings)

Both sync jobs skip rows with unparseable timestamps at `debug`+`continue`. Add a per-batch counter and emit ONE finding after the loop when any rows were skipped — a systematic skip means a timestamp-format drift silently under-counting.

**Files:**

- Modify: `src/cofounder_agent/services/jobs/sync_cloudflare_analytics.py` (add helper; counter at ~L277/L307; emit at ~L356)
- Modify: `src/cofounder_agent/services/jobs/sync_affiliate_clicks.py` (add import + helper; counter at ~L208/L224; emit at ~L266)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_sync_cloudflare_analytics.py` (**create** — does not exist)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_sync_affiliate_clicks.py` (**append** — exists)

**Interfaces:**

- Consumes: Task 1 matcher — the `skipped_bad_ts += 1` assignment in each loop handler is a non-low-visibility statement, so the handler reads as visible.
- Produces: module-level `_emit_bad_timestamp_finding(skipped: int) -> None` in each job (no-op when `skipped == 0`).

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/jobs/test_sync_cloudflare_analytics.py` with:

```python
"""sync_cloudflare_analytics — best-effort visibility.

A batch that skips rows with unparseable timestamps must surface ONE aggregate
finding (not per-row noise) so a silent CF-AE timestamp-format drift is caught.
"""


def test_emit_bad_timestamp_finding_aggregates(monkeypatch):
    from services.jobs import sync_cloudflare_analytics as m

    calls = []
    monkeypatch.setattr(m, "emit_finding", lambda **kw: calls.append(kw))

    m._emit_bad_timestamp_finding(0)
    assert calls == []  # nothing skipped -> no finding

    m._emit_bad_timestamp_finding(3)
    assert len(calls) == 1  # ONE finding for the whole batch, not per row
    assert calls[0]["kind"] == "analytics_row_parse_skipped"
    assert calls[0]["severity"] == "info"
    assert calls[0]["extra"]["skipped"] == 3
```

Append to `src/cofounder_agent/tests/unit/services/jobs/test_sync_affiliate_clicks.py`:

```python
def test_emit_bad_timestamp_finding_aggregates(monkeypatch):
    from services.jobs import sync_affiliate_clicks as m

    calls = []
    monkeypatch.setattr(m, "emit_finding", lambda **kw: calls.append(kw))

    m._emit_bad_timestamp_finding(0)
    assert calls == []

    m._emit_bad_timestamp_finding(2)
    assert len(calls) == 1
    assert calls[0]["kind"] == "affiliate_click_parse_skipped"
    assert calls[0]["severity"] == "info"
    assert calls[0]["extra"]["skipped"] == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run:

```
PYTEST "<worktree>\src\cofounder_agent\tests\unit\services\jobs\test_sync_cloudflare_analytics.py::test_emit_bad_timestamp_finding_aggregates" "<worktree>\src\cofounder_agent\tests\unit\services\jobs\test_sync_affiliate_clicks.py::test_emit_bad_timestamp_finding_aggregates" -v
```

Expected: FAIL (`AttributeError: module ... has no attribute '_emit_bad_timestamp_finding'` — and, for affiliate, `emit_finding` not yet imported).

- [ ] **Step 3a: cloudflare — add the helper**

In `src/cofounder_agent/services/jobs/sync_cloudflare_analytics.py` (`emit_finding` is already imported at module top), add a module-level function near the other module-level helpers:

```python
def _emit_bad_timestamp_finding(skipped: int) -> None:
    """Emit ONE aggregate finding when a batch skipped rows with unparseable
    timestamps. No-op when nothing was skipped (per-row skips stay debug-level)."""
    if not skipped:
        return
    emit_finding(
        source="sync_cloudflare_analytics",
        kind="analytics_row_parse_skipped",
        title=f"{skipped} analytics row(s) skipped — unparseable timestamp",
        body=(
            f"sync_cloudflare_analytics skipped {skipped} row(s) this batch because "
            "created_at did not match the expected format. A systematic skip means "
            "Cloudflare Analytics Engine changed its timestamp format and page-view "
            "counts are under-reporting — check the CF AE SQL response shape."
        ),
        severity="info",
        dedup_key="analytics_row_parse_skipped",
        extra={"skipped": skipped},
    )
```

- [ ] **Step 3b: cloudflare — wire the counter**

Initialise the counter alongside `inserted = 0` (currently line 277):

```python
            inserted = 0
            skipped_bad_ts = 0
            seen_slugs: dict[str, int] = {}
```

Increment it at the skip site (the inner `except Exception:` currently at lines 306-311) — add `skipped_bad_ts += 1` as the first statement:

```python
                        except Exception:
                            # Fallback: best-effort ISO parse
                            try:
                                ts = _parse_iso(ts_raw)
                            except Exception:
                                skipped_bad_ts += 1
                                logger.debug(
                                    "[SYNC_CF_AE] skipping row with bad timestamp %r",
                                    ts_raw,
                                )
                                continue
```

Emit after the loop, immediately before `return JobResult(ok=True,` (currently line 358), inside the outer `try`:

```python
            if max_ts is not None:
                await self._update_high_water_mark(pool, max_ts.isoformat())

            _emit_bad_timestamp_finding(skipped_bad_ts)

            return JobResult(
```

- [ ] **Step 3c: affiliate — add the import + helper**

In `src/cofounder_agent/services/jobs/sync_affiliate_clicks.py`, add the import near the other top-level imports:

```python
from utils.findings import emit_finding
```

Add the module-level helper near the other module-level helpers:

```python
def _emit_bad_timestamp_finding(skipped: int) -> None:
    """Emit ONE aggregate finding when a batch skipped affiliate-click rows with
    unparseable timestamps. No-op when nothing was skipped."""
    if not skipped:
        return
    emit_finding(
        source="sync_affiliate_clicks",
        kind="affiliate_click_parse_skipped",
        title=f"{skipped} affiliate-click row(s) skipped — unparseable timestamp",
        body=(
            f"sync_affiliate_clicks skipped {skipped} row(s) this batch because "
            "created_at did not match the expected format. A systematic skip means "
            "the click-beacon timestamp format drifted and click counts are "
            "under-reporting — check the CF AE SQL response shape."
        ),
        severity="info",
        dedup_key="affiliate_click_parse_skipped",
        extra={"skipped": skipped},
    )
```

- [ ] **Step 3d: affiliate — wire the counter**

Initialise alongside `inserted = 0` (currently line 208):

```python
            inserted = 0
            skipped_bad_ts = 0
            max_ts: datetime | None = None
```

Increment at the skip site (inner `except Exception:` currently at lines 223-228):

```python
                        except Exception:
                            try:
                                ts = _parse_iso(c["created_at_raw"])
                            except Exception:
                                skipped_bad_ts += 1
                                logger.debug(
                                    "[SYNC_AFFILIATE] skipping row with bad timestamp %r",
                                    c["created_at_raw"],
                                )
                                continue
```

Emit after the loop, immediately before `return JobResult(ok=True,` (currently line 268):

```python
            if max_ts is not None:
                await self._update_high_water_mark(pool, max_ts.isoformat())

            _emit_bad_timestamp_finding(skipped_bad_ts)

            return JobResult(
```

- [ ] **Step 4: Run both tests to verify they pass**

Run:

```
PYTEST "<worktree>\src\cofounder_agent\tests\unit\services\jobs\test_sync_cloudflare_analytics.py::test_emit_bad_timestamp_finding_aggregates" "<worktree>\src\cofounder_agent\tests\unit\services\jobs\test_sync_affiliate_clicks.py::test_emit_bad_timestamp_finding_aggregates" -v
```

Expected: PASS.

- [ ] **Step 5: Re-baseline and verify green**

Run (from worktree root):

```bash
python scripts/ci/lint_silent_excepts.py --update-baseline
python scripts/ci/lint_silent_excepts.py
```

Expected: `clean`. Both loop `except` bodies now contain `skipped_bad_ts += 1` (an assignment — a non-low-visibility statement), so both handlers read as visible and drop out of the baseline; the real visibility is the post-loop aggregate finding.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/jobs/sync_cloudflare_analytics.py src/cofounder_agent/services/jobs/sync_affiliate_clicks.py src/cofounder_agent/tests/unit/services/jobs/test_sync_cloudflare_analytics.py src/cofounder_agent/tests/unit/services/jobs/test_sync_affiliate_clicks.py scripts/ci/silent_excepts_baseline.json
git commit -m "feat(analytics): aggregate finding when sync jobs skip unparseable-timestamp rows"
```

---

### Task 4: Exemplar — brain notify-env hydration (warning, no finding)

The brain tree can't `emit_finding`; escalate the per-key hydration swallow from `debug` to `warning` so a failed secret read is visible in Loki/GlitchTip.

**Files:**

- Modify: `brain/brain_daemon.py:960-965` (inside `_hydrate_notify_env_from_settings`)
- Test: `src/cofounder_agent/tests/unit/brain/test_brain_daemon_silent_failures.py` (**append** — this file already sets up the `brain` sys.path prelude and the `bd` / `_LOGGER = "brain"` caplog pattern; reuse them)

**Interfaces:**

- Consumes: the visible-warning rule from Task 1.
- Produces: nothing downstream; the per-key loop still `continue`s past a failed read.

- [ ] **Step 1: Write the failing test**

Append a new class to `src/cofounder_agent/tests/unit/brain/test_brain_daemon_silent_failures.py` (the module already imports `logging`, `pytest`, and `from brain import brain_daemon as bd`, and defines `_LOGGER = "brain"`):

```python
@pytest.mark.unit
@pytest.mark.asyncio
class TestNotifyEnvHydrationVisibility:
    """A failed per-key secret read during notify-env hydration must be visible
    at WARNING — the brain tree can't emit_finding, so warning is the bar."""

    async def test_hydrate_skip_logs_warning_on_read_failure(self, caplog, monkeypatch):
        async def _boom(pool, key, default=""):
            raise RuntimeError("secret backend down")

        # The function does `from brain.secret_reader import read_app_setting`
        # at call time, so patching the module attribute intercepts it.
        monkeypatch.setattr("brain.secret_reader.read_app_setting", _boom)

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            # object() is a truthy sentinel pool; read_app_setting is stubbed.
            await bd._hydrate_notify_env_from_settings(pool=object())

        skips = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "notify-env hydrate skipped" in r.getMessage()
        ]
        assert skips, "a failed per-key secret read must be visible at WARNING"
        assert "secret backend down" in " ".join(r.getMessage() for r in skips)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTEST "<worktree>\src\cofounder_agent\tests\unit\brain\test_brain_daemon_silent_failures.py::TestNotifyEnvHydrationVisibility" -v`
Expected: FAIL (the message is currently emitted at `DEBUG`, so no WARNING record matches).

- [ ] **Step 3: Escalate debug → warning**

In `brain/brain_daemon.py`, in `_hydrate_notify_env_from_settings`, change the per-key handler (currently lines 960-965):

```python
        except Exception as e:  # noqa: BLE001 — hydration is best-effort
            logger.warning(
                "[BRAIN] notify-env hydrate skipped %s: %s",
                setting_key, e,
            )
            continue
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTEST "<worktree>\src\cofounder_agent\tests\unit\brain\test_brain_daemon_silent_failures.py::TestNotifyEnvHydrationVisibility" -v`
Expected: PASS.

- [ ] **Step 5: Re-baseline and verify green**

Run (from worktree root):

```bash
python scripts/ci/lint_silent_excepts.py --update-baseline
python scripts/ci/lint_silent_excepts.py
```

Expected: `clean`. The `brain/brain_daemon.py` count drops by 1.

- [ ] **Step 6: Commit**

```bash
git add brain/brain_daemon.py src/cofounder_agent/tests/unit/brain/test_brain_daemon_silent_failures.py scripts/ci/silent_excepts_baseline.json
git commit -m "feat(brain): warn (not debug) when notify-env hydration skips a key"
```

---

### Task 5: Final verification + push

- [ ] **Step 1: Run the full affected test set**

Run:

```
PYTEST "<worktree>\src\cofounder_agent\tests\unit\scripts\test_lint_silent_excepts.py" "<worktree>\src\cofounder_agent\tests\unit\services\test_r2_upload_service.py" "<worktree>\src\cofounder_agent\tests\unit\services\jobs\test_sync_cloudflare_analytics.py" "<worktree>\src\cofounder_agent\tests\unit\services\jobs\test_sync_affiliate_clicks.py" "<worktree>\src\cofounder_agent\tests\unit\brain\test_brain_daemon_silent_failures.py" -q -o addopts=""
```

Expected: all PASS.

- [ ] **Step 2: Final ratchet check**

Run: `python scripts/ci/lint_silent_excepts.py`
Expected: `clean — no new silent handlers`.

- [ ] **Step 3: Push**

```bash
git push
```

The draft PR #2372 updates automatically. Mark it ready for review once CI is green.

---

## Notes / deferred

- **Burn-down (out of scope, follow-up PRs):** the remaining ~72 gap sites + the 287 pre-existing baselined handlers convert to `emit_finding`/`warning+` over time; the ratchet guarantees no regression meanwhile. Candidates called out in the spec but not converted here: `post_pipeline_actions.py:558` (preview-QA skipped — heavy to unit-test), `services/tasks_db.py:900`, `modules/content/multi_model_qa.py:2340/2509`.
- **No settings changes:** best-effort findings emit at `severity="info"`, which the router's fetch floor (`_ROUTABLE_SEVERITIES`) never selects — dashboard-visible, never routed, never paged. No `findings.<kind>` policy is seeded (an inert `info` policy would only trip the zero-reader-settings probe). To graduate a kind to a page later, emit it at `warn`+ and add a `findings.<kind>.delivery` policy then.
- **`findings.default.*` discrepancy (informational, unrelated):** `settings_defaults.py` seeds `findings.default.*` but `findings_alert_router._load_policies` skips `kind == "default"`. The seeds are inert. Not fixed here; noted for a future findings-router cleanup.
