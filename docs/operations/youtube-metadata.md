# YouTube metadata — what we send, and how to fix what we already sent

## What each upload carries

| Field       | Source                                                       | Limit enforced                 |
| ----------- | ------------------------------------------------------------ | ------------------------------ |
| Title       | `posts.title` (+ `#Shorts` suffix on a Short)                | 100 chars (adapter clamps)     |
| Description | `posts.excerpt` + tagged back-link (+ optional body snippet) | 4,800 composed / 5,000 API cap |
| Tags        | `posts.seo_keywords`, comma-split                            | 30 tags / 500 joined chars     |

Composed by `services/jobs/youtube_payload.py`, dispatched by
`services/jobs/media_distribute.py`. The back-link carries
`?utm_source=youtube&utm_medium=video` so a click from a description is
attributable — see [distribution-attribution.md](../architecture/distribution-attribution.md).

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

## Long-form vs Short titles

A post can produce both a long-form video and a Short. Both used to take
`posts.title` verbatim, which would have put two identically-named videos on
the channel with only the thumbnail to tell them apart.

The Short now gets `youtube_short_title_suffix` (default `" #Shorts"`)
appended. It does double duty: it separates the pair in every listing, and
`#Shorts` is one of the markers YouTube keys off for Shorts classification.

The append is budget-aware — a title already near the 100-char cap is trimmed
at a word boundary to make room, so the adapter's clamp can't cut off the very
thing that distinguishes it. It is idempotent (a re-sync never stacks a second
marker), and `sync-metadata` carries the asset type through so re-syncing a
Short doesn't rebuild its title as long-form and re-collide the pair.

Set the suffix empty to opt out.
