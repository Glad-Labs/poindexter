# Show HN submission

**Title options (all ≤80 chars, in order of my preference):**

1. `Show HN: Poindexter – a content pipeline that rejects half of what it writes`
2. `Show HN: Local-first publishing pipeline with adversarial multi-model QA`
3. `Show HN: I run an autonomous blog on one GPU. It rejects 40% of its drafts, and I reject two thirds of what's left`

**URL:** `https://github.com/Glad-Labs/poindexter`

---

## Submission text

(Show HN allows text alongside the URL. Keep it short — the first comment carries the story.)

> Poindexter is an open-source pipeline that discovers topics, researches them, writes long-form posts with local models (Ollama), then runs every draft through 16 QA rails — a critic model from a different family than the writer, deterministic anti-hallucination validators, citation checks against the research corpus. About most drafts don't survive. The ones that do publish to static JSON on any S3-compatible storage.
>
> It's the production system behind gladlabs.io (207 live posts, 2,100+ pipeline runs), running on one RTX 5090 in my house. Apache 2.0, self-host, alpha — rough edges are listed honestly in the README.

---

## Your first comment

(Post this immediately after submitting, from your account.)

> Author here. Some context that doesn't fit in a README:
>
> I'm one person with a full-time job and two small kids; this got built roughly 9pm–1am over the past year. The part I was embarrassed about until recently: I wrote almost none of the ~518k lines of Python by hand. I directed Claude Code sessions — wrote specs, reviewed output, rejected a lot of it — and enforced an 18,000-test CI gate so agent regressions can't land. Whether that counts as engineering is a fair thing to argue about in this thread; I've made my peace with it.
>
> The design idea I care most about: generation is cheap, so the system optimizes for rejection instead. In practice the rails kill about 40% of drafts and I approve a third of what survives — roughly 18 of every 100 drafts started get published. The writer is gemma3:27b and the critic is phi4:14b on purpose — different model family, so their failure modes don't cancel. Deterministic validators catch fabricated people, stats, and quotes; near-miss drafts get one bounded revision pass, then a hard reject. Roughly 50% of drafts die. The same idea applies to how it was built, honestly — my main job all year was rejecting work.
>
> What it's not: a hosted service (self-host only), stable (alpha, schema still moves between releases), or a way to flood the internet — the output feeds one site I run, and the whole point of the QA gauntlet is that publishing less, better, beats publishing more.
>
> Things I'd particularly value from HN: people who run local models telling me where the setup breaks on hardware that isn't mine, and honest reads on whether the anti-hallucination rails hold up against your test topics. Happy to answer anything, including the uncomfortable questions about AI-written content — I have opinions.

---

_Mechanics: submit Tue/Wed/Thu ~8–10am ET (folklore, not physics — don't agonize). Never ask anyone to upvote, and don't share the direct item link asking for support (HN penalizes voting-ring patterns; just post it and walk into your workday). Check at lunch; real session that night. If it gets <10 points and no discussion: normal, not a verdict — HN's FAQ permits a repost after a few weeks for stories that got no traction. Update this draft with whatever r/LocalLLaMA taught you before submitting._
