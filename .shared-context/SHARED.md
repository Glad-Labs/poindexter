# Shared Context — Glad Labs

This directory holds durable cross-session context for Glad Labs — identity,
key decisions, preferences, and dated historical records. Its `.md` files are
embedded into the semantic memory system by the memory tap
(`src/cofounder_agent/services/taps/memory.py`), so the content is recallable
via `search_memory`.

> **Note (retired mechanism, 2026-06):** the live session-state loop — `state/`
> snapshots auto-written by the `sync-shared-context` job, plus the "read on
> startup / write a handoff at session end" workflow — is **retired**. The job
> was deleted and the snapshots went 2+ months stale. Claude Code now uses
> `~/.claude/projects/.../memory/` for session continuity (see CLAUDE.md's
> startup section). What remains here is durable reference content that stays
> useful as embedded memory.

## Directory Layout

- `identity/` — who Matt is, how he thinks, project vision
- `decisions/` — key decisions with reasoning (the WHY)
- `feedback/` — Matt's preferences and system constraints
- `audits/`, `migrations/` — dated historical records (kept as-is)

## Conventions

- Files use markdown with YAML frontmatter for metadata.
- Identity files change rarely (only when Matt gives new guidance).
- Dated records (`audits/`, `migrations/`) are immutable history — don't edit
  them to reflect later state; add a new dated file instead.
