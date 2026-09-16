# Poindexter Launch Plan

**Goal:** get Poindexter in front of the people who'd actually run it — solo operators, self-hosters, local-AI enthusiasts — using pre-written artifacts instead of performance, inside a 9pm–1am work window.

**The division of labor, up front:** Claude drafts every piece of writing in this plan. Your job is three things only: record what's on your screen, edit drafts for truth, and press buttons. Estimated _your-time_ is listed on every task. Nothing here requires charisma.

---

## Operating rules

1. **One task per night.** Each task below fits in one 9pm–1am session (most take far less). Never start two.
2. **Product freeze.** No new features until Phase 2 is complete. Bug fixes only. The product stopped being the bottleneck months ago.
3. **The tripwire (replaces a deadline).** You said no deadline — fine. Instead: **launch goes live the first Tuesday after the last Phase 1 checkbox is ticked.** No re-polishing pass, no "one more thing." The tripwire is the deadline.
4. **Launch days are phone-compatible.** Posting takes 2 minutes from your phone. Comment replies can happen at lunch and after bedtime. No launch step requires daytime desk hours.
5. **When in doubt, ship the honest version.** The README's register — real numbers, real rough edges — is the voice for everything. It's also the one you don't have to fake.

---

> **Status as of 2026-09-16** (audited against the repo, not from memory):
> **1.4 the copy pack is DONE** — all five artifacts exist, and every factual
> claim in them has been verified against the live system (stack#3818/#3819/#3822).
> **1.1 Evening A is DONE** — the capture toolkit is committed at `scripts/demo/`.
> Remaining before launch: **record the demo** (1.1 Evening B, needs you and a
> running stack), **three README items** (demo config, the one-line provenance
> link, embedding the GIF), **the funnel check** (1.3), and the four remaining
> `[FILL]`s in the copy — now down to the 1am failure, the relicensing reason,
> and tooling/subscription spend.

## Phase 1 — Prep (≈ 6 evenings, ~8–10 hours of your time)

### 1.1 Demo GIF/video — 2 evenings ~ 3 hrs · **[YOU record, Claude scripts]**

The single highest-value asset you're missing.

- [x] Evening A (DONE 2026-09-16 — `scripts/demo/` committed: build-demo.sh, VHS tape templates, capture-frames.mjs, README): Have a Claude Code session extend `scripts/capture-readme-screenshots.mjs` (or use asciinema + a screen recorder) into a capture plan for the core loop: `poindexter tasks create` → Grafana pipeline filling in → draft landing in approval queue with QA scores → approve → post live on gladlabs.io.
- [ ] Evening B: Record it against your running stack, trim to 30–60 seconds, compress (GIF <10 MB for GitHub, or an MP4 link). Place it directly under the banner in the README.
- Done when: a stranger can watch the full loop without reading a word.

### 1.2 README fixes — 1 evening ~ 1.5 hrs · **[CLAUDE DRAFTS, you review]**

- [x] Retitle away from "factory" → lead with rejection (e.g., "the content pipeline that rejects half of what it writes").
- [x] Put the actual Pro price in the tier table. **(DONE 2026-09-16 — $19/mo / $180/yr now in the table, replacing the "See gladlabs.ai" deferral.)**
- [ ] Add a "kick the tires in 10 minutes" demo config (one small model in every role, clearly labeled demo-quality) for people below 8 GB VRAM or below 30 GB of patience.
- [ ] Add one line + link in Project Status: built by one person directing AI agents (links to the story post from 1.4).
- [x] Enable GitHub Discussions (verified on 2026-09-16) and seed it with 2–3 starter threads (a welcome/intro thread, a "what are you running it on?" thread).

### 1.3 Funnel check — 1 evening ~ 1 hr · **[YOU]**

Before sending traffic, make sure it has somewhere to land.

- [ ] gladlabs.ai: price visible, checkout actually works end-to-end (test it yourself), page loads fast.
- [ ] Newsletter signup works on gladlabs.io; add one to the docs site if trivial.
- [ ] Confirm GitHub repo Insights → Traffic is something you know how to read (it's your scoreboard for the next 90 days).

### 1.4 The launch copy pack — 2 evenings ~ 3 hrs of editing · **[CLAUDE DRAFTS ALL]**

Claude writes all of these; you edit for truth only. Five artifacts:

- [ ] **The story post** (canonical, on gladlabs.io dev diary): _"One person, ~10,500+ commits, zero hand-written code: a year of directing Claude Code to build a content pipeline."_ This is your best material — the thing you've been calling your fraud is the hook. Honest about what worked and what was painful.
- [ ] **Show HN submission**: title options + a first-comment written in your register (who you are, why local-first, why it rejects half its drafts, what's rough). Show HN norms: it must be something people can try, no signup wall — you're fine on both.
- [ ] **r/LocalLLaMA post**: leads with the cross-family QA design (gemma3 writes, phi4 criticizes — biases don't cancel), exact models and VRAM numbers. That sub rewards precisely this level of detail.
- [ ] **r/selfhosted post**: leads with Docker, the watchdog daemon, self-healing, DB-as-config, S3-push output. Different audience, different hook.
- [ ] **The FAQ crib sheet** (private, for you): pre-written answers to the predictable comments — "isn't this just slop generation?", "why not write it yourself?", "did an AI write this reply?", licensing, benchmark asks, "what's the catch with Pro?". You will be answering these at 10pm after a full day of job and kids; the crib sheet means you're never improvising while exhausted. This is the confidence prosthetic.

**Phase 1 exit → tripwire arms:** launch is the first Tuesday after all boxes above are ticked.

---

## Phase 2 — The launch sweep (3 "on" days + 2 quiet evenings, spread over ~2–3 weeks)

Staggered on purpose: each post is a feedback dry-run for the next, and simultaneous cross-posting splits attention and reads as spam.

### 2.1 Launch #1: r/LocalLLaMA — evening-friendly dry run

- [ ] Re-read the sub's current self-promo rules the night before (they shift; open-source + technical detail is the safe lane).
- [ ] Post at ~9pm your time (the sub is global; timing barely matters). Engage that night, check phone next morning and at lunch.
- What you learn here (questions asked, objections raised) gets folded into the HN first-comment before launch #2.

### 2.2 Launch #2: Show HN — the big one, ~4–7 days later

- [ ] Night before: final read of submission + first-comment, crib sheet on your phone.
- [ ] Submit **Tue/Wed/Thu ~8–10am ET from your phone** (2 minutes — this is the one daytime action in the whole plan; conventional wisdom on timing, not gospel).
- [ ] Post your prepared first comment immediately after submitting.
- [ ] Check at lunch and breaks; real engagement session that night 9pm–1am. One day of presence, then you're released.
- [ ] If it stalls (<10 points, little discussion): that's normal, not a verdict — HN's own FAQ allows a small number of reposts for stories that got no significant attention. Wait 3–4 weeks, improve the angle from what Reddit taught you, repost once.

### 2.3 Launch #3: r/selfhosted + the story post — the following week

- [ ] Publish the story post on gladlabs.io; syndicate to dev.to with canonical link.
- [ ] Post to r/selfhosted (their audience loves a well-tested self-hosted alternative to SaaS).
- [ ] The story post is also independently HN-submittable later — it's a second lottery ticket, separate from the Show HN.

### 2.4 The quiet sweep — 1–2 evenings, zero social exposure · **[CLAUDE DRAFTS PRs/blurbs]**

Pure introvert channels — submission forms and pull requests, no performing, and they compound for years:

- [ ] PR to **awesome-selfhosted** (read their CONTRIBUTING first — strict criteria, but your year of history and test suite are exactly what they filter for).
- [ ] Relevant **awesome-\*** lists: Ollama, LangGraph/LangChain, AI-agents lists.
- [ ] **alternativeto.net** listing — position as self-hosted alternative to Jasper/Copy.ai.
- [ ] **selfh.st** weekly roundup submission; **Console.dev** devtools newsletter submission form; Ollama community Discord showcase channel; LangChain community showcase if open.

---

## Phase 3 — The sustainable loop (~2–3 hrs/week, indefinitely)

This is the part sized for real life. Three habits, nothing else:

1. **First-10-users white glove.** Anyone who opens a Discussion, issue, or installs it gets a real, prompt, warm reply. Ten actual users teach you what Pro should be; they are worth more than 10,000 pageviews. This is the highest-ROI hour of your week.
2. **One dev-diary post a month** — Claude drafts it from your changelog + whatever you fought with that month; you edit for truth; syndicate to whichever channels responded in Phase 2. Release-note posts to r/selfhosted when there's a meaty release.
3. **Scoreboard, monthly, 15 minutes:** GitHub stars + traffic + clones, Discussions activity, newsletter signups, Pro page clicks. Pageviews on gladlabs.io are explicitly **not** on the scoreboard.

---

## What the next 90 days should tell you

- **Good signal:** 100+ stars, a handful of real installs asking questions, first newsletter subscribers, any Pro inquiry. → Double down on whatever channel produced the users.
- **Mixed signal:** stars but silence — people find it interesting but aren't running it. → The friction is setup cost; invest in the 10-minute path and a hosted demo, not new features.
- **Silence everywhere after the full sweep including one HN repost:** that's real information a year of building couldn't give you — the self-hosted content-automation market may be too small. Then the pivot conversation is about the _skill_ (you directing AI agents to production quality is consultable, tonight, without an audience), not more channels. But you don't get to conclude this until the sweep is actually done.

---

## Total cost of this plan

|         | Your hours | Calendar              |
| ------- | ---------- | --------------------- |
| Phase 1 | ~8–10      | ~2 weeks at your pace |
| Phase 2 | ~8–10      | ~2–3 weeks            |
| Phase 3 | 2–3/week   | ongoing               |

Roughly five weeks of evenings, three days of being visible, everything pre-written before anyone can see you. That's the whole price of finding out whether a year of work has a market.
