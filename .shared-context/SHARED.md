# Shared Context — Glad Labs

This directory is shared between Claude Code and OpenClaw (Poindexter).
Both systems read and write here to maintain continuity across sessions.

## How It Works

- **Claude Code** reads this on session startup and writes handoffs/decisions at session end
- **OpenClaw** reads this on main session startup (after SOUL.md and USER.md)
- Files use markdown with YAML frontmatter for metadata
- `updated_by` field tracks which system last modified a file

## Directory Layout

- `identity/` — Who Matt is, how he thinks, project vision
- `decisions/` — Key decisions with reasoning (the WHY)
- `state/` — Current session, latest handoff, backlog
- `feedback/` — Matt's preferences and system constraints

## Rules

- Both systems can read everything
- Both systems can append to decisions/ and update state/
- Identity files are updated rarely (only when Matt gives new guidance)
- state/current-session.md should be updated when starting/ending work
- Never delete another system's entries — append or update your own
