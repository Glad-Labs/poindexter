# FAQ crib sheet — private, never posted

For launch days, on your phone. These are pre-written so 10pm-after-work-and-bedtime brain never has to improvise under fire. Adapt wording so it doesn't read pasted; the _positions_ are the prepared part.

**Ground rules (read these twice before launch day):**

1. Concede fair points immediately and without drama. "Fair" is a complete sentence on HN and buys more credibility than any defense.
2. One reply per hostile commenter, maximum. State your position once; let it stand. Threads where the author fights are threads where the author loses.
3. Never reply to tone, only to content. If a comment has no content, it needs no reply.
4. You don't have to answer everything. Silence on a bad-faith question reads fine; visible exhaustion doesn't.
5. When you don't know, say "I don't know." It's the single most credibility-building move available to you.

---

## The identity questions

**"So you didn't actually build this — Claude did. What did you even do?"**

> Wrote the specs, reviewed every change, rejected the work that was wrong (a lot of it), designed the architecture constraints, and enforced the test gate that keeps 10k commits coherent. Whether that's "building" is a fair question — my honest answer is it felt like being an engineering manager with an infinitely fast, slightly overconfident team. The artifact ships daily and survives an 11,400-test suite; I'll let that carry the argument either way.

**"Did AI write this comment too?"**

> The project's whole premise is that I direct AI and take responsibility for what survives review, so — sometimes, sure. Same editorial standard applies to comments as to code: if it's wrong, it's my fault, not the model's.

(Note from Claude: answer this one in your own typed words in the moment if you can — it's the one place where a slightly rough, clearly-human reply is worth more than a polished one.)

**"This is why software engineering is dying / you're not a real engineer."**

> Maybe. I spent most of the year embarrassed about exactly this, so I'm not the guy who'll give you a confident speech about the future of the field. What I can report is one data point: this way of working produced a system I couldn't have built alone in the hours I have, and it only worked because of very traditional engineering discipline — tests, CI, review, saying no. Take from that what you want.

## The content-ethics questions

**"The internet is drowning in AI slop and you built a slop cannon."**

> The slop problem is real and I built this partly in reaction to it. The pipeline's defining feature is that it kills about half of what it generates — cross-family critic models, deterministic anti-hallucination validators, citation checks. It feeds one site I edit and take responsibility for, not a content farm. If your position is that no AI-written text should be published at all, that's coherent and we just disagree; but "generate less, reject more, sign your name to it" is my answer to slop, not my contribution to it.

**"Isn't this just SEO spam automation with extra steps?"**

> It would be a bad one — it publishes to a single site and the QA gauntlet exists specifically to throw away the high-volume low-quality output that SEO spam runs on. Nothing stops someone from forking it and pointing it at spam, the same way nothing stops them using WordPress for it. I made the thing I'd want to run; I can't make it impossible to run badly.

**"Why not just write the posts yourself?"**

> Full-time job, two small kids, and a 9pm–1am window that I chose to spend building the machine rather than being the machine. Also, honestly: the engineering was the part I loved. The writing was the part I automated. People automate the part they don't love; mine happened to be the words.

## The technical questions

**"How good is the output actually? Cherry-picked examples don't count."**

> gladlabs.io is the entire output, unfiltered — every published post came through the pipeline, and the rejects don't exist anywhere for me to hide. Pick any post at random; that's the honest sample. Some are better than others. The floor is what I'm proudest of, not the ceiling.

**"Local models are worse than cloud models. Why handicap yourself?"**

> They are worse, model for model. Three reasons anyway: marginal cost per draft is electricity, which changes what you can afford to reject (50% rejection on API pricing hurts); my data and drafts stay on my machine; and no per-token meter changes how you design — you can run 13 QA rails because they're free. Cloud models exist as an opt-in plugin behind a spend guard for people who want them.

**"What does it cost to run?"**

> Hardware was **[FILL: rough 5090 rig cost]**, and after that it's electricity — **[FILL: rough monthly kWh or $ if you know it]**. No API costs by default.

**"Why Postgres as a message bus? Why not Kafka/NATS/Redis?"**

> One operator, one machine, and Postgres was already the source of truth. Components communicating through tables means every message is queryable, durable, and debuggable with SQL at 1am, and there's one fewer service for the watchdog to babysit. At my scale the "Postgres doesn't scale as a bus" objection is a problem I'd be lucky to have.

**"Schema is unstable and it's alpha — why would I install this now?"**

> You might not want to, and the README says so. Install it now if you want to shape what it becomes or fork a working reference architecture; wait if you want stability. Migrations do run in-place with zero data loss so far — but "read the CHANGELOG before upgrading" is real advice, not boilerplate.

**"Does it really run on 8 GB VRAM?"**

> The core pipeline, yes — slowly, and image QA (qwen3-vl:30b) needs real headroom past that. My daily driver is a 5090, so 8 GB is the tested floor, not the recommended experience. If you try it on 8 GB I'd honestly love the bug reports.

**"How is this different from AutoGPT / agent frameworks / n8n + an LLM node?"**

> Those are toolkits; this is a shipped application with opinions. The pipeline is a declarative 44-node LangGraph DAG with Postgres checkpointing, the QA rails are the point rather than an afterthought, and it's been running a real publication daily for a year — 2,000+ runs. If you want to build your own thing, a framework is the right choice. If you want a working reference for local-first content automation, that's the gap this fills.

## The business questions

**"What's the catch with Pro?"**

> Engine's Apache 2.0 with nothing feature-gated. Pro is the production-tuned prompt packs and dashboard configs from my live system — the months-of-tuning layer, not the capability layer. **[FILL: price]**, and if you'd rather tune your own from the baselines, that's a fully supported path, not a crippled one.

**"Why Apache 2.0? / I saw it was AGPL before."**

> Relicensed from AGPL in April. **[FILL: your actual reason in one sentence — likely some version of: AGPL was scaring off the users I most wanted, and a solo alpha project needs adoption more than it needs copyleft protection.]**

**"Are you making money? What's the business?"**

> Not yet meaningfully — this launch is me finding out whether anyone besides me wants it. Pro exists, the site runs ads, and I have theories, but I'd be lying if I dressed that up as a business model with traction. Ask me in six months.
