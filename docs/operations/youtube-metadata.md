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
  boundary to `youtube_short_title_max_chars` (default 60), then the suffix.
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
