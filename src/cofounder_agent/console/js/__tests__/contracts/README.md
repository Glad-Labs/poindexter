# Console live-contract test net

Pins every `PX.api` network surface (`console/js/api.js`) so a broken live
branch fails a test **before** it ships. Closes the **mock-first live-branch
drift** class that caused 6 of the 8 bugs the 2026-07-04 console audit found (a
`live:` branch looks fine in dev because the mock returns clean data, so nothing
surfaces until the switch is flipped live).

## What runs when

- **Per PR (offline, deterministic):** `run-contracts.test.js` +
  `serviceHealth.contract.test.js` are auto-discovered by `npm run test:console`
  (the `__tests__/**/*.test.js` glob = the `console-unit` CI job). They replay
  the committed `fixtures/*.json` in a `node:vm` sandbox — **no worker, no
  network, no new CI config.**
- **Nightly (live backend):** `.github/workflows/console-contract-drift.yml` on
  the self-hosted PC runner re-records against the live worker. No schema drift →
  silent green no-op. Drift → opens `chore(console): refresh contract fixtures`;
  **green auto-merges** (Discord ping), **red holds** for a human (Telegram
  ping). No LLM / API cost.

## The manifest

`contracts.manifest.js` is the one source of truth — an array of surface rows,
also the executable endpoint map. Tiers fall out of which keys a row carries:

| Tier                | Keys                             | Asserts                                                              |
| ------------------- | -------------------------------- | -------------------------------------------------------------------- |
| 1 read pass-through | `request` (+`openapi`)           | method + path + query                                                |
| 2 write             | `request.body`                   | method + path + **body** (never recorded — a POST would mutate prod) |
| 3 read transform    | `request` + `shape` (+`openapi`) | + adapter output shape (recorded fixture) + schema                   |

Set a surface's tier by reading its `live:` branch: bare `http(...)` → tier 1;
reshapes the response → tier 3; POST/PUT/PATCH/DELETE → tier 2. The two composite
surfaces that fan out to many calls (`serviceHealth`, `gpu`) live in
`serviceHealth.contract.test.js`, not the manifest.

Cross-realm rule: assert on adapter output (`out`) with `typeof`/`Array.isArray`
only — never `deepEqual` (values carry the vm realm's prototypes). Request
captures are main-realm, so `deepEqual` on a captured body is fine.

## The OpenAPI schema layer

`openapi.snapshot.json` is a committed, pruned dump of the worker's schema
(served at **`/api/openapi.json`** — the default `/openapi.json` is disabled
here, alongside `/docs`). It is pruned to the manifest's anchored paths **plus
the transitive `$ref` closure of the component schemas those paths reference**
(≈8 schemas, not the app's ~65) — so the drift check signals only a change to a
shape the console actually depends on. `openapi` anchors attach **only where the
worker route declares a `response_model`** (budget / settings / media-approval /
scheduling — verified live); every other surface is request- and shape-only.

## Refreshing fixtures

```bash
node record-fixtures.mjs --base http://localhost:8002 --prometheus http://localhost:9091
# creds: --client-id/--client-secret or CONSOLE_CONTRACT_CLIENT_ID/_SECRET
# --force  re-records every read fixture even without schema drift
```

The **drift anchor is the schema snapshot**, compared _semantically_
(`isDeepStrictEqual`, so it survives FastAPI reordering its keys and prettier
reformatting the committed file). Fixtures re-record only when the schema drifts
(or `--force`), so a volatile `last_updated` timestamp never churns a PR. Exit
**0 = snapshot matches the live backend**, **1 = drift** (the path-level diff is
printed and the artifacts are rewritten for review). A slow/unreachable read is
warned + skipped, not fatal — the nightly never aborts on one flaky endpoint.

Provision the read-only OAuth client once:

```bash
poindexter auth register-client --name console-contract-drift --scopes "api:read" --grant-type client_credentials
```

## Nightly job secrets (on `Glad-Labs/glad-labs-stack`)

- `CONSOLE_CONTRACT_CLIENT_ID` / `CONSOLE_CONTRACT_CLIENT_SECRET` — the read-only
  OAuth client above.
- `DISCORD_OPS_WEBHOOK_URL` — benign-drift routine ping.
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — breaking-drift critical ping.
- `CONSOLE_CONTRACT_APP_ID` / `CONSOLE_CONTRACT_APP_PRIVATE_KEY` **(optional but
  required for hands-off auto-merge)** — a GitHub App whose token authors the
  refresh PR. A PR opened by the default `GITHUB_TOKEN` does **not** trigger
  `on: pull_request` CI, so the branch-protection checks `gh pr merge --auto`
  waits on would never start. Dormant until set (the workflow falls back to
  `GITHUB_TOKEN`, opening the PR + pinging, but you merge it by hand).

The job runs only on the self-hosted runner (gated on the `CI_RUNNER` repo
variable) so it can reach `localhost:8002`; it is a quiet green skip whenever the
runner is offline.

## Honest limits

- **Empty-in-prod** endpoints record an honest-empty fixture; row-level shape
  checks only fire when data exists. Rows are never fabricated (no-dummy-data).
- **PR CI is offline** — it validates fixtures against the _committed_ snapshot.
  Live-backend drift is caught by the nightly job, which keeps that snapshot
  honest.
- **Zero runtime dependency** — the schema validator is hand-rolled; `console-unit`
  stays a plain `node --test`. `ajv` may drop in behind `validateAgainstSchema`
  only if full JSON-Schema semantics are ever needed.
- **Mirror-safe** — the whole `console/` subtree (fixtures included) is stripped
  from the public poindexter mirror. Recorded settings secrets arrive pre-masked
  (`********`); the OAuth client is `api:read` only.
