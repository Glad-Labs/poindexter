---
name: sprint-status
description: Show the current sprint status, open issues, and milestones from the primary Gitea repo. Use when the user says "what's in the sprint", "show me open issues", "sprint status", "what are we working on", or similar.
---

# Sprint Status

Queries the self-hosted Gitea instance at `http://localhost:3001` for issues and
milestones on the primary repo. Gitea is the source of truth for Glad Labs tracking
(per `reference_gitea.md` and the `integration/gitea_*` app_settings). GitHub is a
private mirror only — `Glad-Labs/glad-labs-codebase` on github.com does not exist
and `Glad-Labs/glad-labs-website` on GitHub is just a deployment mirror.

The repo defaults to the `integration/gitea_repo` app_setting (currently
`gladlabs/glad-labs-codebase`) but can be overridden per-call via `GITEA_REPO` env.

## Usage

```bash
scripts/run.sh                    # Full overview (milestones + open issues + recently closed)
scripts/run.sh issues             # Open issues only
scripts/run.sh milestones         # All milestones
scripts/run.sh recent             # Issues closed in the last 7 days
```

## Auth

Gitea requires a token for most issue reads in the current config. Set
`GITEA_TOKEN` in your shell environment or in a local .env — the skill passes it
as `Authorization: token $GITEA_TOKEN`. If unset, the skill hits the endpoints
unauthenticated and will likely return 401 or an empty list.

## Output

For each issue: `#number | title | [milestone] | state`. Milestones show open vs
closed counts and due date. The skill does not touch comments — it's a quick sprint
overview, not a full issue view.
