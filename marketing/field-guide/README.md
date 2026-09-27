# The field guide — a product for the audience that can't run Poindexter

**Status:** pre-sale package, drafted 2026-09-26. **Nothing here is live.** The
storefront page exists but 404s until `FIELD_GUIDE_LIVE` is flipped (see
"Turning it on").

## What it is

A paid e-book for developers and engineering leads who are adopting AI coding
agents and running into the problems this repo has already solved: drift,
silent failures, docs that lie to the next session, CI that goes green by
scanning nothing, config sprawl, scanners that bury real findings.

Working title (pick one):

1. **Green Is Not a Result** — _a field guide to running a production codebase
   your AI agents write_
2. **Directing the Machine** — _what a year of building 700,000 lines through
   Claude Code taught one person about keeping it true_
3. **The Agent-Directed Codebase** — _guardrails, ratchets and operating rules
   from a system nobody hand-typed_

Every chapter is built around a real, measured failure from this repo and the
guardrail it earned. That is the whole pitch: not "tips for prompting", but the
operating rules of a system that has run in production for a year, with the
receipts. `outline.md` maps each chapter to its source material;
`sample-chapter.md` is one chapter drafted end to end.

## Why this product, and why now

From the 2026-09-26 evaluation (summarised in `marketing/launch/launch-plan.md`,
"Strategy update"):

- **The audience is large and needs no GPU.** People who can run Poindexter need
  a 16 GB+ NVIDIA card and 23+ containers. People who are directing coding
  agents on a real codebase need neither, and there are far more of them.
- **The story post already reaches them.** "One person, 10,500+ commits, almost
  no hand-written code" is the launch's strongest hook. Without something to
  buy, that traffic reads, stars, and leaves.
- **It fits the hard constraint.** Passive income, zero customer service
  (`docs/superpowers/specs/2026-06-09-site-positioning-pricing-design.md`): a
  one-time digital download, delivered by Lemon Squeezy, with no account, no
  install, no support queue.
- **The material exists.** CLAUDE.md's Key Principles, ~70 architecture docs, 32 CI lints,
  the decision log and the story draft are most of a book. The work is
  selection, narrative and a truth-edit, which is the division of labour the
  launch pack already uses (Claude drafts, you edit for truth).

## Tension with the June spec — your call

The June positioning spec made "single Pro tier, no new SKUs" a non-goal and
deleted an orphan $29 "Claude Code Template Pack". This is a new SKU, but for a
different buyer: the spec's buyer is the self-hoster, and this buyer never
installs anything. If you'd rather keep one SKU, the fallback is to make the
guide a **Pro perk** and use the storefront page as an email waitlist only.
Either way, offering it **free to active Pro subscribers** adds Pro value at no
cost.

## Price and format

- **$29 pre-order**, rising to $39 at release. Set in
  `web/storefront/lib/site.config.js` (`FIELD_GUIDE_PRICE_USD`); Lemon Squeezy
  charges the real price.
- PDF + EPUB, delivered by Lemon Squeezy's built-in file delivery. No repo
  access, no GitHub username at checkout.
- Pre-order terms on the page: a stated ship month, full refund on request if it
  slips. A refund is one click in Lemon Squeezy.

**Pre-order vs waitlist.** A pre-order is the stronger signal (someone paid),
and is honest now that `outline.md` and `sample-chapter.md` exist. If you'd
rather not owe a ship date, create a **$0 "notify me"** product instead and set
`FIELD_GUIDE_PRICE_USD = 0`: the page switches to a free waitlist CTA with no
other change.

## The v1 cut

`outline.md` lists 15 chapters. Don't write 15 before anyone has paid. The v1
cut is 8 chapters plus the guardrail appendix (marked **v1** in the outline),
about 20,000 words. That's the size of the Pro operator book (~23,000 words),
which the same drafting process has already produced once.

## Turning it on

Nothing below is automated. Each step is yours.

1. **Decide:** separate SKU, or Pro-perk plus waitlist (see above).
2. **Before a second product exists in the store,** set
   `pro_delivery_ls_product_id` to the Pro product's id
   (`poindexter settings set pro_delivery_ls_product_id <id>`).
   `docs/operations/pro-delivery.md` asks for this once the store sells more
   than Pro. A one-time product creates orders, not subscriptions, so the Pro
   sync would not invite its buyers anyway, but the filter makes that explicit.
3. **Create the product in Lemon Squeezy:** one-time price, the page copy as its
   description, the ship month in the description and receipt email, and the
   file attached when the book exists.
4. **Wire the storefront** in `web/storefront/lib/site.config.js`:
   - `LS_FIELD_GUIDE_URL` = the product's buy URL
   - `FIELD_GUIDE_SHIPS` = the ship month shown on the page, e.g. "December 2026"
   - `FIELD_GUIDE_PRICE_USD` = the price (0 = free waitlist)
   - `FIELD_GUIDE_LIVE = true`

   The page goes live at `gladlabs.ai/field-guide`, and a "Field guide" link
   appears in the storefront nav.

5. **Point the story post at it:** the closing lines of
   `marketing/launch/copy/01-story-post.md` carry the call to action.
6. **Know the revenue gap:** `SyncProSubscriptionsJob` records revenue from
   `/v1/subscription-invoices` only, so one-time orders will **not** reach
   `revenue_events` or the Revenue board. Track guide sales in the Lemon
   Squeezy dashboard, or extend the poll to `/v1/orders` before launch.

## What would make this a bad idea

- **Nobody pre-orders from the story launch.** Then the method isn't the
  product. Refund anyone who did, and put the effort back into the engine per
  the launch plan's decision rules.
- **It eats the evenings the launch needs.** The pre-sale needs only the page,
  the outline and the sample chapter, which now exist. Writing the full v1
  should wait until pre-orders say it's wanted.
