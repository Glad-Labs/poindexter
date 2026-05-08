# Poindexter is the engine — agents are the employees

> **The mental model**: Poindexter is a content-pipeline engine. Anything that talks to it — Claude Code, OpenClaw, a custom CLI, a Telegram bot, a future agent we haven't built yet — is an _employee_. Employees come and go; the engine is what you own and tend.

This doc captures the architectural framing and the boundary between the engine and its consumers. Written 2026-05-08 to make explicit a design choice that had been implicit until then.

---

## The boundary

```
                    ┌──────────────────────────────────┐
                    │         POINDEXTER (the engine)  │
                    │                                  │
                    │  • PostgreSQL (spinal cord)      │
                    │  • Brain daemon (brainstem)      │
                    │  • FastAPI worker (cerebrum)     │
                    │  • Content pipeline              │
                    │  • Plugin registry               │
                    │  • OAuth 2.1 token endpoint      │
                    │  • MCP server (HTTP + stdio)     │
                    │  • REST API                      │
                    │                                  │
                    │   "Stable contract. Owned. Glue." │
                    └──────────────────────────────────┘
                                    │
                                    │ contract: REST, MCP, OAuth JWT
                                    │
        ┌───────────────┬───────────┼───────────┬───────────────┐
        │               │           │           │               │
        ▼               ▼           ▼           ▼               ▼
   Claude Code    OpenClaw    Telegram   Discord agent   Future agent
   (development) (operator)    (alerts)   (operator)     (?)

   "Employees. Interchangeable. Each has a tenure, a job, a way of speaking."
```

The dotted line is the **product surface**: REST endpoints, MCP tools, OAuth `/token`. Anything below the line is an **employee** — a software persona that consumes Poindexter to do work.

## Why this framing matters

Before today, OpenClaw and Poindexter were de-facto coupled. They shared the host, ran similar-sounding agents, even shared a Discord bot identity (until we untangled that 2026-05-07). It was unclear whether OpenClaw was "part of" Poindexter or a sibling system.

After today's decision, the answer is: **OpenClaw is an employee that uses Poindexter**. Specifically, it's _Matt's current operator overlay_ — the way he chats with the system, approves posts, and runs ops tasks from his desktop or phone. It is private; it is not part of the open-source product; it can be replaced with a different operator overlay tomorrow without changing Poindexter at all.

Same goes for Claude Code, which is _Matt's current development surface_. When he wants to write code, he opens Claude Code and it talks to Poindexter through MCP and the local stdio bridge. If Anthropic shipped a different CLI tomorrow, or Matt switched to Aider, or a future agent like "Devin" matured enough — all of those would just be different employees consuming the same Poindexter contract.

The engine doesn't care who its employees are.

## What "owns" means

Matt owns:

- The architecture, contracts, and glue between OSS components
- The PostgreSQL schema (the canonical state)
- The plugin protocols (Taps, Probes, Jobs, Stages, Packs, LLMProviders)
- The OAuth/auth model that gates access
- The brand and the published content quality bar

Matt does _not_ own:

- LiteLLM (used as-is, replaceable)
- LangGraph (used as-is, replaceable)
- Langfuse (used as-is, replaceable)
- Pyroscope, Grafana, Prometheus (used as-is, replaceable)
- The host OS, Docker, Postgres itself
- Any specific employee (Claude Code, OpenClaw, etc.)

Goal: less moving parts on Matt's end. If a piece can be swapped for a comparable OSS or commercial offering without rewriting his code, swap-in shouldn't require Matt's involvement.

## The contract employees consume

Any Poindexter employee should be able to operate using only:

1. **REST API** at `http://localhost:8002` (or its publicly-routable equivalent via Tailscale Funnel) — full CRUD on posts, settings, audit log, decisions.
2. **MCP server** (HTTP at `:8004/mcp` or stdio via `mcp-server/server.py`) — the same surface, exposed as MCP tools for LLM-driven employees.
3. **OAuth 2.1 token endpoint** at `/token` — mint short-lived JWTs scoped to what the employee needs (`operator.read`, `operator.write`, `operator.approvals`, etc.).
4. **PostgreSQL** (advanced, optional) — direct DB access for employees that need it (the brain daemon does this; most employees should not).

Employees should NOT:

- Hardcode OAuth secrets (they go through `/token`)
- Bypass the REST/MCP surface to reach internal services
- Modify Poindexter source code to add features (use the plugin framework instead)

## How an employee gets onboarded

Same pattern, regardless of whether it's a CLI, an agent, or a one-off script:

1. Matt runs `poindexter auth migrate-<consumer>` to mint OAuth credentials. (Examples already shipping: `migrate-cli`, `migrate-mcp`, `migrate-brain`, `migrate-scripts`, `migrate-mcp-gladlabs`, `migrate-openclaw`, `mint-grafana-token`.)
2. The employee stores those credentials wherever it stores secrets.
3. The employee exchanges its client_id+secret for a JWT at `/token`, scoped appropriately.
4. The employee uses that JWT to call REST or MCP endpoints.
5. When Matt wants to retire an employee, he revokes its `oauth_clients` row. Done.

This is identical to how a human employee would be onboarded and offboarded: badge issued, badge revoked. No code changes required on either side.

## Concrete examples (today's lineup)

| Employee                                                              | What it does                                                       | Tenure                                                                                          | Auth                                         |
| --------------------------------------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **Claude Code** (terminal)                                            | Matt's primary dev interface; writes code, runs migrations, debugs | Long-term while Anthropic ships it                                                              | OAuth via stdio MCP                          |
| **Claude Code** (voice agent in Docker)                               | Same, over LiveKit voice instead of typing                         | Experimental                                                                                    | OAuth via bind-mounted host credentials      |
| **OpenClaw** (operator gateway)                                       | Matt's operator chat — Discord, approvals, ad-hoc commands         | Long-term while Matt finds it useful                                                            | OAuth via `migrate-openclaw`                 |
| **Brain daemon**                                                      | Health probes, self-healing, knowledge graph maintenance           | Permanent (it's part of the engine itself, but talks to the worker over REST like any employee) | OAuth via `migrate-brain`                    |
| **Discord ClaudeBot**                                                 | Direct Discord access to Claude Code sessions                      | Experimental                                                                                    | Telegram-style pairing via `/discord:access` |
| **Telegram bot**                                                      | Critical alerts + voice-note bridge                                | Permanent                                                                                       | Bot token in bootstrap.toml                  |
| **Scheduled Claude Sessions** (alert-triage, dependency-review, etc.) | Nightly autonomous Claude runs that touch the codebase             | As-needed                                                                                       | Inherits Matt's terminal Claude credentials  |

Every entry above can be retired independently of every other. None of them is "Poindexter" — they all _use_ Poindexter.

## Anti-patterns to avoid

- **Don't hardcode an employee's name into Poindexter core.** If `content_router_service.py` ever has `if user == "openclaw":`, that's a bug.
- **Don't let an employee write to `app_settings` without an OAuth-scoped token that covers it.** This is what the `is_secret=true` decryption path enforces today.
- **Don't ship employee-specific code in the public Poindexter repo.** Glad Labs operator overlay (OpenClaw, the storefront, premium prompts) lives in `glad-labs-stack` and is stripped from the public mirror by `scripts/sync-to-github.sh`.
- **Don't let an employee become load-bearing for non-employee work.** If Poindexter's content pipeline can't run without OpenClaw alive, the boundary has been violated.

## What this implies for development

- **New feature in Poindexter?** Lives in the engine. Bound by the OAuth scopes; exposed via REST + MCP; configurable via `app_settings`.
- **New agent or operator UI?** Lives outside the engine. Calls the engine through the contract. Has its own `oauth_clients` row.
- **Something between?** Probably a **plugin** (Tap / Probe / Job / Stage / Pack / LLMProvider) — Poindexter's extension framework lets external packages register without modifying core.

If you're not sure which category a new piece falls into, the test is: _if Matt fired this employee tomorrow, would the engine keep working?_ If yes → employee. If no → engine.

---

**Last updated:** 2026-05-08
**Decision owner:** Matt (Glad Labs LLC)
**Related:** [holistic architectural review 2026-05-07](../../.shared-context/audits/2026-05-07-holistic-architectural-review.md), [self-healing audit 2026-05-08](../../.shared-context/audits/2026-05-08-self-healing-and-backups-audit.md)
