# News Shorts

A YouTube Short made from one news story, with no blog post behind it.

Added 2026-10-18. YouTube is the channel that grows, but every video on it came
from a blog post. A Short existed only when a post ran the whole
`canonical_blog` pipeline, so output was capped at the blog's cadence of about
one post a day, and it lagged the news by however long a post takes. The topic
pool already holds the day's stories: Hacker News and the RSS feeds, ingested
hourly. A Short needs a 30 to 45 second script, far less than a post, so it can
be made from a story on the day the story breaks.

Long videos are unchanged. A news Short is an extra kind of Short, not a
replacement for the blog's.

## The flow

One `pipeline_tasks` row per Short, `task_type` and `template_slug` both
`news_short`. The Prefect content flow claims it like any other task, and runs
the graph in `services/news_short_spec.py`:

| Node                                   | Atom                                                  | What it does                                                                                                                                                                                                        |
| -------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `research`                             | `news_short.research`                                 | Fetches the story's own page. The page is the whole corpus, and a page with fewer than `news_shorts_min_source_chars` readable characters stops the run. Also finds the related post and the niche's topic subject. |
| `write_script`                         | `news_short.write_script`                             | The director model writes the narration (`news_short.script` in `skills/content/news-short/SKILL.md`), code checks it, one corrective retry. Fails the task after two rejections.                                   |
| `shot_list`                            | `stage.generate_video_shot_list` (`short_only: true`) | The 9:16 director only. The long director is skipped.                                                                                                                                                               |
| `review_shot_list`                     | `stage.review_video_shot_list`                        | Reviews the Short's list when there is no long one.                                                                                                                                                                 |
| `render_narration` … `qa_audio`        | the `media_pipeline` Short steps                      | Narration, captions and audio QA. The long lane has no script and does nothing.                                                                                                                                     |
| `ambient_bed`                          | `media.generate_ambient_bed`                          | The instrumental bed mixed under the narration at -18 dB, the same one a blog Short carries. Best-effort: no bed, the Short renders narration-only.                                                                 |
| `render_short_video` … `persist_media` | the `media_pipeline` Short steps                      | The 9:16 render, the YouTube package, video QA, the durable `video_short` row.                                                                                                                                      |
| `finalize`                             | `news_short.finalize`                                 | Marks the row standalone, titles it with the headline, stores the narration where the existing Short readers look for it. Fails when nothing rendered.                                                              |
| `done`                                 | `atoms.set_task_status`                               | The task lands at `completed`.                                                                                                                                                                                      |

Everything after `write_script` is the same atom the blog's Short runs, so a
fix to any of them reaches both. The one exception is the music bed: a blog
post makes it in `generate_media_scripts`, which this graph doesn't run, so the
`ambient_bed` node makes it here. Both call `modules/content/ambient_bed.py`.
Until 2026-10-30 news Shorts had no bed at all and went out narration-only
beside blog Shorts with music. A bed costs about a minute of GPU time on the
`stable-audio` sidecar; a busy GPU skips it and raises a `media_gpu_busy_skip`
finding.

## Picking a story

`EnqueueNewsShortsJob` fires hourly and queues at most one task per fire, and
only when all of these hold:

- `news_shorts_enabled` is on. It is off by default.
- The operator-local hour is at or past `news_shorts_earliest_hour` (8), so the
  day's Short is made from the day's news, not from whatever the pool held at
  midnight.
- Fewer than `news_shorts_per_day` (1) news Short tasks were created since
  local midnight. A failed task still counts, so a failed render does not get a
  second attempt the same day.
- No news Short task is pending or running. A slow render never queues a
  second one behind it.

`services.news_shorts.pick_story` then takes up to `news_shorts_candidate_limit`
(20) pooled rows for `news_shorts_niche` (`starter-blog` in the open-source seed; the operator overlay sets `glad-labs`). Each must:

- come from a source in `news_shorts_sources` (`hackernews,rss`);
- be fresher than `news_shorts_max_age_hours` (24);
- have a URL;
- not have been used by any earlier news Short, matched on pool row or URL.

Rows are ranked by score and sent to the niche scope check
(`services/topic_scope.py`, [niche-topic-scope.md](niche-topic-scope.md)). The
best-ranked row judged in scope is used.

**The scope check fails closed here**, unlike in the batch sweep. A story the
check could not judge is skipped, and a niche with no `topic_subject` gets no
news Shorts at all. The sweep can afford to keep an unjudged topic, because a
person picks from its batch. A wrong pick here costs a GPU render.

## The script checks

`clean_script` accepts a reply only when:

- **It is spoken prose:** no links, markdown, list markers or emoji, and it
  ends on a complete sentence.
- **Its length fits.** The range comes from `video_short_target_seconds` ×
  `media_narration_words_per_second` (the same two settings the blog's Short
  lane uses), with `news_shorts_script_min_words` as the floor and 15% headroom
  above the target. The script, the shot list's duration estimate and the
  render therefore agree.
- **The first sentence makes a claim.** It must pass `short_hook`'s content
  defects: no question, no run-up, not describing the video.
- **Every number appears in the source.** This takes two checks:
  - Every digit token in the script must appear in the headline, the feed
    summary or the page. The source's `zero` to `twenty` count as numbers, and
    digits fused to a name (`H100`, `phi4`) are not counted.
  - `numeric_fidelity.verify` then runs in strict mode
    (`score_unattributed=True`), where every claim it recognises must
    reconcile at the precision written.

  The first check exists because the second recognises only percentages,
  currency and numbers with a unit. A script that invented "9 pallets" passed
  it.

The rejection reason names the fault, such as the numbers missing from the
source, or the word count against the range. That reason is what the
corrective retry is told.

## The related article

The upload links the published post closest to the story's headline:

- It is a plain pgvector cosine search over the post embeddings, kept only
  above `news_shorts_related_post_min_similarity` (0.63; 0 turns links off).
- It is not `MemoryClient.search`. That search fuses BM25 with the vectors
  and reranks, so a shared word let a weak match through the floor: an RTX
  5090 theft "matched" a post about training a transformer.
- The floor was measured on five live stories (2026-10-18). A real match
  scored 0.649 (a data-centre story against the data-centre post). Posts
  sharing only a name or a part scored 0.58 to 0.61, for example Anthropic's
  safety bill against a post on Anthropic's SDK.

No link beats a wrong link.

## Review and upload

`news_short.finalize` writes `metadata.standalone`, `news_source_url`,
`related_post_slug` and the story onto the `video_short` row. `media_distribute`
treats it as it treats a creative-studio video (poindexter#1140): it seeds a
task-keyed review row in `media_approvals`, and the Short waits for the
operator.

An approved news Short uploads in the next Shorts slot
([youtube-metadata.md](../operations/youtube-metadata.md)):

- **Title and description lead:** from its package (`media.package_short`).
- **Description:** `Source: <url>` under the lead, then `More on this: <post>`
  when a related post was found.

`sync-metadata` never rewrites a news Short, because it reads only videos with
a post.

## Trying it

From the worker container, which reaches the model:

```bash
docker exec poindexter-worker python -m poindexter.cli integrations youtube news-short --draft
```

With no flags it shows the story the job would pick. `--draft` also reads the
page and writes the checked script, with nothing queued. `--queue` makes the
task. `--url … --title …` names a story yourself. The command works while
`news_shorts_enabled` is off.
