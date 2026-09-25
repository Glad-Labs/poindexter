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
