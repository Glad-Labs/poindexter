# Launch asset drafts — 2026-04-24

All copy below is first-draft. Edit in your own voice before posting.

**Target channels:**

1. Show HN (early AM ET, ~8am)
2. r/selfhosted
3. r/LocalLLaMA (bonus — same body as r/selfhosted, minor framing)
4. X thread
5. Loom demo (script below; you record it)

**Common assets both subreddits and HN need:**

- Screenshot or GIF of the pipeline running — suggest the Grafana "QA Observability" dashboard with a task in flight, OR a terminal with `poindexter tasks list` + a freshly approved post
- GitHub repo link: https://github.com/Glad-Labs/poindexter
- Loom URL (record from the script below)

---

## Show HN

**Title (80-char limit):**

```
Show HN: Poindexter – self-hosted AI content pipeline with multi-model QA
```

(Alternates: "Show HN: An AI writer that doesn't hallucinate as much" — more provocative but harder to justify; the first option is cleaner.)

**Body (~200 words, HN likes terse and specific):**

```
Poindexter is an open-source content pipeline that researches, writes, reviews,
and publishes blog posts on your own hardware. Ollama-only inference, AGPL,
built for a solo operator on a single workstation.

The whole thing exists because every AI content tool I tried either
(a) hallucinated a fake Dr. Chen and "a 47% increase" on every page,
(b) locked the prompts behind a SaaS pricing page, or
(c) wasn't forkable. So I built the thing I wished existed.

What's in the repo:
- 12-stage pipeline: discovery → research → draft → self-review → multi-model
  QA → image → SEO → publish. Every stage is a plugin; swap or add without
  forking.
- Programmatic validator with hallucination detection — catches fake people,
  stats, citations, dead links, and named sources without URLs. Shipped
  fixes today for four false-positive classes found during stress testing.
- Multi-model QA: writer + LLM critic + gate reviewers (URL, consistency,
  web fact-check, vision). Aggregated score, rewrite loop, human approval
  queue.
- Runs on any RTX 3060 or better with Ollama. Matt's daily driver is a 5090.

Stack: Python 3.12 + FastAPI + asyncpg + PostgreSQL 16 + pgvector + Grafana +
Ollama. 5,000+ tests.

Free engine + optional $9/mo Pro subscription with tuned prompts + premium
dashboards. 7-day free trial. Everything is in the GitHub repo either way;
Pro just gives you my production config.

Feedback welcome. I'm the operator and the maintainer — there's one of me.
```

---

## r/selfhosted

**Title:**

```
I built a self-hosted AI content pipeline that runs on my own GPU — 100% Ollama, full source, AGPL
```

**Body (~300 words, Reddit rewards concrete + specific):**

```
TL;DR: Open-source AI content pipeline. Runs on your hardware with Ollama.
Catches hallucinations. No cloud dependency. Docker Compose up.
https://github.com/Glad-Labs/poindexter

---

I was going to subscribe to yet another "AI writer" SaaS, then realized the
real cost wasn't the $40/month — it was handing over my brand voice to a
vendor I had no visibility into. So I built the thing I wanted.

**What's in it:**

- **Pipeline**: 12 sequential stages from topic discovery through publish.
  Research (HackerNews + Dev.to + your own knowledge base), multi-model QA
  (writer + critic + gate reviewers), image generation (SDXL local, Pexels
  fallback), SEO metadata, podcast script, human-approval queue. Every stage
  is a plugin file — add new ones without forking.

- **Hallucination detection**: a programmatic validator that catches fake
  people ("Dr. Chen"), fake stats ("47% increase"), fake papers, dead links,
  unlinked "according to the documentation" citations. Tonight's stress test
  found four validator false-positive classes and they're all fixed —
  regression tests and commit history in the repo.

- **Observability**: Grafana dashboards showing rejection rate, per-writer-
  model approval rate, hallucination warnings by category, score
  distributions. Because flying blind sucks.

- **Self-hosted, full stack**: FastAPI worker, PostgreSQL 16 + pgvector,
  Ollama, SDXL server, Grafana + Prometheus + Loki, pgAdmin. All containerized.

**Hardware:** RTX 3060 minimum (8GB VRAM), 5090 is what I run. CPU-only
works but you'll wait.

**Cost:** engine is AGPL-free. Optional Pro subscription ($9/mo, 7-day trial)
unlocks my production prompts + 5 premium Grafana dashboards + the operator
book. Repo works without it.

The repo is still alpha. Publish cadence I can sustain is ~1 post/day at a
quality score above 80. Your mileage will vary based on writer model and
topic. Happy to answer config questions in the comments — ping with your
setup.
```

(For **r/LocalLLaMA**, same body but change the opening to emphasize model
choice — "Writer model is swappable per-task via `pipeline_writer_model`
app_setting. Tested: glm-4.7, qwen3:30b, gemma3:27b, phi4:14b, llama3.3:70b.
Here's what I found…" — then pivot into the comparison data from
`docs/experiments/pipeline-tuning.md`.)

---

## X thread (10 posts, 280-char budget each)

**Post 1 (hook):**

```
I open-sourced my AI content pipeline.

Runs on your own GPU with Ollama.
Catches hallucinations.
Writes one post per day at a quality score above 80.
AGPL, forkable, no cloud dependency.

Demo + repo: [Loom link] [GitHub link]

🧵
```

**Post 2 (pain):**

```
Every AI content tool I tried did one of three things:

1. Hallucinated "Dr. Chen" and "a 47% increase" on every page
2. Locked prompts behind SaaS pricing
3. Wasn't forkable

So I built the thing I wished existed. It's called Poindexter.
```

**Post 3 (how it's different):**

```
Poindexter runs a 12-stage pipeline per task:

topic → research → draft → self-review → multi-model QA → image →
SEO → finalize → human approval → publish

Each stage is a plugin. Add new ones without forking. Swap the writer
model via one DB row.
```

**Post 4 (hallucination defense):**

```
The programmatic validator catches:
- Fake people (Dr. / CEO / VP titles without sources)
- Fake stats (any %% claim without research context)
- Dead links and hallucinated URLs
- "According to the documentation" without naming which docs
- Fictional library/API references

Shipped fixes for 4 false-positive classes last night.
```

**Post 5 (multi-model QA):**

```
Every draft gets reviewed by:
- Programmatic validator (deterministic rules)
- LLM critic (gemma3 in my setup; swappable)
- URL verifier (HTTP HEAD on every citation)
- Web fact-check (external claims)
- Internal consistency gate (section-to-section tension)

Below threshold? Rewrite loop. Still below? Reject.
```

**Post 6 (observability):**

```
Grafana dashboards (screenshot attached):
- Rejection rate over time
- Approval rate by writer model
- Hallucination warnings by category
- Score distribution — approved vs rejected
- Top rejection reasons

You see what the pipeline sees.
```

**Post 7 (self-hosted tax):**

```
No paid APIs. No vendor-locked prompts. No data leaving your machine.

Stack: Python 3.12, FastAPI, asyncpg, PostgreSQL 16 + pgvector,
Ollama, SDXL, Grafana + Prometheus + Loki.

5,000+ tests. Docker Compose. `poindexter setup` wizard writes
~/.poindexter/bootstrap.toml.
```

**Post 8 (pricing):**

```
Engine is free (AGPL).

Optional Pro subscription — $9/mo or $89/yr with a 7-day free trial.

Pro includes my production prompts (continuously tuned), 5 premium
Grafana dashboards, fact-override DB, 200+ tuned settings, the full
operator book, VIP Discord.

Cancel anytime, keep what you downloaded.
```

**Post 9 (the honest part):**

```
What it isn't:
- A hosted SaaS (run it yourself)
- Multi-tenant (one operator, one machine)
- Bug-free (alpha — database schema moves)
- For non-technical folks (there's a 30-min setup)

I run Poindexter daily for gladlabs.io. If you want what I built,
come use it.
```

**Post 10 (CTA):**

```
Repo: https://github.com/Glad-Labs/poindexter
Docs: https://github.com/Glad-Labs/poindexter/tree/main/docs
Pro: https://gladlabs.ai/guide
Loom: [5-min walkthrough]

If you install it and hit a wall, open a GitHub issue. I read them.
```

---

## Loom script (5-7 min walkthrough)

**Setup: record at 1080p. Two tabs open — terminal and Grafana. Optional third tab for the README.**

**[0:00] Opening (30s)**

"Hey, this is Poindexter — an open-source AI content pipeline that runs on
your own hardware. I built it because every AI content tool I tried either
hallucinated like crazy, hid the prompts behind SaaS paywalls, or wasn't
forkable. Let me show you what it does."

[Show the README.md in the browser — the tagline and feature table.]

**[0:30] What runs where (60s)**

"The whole stack is self-hosted. FastAPI worker here, PostgreSQL 16 with
pgvector, Ollama for inference — I'm running a custom-tuned glm-4.7 model on
a 5090, but anything from a 3060 up works. SDXL for image generation.
Grafana + Prometheus for observability."

[Show `docker ps` in terminal — all containers running.]

**[1:30] Creating a task (90s)**

[Switch to terminal.]

"Let's ship a post. I'll use the CLI."

```
poindexter tasks create "Self-hosting Qwen 3 on a 5090" --category technology
```

"That queues a task. The background executor picks it up within 5 seconds and
starts the pipeline."

[Switch to Grafana — QA Observability dashboard.]

"Here's the pipeline in real-time. You see the task showing up in 'Tasks in
Window' and the approval rate tracking as it finishes."

**[3:00] The validator catching hallucinations (90s)**

[Scroll to the 'Top Rejection Reasons' table.]

"Here's why I built this — the programmatic validator catches what LLMs
hallucinate. Fake people, fake stats, fake papers, dead links, unlinked
'according to the documentation' citations. Earlier tonight I ran six batches
of the same ten topics across different writer models. qwen3:30b fabricated
Dr. Chen and a 12% drop in stats; glm-4.7 did zero fabrications on the same
topics."

[Show the pipeline-tuning.md experiment log.]

"Every batch is logged. Every rejection has a reason. You can see what the
pipeline sees."

**[4:30] Multi-model QA (60s)**

[Show one rejected task's error message.]

"Each draft runs through multiple reviewers — programmatic validator, LLM
critic, URL verifier, consistency gate, web fact-check. Below threshold? The
pipeline rewrites up to twice. Still below? Reject with a specific reason.
Nothing garbage ships."

**[5:30] Human approval (60s)**

[Switch to the approval queue view — show an awaiting-approval post.]

"Approved posts land here. I review them on my phone via Telegram or Discord
before they go live. No auto-publish by default. Click approve, Poindexter
pushes static JSON to R2, the Next.js public site revalidates, post is live."

**[6:30] Close (30s)**

"Free and open source. Optional Pro subscription gets you my production
prompts and premium Grafana dashboards. Seven-day trial. Repo link's in the
description. If you install it and something breaks, open an issue — I read
them."

---

## Launch-day checklist

- [ ] Loom uploaded, public URL obtained
- [ ] Show HN posted at 8:00-8:30am ET
- [ ] r/selfhosted post submitted within 30 min of HN post
- [ ] r/LocalLLaMA variant posted 30-60 min later
- [ ] X thread posted with Loom embedded in post 1
- [ ] First hour: check HN + Reddit every 10 minutes to answer questions
- [ ] Note any "where do I find X" questions — those become README gaps to fix
- [ ] Screenshot any positive quotes for the gladlabs.ai landing page later

**If HN gains traction:** don't respond defensively. Specific > clever. If
someone says "this is just another AI content farm tool" — show them the
hallucination-catching validator output. Proof beats argument.
