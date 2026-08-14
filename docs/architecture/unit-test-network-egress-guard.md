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
fails with the test id, the target `host:port`, and what to do about it.

**Loopback counts.** `127.0.0.1` _is_ the problem here; the services under test run
locally. A guard that exempted loopback would exempt the bug.

**AF_UNIX and odd address shapes pass through** — local IPC is not egress.

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

Host and CI **do not produce the same offenders**. The first CI run of this guard failed on
7 files that never egress on the host — they reach image-gen (`:9836`), wan (`:9840`),
chatterbox (`:9839`) and Ollama (`:11434`) across the Docker bridge, addresses that don't
resolve outside CI. Different environment, different reachable services, different code
path, different egress.

So regenerating the baseline from one environment alone produces a file that is wrong in the
other. **Regenerate from both and take the per-file max.**

Two of those CI-only files are `test_shot_list_renderer.py` and
`test_hero_vram_choreography.py` — the two "fixed" by glad-labs-stack#3193. That fix closed
the one seam causing the failure (`_live_free_vram_gb`); the tests still reach the network
through others, and passed only because the values happened to work out. A targeted fix to a
failing symptom is not the same as making a test hermetic, which is the argument for a guard
over case-by-case repair.

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
