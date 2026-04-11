# Documentation split plan

**Goal:** free tier (public repo) has everything needed to master Poindexter,
but reading it takes hours. Paid tiers sell convenience.

**Principle:** the public repo must _not_ look abandoned. "Repo with no
docs" is a credibility killer. Docs are the trust signal that makes the
paid tiers sellable.

---

## Current state

### Public repo (poindexter) — what's here now

| File                                | Lines | Status                                 |
| ----------------------------------- | ----- | -------------------------------------- |
| `README.md`                         | 177   | Good. Setup + architecture + status.   |
| `CONTRIBUTING.md`                   | 54    | Thin but present.                      |
| `SECURITY.md`                       | 138   | Good.                                  |
| `CHANGELOG.md`                      | 1,096 | Thorough.                              |
| `CODE_OF_CONDUCT.md`                | 31    | Standard.                              |
| `SUPPORT.md`                        | 73    | Standard.                              |
| `docs/quick-start-guide-outline.md` | 217   | Just an outline — not a usable guide.  |
| `src/cofounder_agent/README.md`     | 84    | Backend-specific.                      |
| `infrastructure/grafana/README.md`  | ~85   | Grafana setup (rewrote earlier today). |
| `infrastructure/openclaw/README.md` | ?     | OpenClaw setup.                        |

**Total technical content that would let someone master the system:** ~400
usable lines once you strip CHANGELOG. That is **not enough to master
Poindexter from the public repo alone.**

### Premium repo (glad-labs-prompts) — what's over there now

```
docs/
├── architecture/
│   ├── API-Design.md              264 lines  ← should move to public
│   ├── Database-Schema.md         273 lines  ← should move to public
│   ├── Multi-Agent-Pipeline.md    572 lines  ← should move to public
│   ├── System-Design.md           715 lines  ← should move to public
│   └── README.md                   19 lines
├── operations/
│   ├── CI-Deploy-Chain.md          89 lines  ← should move to public
│   ├── Environment-Variables.md   136 lines  ← should move to public
│   ├── Local-Development-Setup.md  39 lines  ← should move to public
│   ├── Quick-Start-Guide.md       425 lines  ← stays premium
│   └── README.md                   18 lines
├── guide/
│   ├── ai-content-pipeline-guide-outline.md  346 lines  ← stays premium
│   └── chapters/
│       ├── 01-why-this-architecture-exists.md  113  ← stays premium
│       ├── 02-system-overview.md                179  ← stays premium
│       └── 03-the-foundation.md                 193  ← stays premium
├── FEATURE-STATUS.md              177 lines  ← should move to public
├── TROUBLESHOOTING.md             173 lines  ← should move to public
├── quick-start-guide.md           431 lines  ← stays premium
└── quick-start-guide-outline.md   217 lines  ← stays premium
```

**Total:** ~4,160 lines. Of those, **~2,458 should move to the public
repo** (architecture + ops reference + feature status + troubleshooting)
and **~1,702 should stay premium** (curated Quick Start guides + the
long-form book).

---

## What goes where

### Public (poindexter) — "mastery but inconvenient"

Everything you need to deeply understand and run Poindexter. **No holes.**
Reading all of it takes 6–8 hours.

```
docs/
├── README.md                    (new, 1-page doc index — 50 lines)
├── ARCHITECTURE.md              (move from premium, System-Design.md — 715 lines)
├── api/
│   ├── README.md                (API design overview — from API-Design.md, 264 lines)
│   ├── endpoints.md             (new, route reference — ~300 lines)
│   └── mcp-tools.md             (new, list of MCP tools + args — ~150 lines)
├── architecture/
│   ├── multi-agent-pipeline.md  (move from premium, 572 lines)
│   ├── database-schema.md       (move from premium, 273 lines)
│   ├── config-as-db.md          (new, how app_settings replaces env — ~200 lines)
│   └── brain-daemon.md          (new, self-healing loop — ~150 lines)
├── operations/
│   ├── local-development-setup.md  (move from premium, 39 lines — expand to ~200)
│   ├── environment-variables.md    (move from premium, 136 lines)
│   ├── ci-deploy-chain.md          (move from premium, 89 lines)
│   ├── troubleshooting.md          (move from premium, 173 lines)
│   └── upgrading.md                (new, in-place upgrade guide — ~200 lines)
├── reference/
│   ├── app-settings.md          (new, every DB setting + default + purpose — ~500 lines)
│   ├── prompt-templates.md      (new, how prompt_templates table works — ~200 lines)
│   ├── qa-pipeline.md           (new, 6-stage pipeline + score aggregation — ~300 lines)
│   └── validators.md            (new, every ValidationResult category — ~200 lines)
├── extending/
│   ├── adding-a-provider.md     (new, how to plug in a new LLM provider — ~150 lines)
│   ├── adding-a-validator.md    (new, how to add a new programmatic check — ~100 lines)
│   ├── custom-workflows.md      (new, phase registry extension — ~200 lines)
│   └── writing-skills.md        (new, OpenClaw skill format — ~150 lines)
└── feature-status.md            (move from premium, 177 lines)
```

**Total new public docs:** ~4,500–5,000 lines of comprehensive technical
reference. Mastery-grade. **Enough to replicate Poindexter from scratch
without ever opening the paid guide.**

### Premium ($29 Quick Start one-time) — "convenience"

Stays in `glad-labs-prompts` repo, behind a purchase gate:

```
docs/
├── quick-start-guide.md                 (431 lines — the curated "setup
│                                         to first post in 30 min")
├── operations/Quick-Start-Guide.md      (425 lines — Matt's exact
│                                         working setup walkthrough)
├── quick-start-guide-outline.md         (217 lines — planning artifact,
│                                         keep or drop)
└── (the install-script + docker-compose preset + DB seed)
```

The $29 product is: **"run one script, get a working Poindexter in 5
minutes instead of 5 hours of reading the public docs."** The public
docs get you there; the $29 product skips the reading.

### Premium ($9/mo subscription) — "ongoing relationship"

```
docs/
├── guide/                               (831 lines of long-form book)
│   ├── ai-content-pipeline-guide-outline.md
│   └── chapters/
│       ├── 01-why-this-architecture-exists.md
│       ├── 02-system-overview.md
│       └── 03-the-foundation.md
│                                         ← plus future chapters
│                                         added monthly
├── premium-prompts/                      (the YAML files — plus monthly
│                                         improvement diffs)
└── (Discord access + monthly Q&A with Matt)
```

The $9/mo product is: **"Matt keeps updating this and you ride along."**
The public docs cover today; the subscription covers tomorrow.

---

## What to move right now

Concrete file ops (assuming current paths on this machine):

```bash
# From C:\Users\mattm\glad-labs-prompts → to C:\Users\mattm\glad-labs-website\docs\

# Architecture (move + rename)
cp docs/architecture/System-Design.md          ../glad-labs-website/docs/ARCHITECTURE.md
cp docs/architecture/API-Design.md             ../glad-labs-website/docs/api/README.md
cp docs/architecture/Multi-Agent-Pipeline.md   ../glad-labs-website/docs/architecture/multi-agent-pipeline.md
cp docs/architecture/Database-Schema.md        ../glad-labs-website/docs/architecture/database-schema.md

# Operations (move)
cp docs/operations/CI-Deploy-Chain.md          ../glad-labs-website/docs/operations/ci-deploy-chain.md
cp docs/operations/Environment-Variables.md    ../glad-labs-website/docs/operations/environment-variables.md
cp docs/operations/Local-Development-Setup.md  ../glad-labs-website/docs/operations/local-development-setup.md

# Status + troubleshooting (move)
cp docs/FEATURE-STATUS.md                       ../glad-labs-website/docs/feature-status.md
cp docs/TROUBLESHOOTING.md                      ../glad-labs-website/docs/operations/troubleshooting.md
```

Then each moved doc needs:

1. **De-Glad-Labs-ify the brand references** — the premium docs still
   call the engine "Glad Labs" or "Co-Founder Orchestrator" in places.
   Rename to Poindexter throughout and rewrite the "vision" sections
   so they describe the product, not Matt's personal business.
2. **Strip any text that duplicates premium material** — e.g. the
   Quick-Start-Guide.md has step-by-step walkthroughs that are the
   paid product. Those must NOT move; only the architecture/reference
   material moves.
3. **Add a banner at the top:** "This is the open-source documentation.
   For a guided, step-by-step setup in 30 minutes, see the Quick Start
   Guide at gladlabs.io/products/quick-start."
4. **Wire into `docs/README.md`** as a doc index so visitors see a
   curated TOC instead of a flat file dump.

## What to write from scratch

New docs that don't exist yet in either repo but are required for
"mastery":

1. **`docs/api/endpoints.md`** — route reference. Generate from FastAPI's
   openapi.json + hand-write behavior notes. ~300 lines.
2. **`docs/api/mcp-tools.md`** — every `mcp__poindexter__*` tool with
   args, return shape, example. ~150 lines.
3. **`docs/architecture/config-as-db.md`** — explain how `app_settings`
   replaces env vars, how to add a setting, how to migrate one. ~200 lines.
4. **`docs/architecture/brain-daemon.md`** — self-healing probes,
   restart logic, what to monitor. ~150 lines.
5. **`docs/reference/app-settings.md`** — every `app_settings` key,
   default, purpose, who reads it. **Auto-generatable** from the
   `_DEFAULTS` dict in `site_config.py`. ~500 lines.
6. **`docs/reference/prompt-templates.md`** — the `prompt_templates`
   table schema, how `pm.get_prompt()` resolves, how to override a
   prompt without a deploy. ~200 lines.
7. **`docs/reference/qa-pipeline.md`** — the 6-stage content pipeline
   - weighted score aggregation + gate veto rules. ~300 lines.
8. **`docs/reference/validators.md`** — every `ValidationIssue` category,
   what it catches, example match, score penalty. ~200 lines.
9. **`docs/extending/adding-a-provider.md`** — walk through adding a
   new LLM provider via the model router. ~150 lines.
10. **`docs/extending/adding-a-validator.md`** — walk through adding a
    new programmatic check. ~100 lines.
11. **`docs/extending/custom-workflows.md`** — phase registry + custom
    workflow authoring. ~200 lines.
12. **`docs/extending/writing-skills.md`** — OpenClaw skill format. ~150 lines.
13. **`docs/operations/upgrading.md`** — in-place upgrade guide (Matt's
    "live rename + migration" approach). ~200 lines.
14. **`docs/README.md`** — the doc index. ~50 lines.

**Total new content:** ~2,850 lines. Combined with ~2,458 moved from
premium, the public `docs/` grows to roughly **5,300 lines** — the
"hours and hours of reading" requirement.

## Recommended execution order

Day 1 (biggest mastery unlock, least churn):

1. Move System-Design.md → ARCHITECTURE.md (brand fix)
2. Move Multi-Agent-Pipeline.md + Database-Schema.md + API-Design.md
3. Move Environment-Variables.md + Local-Development-Setup.md
4. Move FEATURE-STATUS.md + TROUBLESHOOTING.md
5. Write docs/README.md (the index)
6. Update the main README.md to point to docs/ for deeper dives
7. Ship

Day 2 (reference material — auto-generatable wins):

8. Auto-generate `docs/reference/app-settings.md` from `site_config._DEFAULTS`
9. Write `docs/reference/qa-pipeline.md` (Matt's domain knowledge)
10. Write `docs/reference/validators.md` (enumerate from `content_validator.py`)
11. Write `docs/reference/prompt-templates.md`
12. Ship

Day 3 (extension points — developer growth material):

13. `docs/extending/adding-a-provider.md`
14. `docs/extending/adding-a-validator.md`
15. `docs/extending/custom-workflows.md`
16. `docs/extending/writing-skills.md`
17. Ship

Day 4 (fill-ins):

18. `docs/api/endpoints.md` — generate from openapi.json
19. `docs/api/mcp-tools.md`
20. `docs/architecture/config-as-db.md`
21. `docs/architecture/brain-daemon.md`
22. `docs/operations/upgrading.md`
23. Final ship + announcement

## What changes in `glad-labs-prompts` after the move

- Delete (now public) architecture/, environment-variables, local-dev-setup,
  CI-deploy-chain, feature-status, troubleshooting.
- Keep quick-start-guide.md + operations/Quick-Start-Guide.md as the
  curated paid experience.
- Keep docs/guide/ (the book) — the recurring-subscription value.
- Keep the YAML prompts (the actual premium artifacts).
- Rewrite the top-level README.md to say: "premium add-ons for
  Poindexter. The open-source engine + full architecture docs live at
  github.com/Glad-Labs/poindexter. This repo contains the $29 Quick
  Start Guide, the long-form $9/mo content pipeline book, and the
  monthly-updated prompt templates."

## Time estimate

- Day 1 move + rebrand: 2–3 hours of focused work
- Day 2 reference material: 3–4 hours
- Day 3 extending guides: 3–4 hours
- Day 4 fill-ins: 2–3 hours

**Total: ~12–15 hours over 4 sessions.** All of it can be autonomous
on my end; you just need to review before ship.

## Recommended Day 1 commit: just move + de-Glad-Labs-ify

If you only approve one thing, approve Day 1. It's the highest-impact,
lowest-risk move — 2,458 lines of existing material moved from a
private repo to a public one, with a search-and-replace pass to fix
the branding. It takes the public repo from "looks abandoned" to
"looks like a real open-source project" in a single commit.
