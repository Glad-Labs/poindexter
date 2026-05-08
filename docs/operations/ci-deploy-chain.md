# How Poindexter itself is tested and deployed

**Last Updated:** 2026-05-08

> **What this doc is.** A transparency record of how Poindexter (the
> project, not your self-host) is tested and shipped to gladlabs.io.
> Kept here so that contributors can understand why a PR fails CI,
> what gets run on every push, and how changes reach production.
>
> **What this doc isn't.** A recipe for setting up your own CI. If
> you want to run Poindexter on your own infrastructure, the only
> supported deployment is `poindexter setup` then
> `bash scripts/start-stack.sh` on a single machine.

## The flow

```
Glad-Labs/glad-labs-stack (private GitHub, source of truth)
    │
    ├─→ GitHub Actions (.github/workflows/ci.yml)
    │       runs backend pytest + frontend tests + lint
    │       + migrations smoke + link-rot CI
    │       on every push to main
    │
    ├─→ Vercel (auto-deploy on push to main)
    │       └─→ www.gladlabs.io
    │
    └─→ sync-to-public-poindexter.yml (auto, filtered)
            │
            └─→ Glad-Labs/poindexter (public GitHub mirror)
                    │
                    ├─→ public-side CI checks
                    │       (test-backend, migrations-smoke,
                    │        Mintlify Deployment, link-rot)
                    │
                    └─→ Release Please runs for versioning only
                        (no deploy)
```

Vercel watches `Glad-Labs/glad-labs-stack` (the private origin),
NOT the public `poindexter` repo. The public repo has no deploy
workflow — Release Please is the only thing producing artifacts.

The cross-repo sync is automatic: GitHub Actions workflow
`.github/workflows/sync-to-public-poindexter.yml` runs on every push
to `origin/main` and mirrors the filtered subset to the public repo
in ~30s, using a write-enabled deploy key (private key stored as
`POINDEXTER_DEPLOY_KEY` secret on glad-labs-stack). Just
`git push origin main` and the public mirror updates itself.

`scripts/sync-to-github.sh` strips private files (web/public-site,
web/storefront, mcp-server-gladlabs, marketing, premium dashboards,
writing_samples, gladlabs-config, .shared-context, CLAUDE.md, docs/,
etc.) before pushing.

**Bypass:** include `[skip-public-sync]` in the commit message to
keep a particular commit private (in-progress branches, sensitive
WIP).

## Debugging "Vercel is failing"

If you see a notification that Vercel deploy failed:

1. **Check the Vercel dashboard** — the deploy runs directly from
   the `glad-labs-stack` repo via Vercel's GitHub integration, not
   via a GitHub Actions workflow.
2. **Common build failure:** `next.config.js` rejects localhost
   URLs in production. Set `SKIP_ENV_VALIDATION=true` in Vercel
   env vars, or ensure `NEXT_PUBLIC_API_BASE_URL` is set to a
   real URL (or left empty for static-only builds).
3. **If tests fail locally:** reproduce with
   `docker exec poindexter-worker python -m pytest tests/unit/ -q`.
   Frontend: `cd web/public-site && npm run test`.

## Local vs CI environment differences

A few tests pass in CI but fail inside the worker container because
the worker runs with `ENVIRONMENT=production` set and some
middleware evaluates that at import time. Tests that depend on the
`brain` module or `sentry-sdk` are skipped in Docker (the modules
aren't available in the worker container). See the `skipif`
decorators in `test_database_service.py` and
`test_sentry_integration.py`.

## Key files

- `.github/workflows/ci.yml` — GitHub Actions pipeline, runs backend
  pytest + frontend Jest on every push. No deploy step.
- `.github/workflows/sync-to-public-poindexter.yml` — auto-mirror
  from glad-labs-stack to poindexter on every push to main.
- `scripts/sync-to-github.sh` — filter that runs inside the sync
  workflow. Strips operator-only files before pushing the public
  subset.
- `.github/workflows/release-please.yml` — Release Please on the
  public poindexter repo. Versioning only.
- `src/cofounder_agent/tests/` — Python unit tests (pytest), 7,900+
  cases across 329 test files.
- `web/public-site/next.config.js` — has a `validateEnv` check that
  rejects localhost URLs in production. `SKIP_ENV_VALIDATION=true`
  bypasses for local dev.

## The public release repo is separate

`github.com/Glad-Labs/poindexter` is the open-source release repo.
It gets a filtered snapshot via the auto-sync workflow above. It
does NOT auto-deploy anywhere. Vercel watches the private origin
(`Glad-Labs/glad-labs-stack`), not the public mirror.

The public mirror has `allow_force_pushes: true` in its branch
protection — the mirror is rebuilt from scratch on every sync, so
force-push protection on a derived branch would just keep the mirror
permanently stale. Public-side CI (test-backend, migrations-smoke,
Mintlify Deployment, link-rot) still has to pass on the resulting
commit.

## If you're self-hosting Poindexter

You don't need any of this. Your deployment is:

```bash
poindexter setup --auto
bash scripts/start-stack.sh
```

CI is useful if you fork and want PR checks, but the stock setup
has no notion of "deploy." The worker container is your production.
