# Console Reliability & Trust — Design (Sub-project A)

**Date:** 2026-07-04
**Status:** Approved design, pending implementation plan
**Scope:** Operator console (`src/cofounder_agent/console/`)

## Context

The operator console is being promoted from "prototype" to Matt's **primary
operating UI**, with Grafana kept only as a fallback for deep digging. A whole-
console audit (2026-07-04) surfaced eight bugs in one afternoon of real use; the
root causes clustered into two themes:

1. **Mock-first live-branch drift** — a `pick(liveFn, mockFn)` seam where broken
   `live:` branches look fine in dev because the mock returns clean data.
2. **No failure-mode hardening** — fetches can hang (no timeout/abort),
   overlapping polls race, the two Prometheus helpers throw unguarded, ~21 poll
   `catch` blocks swallow errors to silent-stale, and the only connection
   indicator ("LIVE · 5m SYNC") is a hardcoded decorative string. When the worker
   restarted, every panel filled with `ERR_EMPTY_RESPONSE` and the operator got
   no legible "you're disconnected" signal.

The full "console as primary UI" effort is decomposed into sub-projects, each
with its own spec → plan → build cycle:

- **A. Reliability & trust** _(this spec)_ — the runtime foundation.
- **B. Live-contract test net** — wire-shape + response-schema tests across the
  36 `api.js` surfaces (would have caught 6 of the 8 audit bugs).
- **C. Time-series history** — native range-query trend panels.
- **D. Hardware / GPU / power** — native `nvidia_gpu_*` + power-sensor panels.
- **E. DB internals** — native Postgres-stats panels.

Sequence: **A → B → then C/D/E** (independent). C/D/E's native panels build on
A's polling layer, inheriting freshness/abort for free. This spec covers **A
only**.

## Goals

- No fetch can hang: every request has a timeout and is abortable.
- Overlapping polls for the same resource cannot race (stale response cannot
  clobber a fresher one).
- A **global connection banner**, driven by `/api/health`, makes a backend
  outage legible and auto-clears on recovery — replacing the hardcoded "LIVE".
- **Per-panel freshness**: each panel shows how old its data is and goes visibly
  stale when _its_ endpoint is failing, distinguishing "whole worker down" from
  "one flaky endpoint."
- Errors become **visible staleness**, never silent swallow.
- The new logic is the console's first real unit-tested runtime layer.

## Non-goals (explicitly out of scope for A)

- The 36-surface contract-test backfill → **sub-project B**.
- Native history / hardware / DB panels → **sub-projects C / D / E**.
- Removing in-browser Babel / adding a build step → separate, optional.
- Fixing the dead "Restart service" placeholder button → separate polish
  (noted as a follow-up, not part of A).

## Design

### New units

Each unit has one purpose, a defined interface, and is testable in isolation.
Non-React logic is extracted into pure functions so it can be unit-tested with
the existing Node `node:test` + `vm` harness (as `api.token.test.js` does),
without a DOM.

1. **`usePolledResource(fetchFn, { intervalMs, key })`** — React hook.
   - Runs `fetchFn` on mount and every `intervalMs`.
   - Wraps each call in a timeout (default **8000 ms**) + `AbortController`.
     Aborts the in-flight request on unmount and whenever a new cycle starts for
     the same resource (kills the stale-clobbers-fresh race).
   - Returns `{ data, lastUpdatedAt, error, stale }`.
   - **Retains the last good `data` on error** (panels hold their last values)
     while flipping `error`/`stale`.
   - Reports each success/failure to the shared `ConnectionState` (keyed by
     `key`) so the health resource can drive the global banner.
   - Depends on: the hardened `http()` (below), `ConnectionState`, and a pure
     `computeStale()` helper.

2. **`computeStale(lastUpdatedAt, intervalMs, error, now)`** — pure function.
   - `stale = error != null || (now - lastUpdatedAt) > 2 * intervalMs`.
   - Extracted so staleness math is unit-tested independent of React/timers.

3. **`ConnectionState` + `connectionReducer(state, event)`** — a module-level
   pub/sub store (a `window`-attached singleton with `subscribe()`, matching the
   console's existing no-build window-global module style; not React context,
   which would require wrapping the tree in a provider) fed by every resource's
   success/failure. The `/api/health` resource is the heartbeat.
   - `connectionReducer` is pure: `{ consecutiveHealthFailures, lastSeenAt }`.
   - `disconnected = consecutiveHealthFailures >= 3`.
   - Any successful health poll resets `consecutiveHealthFailures` to 0.
   - Pure reducer is unit-tested (fail×3 → disconnected; success → clear).

4. **`<ConnectionBanner />`** — fixed non-blocking top bar. Hidden when
   connected; when `disconnected`, shows `⚠ Backend unreachable —
reconnecting… (last seen {ago})`. Auto-clears on recovery. Does **not**
   block interaction; actions attempted while down fail via their existing
   error toast.

5. **`<Freshness ago={…} stale={…} />`** — small panel-header badge.
   - Fresh: `updated {ago}` (muted).
   - Stale: `stale {ago}` + a stale icon + reduced panel opacity.
   - **Colorblind-safe**: distinguished by text + icon + opacity, never by
     color alone (Matt is colorblind).

6. **`http()` hardening** (in `api.js`) — timeout + `AbortController` baked into
   the shared fetch path so _all_ callers benefit (mutations included — they can
   no longer hang). The two existing Prometheus instant-query helpers
   (`promScalar`, `api.js:166`, and `promVector`, `api.js:177` — the former's
   comment even claims "best-effort" but it currently throws on a failed fetch)
   get `try/catch` → return `null` / `[]` on failure instead of throwing.

### Data flow

`app.jsx`'s ~15 hand-rolled `useEffect + setInterval + fetch + catch(){}` blocks
are replaced by `usePolledResource(fetchFn, { intervalMs, key })` calls. Panels
receive `data` exactly as today, plus `stale` + `lastUpdatedAt` for their
`<Freshness>` badge. Panel internals are otherwise unchanged. The `/api/health`
resource is special only in that its outcomes drive `ConnectionState` and thus
the global banner.

### Error handling

- Fetch exceeds timeout → aborted → counts as that cycle's error → panel goes
  stale, last-good data retained. The next interval naturally retries (the poll
  cadence _is_ the retry; no extra backoff logic in A).
- Health resource fails ≥3 consecutive cycles → global banner. First success
  clears it.
- Mutations (`approve`/`reject`/etc.) keep their existing failure toasts and now
  additionally cannot hang.
- No silent swallow: an error that previously vanished into `catch(){}` now
  surfaces as visible per-panel staleness (and, if it's health, the banner).

### Testing

Node `node:test` + `vm`/fake-timers harness (extends the existing
`js/__tests__/` suite; runs under the `console-unit` CI job):

- `computeStale` — fresh vs stale by age; error forces stale regardless of age.
- `connectionReducer` — 3 consecutive health failures → `disconnected`; a
  success resets the counter and clears.
- `http()` — aborts and surfaces a typed timeout error after the configured ms;
  a 401 still triggers the existing re-mint-once path (no regression).
- Prometheus helpers — return `null`/`[]` when the underlying fetch throws,
  rather than propagating.
- Retain-on-error — a resource whose fetch throws keeps its previous `data` and
  flips `stale`.

## Acceptance criteria

1. Every polling surface in `app.jsx` reads through `usePolledResource` (or an
   explicit, listed exception).
2. Simulated worker outage: the global banner appears within ~3 health cycles
   and clears within one cycle of recovery.
3. A single failing endpoint shows that panel stale while others stay fresh; a
   full outage shows the banner + all panels stale.
4. No fetch can remain pending past the timeout (verified by a hung-fetch stub).
5. Prometheus helpers never throw when Prometheus is unreachable.
6. New unit tests green under `console-unit`; happy-path panels still render live
   data (no regression).
7. `prettier` + `eslint` clean; no `console-unit` regression.

## Decided defaults (no open questions)

- Fetch timeout: **8000 ms** (single shared constant).
- Stale threshold: **> 2× the resource's own `intervalMs`**, or any fetch error.
- Disconnected threshold: **3 consecutive `/api/health` failures**; cleared on
  first success.
- Banner is **non-blocking**; actions during an outage fail via existing toasts.
- Poll intervals: **unchanged** from today's per-surface cadences.
