# Topic demand signals — ranking by whether anyone is looking

**Why this exists (2026-09-08).** The batch pre-rank scores each candidate
topic by embedding cosine against the niche's goals. That measures _fit_, and
nothing about _demand_: the 2026-08-09 Search Console audit found the site
reliably picked on-goal subjects nobody searches for (~45,500 impressions →
~70 clicks lifetime), and the August 2026 cohort drew 3.4 first-21-day
impressions per post. Every topic source except two measures publication or
conversation — HackerNews (what's discussed), Dev.to (what's blogged), RSS
(what's curated), `internal_rag` (what we already wrote). Only
`search_autocomplete` and `gsc_query_gap` measure what people _type_, and
until this week neither was scheduled ([`external_taps` rows are what
schedule ingestion](../../CLAUDE.md); the plugin row only permits).

Three signals now act on the pre-rank score, in this order, each recorded in
`topic_candidates.score_breakdown` so a batch shows its reasoning:

| Signal           | Setting                                                                   | Breakdown key                                  | What it says                                                    |
| ---------------- | ------------------------------------------------------------------------- | ---------------------------------------------- | --------------------------------------------------------------- |
| Source weight    | `topic_source_rank_weights` (`search_autocomplete=1.5,gsc_query_gap=1.5`) | `_source_weight`                               | this candidate came from a demand-measuring source              |
| Wikipedia demand | `topic_demand_wiki_*`                                                     | `_wiki_views`, `_wiki_title`, `_demand_factor` | people read about the entity this candidate names               |
| Dual signal      | `topic_demand_dual_signal_factor` (1.25)                                  | `_dual_signal`                                 | **both**: people type it (Google) AND read about it (Wikipedia) |

## Wikipedia pageviews as the demand instrument

Free keyword-volume data does not exist: Google Trends returns 429
unauthenticated, Google/Bing suggest return rank only (already used by
`search_autocomplete`), and the volume APIs are paid. Wikimedia's REST API is
public, absolute, and monthly: August 2026 pageviews for
_Retrieval-augmented generation_ 33,958, _GeForce RTX 50 series_ 31,524,
_Llama.cpp_ 10,031, _vLLM_ 5,431, _GGUF_ 3,754. Those are counts of people
interested in the entity right now, which is the property every page that
earned a click has in common.

`services/entity_demand.py`:

1. **Resolve** — whole titles resolve badly ("Rtx 5090 Local Llm
   Performance" → nothing; "FastAPI best practices" → "Coding best
   practices"), so `resolution_candidates` searches entity-shaped pieces in
   priority order: the content-word phrase, digit phrases ("rtx 5090"; bare
   years are dates, not entities), dotted / mixed-case / ALL-CAPS tokens
   ("llama.cpp", "vLLM"), then two- and three-word windows ("cosine
   similarity"). Plain single words are never searched alone — "vram" is a
   person, "information" is read 70k times a month. A hit is accepted
   (`accept_hit`) only if it shares a distinctive, non-generic token with the
   candidate AND, unless the candidate carries a digit, the article is
   _headed_ by the candidate's first word: "Cosine similarity" passes,
   "Plus and minus signs" for "minus signs" and "MAC address" for "locally
   mac" do not — they merely mention the words. Known residual: a 2-word
   window can still land on an unrelated article that happens to be headed
   by the same word ("stuck task" → "Stuck (2017 film)"); those articles are
   rarely above the 1,000-view floor, so they cost a wrong `_wiki_title` in
   the breakdown, not a wrong factor.
2. **Measure** — pageviews for the last 30 complete UTC days.
3. **Score** — `demand_factor` is 1.0 below `topic_demand_wiki_min_views`
   (1,000/month) and rises log-linearly to `topic_demand_wiki_max_factor`
   (2.0) at 100× the floor: 1k → 1.0, 10k → 1.5, 100k → 2.0. A nudge, not a
   lock: an off-goal entity with big traffic still loses to a strongly on-goal
   one.
4. **Dual signal** — a candidate whose `source_name` is in
   `topic_demand_google_sources` and whose entity clears the floor gets
   `topic_demand_dual_signal_factor` on top. Two independent instruments
   agreeing is the strongest demand evidence the pipeline can currently
   gather. With the defaults an autocomplete topic naming a 10k-view entity
   scores 1.5 × 1.5 × 1.25 ≈ 2.8× its bare embedding score.

**Honesty rules.** Any lookup failure is factor 1.0 with `_wiki_views: None`
— unknown, never a fabricated zero (`feedback_no_dummy_data`). A confirmed
"no article matched" is cached (`entity_demand_cache`, TTL
`topic_demand_wiki_cache_days` = 7) because asking again next sweep will not
help; a transport failure is **not** cached so the next sweep retries. The
whole lookup is wrapped so a Wikimedia outage degrades to "rank without it",
never to "no batch".

**Cost.** One sweep resolves ~60 titles at concurrency 4, two requests each,
once per cache TTL. Wikimedia asks for a descriptive User-Agent
(`topic_demand_wiki_user_agent`); a fork should identify itself.

## What this is not

It is not keyword volume. An entity's Wikipedia readership is a proxy for
interest, not for the query the article would rank for. When a real volume
instrument becomes available (Bing Webmaster Tools keyword research is free
once the site is verified there), it belongs beside these as a fourth
signal, not instead of them — the dual-signal rule is the pattern: agreement
between independent instruments beats any one of them.

## Cadence (same day)

Demand also set the publishing rate. With ~5 search clicks a week site-wide,
30 posts a month is output without curation. The operator values on prod are
now: `topic_auto_resolve` daily at 09:00 operator-local (was every 2 h),
glad-labs `batch_size` 3 (was 5), `cadence_slo_expected_posts_per_day` 0.5
over a 168-hour window (pages under ~1.75 posts/week; was 1/day over 24 h,
which paged on any quiet day). Code defaults are unchanged — a fresh install
keeps its own cadence.
