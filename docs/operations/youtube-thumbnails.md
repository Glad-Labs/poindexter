# Custom YouTube thumbnails

Every long-form video gets a composed thumbnail when it is rendered. You review
it with the video, and it uploads with the video. Before 2026-09-25 no upload
carried one: all 13 long videos and 10 Shorts on the channel showed a frame
YouTube picked, which can be a transition, a presenter mid-blink, or a held
frame from the end of a render.

A thumbnail needs words, and a diffusion model cannot set type. So the
thumbnail is **composed, not generated**: headless chromium lays real HTML type
over a text-free background and screenshots it, the same way the brand hero
image is made. It uses no GPU and cannot trip the OCR gate.

Code: `services/video_thumbnail.py` (compose + store),
`modules/content/atoms/media_render_thumbnail.py` (the media-pipeline node),
`services/youtube_thumbnail_backfill.py` (videos already on the channel).

## What goes into one

**Background:** the first source in `video_thumbnail_background_order` that
yields an image.

| Source               | What it is                                                                                                                                                                                              |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `featured_image`     | The post's featured image. On-brand and already OCR-gated. Passed over when it carries its own type by design (a composed brand card, a chart, a screenshot), because the hook would land on that text. |
| `presenter_portrait` | The niche persona's studio portrait, the still the talking head is animated from. Composed, mouth closed.                                                                                               |
| `presenter_frame`    | A frame from the opening presenter scene, `video_thumbnail_presenter_offset_s` in. The caption band is cropped.                                                                                         |
| `video_frame`        | The frame at `video_thumbnail_frame_at_s`. The caption band is cropped.                                                                                                                                 |
| `brand`              | The brand ground with no image. Always available, so an order ending in it always produces a thumbnail.                                                                                                 |

A person (`presenter_portrait`, `presenter_frame`) sits on the right with the
text beside it (`video_thumbnail_person_layout=right`). The first test render
put full-bleed type over a mid-speech frame, and it read as a paused video.

**Hook:** a few words that add to the title rather than repeat it. The
director model writes it from the `video.thumbnail_hook` section of
`skills/content/video-director/SKILL.md`, and code checks it. The hook must:

- fit in `video_thumbnail_hook_max_chars`, so it reads at thumbnail size;
- carry no number that the post and the narration do not contain;
- not be made only of the title's words;
- not open with the same word as `video_thumbnail_hook_opener_max_repeats` or
  more of the last `video_thumbnail_hook_opener_window` thumbnails. The first
  backfill opened six of thirteen with "STOP", and a channel page of those
  reads as a template. In a backfill each stored thumbnail counts toward the
  next, so a batch spreads itself out.

A rejected hook gets one corrective retry that carries the reason (the
`video.thumbnail_hook_fix` prompt, in the same SKILL file). If the retry is also
rejected, the thumbnail ships with no text, because no text is better than
bad text. `video_thumbnail_hook_enabled=false` skips the model call and gives
image-only thumbnails.

**Look:** size, typeface, colours, scrim, text position and brand mark. All of
them are settings (table below). The type shrinks until it fits, measured in
the page itself: text measured anywhere but the rendering chromium is measured
wrong, and the worker ships only JetBrains Mono and Liberation.

## Where you review it

The media pipeline's `render_thumbnail` node runs after the renders and before
media QA. `media.persist` stores the result as a `video_thumbnail` row in
`media_assets` beside its video, so it waits for approval with the video.

In the console's media drawer the thumbnail sits above the player, captioned
"YouTube thumbnail — uploads with this video", and it is the player's poster
frame. The route behind it is
`GET /api/media-approval/{post_id}/video/thumbnail`.

A re-render replaces the thumbnail. If a run is replayed without re-rendering
its video, the stored thumbnail is kept, because it may already be on YouTube.

## How it reaches YouTube

When an approved long-form video is distributed, `media_distribute` hands the
stored thumbnail to the YouTube adapter. The adapter calls `thumbnails.set`
right after `videos.insert`. The outcome is stamped on the thumbnail's row:

```sql
SELECT task_id, metadata->'youtube' FROM media_assets WHERE type = 'video_thumbnail';
-- {"video_id": "…", "status": "set" | "failed: …", "at": "2026-…Z"}
```

A thumbnail failure never fails the upload. The video goes up with YouTube's
own frame, and a `youtube_thumbnail_failed` finding names the video and the
reason, so you can retry with the backfill command below.

Shorts keep YouTube's frame. The Shorts feed, where most Short views start,
plays the video instead of showing a thumbnail. Long-form videos are chosen from
their thumbnail in search, browse and suggested, so that is where a composed one
pays off.

### Channel eligibility

YouTube only accepts custom thumbnails from a verified channel. An unverified
channel gets a `403`, which the adapter reports with the fix: YouTube Studio →
Settings → Channel → Feature eligibility → verify a phone number. No new OAuth
scope is needed, because `thumbnails.set` works with the upload scope.

## Videos already on the channel

Run this inside the worker container, which has the renderer and its fonts:

```bash
docker exec poindexter-worker python -m poindexter.cli integrations youtube thumbnails
```

It works in two passes, so what gets uploaded is exactly what you looked at:

1. **Dry run (default):** composes and stores a thumbnail for each published
   long video that has none, then prints each file's path, hook and
   background. Nothing leaves the machine. Stored thumbnails are listed, not
   recomposed, unless you add `--recompose`.
2. **`--apply`:** uploads each video's **stored** thumbnail and stamps the
   outcome. It never recomposes a stored thumbnail, because a second model call
   could write different words from the ones you reviewed. Videos stamped
   `set` are skipped.

Add `--limit N` to cap a run. The command exits non-zero if any compose or
upload fails.

## Changing one video's thumbnail

`--post <post id | slug | task id | YouTube video id>` narrows any run to one
video, and it also reaches a video **still awaiting approval**. Two ways to
change what that video's thumbnail says:

- `--recompose` re-rolls it: a fresh hook from the model and a fresh render.
- `--hook "TEXT"` sets the text yourself. The model is skipped, and so are the
  checks on its output; the type still shrinks to fit. The stored row records
  `hook_note: "set by operator"`.

```bash
docker exec poindexter-worker python -m poindexter.cli integrations youtube thumbnails --post <id> --hook "Your text"
```

Both are dry runs that replace the stored thumbnail. Look at it, then:

- **video on YouTube:** `--apply --post <id>` uploads it, replacing the one
  there.
- **video awaiting approval:** approve the video. `media_distribute` uploads
  the latest stored thumbnail with it.

`--hook` refuses to run with `--apply`, or without a `--post` that matches
exactly one video, since either would upload text nobody reviewed.

## Measuring whether it works

The [`youtube_reach` tap](../integrations/tap_youtube_reporting.md) lands
thumbnail impressions and thumbnail click-through rate per video per day in
`external_metrics`. To compare CTR before and after a video got its custom
thumbnail:

```sql
WITH set_at AS (
  SELECT metadata->'youtube'->>'video_id'          AS video_id,
         (metadata->'youtube'->>'at')::timestamptz AS at
    FROM media_assets
   WHERE type = 'video_thumbnail'
     AND metadata->'youtube'->>'status' = 'set'
)
SELECT m.dimensions->>'video_id' AS video_id,
       m.date >= s.at::date      AS custom_thumbnail,
       sum(m.metric_value) FILTER (WHERE m.metric_name = 'video_thumbnail_impressions')     AS impressions,
       avg(m.metric_value) FILTER (WHERE m.metric_name = 'video_thumbnail_impressions_ctr') AS avg_daily_ctr
  FROM external_metrics m
  JOIN set_at s ON s.video_id = m.dimensions->>'video_id'
 WHERE m.source = 'youtube'
 GROUP BY 1, 2
 ORDER BY 1, 2;
```

Impressions are the denominator, so read a CTR only once the video has a few
hundred impressions on each side of the change. The tap doc also covers the
open question of whether the CTR arrives as a fraction or a percentage.

## Settings

All in `app_settings`, seeded from `settings_defaults.py`.

| Key                                            | Default                                   | What it does                                                                                                                                                            |
| ---------------------------------------------- | ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `video_thumbnail_enabled`                      | `true`                                    | Compose a thumbnail at render time. Off: no thumbnail is made, and uploads keep YouTube's frame.                                                                        |
| `youtube_custom_thumbnail_enabled`             | `true`                                    | Upload the composed thumbnail with the long-form video. Off: thumbnails are still composed and reviewable, but not uploaded.                                            |
| `video_thumbnail_background_order`             | `featured_image,presenter_portrait,brand` | Background sources tried in order (table above).                                                                                                                        |
| `video_thumbnail_hook_enabled`                 | `true`                                    | Ask the model for hook text. Off: image-only thumbnails.                                                                                                                |
| `video_thumbnail_hook_model`                   | empty                                     | Model for the hook. Empty = `video_director_model`.                                                                                                                     |
| `video_thumbnail_hook_max_chars`               | `32`                                      | Longest hook accepted. Longer is rejected.                                                                                                                              |
| `video_thumbnail_hook_temperature`             | `0.7`                                     | Sampling temperature for the hook call.                                                                                                                                 |
| `video_thumbnail_hook_max_tokens`              | `256`                                     | Reply budget for the hook call. Raise it only for a model that thinks aloud before answering.                                                                           |
| `video_thumbnail_hook_timeout_seconds`         | `90`                                      | How long the hook call may run before the thumbnail ships without text.                                                                                                 |
| `video_thumbnail_hook_opener_window`           | `12`                                      | How many recent thumbnails the opener-variety rule looks at.                                                                                                            |
| `video_thumbnail_hook_opener_max_repeats`      | `2`                                       | A hook whose first word already opens this many of those is sent back once. `0` = off.                                                                                  |
| `video_thumbnail_width` / `_height`            | `1280` / `720`                            | Output size in pixels.                                                                                                                                                  |
| `video_thumbnail_font_family`                  | `JetBrains Mono`                          | Hook typeface. Use a family installed in the worker image, or chromium substitutes one.                                                                                 |
| `video_thumbnail_font_weight`                  | `800`                                     | Hook weight.                                                                                                                                                            |
| `video_thumbnail_max_font_px` / `_min_font_px` | `120` / `48`                              | The type starts at the max and shrinks until it fits, never below the min.                                                                                              |
| `video_thumbnail_text_color`                   | `#f4f8fb`                                 | Hook colour. The `brand` ground's grid lines are drawn in it at low opacity.                                                                                            |
| `video_thumbnail_accent_color`                 | `#00e5ff`                                 | Colour of the hook's last words, the `//` in the brand mark, and the glow on the `brand` ground.                                                                        |
| `video_thumbnail_accent_words`                 | `1`                                       | How many trailing words take the accent colour. `0` = none.                                                                                                             |
| `video_thumbnail_uppercase`                    | `true`                                    | Set the hook in capitals.                                                                                                                                               |
| `video_thumbnail_text_position`                | `left`                                    | `left`, `center` or `bottom`, over full-bleed backgrounds. Beside a person, the text always takes the free side.                                                        |
| `video_thumbnail_text_width_pct`               | `52`                                      | Width of the text column for `left` text, as a percentage of the frame.                                                                                                 |
| `video_thumbnail_person_layout`                | `right`                                   | `right` = person on the right with the text beside it; `cover` = full-bleed like any other background.                                                                  |
| `video_thumbnail_person_width_pct`             | `58`                                      | Width of the person's panel in the `right` layout.                                                                                                                      |
| `video_thumbnail_person_focus_y_pct`           | `22`                                      | Vertical crop anchor for the person's image, `0` = top, `100` = bottom. Raise it for a portrait framed with more headroom.                                              |
| `video_thumbnail_scrim_opacity`                | `0.78`                                    | Strength of the scrim behind the text over full-bleed backgrounds, `0`–`1`. The scrim is in the ground colour.                                                          |
| `video_thumbnail_brand_mark_enabled`           | `true`                                    | Draw the brand mark.                                                                                                                                                    |
| `video_thumbnail_brand_mark`                   | empty                                     | Brand mark text. Empty = `site_name`.                                                                                                                                   |
| `video_thumbnail_brand_mark_color`             | `#7a8a92`                                 | Brand mark text colour.                                                                                                                                                 |
| `video_thumbnail_background_color`             | `#070a0f`                                 | The ground: the `brand` background, the fill beside a `right`-layout person, and the colour of the scrim and text shadow. Pair a light ground with a dark `text_color`. |
| `video_thumbnail_jpeg_quality`                 | `88`                                      | Starting JPEG quality. It steps down until the file fits `_max_bytes`.                                                                                                  |
| `video_thumbnail_max_bytes`                    | `2000000`                                 | YouTube's upload ceiling for a thumbnail.                                                                                                                               |
| `video_thumbnail_frame_crop_bottom`            | `0.22`                                    | Share of a video frame cropped off the bottom, where the burned-in captions are.                                                                                        |
| `video_thumbnail_presenter_offset_s`           | `1.5`                                     | How far into the opening presenter scene `presenter_frame` grabs.                                                                                                       |
| `video_thumbnail_frame_at_s`                   | `5.0`                                     | Where `video_frame` grabs.                                                                                                                                              |
