# Launch checklist — the short version

The order to do things in. Times are **your** time; **[Claude]** marks work you
hand over. [`launch-plan.md`](launch-plan.md) wins if the two disagree. Checked
against the repo on 2026-10-05. The posts are in `copy/`: 01 story, 02 Show HN,
03 r/LocalLLaMA, 04 r/selfhosted, 05 FAQ sheet.

**While you work down this list:** one task per night · no new features · no
income claims in any post.

## 1. Field-guide waitlist — about 30 minutes, optional

Skipping it? Delete the bracketed paragraph near the end of `copy/01`.

- [ ] **Set the Pro product ID** on your PC, _before_ making a second product:
      `poindexter settings set pro_delivery_ls_product_id <id>` (the number in
      the Pro product's address in the Lemon Squeezy dashboard).
- [ ] **Create the waitlist product** in Lemon Squeezy: one-time, $0, described
      as "ships as part of Pro, not sold separately, one email when it's out".
      Send Claude the buy link.
- [ ] **[Claude]** switches the page on: sets `LS_FIELD_GUIDE_WAITLIST_URL` and
      `FIELD_GUIDE_LIVE` in `web/storefront/lib/site.config.js`. You merge the
      PR.
- [ ] **Skim the six chapter blurbs** at gladlabs.ai/field-guide for anything
      untrue.

## 2. Finish prep — launch is the first Tuesday after these are ticked

- [ ] **Funnel check** (1 hr): the price shows on gladlabs.ai; Pro checkout
      works end to end (buy it yourself, then cancel and refund); the
      newsletter signup works on gladlabs.io; you know where GitHub → Insights
      → Traffic is.
- [ ] **Record the demo** (about 3 hrs): setup in `scripts/demo/README.md`, then
      `cd scripts/demo && ./build-demo.sh` with the stack running. No demo is
      committed yet. Then ask **[Claude]** to embed the GIF under the README
      banner.
- [ ] **Read the launch posts once, for truth** (about 1 hr). No `[FILL]` blanks
      are left. The story post, `copy/01`, matters most.

Parked, not blocking: the README's "kick the tires" config (issue #4100).

## 3. Launch — three visible days over about two weeks

- [ ] **r/LocalLLaMA** (`copy/03`), about 9pm. Re-read the sub's self-promo
      rules the night before. Reply that night and at lunch; note any
      surprising questions.
- [ ] **Night before HN:** publish the story on gladlabs.io · add its URL to the
      README line under Project Status · refresh the numbers that drift · re-read
      `marketing/pro-offer.md` against the Pro repo's latest CHANGELOG.
- [ ] **Hacker News, 4–7 days later, Tue–Thu about 8–10am ET.** Submit the
      **story** as a plain link (not Show HN) from your phone, then post a first
      comment (adapt `copy/02`). Check at lunch, one evening session, done. If
      it stalls under 10 points, wait 3–4 weeks and repost once.
- [ ] **The next week:** r/selfhosted (`copy/04`) and the story on dev.to
      (canonical link to gladlabs.io).
- [ ] **Quiet sweep** (**[Claude]** drafts, 1–2 evenings): awesome-selfhosted
      PR, other awesome-\* lists, alternativeto.net, selfh.st, Console.dev,
      Ollama Discord.

## 4. After — 2–3 hours a week

- [ ] Reply fast and warmly to every first-time user. Ten real users beat
      10,000 pageviews.
- [ ] One dev-diary post a month (**[Claude]** drafts), plus a 15-minute monthly
      scoreboard: stars, traffic, Discussions, newsletter signups, Pro trials
      started and converted, waitlist signups. First money milestone: about 18
      monthly Pro subscribers.
- [ ] About 90 days after the sweep, read "Decision rules" in the plan.
