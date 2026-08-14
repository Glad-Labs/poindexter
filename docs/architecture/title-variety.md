# Title variety — how titles are steered away from the house formula

Poindexter titles posts on two independent paths, and both were producing
recognisably formulaic titles. Two mechanisms address that, each calibrated
against the live corpus rather than guessed:

1. **Variety guidance** — describes the recent corpus's structural and lexical
   _habits_ in the title prompt, so the model reaches outside them.
2. **Duplicate detection** — compares the candidate against titles we have
   already published or queued, and routes a real collision back through the
   regeneration loop.

Issues: Glad-Labs/poindexter#1043 (variety), #1044 (duplicate detection).

## The measurement that motivated it

151 published posts over 120 days, split by the graph that titled them:

| path             | n   | dominant habits                                                                         |
| ---------------- | --- | --------------------------------------------------------------------------------------- |
| `canonical_blog` | 62  | 50% opened with a leading `"The "`, 38% carried a colon subtitle                        |
| `dev_diary`      | 82  | 36% joined two ideas with `"and"`, 17% opened on a gerund (Hunting / Chasing / Locking) |

The two paths had _different_ formulas because they have different machinery —
not because anyone chose two house styles.

## Two title paths

**`canonical_blog`** titles at the `content.generate_title` node
(`modules/content/atoms/content_generate_title.py`), which calls
`services.title_generation.generate_canonical_title`. It always had an
avoidance mechanism: the 20 most recent published titles, pasted into the
prompt under an `AVOID SIMILARITY` banner.

**`dev_diary`** never runs `content.generate_title` at all. Its title comes
from the `TITLE:` line the writer emits inside `atoms.narrate_bundle`, parsed
back out by `_parse_title_and_prose`. Before #1043 that path had **no
avoidance mechanism of any kind** — no recent-title context, no originality
check, no `pipeline_title_model` involvement — and it is the larger half of
published output.

`modules/content/writer_core.py` holds a third copy of the canonical_blog
titling sequence for the legacy `generate_content` path.

## Why the old avoid-list made things worse

Handing a model twenty titles that are themselves ~50% `"The …"` is few-shot
priming toward the habit. One sentence asking for something "DISTINCTLY
DIFFERENT" does not outweigh twenty worked examples of the pattern — and the
measurement above is what that looks like in production: the path _with_ the
avoid-list had the strongest single habit of either path.

So `services/title_avoidance.py` describes the corpus's **habits** instead of
showing its **titles**. It profiles the recent window, names the structural
and lexical patterns that are over-represented, and asks for a title outside
them. Nothing from the corpus is quoted.

What the model actually receives, rendered from the live window:

```
TITLE VARIETY — recent titles on this site have settled into these habits:
- About half split into a headline and a colon subtitle.
- Well over a third join two ideas with "and".
- Roughly a third open on an "-ing" verb (Hunting, Chasing, Locking).
- Roughly a third open with a leading "The".

Write a title that reads as though it came from outside that run — reach for a
structure and vocabulary none of the above describes. Accuracy outranks
variety: a title the article does not support is worse than a familiar one.
```

The closing clause is load-bearing. Without it a model told to be different
will invent a detail to be different _about_ — trading a repetitive title for
an inaccurate one.

### The one case that still lists titles verbatim

A confirmed near-duplicate from `check_title_originality` renders as an
explicit list. Those are specific collisions to dodge, not a corpus to
imitate, so naming them is correct. This holds even when
`title_avoidance_mode='off'` — turning off the variety strategy must not
disable collision avoidance.

## Detected patterns

`analyze_title_patterns` is pure and deterministic — no LLM, no IO. It reports
a structural pattern when it covers `title_avoidance_pattern_threshold` or
more of the window, and a content word when it recurs in
`title_avoidance_lexical_min_count` or more titles.

| pattern          | fires on                                                               |
| ---------------- | ---------------------------------------------------------------------- |
| `leading_the`    | title starts with `"The "`                                             |
| `colon_subtitle` | a colon splitting headline from subtitle (trailing colons don't count) |
| `and_compound`   | two ideas joined with `"and"`                                          |
| `gerund_open`    | first word is a >4-char `-ing` verb (so "King"/"Ring" don't fire)      |
| `question_open`  | opens on Why / How / What / Should / Is / Can …                        |
| `listicle`       | leads with a number, or "N ways/reasons/steps/…"                       |

Structural and lexical are **independent axes with their own settings** —
raising the pattern threshold does not silence over-used vocabulary.

A word is counted once per title: a single title saying "gap" three times does
not make "gap" a corpus-wide habit. Structural function words and the site's
unavoidable subject nouns are excluded — telling the model to stop saying "AI"
on an AI blog is not useful guidance. The named-word list is capped, because
past a handful it reads as a banned-word list and pushes the model into
contortions to avoid accurate terminology.

## Settings

| key                                 | default    | what it does                                                      |
| ----------------------------------- | ---------- | ----------------------------------------------------------------- |
| `title_avoidance_mode`              | `patterns` | `patterns` (habits) / `titles` (legacy raw dump) / `both` / `off` |
| `title_avoidance_recent_count`      | `20`       | size of the recent-published-title window                         |
| `title_avoidance_pattern_threshold` | `0.20`     | share of the window a structural pattern must cover to be named   |
| `title_avoidance_lexical_min_count` | `3`        | times a word must recur to be called out                          |

Internal-duplicate detection (below) has its own keys:

| key                                   | default | what it does                                          |
| ------------------------------------- | ------- | ----------------------------------------------------- |
| `title_internal_similarity_enabled`   | `true`  | master switch for the internal-corpus check           |
| `title_internal_similarity_threshold` | `0.58`  | similarity at which a candidate counts as a duplicate |
| `title_internal_corpus_limit`         | `500`   | how many already-used titles to compare against       |

`title_avoidance_mode='titles'` restores the exact pre-2026-08 behaviour
without a deploy, which is what makes the change A/B-able against the corpus
it is meant to fix.

The window is niche-blind — it reads recent published titles across every
niche, matching the behaviour it replaced. A cross-niche variety push is the
desired signal; per-niche windows would be a reasonable refinement if the two
voices should diverge deliberately.

## Two originality axes, deliberately separate

`check_title_originality` now answers two different questions, each with its
own switch and threshold. Conflating them is what let the gap below exist.

| axis         | compares against              | threshold                                    | question                       |
| ------------ | ----------------------------- | -------------------------------------------- | ------------------------------ |
| **External** | DuckDuckGo results            | `qa_title_similarity_threshold` (0.6)        | did we restate someone else's? |
| **Internal** | titles we published or queued | `title_internal_similarity_threshold` (0.58) | did we already write this?     |

`is_original` is false when **either** trips, which is what routes a collision
into the regeneration loop that `content_generate_title.run` already had.

The internal check runs even when `qa_title_originality_enabled=false`: those
flags govern unrelated behaviours, and folding them together would mean
disabling the web check silently stops detecting duplicates of our own posts.

**Until 2026-08-14 the internal axis did not exist.** `check_title_originality`
read like an internal-diversity gate and was purely external, so nothing
compared our titles to each other — _"The Shift to Native Telemetry"_ and
_"The Shift to a Native UI"_ score **0.755** and both shipped.

### Calibration

Measured on 179 published titles / 9,870 pairs, excluding the ~38 legacy
`"What we shipped on <date>"` dev_diary titles which form one degenerate 0.96+
cluster:

    mean 0.271    p50 0.270    p90 0.368    p99 0.471    max 0.755

Genuine near-duplicates live in a thin tail well clear of the bulk:

| similarity | pair                                                                                                |
| ---------- | --------------------------------------------------------------------------------------------------- |
| 0.755      | "The Shift to Native Telemetry" / "The Shift to a Native UI"                                        |
| 0.697      | "The 32GB Threshold: How the RTX 5090 Redefines…" / "The 70B Threshold: How the RTX 5090 Rewrites…" |
| 0.615      | "Hunting Ghosts in the Middleware" / "Hunting ghosts in the metrics…"                               |
| 0.588      | "Silent Failures and Crying Wolf" / "Where the Silent Failures Were Hiding"                         |

0.58 admits all four (12 of 9,870 pairs, 0.12%). 0.60 would drop the last two,
which are exactly the confusable kind. A false positive costs one extra LLM
call and is discarded unless it ranks better, so the asymmetry favours
catching more.

### What counts as "taken"

Published **plus** `approved` and `awaiting_approval`. An awaiting-approval
post has a title a reader will see; excluding it lets two in-flight posts
collide with each other and nothing notice until both ship. `exclude_task_id`
keeps a re-run from matching the title it wrote last time.

### Ranking, not just detection

`originality_rank` orders two reports by `(axes_still_colliding,
worst_similarity)` — lower is better. Clearing a collision axis outranks any
similarity delta; the float only breaks ties.

This matters because the pre-#1044 comparison read `max_similarity`, which is
**external-only**. A regenerated title that fixed an internal duplicate while
scoring identically against the web was discarded as "not more unique" —
silently defeating the gate it was supposed to serve.

### It is a soft gate, on purpose

A duplicate that survives regeneration still ships: a slightly repetitive
title beats no post. But it emits a `title_internal_duplicate` finding
(severity `info`, deduped per task) so it lands on the Findings board. That
finding is the **only** signal the threshold or the avoidance prompt needs
attention — if it starts firing regularly, tune before the corpus drifts.

An unreadable corpus reports `internal_fail_open=True` rather than "original",
per the QA-rail fail-open contract: a degraded check is never a fabricated
pass.

## Where the prompts live

Per `feedback_prompts_must_be_db_configurable`, the standing directives are in
the SKILL.md packs, not in code:

- `seo.generate_title` → `src/cofounder_agent/skills/content/seo-metadata/SKILL.md`
- `atoms.narrate_bundle.system_prompt` → `src/cofounder_agent/skills/content/atoms/SKILL.md`

`narrate_bundle` also carries an inline fallback constant used only when the
prompt registry is unreachable (bootstrap / tests). **The two must be kept in
sync** — the file's own docstring records a prior drift where the fallback
gained a `TITLE:` contract the DB prompt lacked.

The measured, corpus-specific habits are _not_ in the packs — they are
computed per run and appended, because they change as the corpus changes.
