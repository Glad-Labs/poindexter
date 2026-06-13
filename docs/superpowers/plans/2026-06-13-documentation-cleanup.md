# Documentation update + cleanup — 2026-06-13

Repo-wide documentation audit and fix-in-place pass. Goal: every doc surface
accurately reflects the live system, with dead references, stale self-notes,
and retired vocabulary corrected.

## Scope (operator-approved)

All doc surfaces, fix-in-place via PR: public/shipped docs (root `README.md` +
`docs/` tree excl. private `docs/superpowers/` + subdir READMEs), internal /
operator docs (`.shared-context/`, `.claude/` — though `.claude/` is gitignored
so those are local-only), and the hand-maintained CLAUDE.md narrative (the
DB-derived counts auto-sync via `.github/workflows/sync-claude-md.yml`, so those
are left alone).

## Locked decisions

1. **Extensionless internal links → leave as-is.** Mintlify's canonical
   convention; renders correctly on the hosted site, only "breaks" on GitHub's
   raw view. Not worth churning ~89 links.
2. **Point-in-time files → refresh live-state + prune dead.**

## What the audit found

- **Link rot is a non-problem.** Scan of 208 markdown files: the single
  "truly broken" link (`anti-hallucination.md` → `internal_context_link`) is a
  deliberate _illustrative example_ of a placeholder citation, not a real link.
  The 89 other "broken" links are Mintlify extensionless (decision 1). Net real
  link rot: **0**.
- **The real drift is count/narrative**, only visible by reading + verifying
  against source (the code) and live state (the prod DB). Recurring vectors:
  counts copied into prose without auto-sync (dashboards, pipeline nodes),
  retired vocabulary, and references to since-deleted/moved/decomposed code.

## Status

### Shipped — PR 1 (this branch): public + reference doc accuracy

- `welcome.mdx` / `architecture/index.mdx` — `guardrails-ai` → `guardrails`
  (package dropped 2026-05-12 for CVE-2026-45758, native/dep-free since #996);
  "By the numbers" cards corrected to 36-node pipeline + 13 Grafana dashboards
  (were 18-node + 8 dashboards listing the retired Auto-Publish Gate board).
- `skills/poindexter/create-post/SKILL.md` — stale 18-node numbered stage list
  rewritten to the current 36-node six-block flow.
- `skills/poindexter/quality-report/SKILL.md` + `src/LICENSE.md` — guardrails-ai
  name/dependency removal.
- `services/canonical_blog_spec.py` docstring — corrected stale
  `guardrails_enabled=false` (live DB = true).
- `architecture/overview.md` + `architecture/anti-hallucination.md` — `qa.guardrails`
  removed from live node lists (#730), `qa.self_consistency` added (#1447), atoms
  path `services/atoms/` → `modules/content/atoms/`, "Location" off the deleted
  `services/stages/` tree.
- `README.md` — architecture diagram retired the 5 dead brain-anatomy labels for
  kernel/module/capability (kept Brainstem + Spinal Cord).

### Spawned as a focused task — `services.md` regeneration

`docs/reference/services.md` is a hand-maintained catalog ~28% stale (~24 of 85
listed `.py` files no longer exist by name — `*_database.py` consolidated into
`database_service.py`, `image_*` restructured into `image_providers/`, several
utilities deleted, pipeline table pre-#362). Too large + rename-ambiguous to
fix inline; spun off with the mappings pre-loaded, ideally adding a generator.

### Flagged for operator decision (not auto-changed)

- **`src/LICENSE.md` Pro pricing/naming** — says "Glad Labs Pro ($9/mo or
  $89/yr)"; the monetization plan + the 2026-06-10 "$19 founding launch" commit
  say **$19/mo · $180/yr**, and public-naming convention is **"Poindexter Pro"**
  in a mirrored file. Pricing in a license warrants a human confirm.
- **Public docs-site brain metaphor** — `welcome.mdx` / `index.mdx` /
  `overview.md` still frame the system "by analogy to brain anatomy / region by
  region." The README's _misleading specific labels_ were clearly drift and got
  fixed; whether to migrate the docs-site's broader pedagogical metaphor is an
  editorial/brand call (the growth metaphor is noted "durable").

### Remaining (follow-up batches)

- **CLAUDE.md narrative** — stale self-note claiming `claude-md-sync` disabled
  means counts don't auto-update (the GH Actions workflow still syncs them); the
  Monitoring dashboard list misses the new SEO Harvest board; "12 dashboards" in
  the URL table vs 13 actual.
- **`.shared-context/` state refresh + prune** — `state/*` snapshots, dead
  agent-memory.
- **Long-tail read-and-judge** — `docs/operations/*` (27), `docs/integrations/*`
  (15), `architecture/services/*.md` deep-dives (18). Keyword/link scans show
  these are mostly clean; a full per-claim read remains.

## Acceptance criteria

- Link scan: 0 truly-broken links. ✅ (the 1 candidate was a false positive)
- No prose reference to a file/dir/service that no longer exists (in scope so far).
- Each PR green on required CI (test-backend, migrations-smoke) before merge.
- `[skip-public-sync]` NOT used — these corrections should mirror.
