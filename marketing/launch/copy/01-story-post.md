# The story post

**Title options (pick one):**

1. One person, 10,500+ commits, almost no hand-written code: a year of directing AI agents to build a content pipeline
2. I spent a year managing Claude Code sessions instead of writing code. Here's the 700,000-line result.
3. What I learned shipping 220 releases of software I mostly didn't type

---

I'm an engineer with a full-time job and two small kids. For about the last year, roughly 9pm to 1am, I've been building [Poindexter](https://github.com/Glad-Labs/poindexter) — an open-source content pipeline that discovers topics, researches them, writes long-form posts with local models, reviews its own drafts across 16 QA rails, rejects about half of them, and publishes the survivors to [gladlabs.io](https://www.gladlabs.io).

The part people find strange: I wrote almost none of the code by hand.

The repo is over 10,500 commits, about 700,000 lines of Python across 2,311 files, 18,000+ unit tests, 220 tagged releases. Nearly all of it was written by Claude Code sessions that I directed — specifying what to build, reviewing what came back, rejecting what wasn't right, and deciding what came next. I've been embarrassed about this for most of the year. I'd say "I didn't actually code it" the way you'd confess to something. I'm writing this post partly because I've stopped believing that framing.

## What "directing" actually means

It is not "type a wish, receive software." A typical night looks like: read the state of the system, pick the one thing that matters most, write a task description precise enough that a very fast, very literal engineer can't wander off, and let the session work.

What I do with the result is the part I had wrong for a long time. I don't really reject it. The output is too large to review the way you'd review a PR — by the time you've read it closely enough to reject it honestly, you could have refined it twice. So I take what comes back and fix what's broken or isn't good enough, and the refining is where my actual judgment goes. I haven't written code in months; I direct, then I repair. Subtly wrong is still far more dangerous than obviously wrong, and that danger is why the test gate below is non-negotiable — it catches what I've stopped pretending I'll catch by reading.

The skills that turned out to matter were not coding skills, exactly. They were: knowing what to build next, writing specifications that survive contact with a literal-minded reader, smelling when something is off before the tests catch it, and being willing to throw away work — the agent's and mine — without sunk-cost flinching.

## The test suite is the real product

The single decision that made this possible: tests are non-negotiable, and they gate every merge. AI agents have no memory of last month's regressions and no shame about reintroducing them. The 18,000-odd tests are the institutional memory a solo operation doesn't otherwise have. When a session breaks something, CI catches it before I merge; without that ratchet, a codebase built this way would rot in weeks. If you take one thing from this post: agent-built software without a hard test gate isn't a codebase, it's a pile.

The same philosophy ended up inside the product itself. Poindexter generates drafts and then tries hard to kill them — a critic model from a different family than the writer, deterministic anti-hallucination validators, citation checks against the research corpus. About four in ten drafts die at the rails — and of the ones that survive and reach me, I approve a third. Two gates stacked: the machine kills 40%, I kill two thirds of the survivors, and roughly 18 of every 100 drafts started end up published. It took me an embarrassingly long time to notice that the product and the process are the same idea: generation is cheap now; judgment is the scarce input. The two sides spend it differently — on the content I reject, on the code I refine — but it's the same scarce thing, and it's the whole job.

## Things that went wrong anyway

Some real entries from the changelog, because a year of this is not a montage of wins:

The memory and session ingestion taps silently pulled in nothing for 17 days before I noticed. Nothing crashed — that's why I didn't notice. The fix wasn't just the bug; it was building staleness detection so silence itself becomes an alarm. A telemetry crash 500'd every CORS preflight in the API. A reasoning model started leaking its `<think>` spiral into what was supposed to be structured JSON output, which is why composition is now grammar-constrained — the spiral literally cannot reach the wire. My VRAM estimator was wrong about sliding-window KV caches for months.

**[FILL: the failure that actually hurt — the one you remember at 1am. One paragraph, specific. This is the paragraph readers will trust the whole post because of.]**

## What this cost

Running it costs about $69 a month, measured over the last 30 days: roughly $59 of electricity (54 kWh) and $10 of API spend. **[FILL: what you paid for tooling and subscriptions on top of that — the Claude Code subscription is the number I can't read off the machine, and it's the one people will actually want.]** The hardware is one machine: an RTX 5090 and a 3090, 64 GB of RAM, running Pop!_OS in my house.

I should be precise about "local," because this is the claim I'd most want someone to push back on. The local models do the judging and the seeing — QA rails, the vision critic, title and metadata work, the aux tasks — and the shipped default writes drafts with a local model too. On my own install I've pinned the writer to Claude Sonnet, because on the thing readers actually read, it is still better than what I can run at home. That's the $10. So: local inference is real and load-bearing here, but if you install this today and change nothing, your drafts come from your own GPU, and mine don't.

The other cost is the one I'd warn you about. Building this way is so productive, and so private, that I spent a year doing almost nothing else. No launch, no posts, no community. As of this writing the repo has 5 stars. The machine got very good while nobody was watching — including me forgetting that "nobody is watching" is a choice you're making, not a fact about the world. This post is the start of correcting that.

## Where it stands

Poindexter is in alpha, Apache 2.0, self-host only, honest rough edges listed in the [README](https://github.com/Glad-Labs/poindexter). It runs a real publication daily — 207 live posts out of 2,115 pipeline runs. If you run local models and the idea of a pipeline that grades its own homework appeals to you, I'd genuinely like to know what breaks when someone who isn't me installs it.

And if you're an engineer wondering whether directing agents is real engineering: I spent a year embarrassed about it, and the artifact ships daily and survives its own test suite. Draw your own conclusion — I've drawn mine.

---

_Posting notes: publish on gladlabs.io dev diary as canonical. Syndicate to dev.to with `canonical_url` set. This post is separately submittable to HN later (plain link submission, not Show HN) — it's a second lottery ticket, at least a few weeks after the Show HN._
