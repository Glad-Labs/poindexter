# Claude Code permission patterns — what wildcards actually match

The `~/.claude/settings.json` `permissions.allow` list controls which
tools a non-interactive Claude session can call without prompting. The
glob syntax looks deceptively shell-like — but its semantics differ in
one critical way that has burned the voice agent twice. This doc
captures the rule and the working configuration.

## TL;DR

**`*` does NOT cross `__` boundaries.** Use one wildcard entry per MCP
server (and one per tool family for Bash etc.) — never a single
broader catch-all.

## The gotcha

Confirmed empirically 2026-05-08 inside the `voice-agent-livekit`
container while debugging "Permission denied — check_health" errors:

| Pattern in `permissions.allow`            | What it actually matches                                               |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| `mcp__*`                                  | **nothing** — not even local stdio MCP tools                           |
| `mcp__claude_ai_*`                        | **nothing** — does NOT match `mcp__claude_ai_Poindexter__check_health` |
| `mcp__claude_ai_Poindexter__*`            | ✅ all Poindexter MCP tools                                            |
| `mcp__claude_ai_Poindexter__check_health` | ✅ exact match                                                         |

The reason: `__` is the structural separator in the canonical MCP tool
name `mcp__<server>__<tool>`. Claude Code's permission engine treats
that separator as opaque punctuation that `*` is not allowed to span.
A trailing `*` only consumes characters until the next `__`.

This is consistent with how `Bash(git *)` works — the `*` operates
inside the parenthesized argument scope, not across the tool name.
Treat `__` like the `(` boundary: a structural marker, not a
character `*` can eat.

## Working configuration

The current allowlist in `~/.claude/settings.json` (Matt's host
session and the bind-mounted voice-agent container both read from
this file):

```json
{
  "permissions": {
    "defaultMode": "dontAsk",
    "allow": [
      "mcp__poindexter__*",
      "mcp__gladlabs__*",
      "mcp__claude_ai_Poindexter__*",
      "mcp__claude_ai_Notion__*",
      "mcp__claude_ai_Sentry__*",
      "mcp__claude_ai_Cloudinary__*",
      "mcp__claude_ai_Vercel__*",
      "mcp__claude_ai_Mermaid_Chart__*",
      "mcp__claude_ai_Cloudflare_Developer_Platform__*",
      "mcp__claude_ai_Hugging_Face__*",
      "mcp__claude_ai_Google_Drive__*",
      "mcp__claude_ai_Google_Calendar__*",
      "mcp__claude_ai_Gmail__*",
      "mcp__claude_ai_Stripe__*",
      "Read",
      "Glob",
      "Grep",
      "Bash",
      "Edit",
      "Write",
      "WebFetch",
      "WebSearch",
      "Skill",
      "Task",
      "TodoWrite"
    ]
  }
}
```

One entry per MCP server, plus a non-MCP allowlist for the built-in
tools. Adding a new MCP server means appending a new
`mcp__<server>__*` line — there is no shortcut that covers all of
them at once.

## How this fails in practice

The failure mode is silent: the tool simply isn't on the allowlist,
so the engine prompts for confirmation. In a voice-agent or scheduled
session there is no human to confirm, so the call denies and the
agent tells the user "Permission denied" or "Sorry, I had trouble
talking to Claude Code".

If you see permission denials for a tool you thought was wildcarded,
the first thing to check is whether your wildcard crosses a `__`. If
it does, split it into per-prefix entries.

## OAuth scope vs. permission allowlist

These are two different gates and both must pass:

1. **OAuth scope** (`oauth_clients.scopes` column) — controls which
   API routes the bearer JWT can hit. Server-side, cryptographic.
2. **Permission allowlist** (`~/.claude/settings.json`) — controls
   which tools the local Claude Code process will call without
   prompting. Client-side, policy-only.

A scope-rich JWT still gets blocked if the local allowlist doesn't
cover the matching tool name, and a permissive allowlist can't
unlock a route the JWT lacks scope for.

## See also

- [`docs/operations/oauth-grafana.md`](oauth-grafana.md) — OAuth
  scope minting (the other half of the gate)
- [Glad-Labs/poindexter#443](https://github.com/Glad-Labs/poindexter/issues/443) — original report
- `~/.claude/settings.json` — current host-side allowlist
- `docker-compose.local.yml` (`voice-agent-livekit` block) — the
  bind-mount that makes the host allowlist apply inside the
  container
