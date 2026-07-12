# Best-effort failure visibility — design

**Date:** 2026-07-12
**Status:** Approved (brainstorm) → pending implementation plan
**Author:** Claude (with Matt)

## Problem

`scripts/ci/lint_silent_excepts.py` is a ratchet that stops NEW silently-swallowed
exceptions from entering production code. It works — the tree is clean (287
handlers baselined, 0 regressions). But its AST matcher has **structural blind
spots**: `_handler_is_silent` bails at `if len(body) != 1`, so it only ever
inspects single-statement handlers. A companion scan (reusing the linter's own
helpers) found **76 broad handlers whose entire body is below the operator's
alerting bar but which the linter cannot see** — meaning a brand-new one in
these shapes passes CI unnoticed, and the ratchet never freezes them:

| Shape the matcher misses                           | Count | Why it's invisible                                                     |
| -------------------------------------------------- | ----- | ---------------------------------------------------------------------- |
| `logger.debug(...)` then `return None/[]/{}`       | 64    | two statements — `len(body) != 1` short-circuits                       |
| `except Exception: continue` (± a debug log)       | 10    | `continue`/`break` are neither `pass`, a log call, nor a `return`      |
| `except Exception: print(...)` (± sentinel-return) | 2     | `print` is an `ast.Name`, not the `ast.Attribute` a `.debug()` call is |

Beyond the linter gap, spot-checking the highest-risk-by-name sites (alert
dispatcher, `operator_notify`, `task_failure_alerts`, the affiliate/analytics
sync loops) found them all _defensible_ — fail-closed config reads, a dedup
check that fails **open** toward more alerting, per-row skips. So this is **not a
pile of hidden bugs**. It is a coverage-and-consistency gap, plus a perverse
incentive: a single-statement `except: logger.debug(...)` must carry a
`# silent-ok` justification, but adding `return None` under it — swallowing
_more_ — currently needs none (observed directly at `brain/alert_dispatcher.py`
1731/1745 carrying markers while 1703/1773 do not).

## Principle (the operator's ask)

> Best-effort failures should be observable — "we should still see that
> something failed or didn't pass as it should have" — **without blocking** the
> pipeline and **without spamming**. Best-effort work that doesn't complete
> should say so; it just shouldn't page or halt.

### Visibility bar

A broad exception handler is **acceptable (not a silent swallow)** when it does
at least one of:

- calls `emit_finding(...)` — surfaced on the Findings dashboard + console
  Telemetry, persisted in `audit_log`, deduped, non-paging at `info`;
- logs at `warning` / `error` / `exception` / `critical`;
- re-raises (`raise`).

A handler is a **silent swallow** when every statement is low-visibility:
`pass` / `...` / `logger.debug(...)` / `logger.info(...)` / `print(...)` (any
breadth), or — under a broad except only — `continue` / `break` / a
sentinel `return` (bare / constant / empty collection). A bare `debug`/`info`
log is explicitly **not** enough: it drowns in Loki and never reaches a surfaced
board.

`# silent-ok: <reason>` remains the escape hatch, reserved for genuine no-op
cases (e.g. the two `services/logger_config.py` `print`s that run _before_
logging is configured, where `emit_finding` has no pool and `print` is the only
channel).

## Goals / non-goals

**Goals**

- Close the linter's three blind shapes so future silent swallows in the
  idiomatic style are caught and ratcheted.
- Establish `emit_finding` (at `info` severity — non-paging by default via the
  router's severity floor) as the blessed "best-effort failed" signal, with a
  loop-aggregation rule.
- Convert a first high-signal **exemplar batch** to prove the pattern end-to-end.

**Non-goals (YAGNI)**

- No `with best_effort(...)` context manager.
- No new helper wrapper — call `emit_finding` directly (chosen for minimal surface).
- No big-bang conversion of the other ~358 baselined handlers; they burn down in
  follow-up PRs as the ratchet tightens.
- No settings or schema changes at all (see Component 3 — the `info` severity
  floor makes per-kind policy seeds unnecessary).

## Component 1 — Linter matcher (`scripts/ci/lint_silent_excepts.py`)

Replace the single-statement matcher with a whole-body rule. New/changed helpers:

```
_is_ellipsis(stmt)      -> Expr(Constant(Ellipsis))
_is_print_call(stmt)    -> Expr(Call(func=Name("print")))

_stmt_is_low_visibility(stmt, *, broad) -> bool:
    # always low-visibility, regardless of except breadth
    pass | ellipsis | _is_low_visibility_log_call (debug/info) | print   -> True
    # low-visibility ONLY under a broad except (narrow = deliberate control flow)
    broad and (continue | break | _is_sentinel_return)                    -> True
    otherwise                                                             -> False

_handler_is_silent(handler):
    broad = _is_broad_except(handler)
    return all(_stmt_is_low_visibility(s, broad=broad) for s in handler.body)
```

Properties:

- **Strict superset** of today's matcher — every currently-counted handler stays
  counted (a lone low-visibility statement satisfies "all statements
  low-visibility"), so re-baselining only adds, never removes.
- A handler containing an `emit_finding(...)` call, a `warning`+/`error`/
  `exception`/`critical` log, a `raise`, or any other real statement has a
  non-low-visibility statement → **visible**, not counted.
- `continue`/`break`/sentinel-return stay gated on broad excepts, matching the
  existing `_is_sentinel_return` breadth gate — narrow `except ValueError:
continue` remains deliberate control flow.
- `contextlib.suppress(Exception)` handling (`_is_broad_suppress`) is unchanged.

Then re-run `python scripts/ci/lint_silent_excepts.py --update-baseline`. The
~76 newly-visible sites grandfather into `silent_excepts_baseline.json`
alongside the existing 287 (minus any converted in Component 4). Ratchet
semantics are unchanged: counts may only shrink.

## Component 2 — Convention

Documented authoritatively in the linter's module docstring (next to
enforcement), with a one-line pointer from the observability docs.

- **Signal:** best-effort swallow → `emit_finding(source=__name__, kind="...",
title=..., body=..., severity="info", dedup_key="...")`. `info` is correct —
  surfaced + deduped, never pages.
- **Loops aggregate.** Never `emit_finding` per iteration (one `audit_log` row
  per row = write-amplification; `dedup_key` only dedups _delivery_, not
  emission). Count failures in the loop, emit **one** finding after the loop when
  `count > 0`, with the count in `body`/`extra`.
- **Brain tree** (`brain/`) cannot import `services.audit_log`, so it cannot call
  `emit_finding`. Brain best-effort swallows use `logger.warning(...)` (brain
  logs reach Loki + GlitchTip). The linter already treats `warning` as visible,
  so no brain-specific linter branch is needed.
- **`# silent-ok`** stays for genuine no-op cleanup only.

## Component 3 — Why `info` findings are non-paging (no seed needed)

Best-effort findings are emitted at `severity="info"`, and the router **never
fetches `info`**. `findings_alert_router._fetch_unrouted_findings` gates on
`WHERE severity = ANY(_ROUTABLE_SEVERITIES)` with
`_ROUTABLE_SEVERITIES = ("warn", "warning", "critical")` (findings_alert_router.py
L69, L395). An `info` finding is dropped one layer _before_ per-kind policy
(`_delivery_for`) is ever consulted: it is written to `audit_log` — visible on
the Findings dashboard + console Telemetry + queryable — but never routed and
never paged. **No per-kind `findings.<kind>.delivery` seed is required, and none
should be added**: an inert `info` policy would never be read and would trip the
project's zero-reader-settings probe.

> _Correction._ An earlier draft of this spec claimed an unlisted kind would
> page unless seeded `log_only`. That is true only at `warn`+ severity, where
> `_fetch_unrouted_findings` _does_ select the row and `_delivery_for` returns
> `route` for an unlisted kind. At `info` the severity floor makes it moot — the
> verification step caught the over-claim.

**Graduation path:** to make a best-effort failure page later, emit it at `warn`+.
It then clears the fetch floor, and because `_load_policies` gives an unlisted
kind `route`, it will page — add an explicit `findings.<kind>.delivery` policy in
`settings_defaults.py` at that point to pick the channel. Not needed now.

## Component 4 — Exemplar batch

Demonstrates every shape and the loop-aggregation rule. Final selection is made
in the implementation plan; candidates:

- **Simple `debug`+`return None` → `emit_finding(info)`**
  - `services/r2_upload_service.py:73` — a silent media-persistence failure is
    exactly "I'd want to know."
  - `services/post_pipeline_actions.py:558` — post-pipeline side-effect swallow.
- **`continue`-loop → aggregate count + one finding**
  - `services/jobs/sync_cloudflare_analytics.py:307` and
    `services/jobs/sync_affiliate_clicks.py:224` — bad-timestamp row skips. A
    systematic timestamp-format drift would silently undercount; an aggregate
    finding surfaces it. Directly serves the analytics-integrity and affiliate
    workstreams.
- **Brain → `warning+`**
  - one `brain/` site (candidate `brain/brain_daemon.py:960`, a `debug`+`continue`
    in the daemon loop), converted to `logger.warning`.

Each converted handler drops out of the baseline (its count decreases) in the
same PR, tightening the ratchet.

## Invariants & data flow

**Non-blocking invariant.** No converted handler may begin to raise or block.
`emit_finding` is fire-and-forget (never raises, drops silently if the audit
pool is uninitialised). The swallow + sentinel-return / `continue` behavior is
preserved exactly — only an observability side-effect is added.

**Flow.**

```
except (broad)
  └─ emit_finding(kind, severity="info", dedup_key)     # fire-and-forget
       └─ audit_log(event_type='finding')               # persisted, visible NOW
       │    └─ Findings dashboard / console Telemetry   # read audit_log directly
       └─ findings_alert_router (every 5 min)
            └─ _fetch_unrouted_findings:
                 WHERE severity IN ('warn','warning','critical')
                 → info row NOT selected → never routed, never paged
  └─ return <sentinel>                                   # unchanged, non-blocking
```

Loop variant: accumulate `skipped += 1` per bad row; after the loop,
`if skipped: emit_finding(..., extra={"skipped": skipped})`.

## Testing (`feedback_docs_and_tests_default`)

**Linter** (`tests/unit/scripts/test_lint_silent_excepts.py`, extend):

- multi-statement `debug`+`return None` under broad except → counted;
- `except Exception: continue` / `break` / `...` / `print(...)` → counted;
- narrow `except ValueError: continue` → **not** counted;
- handler containing `emit_finding(...)` → not counted;
- handler containing `logger.warning(...)` / `raise` → not counted;
- `# silent-ok` marker still exempts;
- backward-compat: every previously-counted single-statement shape still counts.

**Exemplar sites:** per converted handler, a unit test asserting (a) the finding
fires on the failure path (or `logger.warning` for brain), and (b) the function
still returns its sentinel **without raising** (non-blocking invariant). Loop
sites: N bad rows → exactly **one** finding whose count == N.

**Docs:** linter module docstring updated with the visibility bar + convention;
a convention pointer added to `utils/findings.py`'s module docstring (the
discoverable home next to `emit_finding`), including why `info` is non-paging.

## Rollout & follow-up

1. Land Components 1–4 in one PR (linter + convention docs + exemplars + tests),
   via PR per `feedback_all_changes_via_pr`.
2. Follow-up PRs burn down the remaining baselined handlers toward `emit_finding`
   / `warning+`, shrinking the baseline each time. No deadline — the ratchet
   guarantees no regression while the debt is paid down.
3. Graduate a best-effort failure to a page later by emitting it at `warn`+ and
   adding a `findings.<kind>` policy — as operational experience shows which are
   worth a routine ping.

## Open items to confirm during implementation

- **Resolved during verification:** the `info` non-paging guarantee is the
  router's fetch-time severity floor (`_ROUTABLE_SEVERITIES`, findings_alert_router.py
  L69/L395), confirmed by reading — see Component 3. No seed needed.
- Confirm brain container logs at `warning` reach Loki/GlitchTip in the current
  compose (so the brain exemplar is genuinely visible).
