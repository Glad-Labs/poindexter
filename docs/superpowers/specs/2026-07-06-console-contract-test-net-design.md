# Console Live-Contract Test Net — Design (Sub-project B)

**Date:** 2026-07-06
**Status:** Approved design, pending implementation plan
**Scope:** Operator console API adapter (`src/cofounder_agent/console/js/api.js`) + its test harness (`src/cofounder_agent/console/js/__tests__/`)
**Parent:** [2026-07-04-console-reliability-design.md](2026-07-04-console-reliability-design.md) (sub-project A — shipped as #2141 + #2154)

## Context

The console's API layer is a single adapter, `PX.api`, where every surface is a
`pick(liveFn, mockFn)` seam: in mock mode the surface returns clean pre-shaped
data; in live mode it hits the worker and (often) reshapes the response. The
2026-07-04 whole-console audit found eight bugs in an afternoon; the dominant
root cause was **mock-first live-branch drift** — a broken `live:` branch looks
fine in dev because the mock returns clean data, so nothing surfaces until the
switch is flipped live. **Six of the eight audit bugs were exactly this class**
(e.g. the logs level filter sent lowercase where Loki labels are UPPERCASE; a
reject POSTed the approve route's body field; response adapters mapped the wrong
envelope field). The remaining two were panel-render bugs, out of scope here.

Sub-project A (this spec's parent) hardened the console's _runtime_ — timeouts,
abort, freshness, a connection banner. It did **not** test the adapter layer;
that 36-surface backfill was explicitly deferred to **sub-project B** (this
spec). B closes the drift class by making each live branch's request contract
and response adapter an assertion, so the bug that "looks fine on mock" fails a
test before it ships.

### Decisions settled during brainstorming (2026-07-06)

1. **Layered purpose.** An always-on **offline regression guard** (does an edit
   to `api.js` silently break a live branch?) **plus** an **OpenAPI drift check**
   (did the backend change a shape the console still expects?), where the
   backend declares a `response_model`.
2. **Coverage: all 36 surfaces, tiered depth.** Every network surface is pinned;
   depth scales with how much the surface actually does.
3. **Structure: manifest-driven.** One table of surfaces feeds a generic runner,
   the recording script, and the schema validator — and doubles as an
   executable, always-current endpoint map (today a hand-maintained comment atop
   `api.js`).
4. **Fixtures are recorded from the real worker, never fabricated** (real values
   or empty, never placeholder — the project's no-dummy-data rule). The OpenAPI
   drift check validates those recorded fixtures against a committed schema
   snapshot.

## Goals

- Every live branch's **request contract** (method, path, query-shaping, body)
  is asserted — a wrong path/param-case/body-field fails a test.
- Every live branch that **transforms a response** has its adapter output shape
  asserted against a **recorded real fixture**.
- Where the backend declares a `response_model`, the recorded fixture is
  **validated against a committed OpenAPI snapshot**, so backend drift is
  detectable.
- **Backend drift surfaces itself.** A nightly scheduled job re-records against
  the live worker; benign drift **auto-merges** a refresh PR (Discord note) and
  only a breaking change holds for a human (Telegram) — detection and the benign
  case are automated, not a chore to remember.
- The per-PR net runs **fully offline and deterministically** in the existing
  `console-unit` CI job — no worker, no network, no new PR-CI configuration.
- The surface list becomes a **single legible manifest** that doubles as the
  endpoint map and the input to the recording + validation tooling.

## Non-goals

- **Testing the backend.** Route behavior is `test-backend`'s job; B tests the
  console's side of the contract only.
- **A live-worker PR gate.** Per-PR CI stays offline and deterministic; the only
  live-backend touch is the nightly drift workflow (a scheduled job on the
  self-hosted runner, never a PR gate).
- **Back-filling `response_model`** onto endpoints that lack one. Those surfaces
  get request + recorded-shape coverage but no schema layer (a possible later
  widening, tracked as a follow-up, not part of B).
- **Full JSON-Schema semantics** (`oneOf`/`allOf`/`format`/`patternProperties`).
  FastAPI's response models here are plain object/array/primitive shapes;
  structural conformance is enough. `ajv` can drop in behind the validator seam
  later if that changes.
- **Panel-render tests.** The two non-contract audit bugs (e.g. Sparkline SVG on
  empty data) are component bugs, addressed elsewhere.

## Design

All new files live under
`src/cofounder_agent/console/js/__tests__/contracts/`.

### Components

| File                                           | Responsibility                                                                                                                                                                                                                                                         |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `contracts.manifest.js`                        | The one source of truth — an array of 36 surface descriptors. Dual-mode (`module.exports` for Node). Doubles as the executable endpoint map.                                                                                                                           |
| `contract-runtime.js`                          | Shared helpers: `loadApiWithRecorder(fetchImpl)` (the `api.prom.test.js` vm-sandbox pattern, but `fetch` records each call and replays a fixture), `assertRequest(capture, expected)`, `validateAgainstSchema(value, snapshot, ref)`. Dual-mode.                       |
| `run-contracts.test.js`                        | The generic `node:test` runner. Iterates the manifest, emits one named subtest per surface. Auto-discovered by `npm run test:console` — **zero CI config change**.                                                                                                     |
| `fixtures/*.json`                              | Recorded real responses, one per read surface.                                                                                                                                                                                                                         |
| `openapi.snapshot.json`                        | Pruned, committed snapshot of the worker's `/openapi.json` (only the console's paths + their `components/schemas`).                                                                                                                                                    |
| `record-fixtures.mjs`                          | Refresh tool (needs a worker + token). Re-records read fixtures, re-dumps the pruned OpenAPI snapshot, prints the old-vs-new drift diff, and exits **0 = no drift / 1 = drift**. Run manually on demand, and by the nightly workflow. Never gates a PR.                |
| `.github/workflows/console-contract-drift.yml` | Scheduled (nightly) + manual `workflow_dispatch` drift job on the self-hosted runner. Runs `record-fixtures.mjs` against the live worker; on drift, opens a refresh PR (**auto-merges when green**) + pings (Discord benign / Telegram breaking). No LLM, no API cost. |
| `README.md`                                    | When/how to refresh; the tiering rules; the empty-in-prod caveat; the nightly job + its secrets.                                                                                                                                                                       |

### The manifest row

One row per surface. Tiered depth falls out of which optional keys a row carries:

```js
// TIER 1 — read pass-through: request-contract only
{ name: 'seo', invoke: (api) => api.seo(), request: { method: 'GET', path: '/api/seo' } },

// TIER 3 — read transform: request + response-adapter shape + schema anchor
{
  name: 'mediaQueue',
  invoke: (api) => api.mediaQueue(),
  request: { method: 'GET', path: '/api/media-approval/pending' },
  fixture: 'media-approval-pending.json',           // recorded real response
  shape: (out) => {                                  // adapter output contract
    assert.ok(Array.isArray(out.queue));
    assert.equal(typeof out.gate2Pending, 'number');
    out.queue.forEach((r) => assert.equal(typeof r.post_id, 'string'));
  },
  openapi: { path: '/api/media-approval/pending', method: 'get' },
},

// TIER 2 — write: request-contract ONLY (recording a POST would mutate prod)
{
  name: 'mediaReject',
  invoke: (api) => api.mediaDecide('post_1', 'video', false),
  request: {
    method: 'POST',
    path: '/api/media-approval/post_1/video/decide',
    body: { approved: false, notes: null },          // catches the reject-wrong-field class
  },
},

// PROMETHEUS — assert the PromQL + the vector-mapping shape
{
  name: 'promVector',
  invoke: (api) => api.promVector('up'),
  request: { host: 'prometheus', query: 'up' },
  fixture: 'prom-up.json',
  shape: (out) => { assert.ok(Array.isArray(out)); out.forEach((s) => assert.ok('labels' in s)); },
},
```

Row fields: `name` (required, the subtest label + `PX.api` method), `invoke`
(required, calls the method with encoded args), `request` (required, the
expected outgoing call), `fixture`/`shape`/`openapi` (optional, present per
tier).

**Composite surfaces.** A few surfaces fan out to more than one endpoint —
`serviceHealth` issues several cAdvisor Prometheus queries **and** hits
`/api/health`, then merges. For these, `request` is an **array** of expected
calls and `assertRequest` matches each against the captures (order-independent).
If a surface is complex enough that a manifest row obscures more than it
documents (`serviceHealth`'s cAdvisor label parsing is the likely case), it may
instead keep a **bespoke hand-written test** alongside the manifest — the
manifest covers the uniform surfaces, bespoke tests (like today's
`api.approvals.test.js`) cover the genuinely irregular few. The plan decides
per-surface after reading the `live:` branch.

### Offline data flow (per surface, in CI)

1. The runner builds a **recorder** `fetch`: it answers a `/token` POST with a
   fake JWT (`{ access_token, expires_in }`) so `getToken()` proceeds; for any
   other URL it pushes `{ method, url, body: opts.body ? JSON.parse(opts.body) : undefined }`
   onto a capture list and replays the row's fixture (or `{}`) as a 200.
2. `loadApiWithRecorder(fetch)` evaluates `api.js` in a `node:vm` sandbox with
   `PX_API_LIVE = true` and preset client creds in the fake `localStorage` (so
   the token mint runs). Returns `{ api, captures }`.
3. `await row.invoke(api)` runs the live branch end-to-end.
4. **Request assertion** — the non-`/token` capture(s) are checked against
   `row.request` (a single expected call, or an array for composite surfaces):
   method + path always; `body` deep-equal for writes; `query` (the decoded
   PromQL / query string) for Prometheus + query-carrying reads.
5. **Shape assertion** — if `row.shape`, run it on the adapter output.
6. **Schema assertion** — if `row.openapi`, `validateAgainstSchema(fixture,
snapshot, row.openapi)`.

Every assertion message is prefixed with `row.name`, e.g.
`[mediaQueue] request path mismatch: expected /api/media-approval/pending, got …/pendign`
— recovering the readability the manifest indirection would otherwise cost.

### Recording / refresh ritual (`record-fixtures.mjs`)

Run manually (or from the self-hosted runner), never in CI:

```
node record-fixtures.mjs --base http://localhost:8002 --client-id … --client-secret …
```

- Mints a token, **GET**s each read surface's path (Prometheus rows hit
  `--prometheus`), writes `fixtures/<name>.json` from the real response.
- Dumps `/openapi.json`, prunes `.paths` to the manifest's paths (keeping
  referenced `components/schemas`), writes `openapi.snapshot.json`.
- **Diffs the new snapshot against the committed one** and prints
  `+added / −removed / ~changed` response schemas — the point where "backend
  moved under us" becomes visible. The operator reviews the git diff of
  `fixtures/` + the snapshot, fixes any adapter/shape a real change broke, and
  commits the refreshed artifacts.

Writes are never invoked here (a real `POST …/decide` would approve content), so
write surfaces carry no fixture — request-contract only.

### Schema validator (`validateAgainstSchema`) — dependency-free

Locates `snapshot.paths[path][method].responses['200'].content['application/json'].schema`,
resolves local `$ref`s (`#/components/schemas/X`) into `snapshot.components.schemas`,
and structurally checks:

- `type: object` → every `required` key is present; recurse into each present
  `properties[k]`.
- `type: array` → recurse each element against `items`.
- primitives → `typeof` matches (`integer`/`number` → `number`, `string` →
  `string`, `boolean` → `boolean`; `nullable`/`null` tolerated).
- **Extra fields in the response are allowed** — additive backend changes must
  not break the console.

No `response_model` for an endpoint → the check is a no-op (tier-2 schema layer
applies only where a schema exists). ~50–60 lines, no dependency, which keeps
the console subtree **zero-dependency** (a stated architectural value of the
no-build console) and keeps `console-unit` a plain `node --test` with no
`npm ci`. If full JSON-Schema semantics are ever needed, `ajv` drops in behind
this same `validateAgainstSchema` seam as a devDependency.

### Tiered coverage across the ~36 surfaces

The manifest enumerates every network surface of `PX.api`. Local config setters
(`setLive`, `setClient`, `setBase`, `setPrometheus`, `setSim`, `getSim`,
`setScope`, `grafanaBase`, `setGrafanaEmbed`) touch no network and are excluded.

| Tier                      | What it asserts                                                                     | Surfaces (representative)                                                                                                                                                                                                           |
| ------------------------- | ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1 — read pass-through** | request contract (method + path + query)                                            | `seo`, `findings`, `logs`, `traces`, `newsletter`, `schedule`, `listTasks`, `getTask`, `listApprovals`, `listSettings`, `listTopicProposals`, `pipelineEvents`, `voiceJoinUrl`, `posts`, `analyticsViews`, `budget`, `memorySearch` |
| **2 — write / mutation**  | request contract incl. **body**                                                     | `approve`, `reject`, `publishTask`, `retryTask`, `killTask`, `updateSetting`, `rankTopicBatch`, `resolveTopicBatch`, `rejectTopicBatch`, `mediaDecide`, `scheduleShift`, `socialDraftAction`, `restartService`, `rebuildExport`     |
| **3 — read transform**    | request + **response-adapter shape** + **schema** (where a `response_model` exists) | `mediaQueue` (`r.items → {queue,…}`), `schedule` (`r.rows`), `serviceHealth` (cAdvisor + `/api/health` merge; composite/bespoke), `memoryStats`, `brainActivity`, `socialDrafts`, `promScalar`, `promVector`                        |

The schema layer attaches only where the **worker** declares a `response_model`.
Prometheus surfaces (`promScalar`, `promVector`) hit Prometheus's own API, not
the worker, so they get request + response-shape coverage but **no** OpenAPI
anchor.

The exact tier of each surface is set by reading its `live:` branch: a branch
that only returns `http(...)` is tier 1; one that reshapes the response is
tier 3; a `POST`/`PATCH`/`DELETE`/`PUT` is tier 2.

### CI wiring

**Per-PR path: none.** `contracts/run-contracts.test.js` is auto-discovered by
the existing `npm run test:console` glob
(`node --test "…/console/js/__tests__/**/*.test.js"`), which is the
`console-unit` GitHub Actions job. Committed fixtures + snapshot make the run
offline and deterministic — no new PR-CI configuration.

**One new scheduled workflow** — `console-contract-drift.yml` (see [Automated
drift detection](#automated-drift-detection-nightly)) — is the only added CI
config, and it runs on a cron/dispatch trigger on the self-hosted runner, never
on pull requests.

### Known limits (honest)

- **Empty-in-prod endpoints** (e.g. `media-approval/pending` when nothing is
  pending) record an empty fixture, so tier-3 row-level checks have no rows to
  inspect — they assert the **envelope** (`Array.isArray(out.queue)`,
  `typeof out.gate2Pending === 'number'`) and cover rows only when data exists.
  Rows are never fabricated to pad the fixture (no-dummy-data); the refresh
  ritual captures real rows whenever they are present.
- **PR CI is offline** — it validates fixtures against the _committed_ snapshot,
  so it can't itself see the live backend. Live-backend drift is caught instead
  by the nightly automated job below, which keeps that snapshot honest. (The
  main CI path never depends on a running worker.)

### Automated drift detection (nightly)

The OpenAPI drift check is only as fresh as the committed snapshot, so B closes
the loop with a **scheduled GitHub Actions workflow** — the manual refresh
ritual exists for on-demand use, but detection is automated, not a chore
someone has to remember.

**`.github/workflows/console-contract-drift.yml`** — `on: schedule` (nightly
cron) + `workflow_dispatch` (manual trigger), `runs-on: self-hosted` (the PC
runner, which can reach the worker at `localhost:8002`; base is a workflow
input so a containerized runner can use `host.docker.internal`). It is a plain
Node + git + `gh` workflow with **no LLM and no Anthropic-API cost** — distinct
from the disabled Claude-Code scheduled agents. Steps:

1. Run `record-fixtures.mjs` against the live worker (read-only OAuth client;
   creds from GitHub Actions secrets, least-privilege `api:read`). It re-records
   read fixtures + the pruned `openapi.snapshot.json` and exits **0 = no drift**
   / **1 = drift** (the diff is printed to the job log).
2. **No drift → done** (the nightly is a silent no-op on a stable backend).
3. **Drift → run the contract tests against the freshly-recorded fixtures**,
   then open (or update) a `chore(console): refresh contract fixtures` PR with
   the refreshed artifacts, and notify:
   - **Benign/additive drift** (contract tests still **green** against the fresh
     fixtures) → the PR **auto-merges once its required checks pass**
     (`gh pr merge --auto --squash`, gated by the same branch-protection checks
     as any PR); a **Discord** ping fires (routine — the refresh already landed,
     and the recorded-shape delta is preserved in the merged PR for after-the-fact
     review). The steady state is zero-touch.
   - **Breaking drift** (contract tests **red** against the fresh fixtures — the
     backend changed a shape the console still assumes) → the PR carries the red
     CI, and a **Telegram** ping fires (critical: the live console contract is
     broken and an adapter needs updating).

This makes the layered design fully autonomous: the offline regression guard
gates every PR, benign backend drift self-heals nightly (auto-merged refresh +
Discord note), and only a **breaking** change stops for a human (held PR +
Telegram) — no manual step in the steady state.

## Acceptance criteria

1. `contracts.manifest.js` enumerates every network surface of `PX.api`; local
   config setters are excluded; each row states its `invoke` + `request`.
2. `run-contracts.test.js` emits one passing named subtest per surface under
   `npm run test:console`, offline, with no new CI configuration.
3. Each tier-2 (write) row asserts the request **body**; a deliberately wrong
   body field (the reject-field regression) makes its test fail.
4. Each tier-3 (transform) row asserts the adapter output shape against a
   **recorded** fixture; where a `response_model` exists, the fixture also passes
   `validateAgainstSchema` against the committed snapshot.
5. `record-fixtures.mjs` regenerates all read fixtures + the pruned
   `openapi.snapshot.json` from a live worker and prints a schema drift-diff.
6. The whole `contracts/` subtree adds **no runtime dependency**; `console-unit`
   remains a plain `node --test`.
7. Fixtures contain only real recorded values or honest-empty payloads — no
   fabricated rows.
8. `record-fixtures.mjs` exits **0** when the live backend matches the committed
   snapshot and **1** when it drifts, printing the diff either way.
9. `console-contract-drift.yml` runs on a nightly schedule + manual dispatch on
   the self-hosted runner; on drift it opens a `chore(console): refresh contract
fixtures` PR. When the refreshed fixtures still **pass** the contract tests
   the PR **auto-merges once required checks pass** and pings **Discord**; when
   they **fail** the PR is held and **Telegram** fires. It authenticates with a
   read-only OAuth client and adds no Anthropic-API cost.

## Decisions log

- **Offline core + OpenAPI drift check (layered), not live-only or fixtures-only.**
  Live-only can't gate the worker-less `console-unit` CI job; fixtures-only can't
  see backend drift. Layered gets a deterministic gate plus a real truth anchor.
- **All 36 tiered, not a high-value subset.** A complete, executable endpoint map
  has no "why isn't this covered?" gaps and pins path/method typos everywhere;
  tiering keeps the thin surfaces to one line each.
- **Manifest-driven, not per-surface files.** 36× hand-written test blocks are
  boilerplate and leave the endpoint map implicit; one manifest is DRY, is shared
  by the runner + recorder + validator, and is itself the map.
- **Recorded fixtures, not hand-authored.** No-dummy-data: the shapes under test
  are captured from the real backend; the schema check keeps them honest.
- **Dep-free structural validator, not `ajv` (for now).** Keeps the console
  subtree zero-dependency and `console-unit` a plain `node --test`; FastAPI's
  response models are simple enough that structural conformance suffices. `ajv`
  drops in behind the same seam if richer semantics are ever needed.
- **Nightly drift detection is core, not an optional follow-up.** A committed
  snapshot goes stale silently, which would neuter the whole drift-check layer;
  a scheduled self-hosted job that re-records and opens a PR (with a
  severity-tiered ping) makes the layered design autonomous. It's a plain
  Node + git + `gh` workflow — no LLM, no Anthropic-API cost — kept off the
  PR-CI path so per-PR runs stay offline and deterministic.
- **Benign refresh PRs auto-merge (operator directive).** A green fixture
  refresh (backend added a field / new endpoint, console still passes) merges
  itself once required checks pass — the recorded delta stays in merged-PR
  history for after-the-fact review. Only a **breaking** refresh (console
  contract tests now fail) holds for a human + Telegram, so nothing silently
  ships a broken contract while the steady state stays zero-touch.
