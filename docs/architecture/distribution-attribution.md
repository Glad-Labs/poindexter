# Distribution attribution — which surface actually delivered

## The problem this exists to solve

Poindexter places outbound links on several platforms: a social promo on X and
Bluesky, a Dev.to crosspost, a YouTube description, an internal link inside the
syndicated copy. Every one of them pointed at a bare
`{site_url}/posts/{slug}`.

That means the only attribution signal a view carried was `document.referrer` —
and that signal is structurally lossy for exactly the surfaces we use:

| Surface    | What the referrer says                                            |
| ---------- | ----------------------------------------------------------------- |
| X          | `t.co` (the shortener), never the tweet                           |
| Bluesky    | `go.bsky.app`, or nothing from the mobile app                     |
| Dev.to     | nothing — no referrer is sent at all                              |
| Mastodon   | a different host per instance; no canonical value to match        |
| YouTube    | `youtube.com` when it appears; in-app browsers often send nothing |
| Newsletter | nothing (mail clients strip it)                                   |

The result, measured over the 90 days to 2026-08-31:

|                                            |                                                                              |
| ------------------------------------------ | ---------------------------------------------------------------------------- |
| Outbound placements                        | **197** (77 posted social promos, 107 Dev.to crossposts, 12 YouTube uploads) |
| Referrals identifiable from those surfaces | **5** (t.co ×2, go.bsky.app ×1, youtube.com ×2)                              |
| Dev.to referrals                           | **0**, across 146 lifetime crossposts                                        |

Five is a real number and it may even be the true number. The problem is that
**there was no way to tell "this surface delivers nothing" from "this surface
delivers invisibly"** — and the two call for opposite decisions. Adding a
seventh surface on that evidence is a coin toss dressed as a strategy.

## The two halves

### Writing the tag — `services/distribution_ref.py`

One helper every outbound-link composer routes through:

```python
from services.distribution_ref import tag_for

url = tag_for(site_config, f"{site_url}/posts/{slug}", surface="devto")
# → https://www.gladlabs.io/posts/…?utm_source=devto&utm_medium=syndication
```

- **`utm_source` / `utm_medium` by default**, not a bespoke `ref`. Google
  Analytics already parses the UTM vocabulary, so one tag feeds two independent
  consumers. `distribution_ref_source_param` shortens it if the extra ~24
  characters of a 280-character promo matter more than the GA4 report.
- **`medium` is the surface class** (`social` / `syndication` / `video` /
  `audio` / `email`), from `SURFACE_MEDIUM`. It makes "what did social deliver
  in total" one query instead of a list of every platform we have ever used.
  A surface with no map entry still gets a `source` — the newest platform, whose
  value is genuinely unknown, must not be the one we cannot measure.
- **Idempotent, and it never clobbers an existing tag.** A link that already
  says where it came from is already doing the job.
- **A malformed surface token raises.** Silently emitting an untagged link
  would reproduce the exact blind spot the module removes, and it would read as
  "that surface delivers nothing" forever.

### Reading the tag — the beacon chain

```
tagged link  →  reader lands on /posts/<slug>?utm_source=devto
             →  ViewTracker.readRefSource() lifts it out of location.search
             →  page-views Worker writes it as blob6
             →  SyncCloudflareAnalyticsJob  →  page_views.ref_source
```

`ViewTracker` previously sent `window.location.pathname` and dropped the query
string entirely, so the tag would have died at the first hop.

**The tag does not ride in `path`.** `posts.view_count`, the `lab_outcomes_v1`
windows and every slug join key on `path`/`slug`; folding a query string into
them would fragment each of those groupings into one row per surface. The
surface is its own dimension and is stored as its own column.

Both the client and the Worker validate the token against
`^[a-z0-9][a-z0-9_-]{0,31}$`. The query string is visitor-controlled and this
value reaches a `GROUP BY` and a Grafana legend, so free text there is both a
cardinality bomb and a needless injection surface. Anything unrecognised
becomes "untagged", which is the honest answer.

## Where links get tagged

| Composer                                              | Surface              | Note                                                                                             |
| ----------------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------------------ |
| `services/social_poster.py`                           | the platform         | Tagged at prompt time so the prose budget (`char_limit − len(url) − 1`) reserves the real length |
| `services/social_drafts.py` → `approve_draft`         | the draft's platform | The last place the link is touched, so this is where attribution is settled                      |
| `services/jobs/youtube_payload.py`                    | `youtube`            | The "Read the full post" line                                                                    |
| `services/devto_service.py` `_clean_markdown`         | `devto`              | Internal links inside the syndicated body                                                        |
| `services/devto_service.py` `_append_origin_backlink` | `devto`              | The origin footer                                                                                |

### Two traps worth knowing

**Never tag a `canonical_url`.** Dev.to's `canonical_url` field is the
canonicalisation signal search engines consolidate on — a query string there is
a different URL. The attributable path is the footer link beside it. `tag_url`
cannot tell the two apart, so this is the caller's responsibility, and
`test_devto_canonical_url_is_never_tagged` is the guard.

**Never tag an image source.** An `<img>` src is fetched by the platform, never
clicked, so tagging it manufactures phantom traffic against our own assets.
This is why the Dev.to link rewrite carries a `(?<!!)` lookbehind: `![alt](…)`
also matches a bare `[…](…)`, so without it the image branch would be dead code
and every image would be tagged.

**Short-form copy is shared; attribution is not.** One LLM call produces the
tweet that Bluesky and Mastodon reuse. Each sibling's stored draft carries its
own tag, and `approve_draft` re-settles it at post time — so a Bluesky click is
never counted as an X click, and the operator's preview shows the link that
will actually go out.

## Reading the result

```bash
poindexter distribution yield --days 30
```

```
surface       medium        placements   tagged  referrer  per 100
devto         syndication          107        0         0      0.0
bluesky       social                40        0         1      0.0
twitter       social                38        0         2      0.0
youtube       video                 12        0         2      0.0
hn            —                      0        0         5        —
```

Three columns on purpose:

- **`tagged`** — the trustworthy signal. Counts only from the day tagging
  shipped; it cannot be backfilled, because the evidence was never recorded.
- **`referrer`** — the legacy signal, kept alongside rather than replaced. It is
  the only evidence covering the earlier period, and **the gap between the two
  columns is itself a measurement**: it shows how much attribution the referrer
  alone was losing.
- **`per 100`** — arrivals per hundred placements, `—` when nothing was placed.
  Deliberately not `0.0`: "we posted nothing there" and "we posted and nobody
  came" are different findings, and collapsing them into one zero is how a
  dormant surface gets mistaken for a dead one. Hacker News in the sample above
  is the case in point — more arrivals than X and Bluesky combined, from zero
  automated placements.

The same numbers render on **Cost & Analytics** under
_Distribution — surfaces & yield_: arrivals by surface over time, the placements
vs arrivals table, the referrer-host table, and an "untagged share" stat.

**Untagged share is expected to be high** — organic search and direct traffic
are untagged by nature — so read it as a trend, not a target. A jump toward 100%
after tagging shipped means the chain broke: check `distribution_ref_enabled`,
then whether the beacon Worker is deployed with the ref blob.

## Settings

| Key                              | Default                                                  | What it does                                                     |
| -------------------------------- | -------------------------------------------------------- | ---------------------------------------------------------------- |
| `distribution_ref_enabled`       | `true`                                                   | Master switch. Off = every link goes out bare, as before         |
| `distribution_ref_source_param`  | `utm_source`                                             | Parameter carrying the surface. `ref` is also read by the beacon |
| `distribution_ref_medium_param`  | `utm_medium`                                             | Empty = source only (saves ~13 chars of promo budget)            |
| `devto_origin_backlink_template` | `---\n\n*Originally published at [{site_host}]({url}).*` | Dev.to origin footer. Empty = no footer                          |

`distribution_ref_enabled` defaults **on**, unlike most new switches. A
default-off attribution feature ships inert and nobody notices for months — the
`devto_syndicate_content_types` gate did exactly that — and the cost of being
wrong is a query parameter on a page that already emits
`<link rel="canonical">` at its untagged URL, so the tagged variant consolidates
back and fragments no ranking authority.

## Rollout order

The beacon Worker deploys independently of the compose stack (`wrangler`, not
`docker compose`), so a window where the Worker predates `blob6` is the **normal**
rollout order, not a fault — the sync job treats a missing `ref_source` key as
"untagged". Until the Worker ships, tagged links still work and GA4 still sees
`utm_source`; only `page_views.ref_source` stays empty.

```bash
cd infrastructure/cloudflare/page-views-beacon && npm run deploy
```

## What this does not do

It does not rank surfaces or recommend cuts. A surface with zero yield may be
badly used rather than worthless — wrong copy, wrong subreddit, wrong time —
and the numbers are here so that call is made from evidence instead of from how
a platform feels. Nor does it capture surfaces we do not link from: a reader who
hears the podcast and types the address is a real arrival that lands in
"(direct)" and always will.
