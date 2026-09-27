# Poindexter Pro — the canonical offer

**This file is the one description of Pro.** Every surface below paraphrases
it; when the offer changes, change it here first, then walk the list. Five
surfaces had drifted into five different stories by 2026-09-26 (see "Why this
file exists"), which is the failure this file prevents.

Last verified against the deliverable: **2026-09-26**, on
`Glad-Labs/poindexter-pro` @ `3e6a847` (weekly rebuild of 2026-09-20).

## Price

**$19/month or $180/year** — Founding Member rate, locked for life; 7-day free
trial. Lemon Squeezy is the only checkout. (Decided in
`docs/superpowers/specs/2026-06-09-site-positioning-pricing-design.md`; do not
re-open the number without re-opening that spec — price thrash was the
symptom it diagnosed.)

## What Pro contains

| Item                     | What it is                                                                                                                                                                                                                                                                                                                                                      | Exclusive?                                                                                                                                                                |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **The operator console** | The cockpit UI: system pulse, approval queue with QA verdicts, pipeline traces, GPU and cost telemetry. Installs with a copy and a restart (`console/INSTALL.md`).                                                                                                                                                                                              | **Yes — the one Pro-only surface.** The public mirror strips `src/cofounder_agent/console/`; an OSS install has no `/console` route. The backend APIs it reads stay free. |
| **The live config seed** | 1,200+ non-secret settings exported from the running business each week (1,228 at the 2026-09-20 build): thresholds, QA-rail strictness, cadence, routing, cost controls. `poindexter pro apply` is a dry-run diff first and only changes keys still at stock defaults; model and GPU pins are held for review because they are tuned to the seller's RTX 5090. | Yes                                                                                                                                                                       |
| **The operator book**    | 15 chapters plus appendices (model sizing by VRAM, troubleshooting) and a quick-start guide, ~23,000 words — including chapter 13, the unflattering economics and traffic chapter. Prose, not regenerated: the weekly rebuild only scans it for deleted-code references and retired prices, and reports what it finds for a manual pass.                        | Yes                                                                                                                                                                       |
| **Weekly rebuilds**      | An automated session (`scripts/ops_sessions/pro_freshness.py`) re-exports the seed, console, prompts and boards every week behind a PII/secret scrub gate; `CHANGELOG.md` is the receipt, `git pull` is the upgrade.                                                                                                                                            | Yes                                                                                                                                                                       |
| **Founding Discord**     | The operators' room.                                                                                                                                                                                                                                                                                                                                            | Yes                                                                                                                                                                       |
| Prompt pack              | Exported from `src/cofounder_agent/skills/*/*/SKILL.md`.                                                                                                                                                                                                                                                                                                        | **No — identical to what ships free.**                                                                                                                                    |
| Grafana boards           | Copies of five boards from `infrastructure/grafana/dashboards/`.                                                                                                                                                                                                                                                                                                | **No — every board ships free.**                                                                                                                                          |

## Rules for describing it

1. **Never list the prompt pack or the dashboards as Pro benefits.** They ride
   along in the Pro repo for convenience, but they ship free with the engine on
   every push. Say so when it helps: "every prompt pack and Grafana dashboard
   ships free with the engine."
2. **Never say "nothing is gated."** Exactly one surface is: the console UI.
   The accurate line is "Pro gates one surface, the console; everything else it
   sells is curation and freshness, not capability."
3. **Seed count is a floor: "1,200+".** It moves weekly (exact count lives in
   the Pro repo's `config/README.md`, written by the freshness session). If a
   rebuild ever drops below 1,200, lower the floor everywhere.
4. **No hardware spec.** The book carries a reference-build table and sizing by
   VRAM; there is no parts list, rack layout, thermals or electrical guide.
   Don't sell one until it exists.
5. **"Local by default", never "no paid APIs" about the live system.** The
   engine runs fully local by default, but the seller's own instance rents one
   hosted model (the writer, about $10 a month) and runs every other call
   locally. The seed's writer pin is that hosted model, which is one more
   reason model pins are held for review.
6. **No income claims.** The live site does not yet earn meaningful money;
   chapter 13 of the book says so. Nothing may imply that buying Pro produces
   income.
7. **"Weekly" belongs to the seed and the console, never the book.** The
   weekly session regenerates the seed, console, prompts and boards; the book
   is prose it only scans for drift. (The `/guide` page said the book was
   "rebuilt from the live system every week" until 2026-09-27.)

## Surfaces that describe Pro

Public (these ship to `Glad-Labs/poindexter` on every push to main):

- `README.md` — the console caption under the banner, "Poindexter Pro" section
- `SUPPORT.md` — "What's free vs paid"
- `docs/welcome.mdx`, `docs/README.md` — one-line Pro mentions

Private (stripped from the mirror):

- `web/storefront/app/page.js` — hero meta + the three cards
- `web/storefront/app/guide/page.js` — the "What's in Pro" checklist
- `web/storefront/app/about/page.js` — the Pro paragraph + the `// MODE` fact
- `web/storefront/app/layout.js` — site metadata description
- `marketing/launch/copy/05-faq-crib-sheet.md` — "What's the catch with Pro?"
- `Glad-Labs/poindexter-pro` `README.md` — buyer-facing; its "950+" seed floor
  is conservative but true

## Why this file exists

On 2026-09-26 the same product was described five ways:

- The public README captioned the console "ships with the repo" — the mirror
  strips it.
- SUPPORT.md sold "premium prompts" and "5 premium Grafana dashboards" (both
  free) and said the subscription buys "not gated features" (the console is
  gated).
- The FAQ crib sheet said "nothing feature-gated", and that Pro is "the
  production-tuned prompt packs and dashboard configs" (both free).
- The storefront advertised "1,800+" settings (the build had 1,228) and a
  "Hardware Spec" with a parts list, rack layout, thermals and electrical guide
  that the deliverable does not contain.
- The storefront About page said "No paid APIs. No external inference" about
  the live system, whose writer is a hosted model.

None of these was a lie when first written; each was true of some earlier
version of the offer. That is why the list of surfaces above exists.
