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
(`services/preview_screenshot.capture_html_screenshot`, JavaScript off), and
asks `qa_preview_vision_model` whether the page looks like a real article:
overflowing tables, missing CSS, broken images, empty sections. Opt-in via
`qa_preview_screenshot_enabled`.

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

| Key                                   | Default                            | Read by                                    | Notes                                                               |
| ------------------------------------- | ---------------------------------- | ------------------------------------------ | ------------------------------------------------------------------- |
| `preview_base_url`                    | `''` (per-operator)                | `services/preview_links.py`, Grafana panel | the operator's device opens this; empty derives from the host below |
| `operator_service_host`               | `localhost`                        | preview links, Grafana link render         | bare hostname only                                                  |
| `qa_preview_screenshot_enabled`       | on in the baseline                 | `qa.vision`                                | switches the rendered-preview leg                                   |
| `qa_preview_vision_model`             | `ollama/qwen3-vl:30b-a3b-instruct` | `multi_model_qa`                           | enabled without a model is a `failed` leg, not a silent skip        |
| `operator_url_probe_tailnet_resolver` | `100.100.100.100`                  | brain URL probe                            | empty turns tailnet resolution off                                  |
| `operator_url_probe_tailnet_suffixes` | `.ts.net`                          | brain URL probe                            | CSV                                                                 |

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
- [`services/preview_screenshot.py`](../../src/cofounder_agent/poindexter/services/preview_screenshot.py): chromium capture (`capture_html_screenshot` for the QA leg)
- [`modules/content/atoms/qa_vision.py`](../../src/cofounder_agent/poindexter/modules/content/atoms/qa_vision.py): the rail
- [`brain/operator_url_probe.py`](../../src/cofounder_agent/poindexter/brain/operator_url_probe.py): tailnet-aware probing
