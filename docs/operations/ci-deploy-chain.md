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
Glad-Labs/glad-labs-stack (private GitHub — source of truth)
    │
    ├─→ GitHub Actions (.github/workflows/*)
    │       backend pytest + frontend tests + lint + migrations smoke
    │       gitleaks, Trivy, syft+grype on every push
    │
    ├─→ Vercel (auto-deploy on push to main)
    │       │
    │       └─→ www.gladlabs.io
    │
    └─→ sync-to-public-poindexter.yml (auto, on every push to main)
            │
            └─→ GitHub public (Glad-Labs/poindexter)
                    filtered subset (no operator overlay, no CLAUDE.md,
                    no premium dashboards). Force-pushed each sync — the
                    mirror is rebuilt from scratch, ~30s end-to-end.
                    Public-side CI (test-backend, migrations-smoke,
                    Mintlify Deployment, link-rot) runs on the result.
                    Release Please cuts versions from this repo.
```

Vercel watches `Glad-Labs/glad-labs-stack` (the private repo),
NOT the public `poindexter` mirror. Backend + brain run locally on
Matt's PC; Vercel only handles the static/SSR frontend slice.

Gitea was the previous source of truth and was decommissioned
2026-04-30; the dual-Gitea/GitHub workflow is now history.

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

- `.github/workflows/` — GitHub Actions pipelines on the private
  source-of-truth repo (`glad-labs-stack`): backend pytest, frontend
  Jest, migrations smoke, gitleaks, Trivy, syft+grype on every push.
- `.github/workflows/sync-to-public-poindexter.yml` — auto-mirrors
  the filtered subset to `Glad-Labs/poindexter` after every push to
  main (~30s). Strips operator overlay, CLAUDE.md, premium
  dashboards, and other private files.
- `scripts/sync-to-github.sh` — local fallback for the same filter
  pipeline; useful when CI is broken or iterating on the filter.
  Reachable as `git pushe` after running
  `bash scripts/install-git-hooks.sh`.
- `.github/workflows/release-please.yml` — Release Please on the
  public poindexter repo. Versioning only.
- `src/cofounder_agent/tests/` — Python unit tests (pytest), 7,900+
  cases.
- `web/public-site/next.config.js` — has a `validateEnv` check that
  rejects localhost URLs in production. `SKIP_ENV_VALIDATION=true`
  bypasses for local dev.

## The public release repo is separate

`github.com/Glad-Labs/poindexter` is the open-source release mirror.
It is auto-rebuilt from `glad-labs-stack/main` by GitHub Actions on
every push (filter → force-push). It does NOT auto-deploy anywhere
— Vercel watches the private source-of-truth repo. Public-side CI
still gates the resulting commit (test-backend, migrations-smoke,
Mintlify Deployment, link-rot).

## If you're self-hosting Poindexter

You don't need any of this. Your deployment is:

```bash
poindexter setup --auto
bash scripts/start-stack.sh
```

CI is useful if you fork and want PR checks, but the stock setup
has no notion of "deploy." The worker container is your production.
