# The unit-test network egress guard

A unit test that opens a socket on this stack does not fail — it **succeeds against the
wrong thing**. CI runs in containers on the operator box, so `127.0.0.1` reaches the real
Postgres, the real image-gen server, the real wan server. The test stops grading the code
and starts grading whatever the pipeline happens to be doing.

That is not a hypothetical. `_fit_hero_dims_to_free_vram` asks a live wan server for free
VRAM and only falls back to `GPURegistry`; ten test call sites patched _the fallback_. The
tests passed at ~20 GB free and failed at ~4.5 GB — read as flakiness for weeks, blocked a
PR, and cost a full debugging session before anyone looked at which seam the code actually
reached (glad-labs-stack#3193).

## What the guard does

An autouse fixture in `tests/unit/conftest.py` patches `socket.socket.connect`,
`connect_ex`, and `socket.create_connection`. Any unit test that opens a TCP connection
fails with the test id, the target `host:port`, and what to do about it. It also patches
`socket.getaddrinfo`, `gethostbyname` and `gethostbyname_ex`, but those refuse only a
production name (see "Production endpoints outrank the baseline" below). An ordinary lookup,
such as an SSRF check resolving `localhost`, is untouched.

**Loopback counts.** `127.0.0.1` _is_ the problem here; the services under test run
locally. A guard that exempted loopback would exempt the bug.

**AF_UNIX and odd address shapes pass through** — local IPC is not egress.

## Production endpoints outrank the baseline

The baseline below lets a grandfathered file keep opening sockets. It was written as a
test-hygiene ratchet, and on the operator box that framing hid a production incident. The
self-hosted CI runners (`poindexter-ci-runner-1/-2`) are containers on the **production**
compose network (`glad-labs-website_default`). Inside them every compose service name
resolves to the live container, and `host.docker.internal` reaches the host's Ollama and
every published sidecar port.

Measured on 2026-09-25 from the sidecars' logs and the runners' job windows. Each row says
how it was attributed:

| production target | what CI sent it                                                                                                 | from                                                           |
| ----------------- | --------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| image-gen         | ~6,850 `POST /unload {"hard": true}` in 30 days, about 11 per `test-backend` job (access log, by client IP)     | `test_shot_list_renderer.py` (hero-clear rung not stubbed)     |
| speaches          | ~6,600 real TTS syntheses in 30 days, 10 per job (access log, by client IP)                                     | `test_generate_media_scripts.py`                               |
| wan               | ~1,400 hard unloads in 30 days, 3 per job (wan logs no client IP; attributed to CI bursts by time)              | `test_shot_list_progress.py`                                   |
| ComfyUI           | `/queue` then `/free`, which drops its loaded models, 3 per job (reproduced host-side; no client IP in its log) | `test_shot_list_progress.py`                                   |
| host Ollama       | `keep_alive: 0` evictions of whatever the pipeline had loaded: 32 in under two days (journal, by client IP)     | `test_writer_markers.py`, `test_image_atom_contracts.py`       |
| Prometheus        | reads that decided the code path under test; `POST /-/reload` whenever prod's last reload had failed            | `test_gpu_scheduler.py`, `test_render_prometheus_rules_job.py` |

image-gen declined 106 of those unloads only because a render happened to be in flight.
At least **9** found it idle with a model loaded, and it `os._exit`ed: a cold reload
(25 s to 5 min), during which a pipeline render falls back to stock art. Each of those nine
is a CI job whose unload burst is missing from image-gen's access log, with an exit landing
exactly where the burst should have been.

So the guard refuses these targets for **every** unit test. A baseline entry does not
cover them, and neither does `@pytest.mark.allow_network`:

- **Names** the compose stack answers to: every service key, `container_name`, `hostname`,
  network alias and `extra_hosts` alias (`host.docker.internal` is one). They are refused at
  `getaddrinfo` / `gethostbyname`, before any connect.
- **Ports** of every GPU service as the operator box publishes them: the published port of
  each compose service that reserves a GPU, and each host-native Ollama instance's
  `OLLAMA_HOST` port. On that box `localhost:9836` _is_ the live image-gen server.

Both sets are **derived** by `load_production_endpoints()` in `tests/unit/_egress_guard.py`
from the compose files and the Ollama launch files, never hand-listed, so a sidecar is
protected the day it lands in compose. `TestProductionEndpointsAreDerived` pins the anchors
and checks that every compose service key made it in, so a parser that stops matching
cannot disarm the refusal silently. Database ports are deliberately not on the port list.
Baselined DB tests reach Postgres through `bootstrap.toml` on the host, a separate and
already-ratcheted concern.

### Refusing the name is what makes the host tell the truth

A compose name fails DNS on the host, and a connection that never resolves never reaches
`socket.connect`. So the old guard saw nothing locally, the code took its "sidecar offline"
branch, and the test passed. In CI the same name resolved, the connect happened, and the
test hit production. Seven baselined files egressed **only** in CI for exactly this reason.
Refusing at resolution makes both environments fail the same way, at the first call in the
chain. For compose names, a host run is now a faithful preview of CI.

### Report mode never reaches production either

Report mode normally records a connection and lets it proceed, so one CI run yields every
offender. For a production target that would do the damage itself. It records the target
and then fails it the way the host always did: `socket.gaierror` for a name, and
`ConnectionRefusedError` for a port. Those are ordinary exceptions the code under test
already handles.

### Fixing a test that trips it

Stub the seam the code reaches through, at the first call in the chain:

| seam                                                                    | stub                                                                                                                            |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| GPU reclaim rungs (`GPUScheduler._unload_*`)                            | `tests.unit._gpu_isolation.make_reclaim_rungs_inert(monkeypatch)`. It derives the rung list, so a new rung is covered           |
| a hard rung's free-VRAM read (`GPUScheduler._render_free_vram_gb`)      | patch it to return `None`, the documented "unreadable" reading that declines any restart                                        |
| writer unload before image-gen (`maybe_unload_writer_before_image_gen`) | patch it at `poindexter.services.llm_providers.ollama_unload` to return `[]`                                                    |
| TTS (`synthesize_speech`, gated by `is_tts_enabled`)                    | patch either one. A bare `MagicMock` SiteConfig's `get_bool` returns a truthy mock and turns TTS **on**; give it real semantics |

The hand-kept-list failure is worth naming. `test_shot_list_renderer.py` carried a fixture
that stubbed the wan and ComfyUI rungs by name and missed image-gen, the one that did the
damage. `test_gpu_scheduler.py::_mock_all_rungs` held the same kind of list. Both now derive
it.

### Why not a network blackhole for the runner

A compose-level fix (a separate network for the runners, or `extra_hosts` mapping the
sidecar names to nothing) would also cover subprocesses, which an in-process guard cannot
see. It was considered and not taken, for three reasons. The runners need
`postgres-local:5432` for the integration step's GPU advisory lock. They need
`host.docker.internal:11434` for the real-model integration tests, which run against the
host Ollama on purpose and queue behind the pipeline's lock. And `host.docker.internal`
also reaches every published sidecar port, so the blackhole would have to be port-shaped
rather than name-shaped. Changing it also means recreating the runners, which cancels
in-flight jobs. The in-process guard knows which test is running and names it; a
harvest of the whole suite with a `sitecustomize` hook found no subprocess reaching a
sidecar (2026-09-25).

## Background exporters: Langfuse tracing is off for the unit tier

The guard judges a connection by the test that is running when it happens. A span processor
breaks that assumption, because it exports from its own thread on a timer. The test it names
is whichever one happened to be running. An export during a baselined test goes through. The
flush at interpreter exit runs after every patch has been undone.

On the operator box that sent unit-test spans to the live Langfuse web on `localhost:3010`. A
2026-09-25 harvest that pointed `localhost:3010` at a recorder saw exports blamed on five
files, and the list changed from run to run: `test_dispatch_phase_coverage.py`,
`test_source_featured_image_transient_retry.py`, `test_ollama_client_resilience.py`,
`test_inline_image_helpers.py` and `test_chatterbox_server_unload.py`. None of them built the
exporter. One `tests/unit/services` run, traced from the exporter's constructor, found one
exporter and 36 connection attempts from the SDK's batch thread:

1. `test_litellm_langfuse_callback.py` calls the real `configure_langfuse_callback`, which
   copies `LANGFUSE_HOST=http://localhost:3010` and a test key pair into `os.environ`. The
   test's `monkeypatch.delenv(..., raising=False)` records nothing for a variable that was
   absent, and the conftest's restore list did not name them, so they outlived the test.
2. The next `@observe` call in the same xdist worker had the Langfuse SDK build a client from
   them. In the traced run that was `plan_images`, in `test_image_decision_agent.py`. With a
   key present, the client starts an OTLP `BatchSpanProcessor` aimed at
   `http://localhost:3010/api/public/otel/v1/traces`.

The fix is at the source. `tests/unit/conftest.py` sets `LANGFUSE_TRACING_ENABLED=false`
before any import. `Langfuse.__init__` combines its own tracing flag with that variable, so no
tracer provider, span processor or exporter is built, whichever path constructs the client:
`@observe`, the prompt manager, the experiment service or the eval harness. `SiteConfig` falls
back to the upper-cased environment variable for a key with no row, so the app-level
`langfuse_tracing_enabled` reads false as well, and `configure_langfuse_callback` registers
no litellm OTEL exporter. The leaked variables are also restored after every test now
(`_ENV_KEYS_TO_ISOLATE`), along with the cloud API keys that `configure_cloud_api_keys`
writes the same way.

Two choices here are deliberate:

- **Assigned, not `setdefault`.** The other unit-tier defaults let an inherited value win.
  This one does not. The worker containers set `LANGFUSE_TRACING_ENABLED: "true"` beside real
  keys, and a unit run inside one (it has been done, in `poindexter-worker` on 2026-07-29)
  would otherwise export with credentials that work.
- **Not `OTEL_SDK_DISABLED`.** That switches off the OpenTelemetry SDK itself, so tests that
  assert on spans through an `InMemorySpanExporter` (`test_pipeline_node_spans.py`,
  `test_brain_health_probes.py`) would record nothing.

A test that exercises an exporter sets the variable to `true` with `monkeypatch` and passes
its own `span_exporter=` and `tracer_provider=`. The second keeps the SDK from installing a
process-global provider. `TestLangfuseExportIsOffForTheUnitTier` in
`tests/unit/test_network_egress_guard.py` does exactly that as its control, and pins that the
switch still reaches the SDK and `configure_langfuse_callback`.

## Two properties that look like style and are not

### 1. The exception derives from `BaseException`

Most code this guard watches is best-effort network code inside a broad
`except Exception` — this repo baselines **108** such handlers. An `Exception` subclass is
swallowed _by the code under test_, the connection is absorbed, and the test passes green.

This was measured, not reasoned about. With an `AssertionError` base, un-baselining
`test_operator_notifier.py` — which really does open TLS to `api.telegram.org` — still
produced `26 passed`. With `BaseException` the same run produces 5 failures, matching the
probe's count for that file exactly.

Pinned by `TestSurvivesBroadExcept` in `tests/unit/test_network_egress_guard.py`.

### 2. The exception lives in `_egress_guard.py`, not `conftest.py`

pytest imports a conftest under its own rootdir-derived module name. A test doing
`from tests.unit.conftest import UnitTestNetworkEgress` gets a **second, unequal class
object**, and `pytest.raises` cannot catch what the guard raised. Same dual-module-identity
trap that broke `test_litellm_langfuse_callback` through `importlib.reload`
(glad-labs-stack#3155): when two paths reach one file, its classes stop being each other.

conftest imports from `_egress_guard`; tests import from `_egress_guard`; one class.

## The baseline, and burning it down

A full-suite socket probe on 2026-08-13 found **95 tests across 27 files** already opening
connections. Failing all of them at once is unlandable, so this follows the pattern already
used by `lint_silent_excepts` (108), `adapter_purity_lint` (69), and `bandit_lint` (36):
baseline what exists, forbid anything new, let the baseline **only shrink**.

`tests/unit/network_egress_baseline.txt` holds `<count> <path>` lines. To burn one down:

1. Find what the test reaches — run it and read the guard's message, or drop its line from
   the baseline and let the failure name the target.
2. Stub the seam the code reaches through. **Patch the first call in the chain, not a
   fallback** — that mistake is what #3193 was.
3. Lower the count (or delete the line) and re-run.

What the probe found, by target:

| target                                          | tests |
| ----------------------------------------------- | ----: |
| Postgres (`:5432` / `:5433`)                    |    85 |
| worker API (`:8002`)                            |     5 |
| external search API                             |     4 |
| alertmanager, Langfuse                          |     4 |
| `api.telegram.org`, `raw.githubusercontent.com` |     2 |

The Postgres majority is the real story: most of these want a stubbed pool, not a live DB.

The worker API row was media feed renders. `media_distribute` and `publish_service`
re-render `/api/podcast/feed.xml` and `/api/video/feed.xml` from `internal_api_base_url`,
which is `localhost:8002` on the operator box. Both go through the shared rebuild seam, so
stub `rebuild_video_feed` or `rebuild_podcast_feed` at
`poindexter.services.media_feed_rebuild`, where they are defined. The callers import them
when they call them, so a patch on the seam module is the one they pick up. Both files were
burned down on 2026-09-25. `publish_service._upload_media_to_r2_bg` still fetched inline
then, and its tests patched `httpx.AsyncClient` until it moved onto the seam the same day.

### The baseline is a union, not a snapshot

Host and CI **did not produce the same offenders**. The first CI run of this guard failed on
7 files that never egressed on the host. They reached the GPU sidecars and Ollama through
compose names and `host.docker.internal`, and those names do not resolve outside CI. Different environment, different reachable services,
different code path, different egress.

That split was the production incident above. Those names are now refused in both places,
so for them the host and CI agree. What still differs is ordinary loopback egress: the host
has Postgres and the worker API on loopback and the CI runner does not. So regenerating the
baseline from one environment alone can still produce a file that is wrong in the other.
**Regenerate from both and take the per-file max.**

Two of those CI-only files were `test_shot_list_renderer.py` and
`test_hero_vram_choreography.py`, the two "fixed" by glad-labs-stack#3193. That fix closed
the one seam causing the failure (`_live_free_vram_gb`). The renderer tests still reached
the network through others, the image-gen hard unload among them, and passed only because
the values happened to work out. They were the ones hard-unloading production. A targeted
fix to a failing symptom is not the same as making a test hermetic, which is the argument
for a guard over case-by-case repair.

## Escape hatch

```python
@pytest.mark.allow_network
def test_thing_that_really_needs_a_socket(): ...
```

Registered in `pyproject.toml` (the suite runs `--strict-markers`), so it is explicit and
greppable. Prefer stubbing; a test that genuinely needs a live service belongs in
`tests/integration/`.

## Why measurement, not review

The issue that prompted this (poindexter#1011) proposed ranking tests by duration, on the
theory that network coupling shows up as wall clock. **That would have found almost none of
them.** Duration only catches connections that hang — about ten tests. The other ~85 connect
to a live local Postgres in milliseconds and are invisible to timing. A socket probe found
roughly ten times more than the timing heuristic did.

Reading code for missing patches is worse still: `test_shot_list_renderer.py` already
carried three autouse fixtures neutralising real HTTP, one of which names "the known
silent-test-network-hazard shape" — and `_live_free_vram_gb` slipped past all of them.
