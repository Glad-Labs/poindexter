# YouTube metadata — what we send, and how to fix what we already sent

## What each upload carries

| Field       | Long form                                                                              | Short                                                                                                            | Limit enforced                 |
| ----------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| Title       | `posts.title`                                                                          | first sentence of its own narration (`short_summary_script`), ≤ `youtube_short_title_max_chars`, + `#Shorts`     | 100 chars (adapter clamps)     |
| Description | `posts.excerpt` · tagged back-link (`utm_medium=video`) · "Watch the Short" when live · optional body snippet | its first two sentences · "Watch the full breakdown" when the long form is live · tagged back-link (`utm_medium=shorts`) · `#Shorts` + N keyword hashtags | 4,800 composed / 5,000 API cap |
| Tags        | `posts.seo_keywords`, comma-split                                                      | same                                                                                                             | 30 tags / 500 joined chars     |

Composed by `services/jobs/youtube_payload.py`, dispatched by
`services/jobs/media_distribute.py`. The back-link carries
`?utm_source=youtube&utm_medium=video` (long form) or `utm_medium=shorts`
(Short) so a click from a description is attributable, and the two renders
stay separable in analytics — see
[distribution-attribution.md](../architecture/distribution-attribution.md).

**The description does not carry the article.** `youtube_description_body_chars`
defaults to `0`. Set it positive to append a sentence-trimmed plain-text
snippet after the link; it is deduped against the excerpt (which _is_ the
post's opening paragraph) and rendered through a markdown→plaintext pass, so
`[text](/go/…)` link syntax and `## heading` markers never reach a viewer.

This default was set 2026-08-31 after auditing the live channel: every one of
the 12 uploads to that point carried the entire stripped article as one
4,800-character paragraph, complete with raw markdown, a duplicated opener,
and a mid-word truncation.

## Editing videos already on the channel

```bash
# Dry run — prints what would be sent, touches nothing
poindexter integrations youtube sync-metadata

# One video or one post
poindexter integrations youtube sync-metadata --post dZxk7FuodZo

# Actually push
poindexter integrations youtube sync-metadata --apply
```

The sync recomposes from the post row using **the same builders the upload
path uses**, so a future change to the composition rules reaches new and old
videos through one code path instead of two that can disagree. Dry run is the
default because this writes to a public channel.

### A Short with a bad stored hook is repaired, not stumped

The gate that writes a good opening line runs at **script-generation** time.
This path can only SHORTEN, so a Short rendered before that gate existed keeps
whatever the model first wrote — and shortening an unfinished sentence gives a
title cut mid-phrase. Measured 2026-09-23, that was **4 of the 9** live Shorts:

```
The hidden debt of five tech giants, including Alphabet, Microsoft #Shorts
Imagine your child learning to code through an adventure filled with #Shorts
```

So `--apply` now repairs a Short whose stored hook has a CONTENT defect — a
question, a run-on, a restatement of the title — with one local LLM call, and
**writes the improved narration back to the task**. The cost is paid once, not
per sync, and the presenter says the better line on any future re-render.

Length alone is never a defect here: over-long is a shortening problem and the
title builder handles it, so a merely-long good claim buys no call.

A **dry run never repairs and never writes.** It still names the defects, so
you can see what `--apply` would fix without spending the calls to find out:

```
Q2Sc2niHIgI    312     4  The hidden debt of five tech giants…  ⚠ hook runaway — --apply would repair
```

The repair is **injected, not imported**: it lives in `modules.content` and the
sync is kernel, so the CLI owns the wiring and passes the callable in
(`scripts/ci/kernel_purity_lint.py`, poindexter#666). A caller that injects
nothing — `media_distribute`'s twin cross-link refresh, for instance — composes
from whatever is stored, which is deliberate: that path runs right after a
render whose script already passed the script-time gate, so a second call there
would buy nothing.

### What it can reach

`pipeline_distributions` is the list of uploads, and until 2026-09-01 it could
not hold all of them. The table was keyed `UNIQUE (task_id, target)`, but one
post ships **two** renders — a long-form `video` and a `video_short` — to the
same `target='youtube'` under the same `task_id`, so the second upsert
overwrote the first. Five of the twelve YouTube rows on prod were collisions;
in every one the Short inserted first and the long form clobbered it, leaving
the Short with no row at all. Nothing was lost outright —
`media_assets.platform_video_ids` keeps one handle per asset row and never
collided — but the sync reads the distribution table, so those Shorts were
simply invisible to it. They kept their 4,800-char description, never got the
`#Shorts` suffix, and stayed byte-identically titled to their long-form twin.

The table now carries a `medium` column and is keyed
`UNIQUE (task_id, target, medium)`. `medium` reuses the `media_approvals`
vocabulary (`video` / `video_short` / `podcast`); `'default'` is the sentinel
for a target that gets one undifferentiated artifact, which is every own-site
row (`target='site'` — see
[distribution-target-vocabulary](../architecture/distribution-target-vocabulary.md)).

`sync-metadata` also cross-checks itself against `media_assets` on every run
and prints a warning naming any handle that has no distribution row. It should
stay permanently silent — `media_distribute` writes both records in one
transaction, and the key now admits every render — but "should be impossible"
is exactly what was believed about the old key, and the failure mode is a sync
that covers N-1 videos and reports success.

`pipeline_distributions` remains the source it _reads_: it is the row the
dispatcher writes transactionally with the upload, and the only one carrying a
`status`, so it is the only place a deleted upload can be recorded.
`media_assets` is the cross-check, not a second source of truth.

**Why the medium and not the video id.** `(task_id, target, external_id)` looks
like the obvious key and is the wrong one twice over: `external_id` is NULL for
every blog-publish row and NULLs are distinct in a unique index, so those would
duplicate without bound — and a genuine re-dispatch mints a _new_ video id, so
it would append a second row rather than superseding the one it replaces. The
medium is stable across re-dispatch, which is what keeps the upsert's
"same render updates in place" contract.

### Videos that are no longer there

`--apply` also reconciles in the one direction the upload path never did. When
YouTube reports a video is not on the channel, the sync demotes its row to
`status='deleted'` and raises a `youtube_upload_vanished` finding.

Nothing else revisits that row, so before this a deleted upload claimed to be
published forever: it inflated the published count and failed every subsequent
`--apply`. Two rows on prod were in exactly that state, both uploaded
2026-06-15.

Only the API's own "not found" demotes a row — never a string match on the
error text, and never a dry run, which makes no call and so has no evidence. An
oembed 404 is _not_ sufficient either: a private video returns one too. The
demotion is keyed on the video handle, so a task's other render stays published.

### It needs a scope you probably don't have yet

`videos.update` requires `youtube.force-ssl`. The original consent requested
`youtube.upload`, which is **insert-only** — verified against the live token
on 2026-08-31, which came back holding exactly that one scope.

Scopes live in the _token_, not in our code: widening the constant grants
nothing. Re-consent once:

```bash
poindexter integrations youtube setup --with-update
```

No flags beyond that: when no client is named on the command line, setup reuses
the OAuth client already in `app_settings` from the first run — only the scopes
being requested differ on a re-consent. Pass `--client-secret-file` to switch to
a different OAuth client.

That opens a browser, requests `youtube.upload` + `youtube.force-ssl`, and
replaces the stored `refresh_token`. It is a superset — uploading keeps
working. Until you run it, `sync-metadata --apply` fails on the first video
with the remediation printed in full rather than a raw Google 403.

Plain `setup` (no flag) still requests upload-only, so an operator who never
edits metadata keeps the narrower grant.

## Never pin a scope list on the Credentials object

`Credentials(scopes=…)` is **not** a local hint. google-auth forwards the list
as the `scope` parameter of the refresh request, and Google rejects the entire
refresh with `invalid_scope` when it exceeds what the token was granted — so
naming the update superset there breaks token refresh outright, **uploads
included**, on a channel whose consent was upload-only. Measured against the
live token:

| `scopes=`                     | refresh result                |
| ----------------------------- | ----------------------------- |
| upload-only                   | OK                            |
| upload + force-ssl (superset) | `RefreshError: invalid_scope` |
| `None`                        | OK                            |

`None` is also the only value correct in both states: Google returns a token
carrying everything the refresh token actually holds, so uploads keep working
today and `videos.update` starts working the moment the operator re-consents,
with no second code change. Pinning a _subset_ would be worse than useless —
it mints a token missing force-ssl and 403s the update forever.

Pinned by `test_credentials_do_not_pin_a_scope_list`.

## Two traps worth knowing

**`videos.update` replaces the whole snippet.** Any mutable field left out of
the request is reset to its default — sending only a description would blank
the title, tags and `categoryId` of a live video. The adapter therefore does a
read-modify-write: `videos.list` for the current snippet, overlay only the
fields the caller passed, write the merged result back. `title` and
`categoryId` are additionally _required_ by the API on any snippet update,
which the merge satisfies by construction.

**A 403 is not always a scope problem.** A suspended channel returns 403 too.
`_is_insufficient_scope` requires both the status code and a scope-ish reason
before it tells the operator to re-consent, so a different failure doesn't
send them down the wrong path.

## Quota

The YouTube Data API v3 is free; quota is units, not dollars (10,000/day).
`videos.insert` costs ~1,600 — about 6 uploads/day. `videos.list` costs 1 and
`videos.update` 50, so a full-channel metadata resync of a few dozen videos is
quota-trivial next to a single upload.

## Long-form vs Short: titles, descriptions, cross-links

A post can produce both a long-form video and a Short. Until 2026-09-22 the
pair shipped as near-copies: both took `posts.title` (the Short with a
`#Shorts` suffix that the Shorts feed, showing ~40 characters, never
displayed), and both carried one identical description — the excerpt plus an
article link tagged `utm_medium=video`, so a click from a Short was
indistinguishable from a long-form click. The Short's own narration hook
(`short_summary_script`, written to open the clip) went unused, and the two
never linked to each other, which is the one lever YouTube gives for turning
Shorts viewers into long-form viewers.

Now (`services/jobs/youtube_payload.py`, both the upload and the sync go
through the same builders):

- **Short title** = the first sentence of its narration, shortened on a word
  boundary to `youtube_short_title_max_chars` (default **70** — punchy
  without clipping; see the budget note below), then the suffix. That first
  sentence is
  gated and, when it has a CONTENT defect, repaired at script time by
  `modules/content/short_hook_repair.py`, so the fixed line is what the
  presenter says as well as what the title shows.
  `youtube_short_title_source=post_title` restores the article title; an empty
  script falls back to it too.
- **Suffix** (`youtube_short_title_suffix`, default `" #Shorts"`) still applies
  either way — it separates the pair in every listing and is one of the
  markers YouTube keys off for Shorts classification. Budget-aware (a title
  near the 100-char cap is trimmed to make room) and idempotent (a re-sync
  never stacks a second marker). Empty = no suffix.
- **Short description** = its first two sentences · `Watch the full breakdown:
  https://www.youtube.com/watch?v=<long id>` · `Read the full post: …
  utm_medium=shorts` · `#Shorts` plus up to `youtube_short_hashtags_max`
  CamelCase hashtags from `seo_keywords`. No body snippet on a Short.
- **Long-form description** gains `Watch the Short:
  https://www.youtube.com/shorts/<short id>` after the article link.

### The hook the title comes from

Measured 2026-09-22 over the ten most recent published posts, with the
production scene model (`video_scene_model`, phi4:14b):

| | result |
| --- | --- |
| a verbatim example in the script prompt | copied onto 4 of 10 unrelated articles (#3951, reverted #3952) |
| example removed | 0/10 parrot, but 4/10 opened "Discover how …" and 10/10 overran the feed's ~40 chars |
| `services/short_hook.py` strip + gate | every title on-topic and inside the budget |

Two lessons are baked into the design:

* **Never put a quotable example sentence in a prompt whose output is
  published.** The model copied it verbatim. `build_hook_prompt` names the
  shapes to avoid instead of demonstrating one.
* **Length is a shortening problem; content is a regeneration problem.** Only
  a CONTENT defect (`services/short_hook.CONTENT_DEFECTS` — run-up, describes
  the article, question, not a claim, restates the title, fragment, runaway)
  buys the one corrective LLM call. Over-long is shortened at a word boundary
  by the title builder, because the sentences phi4 wrote at 59-92 characters
  were good claims.

### What comes off the hook before it becomes a title

Two passes run before either the gate or the title builder sees the sentence.

**Scaffolding** (`strip_scaffold`) is wrapper the model added around the line
instead of writing the line. Over the same 489 stored scripts, **157 first
sentences carried some** and every one became a YouTube title verbatim:

| wrapper | count | example |
| --- | --- | --- |
| a quote character | 144 | `"Imagine having an AI assistant that never goes down` |
| a code fence | 7 | an opened fence the model never closed |
| a stage direction or label | 6 | `[ Hook ]` · `[0:00]` · `HOOK:` · `**Narration:**` |

Only a wrapper comes off. An INTERNAL quote is part of the claim, so
`He said "no" to the merge` keeps its quotes, and a bracketed token the
sentence is about (`Shipping [skip-public-sync] keeps a commit private`) is
not a stage direction. A line that is ONLY scaffolding comes back empty,
which the gate reads as the `empty` defect and regenerates — better than a
title reading `[Intro music plays]`.

### Which opening clauses the strip eats, and which it must not

A census of all 489 stored `short_summary_script` rows: the strip fires on 24
first sentences, and 34 more open with a leading clause. Nine of those 34 are
genuine run-ups, in three families — `In a surprising twist,` /
`In a groundbreaking study,` (6), `In a world where …,` (1), and the bare
stance adverbs `Surprisingly,` / `Finally,` (2), which editorialise the claim
instead of making it.

The other 25 must survive, and are pinned by test as must-survive:

| clause | why it stays |
| --- | --- |
| `In 2026,` · `On June 19th,` · `In December 2025,` | a date is usually the most concrete thing in the hook |
| `In production environments,` · `In our development stack,` | a real qualifier scopes the claim rather than delaying it |
| `In a single afternoon,` | `In a <noun>,` only goes when the noun is a framing device |

`Imagine you're building …` needs no strip: it is already a `question`
defect, so it buys the corrective call instead.

### When the corrective call's answer is accepted

A candidate replaces the original only when it is **strictly better**, ranked
on `(content defects, characters over budget)` — not on defect count alone.

Defect count cannot tell a 201-character run-on from the 119-character
finished claim offered to replace it: both carry exactly `runaway`, so
`len(new) < len(old)` is `1 < 1` and the better sentence is thrown away along
with the call that bought it. Measured on prod 2026-09-22, that was 2 of 2
repair attempts on the published corpus, which holds 16 runaway hooks.

The overage is only a tie-break, so it can never let a candidate in on length
alone: more content defects always ranks worse, whatever the length. Two
sentences both inside the budget tie at 0 overage, so a same-defect swap is
correctly no improvement.

The hook model defaults to the **scene model**, not a bigger one, and that is
measured rather than assumed: given the focused prompt, `gemma-4-31B` restated
the brief on 10 of 10 (`Goal: Write the opening line for a 45-second …`) while
phi4 wrote clean claims, and alternating an 8 GB scene model with a 17 GB hook
model made GPU admission refuse 7 of 10 script calls. Override per install
with `media.short_hook.model`.

### The budget, and why it is 70 and not 42

42 is the Shorts feed's visible window, and it was tried first. Measured over
the same ten posts, the stripped first sentences land at **33, 55, 59, 59, 67,
70, 78, 82, 114, 149** characters — median 70:

| budget | hooks that survive whole |
| --- | --- |
| 42 | 1 of 10 |
| 55 | 2 of 10 |
| 60 | 4 of 10 |
| **70** | **6 of 10** |
| 85 | 8 of 10 |

At 42 the builder was cutting 9 of 10 mid-phrase ("JPMorgan's 2026 report
confirms tech") to win a truncation the feed performs anyway — the feed shows
an ellipsis, the title builder shows a stump, and only the stump is ours.
Asking the model for "at most 42 characters" did not work either. 60 was still
cutting two finished claims at 67 and 70 characters, so the budget is the
median, 70: punchy, not clipped. With the 8-character `" #Shorts"` suffix that
is 78, well inside YouTube's 100-character cap.

**The budget is not a hard cut.** `short_hook_title` keeps a sentence whole
when it is within the budget *plus a quarter* (70 → 87) and only shortens
past that, because chopping a claim that is barely over buys nothing. So the
ten measured hooks land as **8 kept whole, 0 shortened, 2 regenerated** — the
shortening band between 87 and the runaway threshold at 105 is deliberately
narrow. The suffix still fits: 87 + `" #Shorts"` = 95, under the 100 cap.

The two worst hooks in that set were 114 and 149 characters — run-ons the
model never finished, where shortening leaves a stump no matter the budget.
Those are now a `runaway` CONTENT defect past
`media.short_hook.runaway_factor` × the budget (default 1.5, i.e. 105
characters — in the gap between the longest real claim at 82 and those two),
so they buy the corrective call instead. A merely-long good claim is still
just shortened.

**Cross-links follow what is live, never what is planned.** The two renders
are approved, rejected and uploaded independently (7 of 13 posts on the
channel had one of the pair up when this shipped), so:

- the "Watch …" line is composed from `pipeline_distributions` rows with
  `status='published'` for the *other* medium of the same task — absent twin,
  absent line, nothing dangling;
- whichever render lands **second** knows both ids: right after its upload
  `media_distribute` recomposes the already-live twin through
  `sync_youtube_metadata(selector=<twin id>, apply=True)` and pushes one
  `videos.update` (`youtube_pair_cross_links=false` disables both the lines
  and the refresh);
- a twin that later vanishes is demoted to `status='deleted'` by the reconcile
  above, so the next recompose drops the dead link;
- `sync-metadata --apply` remains the sweeper: it recomposes every video from
  the same table, so the channel converges in one pass.

`sync-metadata` reads `pipeline_distributions.medium` for "which render is
this" — one source of truth, the same column that fixed the overwrite bug
above.
