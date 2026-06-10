# Glad Labs — Offer, Pricing & Go-to-Market Realignment

**Date:** 2026-06-09 (audit + posture added 2026-06-10)
**Status:** Approved design — Track A ready for implementation plan; Track B sequenced
**Owner:** Matt (operator)

---

## 1. Problem

`gladlabs.io` and `gladlabs.ai` compete for the same job, and "what we sell" has
drifted across contradictory stories.

- **Two stores.** `gladlabs.io/product` is a complete second storefront firing
  the **same Lemon Squeezy checkout** as `gladlabs.ai`. Two pages, one checkout,
  splitting the click and SEO intent.
- **Offer drift.** "What Pro is" changes page to page (a book bundle on the .ai
  landing; a living-system subscription on /guide; repeated on .io/product), with
  three live contradictions: a book sold as the hero vs. the "docs are free"
  rule; a "Source" card selling the already-free public repo; a "small business
  owner" buyer the product (5090 + Docker) can't actually serve.
- **Price thrash** ($29 one-time → $9.99/mo → $9/mo+$89/yr) — the symptom of an
  unresolved _offer_, not an unresolved number.
- **(Discovered 2026-06-10) The deliverable behind the offer is not sellable.**
  See §6. The repo that backs "Poindexter Pro" has no working delivery channel,
  leaks operator secrets, and is 1–2 months behind the live system.

---

## 2. Decisions — the offer

### 2.1 Buyer

The **builder / self-hoster** (has/will build a 5090-class workstation, wants
their own autonomous content engine). The "small business owner" framing is
retired — serving a non-technical buyer needs hosted/managed service, which
violates the hard constraint of _passive income, zero customer service_.

### 2.2 The offer (the noun)

**Poindexter Pro = a subscription to the living, continuously-tuned system.**
Promise: **"Skip months of tuning. Stay current."** Delivers: the
**Langfuse-managed production prompt packs** (exported from the live system —
Langfuse is the prompt system of record), operator-tuned `app_settings` seed,
fact-overrides, premium dashboards, VIP Discord, continuous updates. The book is
a **listed perk, not the headline.** "The Source" repo-access pillar is deleted
(sells a free thing).

### 2.3 Price

**$19/mo · $180/yr**, badged **Founding Member — rate locked for life**;
standard rate rises after launch. ($9 underpriced the value and signalled "toy"
to an audience that just spent ~$2–3k on hardware; annual ~21% off pushes off the
grab-and-go monthly path; founder-lock drives launch urgency + Discord density.)

### 2.4 Canonical naming

| Thing                         | Name               |
| ----------------------------- | ------------------ |
| The engine (free, Apache-2.0) | **Poindexter**     |
| The paid subscription         | **Poindexter Pro** |
| Retired term                  | ~~"Premium"~~      |

---

## 3. Site architecture

|                 | Role                                                           | Cross-link → other site                             | Checkout                                     |
| --------------- | -------------------------------------------------------------- | --------------------------------------------------- | -------------------------------------------- |
| **gladlabs.ai** | The store. All paid traffic, README links, checkout land here. | "Writing ↗" → gladlabs.io _(already correct)_       | the only one — **gated until Track B ships** |
| **gladlabs.io** | The proof. "This blog is published by the thing we sell."      | "Poindexter ↗" → gladlabs.ai _(replaces "Premium")_ | none                                         |

Rule: **product site → "Writing", blog → "Poindexter"; never two pages claiming
to be the store.**

---

## 4. Launch posture (decided 2026-06-10)

**Ship the honest site now; gate the checkout.** The site realignment (Track A)
ships immediately — it stops the active two-stores/price-drift damage and builds
the founding audience. But because the deliverable is not fulfillable (§6), the
live `$19/mo` Lemon Squeezy checkout is **replaced by a founding-members CTA**
until Track B lands. No customer is charged for something they can't receive,
and no operator secrets ship.

- **Gated CTA (recommended):** primary = **"Join the founding members → Discord"**
  (existing invite `discord.gg/GCDBxBVv`); optional secondary = email waitlist
  (reuse the public-site `NewsletterModal` pattern). Keep the $19/$180 +
  Founding-Member framing visible — sell the offer, capture the lead, don't
  charge yet.
- **Flip-on trigger:** when Track B steps 1–3 (PII scrub + delivery channel +
  pay→deliver) are verified by one live test purchase, swap the CTA back to the
  `LemonSqueezyOverlay`.

---

## 5. Track A — Site realignment (BUILD NOW)

### 5.1 gladlabs.io (`web/public-site/`) — blog becomes pure proof

1. **`components/TopNav.js`** — amber "Premium" (→/product, desktop + mobile)
   becomes **"Poindexter ↗"** → `https://gladlabs.ai` (`target="_blank"`, keep
   amber). _(Alt label: "Product ↗".)_
2. **`/product` → 301 redirect to `https://gladlabs.ai`** via `next.config.js`
   `redirects()` (permanent). Delete `app/product/page.js`. Confirm no internal
   links to `/product`; remove from sitemap if present.
3. **Proof line** — slim line _"Every post here was researched, written, and
   published autonomously by Poindexter →"_ linking to gladlabs.ai. **Default
   placement: global `components/Footer.js`.** Per-article placement is a later
   optional enhancement.

### 5.2 gladlabs.ai (`web/storefront/`) — tighten the store, gate checkout

4. **`lib/site.config.js`** — `PRO_MONTHLY_USD = 19`, `PRO_ANNUAL_USD = 180`; add
   a founding-members CTA target constant (Discord invite) + a
   `CHECKOUT_LIVE = false` flag so flipping billing on later is a one-line diff.
5. **Checkout gating** — wherever `LemonSqueezyOverlay` is the primary CTA
   (`app/page.js`, `app/guide/page.js`), render the founding-members CTA when
   `CHECKOUT_LIVE === false`; keep the overlay code in place behind the flag.
6. **`app/page.js` (landing):** replace the **"03 · SOURCE"** card (sells the
   free repo) with a **living-tuning** card (the real moat); reframe **"01 ·
   PLAYBOOK"** from book-as-product to tuned-system-as-product; price line →
   $19/$180 + Founding-Member note.
7. **`app/guide/page.js` (now Pricing):** price → $19/$180 + Founding framing;
   keep the "WHAT'S IN PRO" checklist with the book as one bullet, not the lead;
   metadata price text updated.
8. **`app/about/page.js`:** "// REVENUE" fact "$9/mo or $89/yr" → "$19/mo or
   $180/yr"; opening Pro paragraph drops "the full operator book" as the lead.
9. **`components/SiteNav.jsx`** — nav "The Guide" → **"Pricing"** (route
   unchanged; optional `/pricing` rename + redirect is polish).

---

## 6. Deliverable readiness audit (2026-06-10)

15-agent adversarial audit of `C:/Users/mattm/glad-labs-prompts` vs the live
system. **Overall: `not-sellable-yet`.**

| Dimension                                                             | Severity     | Sellable | Effort  |
| --------------------------------------------------------------------- | ------------ | -------- | ------- |
| Delivery channel + README                                             | **critical** | no       | days    |
| The 18-chapter book (teaches deleted code)                            | **critical** | no       | days    |
| Prompt-library YAMLs (orphaned; loader moved to `skills/*/SKILL.md`)  | **critical** | no       | days    |
| Seed `app_settings` (221 keys, ~60 orphaned; PII leak)                | high         | no       | hours   |
| `db_prompt_templates.json` (targets dropped `prompt_templates` table) | high         | no       | hours   |
| Premium Grafana dashboards (2 of 5 clean)                             | high         | partial  | hours   |
| Claude Code Template Pack (orphan $29 SKU)                            | low          | yes      | trivial |

**Three independent blockers (each kills the sale alone):**

1. **No delivery channel.** Only remote is the decommissioned Gitea
   (`localhost:3001`); no GitHub repo exists. `poindexter premium activate`
   validates the LS license and sets `premium_active` but **fetches zero files**;
   the LS webhook only writes `revenue_events`. Pay → silence.
2. **Operator secret/PII leak.** `seed-database-full.sql` + YAMLs ship the R2
   `storage_access_key`, `cloudflare_account_id`, `telegram_chat_id`,
   `mercury_balance`, and the operator's LinkedIn — violates "no operator info in
   distributed repos." The R2 key is a live credential → **rotate it** (§10).
3. **Book/prompts teach deleted code.** ~half the chapters document
   `task_executor`, `cross_model_qa`, `model_router`, static-Bearer auth, Gitea
   CI — all removed; every Appendix-D SQL uses `content_tasks` (real:
   `pipeline_tasks`) → "relation does not exist" on a buyer's first run. The 8
   prompt YAMLs are read by no live path; `content_qa.yaml` ships 2 keys vs 12.

**Salvage (not a rebuild from zero):** the premium prompt pack already exists as
the **Langfuse-managed prompts** (export them — §7.5); the
`db_prompt_templates.json` bodies are a usable historical archive/fallback; 2 of 5
dashboards (`approval-queue`, `quality-content`) ship as-is;
`bonus-memory-system.md` is good (fold into Pro); the license-validation half of
#84 already exists (only delivery is missing).

**Root cause / the fix that matters:** the moat claim "continuously updated, stay
current" is currently **false** — the deliverable is hand-maintained and frozen
weeks behind. It must become a **build artifact generated from the live system**
(prompts ← current SKILL.md packs; seed ← `settings_defaults.py` minus secrets;
book ← reconciled against CLAUDE.md), with a CI gate that fails on drift.

---

## 7. Track B — Make the offer fulfillable (BUILD BEFORE CHARGING)

In the audit's recommended order (cheapest-highest-leverage first):

1. **Strip PII** from seed + YAMLs; add a sync-filter (analogous to the
   poindexter public-mirror filter) that scrubs operator identity + secret-
   adjacent values from any file before it lands in the deliverable. _(hours)_
2. **Stand up the delivery channel.** Create a private GitHub repo (e.g.
   `Glad-Labs/poindexter-pro`), remove the dead Gitea remote, rewrite the
   "Gitea-only" README lines. _(≈1 day)_
3. **Wire pay → deliver.** **Decision (recommended): private-repo +
   collaborator-invite.** LS webhook on `subscription_created` → GitHub
   collaborator invite (+ Discord VIP role); `cancelled` → revoke. This matches
   the "stay current = `git pull`" promise and is lower-infra than R2 signing.
   _(Alt: signed tarball served by the worker, gated behind `premium activate`.)_
   Verify with one live test purchase. **This is the checkout flip-on gate.**
   _(≈1 day)_
4. **Ship the 2 clean dashboards now; fix/drop the other 3** (drop/rename the
   `cost-analytics` UID-dup; repoint `infrastructure-data` off the dead
   `gpu-prometheus` datasource; rewrite `link-registry` dead-service text).
   _(hours)_
5. **Package the premium prompt pack (from Langfuse) + regenerate the seed as
   build artifacts.** The premium prompts ARE the **Langfuse-managed** versions —
   Langfuse is the system of record for tuned prompts (confirmed by Matt). Build a
   repeatable `Langfuse export → versioned prompt-pack` step, keyed to the 12
   current `skills/*/SKILL.md` atom keys/contracts, as the artifact the customer
   receives. The OSS `SKILL.md` packs stay the free baseline;
   `db_prompt_templates.json` is a historical archive, NOT the source. Regenerate
   the seed from `settings_defaults.py` DEFAULTS + a scrubbed non-secret baseline;
   drop the dead `prompt_templates` import path. _(hours–1 day)_
6. **Fix the book (long pole).** Global `content_tasks`→`pipeline_tasks`; rewrite
   Ch04 (Prefect + OAuth), Ch07 (qa._ atoms), Ch11 (real 12-board inventory);
   strip Gitea (Ch02/03/10/14); Next.js 16 + tag revalidation (Ch05/09); `_\_usd`
   keys + 901 settings (Ch12/AppB); reconcile internal contradictions incl. the
   $19/$180 price. Re-verify against a fresh-DB install. _(2–4 days)_
7. **Reconcile price strings + fold the orphan SKU.** grep-replace $9/$89/$39 →
   $19/$180 across the deliverable; fold `bonus-memory-system.md` into Pro;
   delete the standalone $29 Template Pack + 5 role templates. _(trivial)_
8. **Automate refresh + freshness CI gate** — a scheduled check (a 10th scheduled
   agent, or extend `claude-md-sync`) that diffs the deliverable against the live
   system and alerts on prompt/seed/book drift. Only after this is "continuously
   updated" true rather than a claim. _(follow-up)_

**Then:** flip `CHECKOUT_LIVE = true` (Track A step 4) → live $19/mo billing.

---

## 8. Success criteria

**Track A (ship now):**

- Exactly one store surface; `gladlabs.io/product` 301s to gladlabs.ai; no page
  on .io fires Lemon Squeezy.
- Every price string on both sites reads **$19/mo · $180/yr** + Founding framing;
  no `$9`/`$89` survives grep on the sites.
- No surface sells "documentation" or "the repo" as the headline; the "Source"
  card is gone; the book is a perk.
- `.io` cross-links to gladlabs.ai as "Poindexter ↗"; `.ai` → "Writing ↗".
- The blog carries the "published by Poindexter →" proof line.
- The .ai checkout shows the **founding-members CTA**, not a live charge;
  `CHECKOUT_LIVE = false`.
- Both sites build clean; TopNav/Footer tests updated.

**Track B (charge-on gate — all must hold before `CHECKOUT_LIVE = true`):**

- A live test purchase results in the buyer **automatically** receiving
  repo/file access (no operator action).
- **No operator PII/secret** survives grep in the deliverable repo.
- No `$9`/`$89`/`$39` survives grep in the deliverable.
- The book's SQL examples run against a fresh-DB install; no chapter teaches
  deleted code.
- The deliverable is generated from the live system + a freshness CI gate exists.

---

## 9. Non-goals

- No hosted/managed offering, no Stripe, no custom checkout (Lemon Squeezy only).
- No new product tiers; single Pro tier, two payment cadences.
- No brand-system / typography / E3 redesign.
- No change to the free engine's Apache-2.0 license.

---

## 10. Operator manual actions

- **Rotate the R2 `storage_access_key`** — it has been sitting in the deliverable
  git repo (was on Gitea). Do this regardless of every other fix.
- **Lemon Squeezy dashboard:** set the charged price to $19/$180 + confirm the
  7-day trial — _before_ flipping `CHECKOUT_LIVE = true`, not now.
- **GitHub README** (`Glad-Labs/poindexter`): point the product link at
  gladlabs.ai (verify current target during Track A).
- **Memory hygiene:** reconcile the drifted memory files
  (`project_monetization.md`, `project_storefront_decisions.md`,
  `project_product_strategy.md`, `project_product_positioning.md`) to this doc.
