# The field guide — a Pro perk for the audience that can't run Poindexter

**Status:** decided 2026-09-27: **a Pro perk with a free waitlist, not a
separate product.** An outline and one sample chapter exist (drafted
2026-09-26). **Nothing here is live.** The storefront page exists but 404s until
`FIELD_GUIDE_LIVE` is flipped (see "Turning it on").

## What it is

An e-book for developers and engineering leads who are adopting AI coding
agents and running into the problems this repo has already solved: drift,
silent failures, docs that lie to the next session, CI that goes green by
scanning nothing, config sprawl, scanners that bury real findings. It ships to
Poindexter Pro subscribers at no extra charge and is not sold separately.

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
  no hand-written code" is the launch's strongest hook. Without somewhere to
  go next, that traffic reads, stars, and leaves.
- **It fits the hard constraints.** Passive income, zero customer service and a
  single Pro SKU
  (`docs/superpowers/specs/2026-06-09-site-positioning-pricing-design.md`). As
  a Pro perk it adds no second price, no new checkout and no support queue; it
  rides the delivery Pro already has.
- **The material exists.** CLAUDE.md's Key Principles, ~70 architecture docs, 32 CI lints,
  the decision log and the story draft are most of a book. The work is
  selection, narrative and a truth-edit, which is the division of labour the
  launch pack already uses (Claude drafts, you edit for truth).

## The decision (2026-09-27)

The June positioning spec made "single Pro tier, no new SKUs" a non-goal, so the
guide is a **Pro perk**, not a second product:

- **Pro subscribers** get it in the Pro repo the day it ships, at no extra
  charge. It reaches them the way everything else in Pro does: the GitHub
  collaborator invite, then `git pull`.
- **Everyone else** can join a free waitlist at `/field-guide`: one email when
  it ships. The waitlist is a $0 "notify me" product in Lemon Squeezy, so it
  needs no new infrastructure.
- **Not sold separately,** so it has no price of its own anywhere.

What this trades away, so it can be judged later against numbers:

- **A weaker demand signal.** A waitlist signup costs nothing, so it says less
  than a pre-order would have. Count signups _and_ the Pro trials that start
  after the guide ships.
- **Trial-and-cancel gets it free.** Trial subscribers get the Pro repo invite
  (`on_trial` is an access status in
  `src/cofounder_agent/poindexter/services/pro_delivery.py`), and Pro promises
  "cancel anytime, keep everything you've downloaded". That's already true of
  everything in Pro; the guide makes it likelier for readers who don't run the
  engine.
- **A self-hoster's price for a reader's product.** Pro is priced for people
  running the stack. A story reader who only wants the guide pays $19 a month
  or uses the trial. If the waitlist is large and conversions are poor, revisit
  a standalone edition then, with numbers.

## Format and delivery

- It lives in the Pro repo (`Glad-Labs/poindexter-pro`) under
  `book/field-guide/`, in Markdown like the book. Add a PDF build only if
  subscribers ask for one.
- Why `book/`: `scripts/ops_sessions/pro_freshness.py` rebuilds the Pro repo
  every week but never edits `book/`. It also scans everything under it for
  deleted-code names and retired prices, so the guide gets the same drift check
  as the book.

## The v1 cut

`outline.md` lists 15 chapters. Don't write 15 before the waitlist says anyone
wants them. The v1 cut is 8 chapters plus the guardrail appendix (marked **v1**
in the outline), about 20,000 words. That's the size of the Pro operator book
(~23,000 words), which the same drafting process has already produced once.

## Turning it on

Nothing below is automated. Each step is yours.

**The waitlist (can go live before a word of v1 is written):**

1. **Before a second product exists in the store,** set
   `pro_delivery_ls_product_id` to the Pro product's id
   (`poindexter settings set pro_delivery_ls_product_id <id>`).
   `docs/operations/pro-delivery.md` asks for this once the store sells more
   than Pro. A one-time $0 product creates orders, not subscriptions, so the Pro
   sync would not invite its sign-ups anyway, but the filter makes that
   explicit.
2. **Create the waitlist in Lemon Squeezy:** a free, one-time "notify me"
   product. Its description and receipt should say that the guide ships as part
   of Pro, that it isn't sold separately, and that signing up means one email
   when it's out.
3. **Wire the storefront** in `web/storefront/lib/site.config.js`:
   - `LS_FIELD_GUIDE_WAITLIST_URL` = the product's buy URL
   - `FIELD_GUIDE_LIVE = true`

   The page goes live at `gladlabs.ai/field-guide`, a "Field guide" link
   appears in the storefront nav, and `/guide` gains a "Coming to Pro" line.

4. **Point the story post at it:** the closing lines of
   `marketing/launch/copy/01-story-post.md` carry the call to action.

**Shipping the guide:**

5. **Add it to the Pro repo** under `book/field-guide/`, with a `CHANGELOG.md`
   entry.
6. **Set `FIELD_GUIDE_SHIPPED = true`.** The page swaps the waitlist for the Pro
   trial button, and `/guide` lists the guide as part of Pro.
7. **Update the canonical offer:** move the guide into the table in
   `marketing/pro-offer.md`, then walk that file's list of surfaces (README,
   SUPPORT.md, the docs pages).
8. **Email the waitlist once:** export the sign-ups from Lemon Squeezy.

## What would make this a bad idea

- **The waitlist stays empty after the story launch.** Then the method isn't
  the product. Put the effort back into the engine per the launch plan's
  decision rules.
- **It eats the evenings the launch needs.** The waitlist needs only the page,
  the outline and the sample chapter, which now exist. Writing the full v1
  should wait until the waitlist says it's wanted.
