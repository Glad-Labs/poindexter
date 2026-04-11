# How Poindexter itself is tested and deployed

> **What this doc is.** A transparency record of how Poindexter (the
> project, not your self-host) is tested and shipped to gladlabs.io.
> Kept here so that contributors can understand why a PR fails CI,
> what gets run on every push, and how changes reach production.
>
> **What this doc isn't.** A recipe for setting up your own CI. If
> you want to run Poindexter on your own infrastructure, the only
> supported deployment is `docker compose -f docker-compose.local.yml
up -d` on a single machine. CI is a Poindexter-development concern,
> not a self-host concern.

## The flow

```
Gitea main (source of truth, local to Matt)
    │
    ├─→ Woodpecker CI (local, .woodpecker.yml)
    │       runs backend pytest + frontend tests + lint
    │       on every push to main
    │       notifies Telegram on pass/fail
    │       does NOT deploy anything
    │
    └─→ Gitea push mirror
            │
            └─→ GitHub private (Glad-Labs/glad-labs-website)
                    │
                    └─→ GitHub Actions (.github/workflows/ci.yml)
                            │
                            ├─ test job
                            │   - npm ci
                            │   - npm run test:ci (frontend Jest)
                            │   - pytest tests/unit/
                            │   - npm run lint
                            │   - npm audit
                            │
                            └─ deploy job  [needs: test]
                                    │
                                    └─→ Vercel
                                            │
                                            └─→ www.gladlabs.io
```

A separate, manually-triggered script
(`scripts/sync-to-github.sh`) pushes a filtered snapshot of the
private repo to the public open-source mirror at
`github.com/Glad-Labs/poindexter`. That sync is destructive
(force-push to `main`) and strips out private files. It does **not**
deploy anything.

## The failure cascade

The `deploy: needs: test` dependency in `ci.yml` is the most
important line in the whole chain. When the `test` job fails, the
`deploy` job is **skipped** — not retried, not failed for an
independent reason. It just never runs.

In the GitHub Actions web UI this shows up as:

- test: ❌ Failed
- deploy: ⚪ Skipped

In Telegram / Discord notifications from GitHub, it reads "CI
failed" or "deploy failed." That's technically true but misleading
— fixing the test also fixes the deploy. Vercel itself is fine.

## Debugging "Vercel is failing"

If you see a notification that Vercel deploy failed:

1. **Check GitHub Actions first, not Vercel.** Find the most recent
   workflow run on `main`. If the `test` job is red, that's the
   actual problem.
2. **Find the specific failing test.** Click into the `test` job,
   scroll to the backend or frontend test step, look for a `FAILED`
   line.
3. **Reproduce locally.** Backend:
   `docker exec poindexter-worker python -m pytest tests/unit/ -q`.
   Frontend: `cd web/public-site && npm run test:ci`.
4. **Fix the test, push, CI re-runs, Vercel unblocks automatically.**
   No Vercel intervention needed.

Only look at Vercel settings if the `test` job is green and the
`deploy` job itself is red. That's rare — Vercel build errors are
usually about `NEXT_PUBLIC_*` env vars missing in the Vercel
dashboard, or a `next.config.js` change that breaks the build.

## Local vs CI environment differences

A few tests pass in CI but fail inside the worker container because
the worker runs with `ENVIRONMENT=production` set and some
middleware evaluates that at import time. If you hit a test that's
green in CI but red against the worker, check whether the
middleware or service involved reads `os.getenv("ENVIRONMENT")` at
import time.

## Key files

- `.woodpecker.yml` — local Woodpecker pipeline, notifies Telegram
  on pass/fail. No deploy step.
- `.github/workflows/ci.yml` — GitHub Actions, the
  `deploy: needs: test` cascade.
- `src/cofounder_agent/tests/` — Python unit tests (pytest), ~4,900
  cases.
- `web/public-site/__tests__/` — frontend Jest tests.
- `web/public-site/next.config.js` — has a `validateEnv` check that
  rejects localhost URLs in production. `SKIP_ENV_VALIDATION=true`
  bypasses for local dev.
- `scripts/sync-to-github.sh` — manual one-shot public mirror push.

## The public release repo is separate

`github.com/Glad-Labs/poindexter` is the open-source release repo.
It gets a filtered snapshot of the private source via
`scripts/sync-to-github.sh`, run manually and only when a release
is intentionally ready. It does NOT auto-deploy anywhere. Vercel
watches the private mirror (`Glad-Labs/glad-labs-website`), not the
public release repo.

## If you're self-hosting Poindexter

You don't need any of this. Your deployment is:

```bash
docker compose -f docker-compose.local.yml build
docker compose -f docker-compose.local.yml up -d
```

CI is useful if you fork and want PR checks, but the stock
docker-compose setup has no notion of "deploy." The worker
container is your production.
