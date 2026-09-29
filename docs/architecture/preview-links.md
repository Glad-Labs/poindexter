# Preview links: one page, two readers

The worker serves every draft at `GET /preview/{token}` (`routes/cms_routes.py`).
Two very different readers need that page, and they cannot share an address:

| Reader                                                 | Where it runs                      | What it needs                                                                                |
| ------------------------------------------------------ | ---------------------------------- | -------------------------------------------------------------------------------------------- |
| **The operator**, from the approval message or Grafana | a phone or browser on the tailnet  | a URL that resolves _there_: over Tailscale, the MagicDNS name                               |
| **The rendered-preview QA leg** (`qa.vision`)          | chromium inside the prefect-worker | the draft under review, as the operator will see it, **before** the draft is in the database |

One setting, `preview_base_url`, used to serve both. It served neither.

## The operator's link

Every operator-facing consumer builds the link through one resolver,
`services/preview_links.py`:

- the awaiting-approval Discord/Telegram message
  (`post_pipeline_actions._notify_operator`);
- the `preview_url` graph channel (`stage.verify_task` mints it at the top of
  the run; `content.compile_meta` rebuilds it after QA), which approval-gate
  artifacts can surface;
- the Grafana approval-queue panel ("Posts Awaiting Your Approval",
  `pipeline-merged.json`), which mirrors the same derivation in SQL.

The base is `preview_base_url` when set, otherwise
`http://{operator_service_host}:8002`: the host the Grafana dashboards already
use for their sibling-service links (see
[grafana-dashboard-links.md](grafana-dashboard-links.md)), so one setting moves
both. A fresh install derives `http://localhost:8002`, right for a browser on
the Docker host.

Over Tailscale, set the **MagicDNS name**, not the tailnet IP:

```bash
poindexter settings set preview_base_url http://<host>.<tailnet>.ts.net:8002 --category infrastructure
```

The name follows the node; an IP does not. The stored value was the retired
Windows node's tailnet IP from the Pop!_OS migration (July 2026) until
2026-09-28, so every approval link went nowhere for about ten weeks.

## The rendered-preview QA leg

`qa.vision`'s second leg (`MultiModelQA._check_rendered_preview_outcome` →
reviewer `rendered_preview`, aliased to the `vision_gate` row) renders **this
draft** with the same renderer the route serves (`services/preview_page.py`),
screenshots it with headless chromium
(`services/preview_screenshot.capture_html_tiles`, JavaScript off), and shows
`qa_preview_vision_model` the page as a few viewport-sized **tiles** in one call,
asking whether it looks like a real article: overflowing tables, missing CSS,
broken images, empty sections. The browser also reports the images that failed to
load and any horizontal overflow, which the model cannot see reliably, and the leg
turns those into objections itself. Opt-in via `qa_preview_screenshot_enabled`. Why
tiles, and not one image, and why the browser measures: [What the judge is
shown](#what-the-judge-is-shown).

It never fetches a URL, for two independent reasons, either of which is
enough:

1. **The page does not exist yet.** The `qa.*` block runs before
   `content.persist_task` writes the draft or its `preview_token`, so at QA
   time `/preview/{token}` answers `{"detail":"Post not found"}`. From
   2026-07-08 to 07-18, the only stretch when the URL was reachable from the
   prefect-worker, **33 of the 34** `rendered_preview` reviews scored that 404
   page at 25-45/100 ("Post not found error displayed instead of blog
   content"). The one exception (90/100, no issues listed) was also a first
   pass, taken before its draft was persisted.
2. **Containers cannot reach the operator's address.** Compose pins the
   worker, prefect-worker and brain to public resolvers (1.1.1.1 / 8.8.8.8).
   There a `*.ts.net` name resolves to Tailscale's Funnel ingress, which does
   not expose :8002, so the connection fails with `Errno 101`. Measured from inside
   `poindexter-prefect-worker` on 2026-09-28:

   | URL                                   | Result                                                 |
   | ------------------------------------- | ------------------------------------------------------ |
   | `http://worker:8002`                  | 200 (compose DNS)                                      |
   | `http://localhost:8002`               | connection refused (that is the prefect-worker itself) |
   | `http://<current tailnet IP>:8002`    | 200                                                    |
   | `http://<retired tailnet IP>:8002`    | timeout                                                |
   | `http://<host>.<tailnet>.ts.net:8002` | `Errno 101` (Funnel ingress)                           |

Rendering in-process removes both problems. The screenshot shows the draft
under review, the vision verdict is about the page the operator will open,
and there is no URL left to rot.

### A leg that gives no verdict says so

`_check_rendered_preview_outcome` returns `(review, status, detail)`.
`disabled` is legitimate (switched off). `failed` names the cause: screenshot
error, no vision model set, or an empty or unparseable answer. On `failed`,
`qa.vision` logs `[qa.vision] rendered-preview leg produced no verdict` and
emits the shared `qa_rail_degraded` finding with `rail=rendered_preview`, the
same convention `qa.web_factcheck` and `qa.title_coherence` follow when they
cannot measure (one dedup key per rail, so the dispatcher collapses a dark
leg's repeats instead of alerting once per post). Before this, every failure returned the same bare `None` as "switched
off": **0 verdicts in 53 runs** in the 30 days to 2026-09-28, with the capture
error logged at WARNING and nothing else. The draft still proceeds on every
other rail. A leg that could not run adds no review
(`_qa_rail_common.not_applicable_review` is for rails that ran and found
nothing to judge).

The approval message quotes the verdict ("Visual QA: 72/100 — table
overflows") from the final QA pass in `result["qa_reviews"]`; it runs no
vision call of its own.

### The post-pipeline pass it replaces

`post_pipeline_actions._maybe_run_preview_qa` used to screenshot
`http://localhost:8002/preview/{token}` after the pipeline and write
`preview_qa_score` / `_approved` / `_feedback` into the task metadata. It
produced 887 verdicts (average ~83) until **2026-05-10**, the Prefect
cutover, and none after. The Prefect flow passes no settings service, so the
pass read its own switch as off, and `localhost:8002` is refused inside the
prefect-worker anyway. Its metadata keys had no reader. The in-graph leg
supersedes it, and it was removed along with its tests, which had passed a
settings stub and so never exercised the real flow.

## What the judge is shown

The first version of the in-process leg sent the judge **one full-page PNG**. On
the draft it was verified against (`3ceda1c0`, 1280×13141 px) the verdict was
75/100, `approved: false`, with issues that were not true: "the image at the top
of the page is a placeholder with a URL", "multiple instances where images are
missing". Chromium's own DOM says all four images loaded (four `<img>`, each
`complete`, 1024×1024 natural size, HTTP 200 from R2), and nothing overflows.

### The judge reads at most 4.19 megapixels per image

Ollama 0.32.1 serves `qwen3-vl:30b-a3b-instruct` through llama.cpp's
`llama-server --mmproj --image-min-tokens 1024`. At model load it logs
`image_min_pixels: 1048576 (custom value)` and `image_max_pixels: 4194304`, and
one token covers a 32×32 px block. Every image is rounded to that grid and pulled
inside 1,024-4,096 tokens, keeping its aspect ratio: **smaller images are scaled
up to 1 MP, larger ones scaled down to 4.19 MP.**
[`vision_image_budget.py`](../../src/cofounder_agent/poindexter/services/vision_image_budget.py)
reproduces the rule. It was calibrated against the judge's own `prompt_eval_count`
and matches it exactly on all 15 sizes tried (see
[Calibrating the estimate](#calibrating-the-estimate)).

So the screenshot reached the model at about half scale, and the taller the page,
the smaller the scale:

| draft      | page (px)   | judge received | scale | flagged over 20 runs (unmodified leg)                                                                  |
| ---------- | ----------- | -------------- | ----- | ------------------------------------------------------------------------------------------------------ |
| `38c4b998` | 1280×2,738  | 1280×2752      | 1.00  | 0 (a miss: this page has a real dead image, see [Measured facts](#measured-facts-not-the-judges-eyes)) |
| `34877671` | 1280×6,653  | 896×4640       | 0.70  | 0                                                                                                      |
| `0bce0e39` | 1280×10,027 | 704×5728       | 0.55  | 0                                                                                                      |
| `16e658ee` | 1280×11,034 | 672×5984       | 0.53  | 0                                                                                                      |
| `4a23f39e` | 1280×11,867 | 672×6208       | 0.53  | 0                                                                                                      |
| `3ceda1c0` | 1280×13,141 | 608×6560       | 0.47  | **12** (score 75, `approved: false`)                                                                   |

"Flagged" is `approved: false` or a score under `qa_preview_pass_threshold`
(70). The four other clean drafts drew no objection in 80 runs, so the failure is
concentrated in the tallest page rather than spread thinly. (`38c4b998` is not a
clean page: its second image is a dead URL.) Every one of the 12
flagged runs on `3ceda1c0` carried an image claim. The claims read like text the
model could not resolve: it invented a filename for the hero (`preview_moderator.png`,
`preview-mock-tl_projects-0-30`), read the banner `IN_PROGRESS | Q: 82` as
`TL_PROJECTS | 0:30`, and "saw" empty gaps under sections whose images the DOM
shows loaded. At 0.47 scale 16 px body text is 7-8 px tall.

Repeating a request with the same PNG hits llama.cpp's prompt cache (about 0.6 s,
identical `prompt_tokens`), so repeats measure the judge's sampling variance on
identical input, which is what production sees for a given page.

### Tiles

`services/preview_screenshot.capture_html_tiles` measures the page, cuts it with
`plan_tiles`, and takes one clipped screenshot per tile, so no single image is
page-sized. Every tile is held to about one viewport of pixels
(`qa_preview_viewport_width` × `qa_preview_viewport_height`, 1280×1024 = 1.31 MP,
1,282 tokens), so it arrives at native scale and a request costs about
`qa_preview_max_tiles` tiles of context whatever the page looks like:

1. **Fits at native scale**: contiguous tiles, scale 1.0.
2. **Too tall, but shrinkable**: `qa_preview_max_tiles` equal tiles cover the whole
   page at `sqrt(tile_area × max_tiles / page_area)`, provided that stays at or
   above `qa_preview_min_scale`.
3. **Taller than that**: scale stops at the floor and the tiles are spread evenly
   down the page (first and last always included) with gaps between them. The
   verdict then says `sampled N tiles of a Mpx page`.

Tiles span the page's real width, so a table that overflows the viewport makes the
tiles wider than 1280 px instead of being cut off; the tile height shrinks with
the width so each still costs the same.

| page (1280 wide) | `min_scale` 1.0 (never shrink) | `min_scale` 0.6              |
| ---------------- | ------------------------------ | ---------------------------- |
| 2,738 px         | 3 tiles, native, whole page    | same                         |
| 8,192 px         | 8 tiles, native, whole page    | same                         |
| 10,027 px        | 8 tiles, native, **sampled**   | 8 tiles at 0.90, whole page  |
| 13,141 px        | 8 tiles, native, **sampled**   | 8 tiles at 0.79, whole page  |
| 22,700 px        | 8 tiles, native, **sampled**   | 8 tiles at 0.60, whole page  |
| 30,000 px        | 8 tiles, native, **sampled**   | 8 tiles at 0.60, **sampled** |

`qa_preview_max_tiles` is a **context budget**, not a preference. The judge runs at
one fixed context (`pinned_llm_endpoint_num_ctx`, 16,384 tokens); a request past it
is rejected. Eight tiles use about 10.3k tokens of it, plus the prompt (~500) and the
JSON verdict (~150-450), so the default leaves a third of the window free.
`MultiModelQA._clamp_preview_tiles` clamps an operator's cap to what the pinned
context holds (10 tiles at 1280×1024 with 3,072 tokens reserved), with a warning,
so raising the cap alone cannot darken the leg. Raise it only together with
`pinned_llm_endpoint_num_ctx`, after checking the judge GPU's VRAM headroom.
`tests/unit/services/test_vision_image_budget.py` derives the check from the
seeded defaults, so neither can move without the other.

The prompt (`qa.vision_preview_screenshot`, SKILL.md) tells the judge it is
looking at consecutive tiles of one screenshot, lists the page rows each covers,
says an element can run across a tile edge, hands it the facts the browser
measured ([below](#measured-facts-not-the-judges-eyes)), defines a loaded image as
"a tile shows a picture there", and asks it to name the tile for every issue.

### Measured facts, not the judge's eyes

Tiles made the pages legible and the false objections stop. They did not make the judge
sensitive. On the same drafts with one known defect injected, and on 8 real drafts that
carry a genuinely dead image, the tiled judge approved almost everything, so a quiet
verdict here is not evidence of a clean page. Chromium's DOM is the ground truth: each of
those 8 drafts (`de1191a4`, `3bc2fe74`, `5a384ae7`, `0293a956`, `308aea69`, `b831525d`,
`f03eddee`, `38c4b998`) has one or two `<img>` that never loaded (`naturalWidth` 0). The `/images/screenshots/…`
object each one points at now answers 404, and chromium refuses the HTML error page it
gets in place of an image (`net::ERR_BLOCKED_BY_ORB`). On a tile that is a 16 px
broken-image icon and a line or two of alt text between paragraphs, which a person spots
at once and a vision model reads as a caption.

Same tiles, five prompts, judge alone (4 samples per page, saved tiles; clean = 5 pages,
each defect = 3 pages, natural = the 8 drafts):

| prompt                                         | clean pages flagged | dead hero | dead inline | table overflows | raw markup | empty headings | real dead images |
| ---------------------------------------------- | ------------------- | --------- | ----------- | --------------- | ---------- | -------------- | ---------------- |
| P0 the tile prompt, original rubric            | 0/20                | 0%        | 0%          | 0%              | 25%        | 0%             | 9%               |
| P1 "inspect four things, list what you find"   | **20/20** (flat 85) | 100%      | 100%        | 100%            | 100%       | 100%           | 100%             |
| P2 a yes/no per tile for each defect           | 0/20                | 0%        | 0%          | 0%              | 25%        | 0%             | **0%**           |
| P3 measured facts, rubric trimmed to the rest  | **11/20**           | 100%      | 75%         | 67%             | 67%        | 67%            | 88%              |
| **P4 measured facts, rubric intact (shipped)** | 0/20                | 17%       | 17%         | 8%              | 0%         | 0%             | 53%              |
| **P4 plus the leg enforcing the facts**        | 0/20                | **100%**  | **100%**    | **100%**        | 0%         | 0%             | **100%**         |

The P4 rows are the same 20 + 12 + 12 + 32 judge runs: "judge alone" is what the model answered, and the last row adds the
leg's own check on top ([below](#measured-facts-not-the-judges-eyes)). P4's judge-alone figures are low on purpose, because
the prompt tells it the browser has already reported these facts and to leave them out of its issues. Leaked markup (3 of 12
under P0, 0 of 12 under P4) and empty headings (0 of 12 under P0, P2 and P4) are not measured by the browser, so they
stay with a judge that misses them. P1 and P3 flag those pages too, but they flag the clean pages just as readily (20 of 20
and 11 of 20), so their hits are not evidence that they saw anything.

No wording gets this judge to see a broken image: asked pointedly, per tile, it answers "no
broken image" on every real one (P2), and told to hunt, it invents problems on every clean
page (P1, and P3 once the rubric loses its image and layout lines). That is a limit of the
model on this task, so it is not fixed with prompt work.

So the leg does not ask the judge what the browser already knows. `capture_html_tiles`
reads two facts in the same CDP evaluation that measures the page (`PageFacts`):

- **Images that failed to load**: any `<img>` that is not both `complete` and non-empty,
  with its alt text and page row. This also catches a 404 page served as an image, which
  chromium blocks.
- **Horizontal overflow**: how many px wider than the viewport the page is.

The pipeline's inline images carry `loading="lazy"`, and with page JavaScript off chromium
loads them eagerly, so an image far below the fold is not reported as failed for never
having been scrolled to (3 of the evidence draft's 4 images are lazy, the lowest at row
8,563, and all four measure as loaded). The measurement depends on that: against a local
server, a lazy image 9,000 px down was fetched with JavaScript off and never fetched with
it on, where it would read as failed. Turning JavaScript on for this capture needs a
scroll pass first.

`MultiModelQA._check_rendered_preview_outcome` then does two things with them. It puts them in
the prompt as exact facts (`{page_facts}`), so on a clean page the judge is told "4 in the
page, all loaded" and has nothing to guess about: guessing about images is where the false
"placeholder hero" verdicts came from. And it enforces them itself: any failed image or
overflow is listed first among the issues, the score is capped one under
`qa_preview_pass_threshold`, and the review is not approved, whatever the judge answered.
By the rubric's own rule a serious visual defect scores below the pass line, so this is the
rubric applied deterministically. It is an objection on an advisory gate: `vision_gate` is
not `required_to_pass`, and if it graduates, a dead image is a veto that
`_NON_TEXT_FIXABLE_PROVIDERS` sends straight to a flag instead of futile rewrite passes.

What stays with the judge is what needs eyes: overall look, balance, empty or mangled
content. It is weak there too. Leaked markup and empty headings went unflagged in nearly
every run, so `qa.programmatic` and the text rails remain the ones that check the draft's
markup at the source. The rendered-preview leg should be read as "the browser measured
these two things exactly, and a vision model looked over the rest", not as a full visual
audit.

### Why the judge sees every tile

Sending several images to this judge does not work the obvious way. **Ollama 0.32.1
drops every second image of a request whose images share one message**: the 1st,
3rd and 5th reach the model and the 2nd, 4th and 6th never do, with HTTP 200 and no
warning. LiteLLM's `ollama/` route (`/api/generate`) merges every image into one
list, so it drops them too, and `/api/chat` drops them when they share a message.
Measured 2026-09-28 against the pinned judge, with distinct randomly numbered images
(`47`, `12`, ...) and no index in the text, so the only way to answer correctly is
to have seen each image in order:

| images (N) | one message, N images          | one image per message |
| ---------- | ------------------------------ | --------------------- |
| 2          | 0 of 2 requests read correctly | 2 of 2                |
| 4          | 0 of 2                         | 2 of 2                |
| 6          | 0 of 2                         | 2 of 2                |
| 8          | 0 of 2                         | 2 of 2                |
| 10         | 0 of 2                         | 2 of 2                |

The judge's `prompt_eval_count` says the same: N=8 in one message was 5,169 tokens
(4 images), one per message 10,337 (8). Through the real dispatcher, one message
with N images now reads back correctly 8 of 8 times for N = 2, 4, 8 and 10.

The fix is in `LiteLLMProvider` (`route_multi_image_for_ollama`): a request with
more than one image to an Ollama model gets one image per message, and an
`ollama/` model reaches LiteLLM as `ollama_chat/` (the `/api/chat` route).
Everything about **where** the call lands still comes from the caller's resolved
name: the `model_api_base_overrides` pin, the GPU-lock and context decisions the
dispatcher makes before the provider runs, and the `Completion.model` the cost log
records. That is deliberate. The override map is keyed on the exact resolved name
(`ollama/qwen3-vl:30b-a3b-instruct`), so a caller that swapped the prefix itself
would miss its pin, land on the default endpoint, and load the ~20 GB judge onto the
writer's GPU. Single-image and text-only requests, and every non-Ollama model, are
untouched.

`_check_image_relevance` never meets this bug in its current form: it already sends
one call per image, which poindexter#1078 added after the judge "did not reliably
keep image order". The symptom it describes (a reason that describes a different
image) is what a dropped image produces, so that workaround was very likely this
defect and not order confusion. Nothing else in the tree sends several images in
one Ollama request.

### Fonts

The preview page is `font-family: -apple-system, system-ui, sans-serif`. In the
worker image those generic names did not resolve to a sans face. fontconfig's
default lists put DejaVu and Noto first, neither is installed, so when
`fonts-jetbrains-mono` arrived for VHS (#937) it won every generic family:
`fc-match sans-serif` answered JetBrains Mono, and the whole page rendered in
monospace, although `fonts-liberation` had been installed all along. Chromium's
own `CSS.getPlatformFontsForNode` reports the font used for the article text:

| font in effect                                 | page @1280 | page @390 (phone) |
| ---------------------------------------------- | ---------- | ----------------- |
| as shipped (generic → JetBrains Mono)          | 13,141 px  | 20,826 px         |
| Liberation Sans alias                          | 10,918 px  | 15,983 px         |
| Roboto (Android's UI font, for reference)      | 10,861 px  | 15,983 px         |
| DejaVu Sans (the default if it were installed) | 11,869 px  | 18,143 px         |

Liberation Sans lands within 0.5% of Roboto and is already in the image, so
`Dockerfile.worker` now writes a fontconfig alias (`sans-serif`, `sans`, `system-ui`
→ Liberation Sans; `serif` → Liberation Serif) and asserts at build time that
`fc-match` agrees. `monospace` is left alone for VHS and the explicit
`'JetBrains Mono'` consumers (brand hero, video thumbnails, demo clips).
`chart_render` already names Liberation Sans first, so charts do not change.

The alias is inline in the Dockerfile rather than a copied file on purpose:
deploy-checkout-sync rebuilds the worker images only when `Dockerfile.worker` or
the dependency locks change, so an edit to a separate conf file would merge and
never reach the container. It takes effect when the worker images are rebuilt, and
it sits after the dependency layers (`poetry install`, the chromium download) and
just before `COPY . .`, so that rebuild reuses those cached layers instead of
re-downloading them.

To try a font setup without touching a container, point one process at another
fontconfig: `FONTCONFIG_FILE=<conf that includes /etc/fonts/fonts.conf and the
aliases>`. That is how the numbers above were taken.

The operator's copy of the page also scrolled sideways on a phone. Measured at 390
and 360 px on the five clean drafts of the benchmark set, one overflowed (`3ceda1c0`, page
`scrollWidth` 618): a link whose text is a bare URL
(`https://en.wikipedia.org/wiki/The_New_York_Times_v._Microsoft_and_Open...`) is 578 px
wide with no break opportunity, in a 358 px column. `article a` now carries
`overflow-wrap: anywhere`, and every draft measures 390 and 360 px wide after it. The rule
is on links alone: `article { overflow-wrap: anywhere }` also cleared it, but it lets
table columns shrink and break words mid-cell, which would hide the wide-table overflow
the QA leg reports. At the leg's 1280 px viewport the change is invisible unless a link is
wider than the 720 px column.

### Cost and stability

- **VRAM:** the pinned 3090 sits at 21,768 MiB with the judge resident (2,808 MiB
  free). The old single 4.19 MP image raised its reserved memory by ~410 MiB the
  first time it arrived; 8 tiles, sent uncached so the vision encoder really ran,
  never went past that mark. The KV cache is sized for 16,384 tokens at load
  whatever the request carries, each tile's encoder pass is smaller than the old
  image's, and prefill runs in 512-token batches.
- **Time:** an uncached judge call went from 6.3 s (one image) to 8.6 s (8 tiles,
  ~10.3k tokens), and the whole leg, chromium render included, from 8.2 s to 11.0 s
  ([Results](#results-before-and-after)); a repeat of the same page is served from
  the prompt cache in under a second.
- **Overrun is loud, not a truncation.** Measured with one image per message and
  distinct labels: 12 tiles (15,485 prompt tokens, 94% of the window) read back all
  12 labels; 13 tiles (16,772 tokens) was rejected with HTTP 400
  `exceed_context_size_error` ("request (16772 tokens) exceeds the available context
  size (16384 tokens)"), the log shows `truncated = 0`, and the next request was
  served normally. So a misconfigured cap darkens the leg with a named cause
  (`qa_rail_degraded`, `rail=rendered_preview`) rather than quietly judging a page
  it did not see. The clamp keeps that from happening in the first place. Chat
  template overhead is about 7 tokens per image message, inside the reserve.

### Calibrating the estimate

`estimate_image_tokens` encodes the deployed judge's sizing. If Ollama or its
bundled llama.cpp changes `--image-min-tokens` or the pixel ceiling, recalibrate:
send synthetic images straight to the pinned instance at its own context and
compare the reported prompt size with the estimate.

```bash
# text-only baseline first (15 tokens), then one image per request; subtract it
curl -s localhost:11435/api/chat -d '{"model":"qwen3-vl:30b-a3b-instruct","stream":false,
  "options":{"num_ctx":16384,"num_predict":1},"messages":[{"role":"user","content":"OK?"}]}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["prompt_eval_count"])'
```

Always send `num_ctx` equal to `pinned_llm_endpoint_num_ctx`: any other size
reloads the resident judge (10-40 s). Measured 2026-09-28, image cost =
`prompt_eval_count` − 15, every row equal to the estimate:

| image     | tokens | image     | tokens | image      | tokens |
| --------- | ------ | --------- | ------ | ---------- | ------ |
| 320×240   | 1038   | 1280×1643 | 2042   | 1280×13141 | 3897   |
| 640×640   | 1026   | 1280×2048 | 2562   | 2000×655   | 1262   |
| 800×600   | 1038   | 1280×3200 | 4002   | 2200×1024  | 2210   |
| 1024×1024 | 1026   | 1280×4096 | 3992   | 3000×3000  | 4098   |
| 1280×1024 | 1282   | 1011×1298 | 1314   | 700×1024   | 1055   |

`test_vision_image_budget.py` pins these. The 2000×655 row is the one that caught
a real bug: 2000 px is 62.5 blocks, and llama.cpp rounds half up (63) where
Python's `round()` goes to even (62).

### Results, before and after

Every number here is the real leg (`MultiModelQA._check_rendered_preview_outcome`)
run inside `poindexter-prefect-worker` against the pinned judge, on real drafts
from `pipeline_versions`, with the page in the state QA sees it: before
`content.compile_meta` runs, so no excerpt or SEO title, which reproduces the
13,141 px evidence page exactly. The code under test came from a copy of the branch
(or of `origin/main`, for "before") on `PYTHONPATH`, the bootstrap mirrors the
content flow's (`DatabaseService` → `build_and_wire_subprocess_with_container` →
`build_platform_for_subprocess` → `SettingsService`), and settings were overlaid in
process, so nothing was written to production: the only trace is `cost_logs` rows
with a `bench_` phase prefix. "Objection" is `approved: false` or a score under
`qa_preview_pass_threshold` (70). The fonts in the "after" rows came from
`FONTCONFIG_FILE` pointed at the alias, not from a rebuilt container.

**Clean pages: objections that should not happen.** Five drafts, 2.7k-13k px, every
image loaded and nothing overflowing (checked against chromium's DOM), 20 runs each:

| configuration                                   | runs | objections | on the evidence draft | runs with an image claim | mean score |
| ----------------------------------------------- | ---- | ---------- | --------------------- | ------------------------ | ---------- |
| before: one full-page image, monospace          | 100  | 12 (12%)   | 12 of 20              | 12                       | 90.9       |
| font only: one full-page image, Liberation      | 60   | 1 (2%)     | 0 of 12               | 1                        | 92.6       |
| tiles only: 8 tiles, monospace, no facts        | 60   | 0          | 0 of 12               | 3                        | 95.0       |
| tiles and font, no facts                        | 100  | 0          | 0 of 20               | 0                        | 94.9       |
| **this change: tiles, font and measured facts** | 100  | **1 (1%)** | **0 of 20**           | **0**                    | 94.8       |

Before, all 12 objections were on the evidence draft and every one carried a false
image claim. After, the single objection ("Tile 3: the table's last column runs past
the right edge of the container") is also false: that page's table is 720 px wide in
a 752 px column and the browser reports no overflow. The font alone brought the
evidence draft under the line by shortening it (13,141 → 10,918 px, so the judge's
scale rose from 0.47 to 0.55, about where the 11k px drafts had always run without an
objection). It is not a fix: the doubled article below is the same failure one page
length later. The 3 image claims in the tiles-only row are "cut off at a tile edge"
(the seam case in the limits below); none appeared in the 200 runs after.

**Real drafts with a dead image.** The 8 drafts above, 10 runs each:

| configuration               | runs | verdict is an objection | the judge alone objected | names the dead image |
| --------------------------- | ---- | ----------------------- | ------------------------ | -------------------- |
| before: one full-page image | 80   | 1 (1%)                  | 1 (1%)                   | 3 (4%)               |
| **this change**             | 80   | **80 (100%)**           | 51 (64%)                 | **80 (100%)**        |

Every draft went from 0-1 objections in 10 runs to 10 of 10. The "judge alone"
column is what the model answered with the facts in its prompt; the rest is the leg
enforcing what the browser measured.

**Known defects injected into real pages** (3 drafts; before 10 runs each, after 6):

| defect                                        | before         | this change                 |
| --------------------------------------------- | -------------- | --------------------------- |
| dead hero image                               | 4 of 30 (13%)  | **18 of 18**                |
| dead inline image                             | 4 of 30 (13%)  | **18 of 18**                |
| table 656 px too wide                         | 19 of 30 (63%) | **18 of 18**                |
| leaked markup (`&lt;details&gt;`, `**bold**`) | 6 of 30 (20%)  | 0 of 17 (1 run unparseable) |
| four empty headings in a row                  | 8 of 30 (27%)  | 0 of 18                     |

The last two rows did not improve, and the "before" figures for them are not sight.
On the two drafts other than the evidence page the old leg objected to 0 of 20 of the
markup pages and 1 of 20 of the empty-heading pages; the rest were the tall page's
false alarm ("placeholder" hero, missing images), which fired whatever was injected.
The judge is as blind to those two now as it was, with the false alarm gone. Of the 17
markup runs one returned JSON the model had broken by quoting `**bold text**` inside an
unescaped string (it was reporting the defect); the leg reads that as `failed`, a
`qa_rail_degraded` finding, and not as a pass.

**The article twice on one page** (19,086 px and 25,277 px as the old worker renders
them; every image loads, nothing overflows), 8 runs each:

| configuration               | runs | objections | images sent | prompt tokens |
| --------------------------- | ---- | ---------- | ----------- | ------------- |
| before: one full-page image | 16   | 16 (100%)  | 1           | 4.2k          |
| **this change**             | 16   | **0**      | 8 tiles     | 10.9k         |

The old leg's false objections grow with the page: at 19k and 25k px it objected every
time (scores 55-75), once complaining that the image was a screenshot and so "impossible
to assess". The aliased fonts make those pages about 16.8k and 20.9k px, and 8 tiles at
0.70 and 0.63 scale covered every row of both and scored 95 in 16 of 16.

**Cost of one call.** The first call for a page is uncached; repeats of an identical
request are served from llama.cpp's prompt cache.

| configuration                | images | prompt tokens | judge s, first call | whole leg s, first call | judge s, cached repeat |
| ---------------------------- | ------ | ------------- | ------------------- | ----------------------- | ---------------------- |
| before, clean pages          | 1      | 4.1k          | 6.3                 | 8.2                     | 0.6                    |
| **this change, clean pages** | 6-8    | 10.3k         | 8.6                 | 11.0                    | 0.6                    |
| before, doubled article      | 1      | 4.2k          | 7.4                 | 10.6                    | 1.1                    |
| **this change, doubled**     | 8      | 10.9k         | 10.7                | 14.1                    | 0.6                    |

2-4 s more per uncached call, inside a QA pass that runs for minutes.

### What is still weak

- **Leaked markup and empty headings** are the judge's alone, and it does not see
  them (0 of 35 injected pages objected). `qa.programmatic` and the text rails check
  the draft's markup at the source, which is where those defects are caught. The
  browser could measure both, but a heading directly under another heading is
  normal in an article (an `h2` then an `h3`), so a rule needs its own measurement
  against the corpus before it is allowed to object.
- **Seams.** A tile edge can cut an image or a table, and the model sometimes calls
  the cut a defect ("partially cut off at the bottom"). It happened in 3 of 60 runs
  in the tiles-only configuration, on one page, without changing a verdict, and in
  0 of 200 later runs. The prompt tells the judge the edges are arbitrary.
- **Very tall pages are sampled.** Past about 22,700 px at 1280 wide the tiles stop
  covering every row and the verdict says `sampled N tiles of a Mpx page`. The measured
  facts still cover the whole page, since the browser reads the DOM, not the tiles.
- **Eight old drafts point at dead screenshot URLs** (rejected drafts from
  2026-08-28 to 09-07). They are the natural test set above; nothing publishes from
  them.

### Re-measuring

The harness is not in the repository: it is a script that mirrors the content flow's
bootstrap (above), loads a draft from `pipeline_versions`, builds the page the way
`qa.vision` does (no excerpt, the writer's title), and calls
`_check_rendered_preview_outcome` with settings overlaid in process. Run it inside
`poindexter-prefect-worker` with **both** `poindexter/` and `skills/` copied to a
scratch directory on `PYTHONPATH` (the prompt pack loads from `skills/`, so copying
the package alone would measure the old prompt), and record what the judge is sent
and answers at the dispatch seam so the same harness measures "before" and "after".
Judge each finding against chromium's DOM, not against the verdict text: the DOM is
what showed the evidence draft's images loaded and the eight dead ones dead.

## The URL probe checks the link

`preview_base_url` sat in `operator_url_probe_skip_keys` from the probe's
false-positive cleanup in early May 2026 (the reason was not recorded). A mute
is blind: on the Linux host it hid the dead link. Un-muting alone would not
have been enough, because the right value, a MagicDNS name, could not be
probed from the container: public DNS answers it with the Funnel ingress, and
every cycle would have paged. So the probe now resolves tailnet names itself:

- a host ending in `operator_url_probe_tailnet_suffixes` (default `.ts.net`)
  is resolved through `operator_url_probe_tailnet_resolver` (default
  `100.100.100.100`, Tailscale's resolver, reachable from containers on a
  Linux host running `tailscaled`), and probed at that address with the
  original `Host` header (and TLS SNI for https). Redirects are not followed,
  because their targets would resolve publicly again, and a 3xx already
  proves the host answered;
- an unresolvable name fails with the host and resolver named; a dead address
  fails with the route in the detail;
- migration `20260928_174820_stop_muting_preview_base_url_in_the_operator_url_probe.py`
  removes the mute on existing installs, and the baseline seed no longer
  carries it.

On Docker Desktop (macOS/Windows) containers cannot reach `100.100.100.100`.
There, set `operator_url_probe_tailnet_resolver` to an empty string (every
host resolves publicly, as before) and, if a MagicDNS link then false-pages,
mute that key again.

## Settings

| Key                                   | Default                            | Read by                                    | Notes                                                                |
| ------------------------------------- | ---------------------------------- | ------------------------------------------ | -------------------------------------------------------------------- |
| `preview_base_url`                    | `''` (per-operator)                | `services/preview_links.py`, Grafana panel | the operator's device opens this; empty derives from the host below  |
| `operator_service_host`               | `localhost`                        | preview links, Grafana link render         | bare hostname only                                                   |
| `qa_preview_screenshot_enabled`       | on in the baseline                 | `qa.vision`                                | switches the rendered-preview leg                                    |
| `qa_preview_vision_model`             | `ollama/qwen3-vl:30b-a3b-instruct` | `multi_model_qa`                           | enabled without a model is a `failed` leg, not a silent skip         |
| `qa_preview_viewport_width`           | `1280`                             | `multi_model_qa`                           | tile width (the page's real width when it overflows)                 |
| `qa_preview_viewport_height`          | `1024`                             | `multi_model_qa`                           | tile height                                                          |
| `qa_preview_max_tiles`                | `8`                                | `multi_model_qa`                           | context budget: tiles per call, clamped to the pinned judge context  |
| `qa_preview_min_scale`                | `0.6`                              | `multi_model_qa`                           | smallest scale a too-tall page is shrunk to before tiles are sampled |
| `pinned_llm_endpoint_num_ctx`         | `16384`                            | dispatcher, `_clamp_preview_tiles`         | the judge's context; the tile clamp reads it                         |
| `operator_url_probe_tailnet_resolver` | `100.100.100.100`                  | brain URL probe                            | empty turns tailnet resolution off                                   |
| `operator_url_probe_tailnet_suffixes` | `.ts.net`                          | brain URL probe                            | CSV                                                                  |

## Checking it

```bash
# The operator's link, from the host (as the phone would, over the tailnet):
curl -s -o /dev/null -w '%{http_code}\n' http://<host>.<tailnet>.ts.net:8002/api/health

# The leg produces verdicts again (atom_runs output_preview holds the review):
psql ... -c "SELECT count(*) FILTER (WHERE output_preview LIKE '%rendered_preview%'), count(*)
             FROM atom_runs WHERE atom = 'qa.vision' AND created_at > now() - interval '1 day';"
```

In Loki, `[qa.vision] rendered-preview leg produced no verdict` means the leg
is on and dark; the finding carries the cause.

## Code

- [`services/preview_links.py`](../../src/cofounder_agent/poindexter/services/preview_links.py): the operator's link
- [`services/preview_page.py`](../../src/cofounder_agent/poindexter/services/preview_page.py): the page renderer, shared by the route and the QA leg
- [`services/preview_screenshot.py`](../../src/cofounder_agent/poindexter/services/preview_screenshot.py): chromium capture (`capture_html_tiles` and `plan_tiles` for the QA leg)
- [`services/vision_image_budget.py`](../../src/cofounder_agent/poindexter/services/vision_image_budget.py): what an image costs the judge, in context tokens
- [`services/llm_providers/litellm_provider.py`](../../src/cofounder_agent/poindexter/services/llm_providers/litellm_provider.py): `route_multi_image_for_ollama`, one image per message
- [`modules/content/atoms/qa_vision.py`](../../src/cofounder_agent/poindexter/modules/content/atoms/qa_vision.py): the rail
- [`brain/operator_url_probe.py`](../../src/cofounder_agent/poindexter/brain/operator_url_probe.py): tailnet-aware probing
