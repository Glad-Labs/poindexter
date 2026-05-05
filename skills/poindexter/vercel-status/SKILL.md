---
name: vercel-status
description: Check Vercel deployment status, recent deployments, and domains for the public site via the Vercel REST API. Use when asked about the website, site status, or Vercel deployments.
---

# Vercel Status

Queries the Vercel REST API (`https://api.vercel.com`) directly — does not shell out
to the `vercel` CLI. This keeps the skill runnable from any environment that has
`curl` + `python`, without needing the CLI installed in the openclaw container.

Project and team IDs are read from `.vercel/project.json` at repo root (created by
`vercel link`). Current values:

- **projectId**: `prj_piKVl6aspWQ1MRhjyEN2KLLeeMx8`
- **orgId (team)**: `team_iKrfrxGNrHONvqCPv4fYx2Ms`
- **projectName**: `glad-labs-codebase-public-site`

Auth is via `VERCEL_TOKEN` env var, passed as `Authorization: Bearer $VERCEL_TOKEN`.
Generate at https://vercel.com/account/settings/tokens. If the token is missing the
skill fails loud — per the "no silent defaults" rule, it will not fall back to
anonymous calls.

## Usage

```bash
scripts/run.sh                    # Full overview: latest prod + recent deployments + domains
scripts/run.sh deployments [n]    # Recent deployments (default 5)
scripts/run.sh production         # Latest production deployment only
scripts/run.sh domains            # Domains attached to the project
```

## Endpoints used

- `GET /v6/deployments?projectId=...&teamId=...&limit=N` — list recent deployments
- `GET /v6/deployments?projectId=...&teamId=...&target=production&limit=1` — latest prod
- `GET /v9/projects/{projectId}/domains?teamId=...` — project domains

## Output

For each deployment: `url`, `state` (READY / BUILDING / ERROR / CANCELED), `target`
(production / preview), `createdAt`, git commit SHA if present. Domains show the
hostname and verification state.
