# Move the rerank ML stack to an opt-in extra so CI skips cu128 torch

- **Date:** 2026-06-23
- **Status:** Design — approved, pending spec review
- **Author:** Matt + Claude (brainstorming session)
- **Scope:** one focused dependency/CI change. testmon (the other CI idea from the
  same conversation) was explicitly de-scoped.

## Problem

CI installs a ~2.5–3 GB **CUDA** build of `torch` onto runners that have **no GPU**,
purely as a transitive consequence of one dependency classification.

Chain of causation:

1. `torch` is pinned to the CUDA index — `torch = { version = ">=2.7", source = "pytorch-cu128", optional = true }`
   ([`src/cofounder_agent/pyproject.toml`](../../../src/cofounder_agent/pyproject.toml)).
   The pin is load-bearing for **prod SDXL** on the RTX 5090 (Blackwell sm_120) and
   must not change.
2. `sentence-transformers` is a **non-optional main dependency** (it powers the
   LlamaIndex cross-encoder reranker). It requires `torch` transitively.
3. CI's `poetry install --no-root` (no extras) is therefore obligated to resolve
   `sentence-transformers` → `torch`, and `torch`'s only registered source is
   `pytorch-cu128`. So **every CI install pulls the full CUDA wheel.**

The cost is visible in the workflows themselves: `unit-tests.yml`, `integration-db.yml`,
and `benchmarks.yml` each carry a `sudo rm -rf /usr/local/lib/android /usr/share/dotnet
/opt/ghc …` step that exists **solely** to free disk for the torch wheel, because the
GitHub-hosted runner otherwise hits `OSError(28) No space left on device` mid-install
(documented inline at [`unit-tests.yml:255`](../../../.github/workflows/unit-tests.yml#L255)
and [`integration-db.yml:100`](../../../.github/workflows/integration-db.yml#L100)).

### Why this is the right thing to fix

- It's the actual CI bottleneck (install size + disk pressure), not redundant test
  execution.
- It's an **honest-dependency-graph** problem: CI does not exercise the GPU ML stack,
  so the stack should be optional, and the graph should say so.
- The fix helps **every** runner — the self-hosted PC runner, the `ubuntu-latest`
  fallback, and the public `poindexter` mirror — not just one.

### Why torch is genuinely not needed in CI

Every `import torch` / `import sentence_transformers` in production code is
**absence-tolerant** — either lazy (inside a function) or guarded at module level by a
`try/except ImportError` that sets a `*_AVAILABLE` flag. None is an unguarded top-level
import. Verified by grep:

| File                                       | Line | Style                                                                              |
| ------------------------------------------ | ---- | ---------------------------------------------------------------------------------- |
| `services/rag_engine.py`                   | 641  | lazy — `from sentence_transformers import CrossEncoder` inside the reranker method |
| `services/topic_dedup_semantic.py`         | 58   | lazy — inside a function                                                           |
| `utils/startup_manager.py`                 | 897  | lazy — inside the lifespan warmup                                                  |
| `services/image_service.py`                | 84   | module-level, `try/except ImportError` → `TORCH_AVAILABLE`                         |
| `services/image_providers/_sdxl_models.py` | 44   | module-level, `try/except ImportError` → `TORCH_AVAILABLE` / `torch = None`        |

Neither style is an unguarded top-level import, so **removing torch from the CI env does
not break test collection** — the guarded modules import fine with `TORCH_AVAILABLE =
False`. Only two test files reference these libs, and both already tolerate absence:

- `tests/unit/services/test_rag_engine.py` — calls `pytest.importorskip(...)` at line 19
  and injects a fake `sentence_transformers` module for the real-rerank cases.
- `tests/unit/cli/test_media_cli.py` — lazy-imports / mocks torch.

## Goals

- CI's default `poetry install` pulls **neither** `sentence-transformers` nor `torch`.
- The disk-reclaim hacks in the three workflows become deletable.
- **Prod is byte-identical:** the worker image still contains both `sentence-transformers` and the cu128 `torch` build.
- Host-dev parity preserved for Matt's machine.

## Non-goals

- Changing the cu128 source pin (prod SDXL depends on it).
- Switching the **worker** to CPU torch. Now that the reranker runs on CPU (#1882) the
  worker arguably no longer needs the CUDA build — a potential ~2 GB image saving — but
  that touches the GPU/SDXL story and is a separate, higher-risk change. Captured as a
  follow-up below.
- pytest-testmon / incremental test selection (de-scoped this session).

## Design

### The dependency change

In `src/cofounder_agent/pyproject.toml`, mark `sentence-transformers` **optional** and
reference it from a new `rerank` extra. (In Poetry's model the dependency stays
_declared_ in `[tool.poetry.dependencies]` — it just gains `optional = true` — and the
extra lists it by name. This is exactly how `youtube`/`profiling`/`ml` already work.)

Two coordinated edits:

```toml
# [tool.poetry.dependencies] — was: sentence-transformers = "^3.4"
sentence-transformers = { version = "^3.4", optional = true }
# torch is already { version = ">=2.7", source = "pytorch-cu128", optional = true } — untouched
```

```toml
[project.optional-dependencies]
ml      = ["torch", "transformers", "diffusers"]   # unchanged
rerank  = ["sentence-transformers", "torch"]        # NEW — the worker's CPU cross-encoder
profiling = ["pyroscope-io"]                         # unchanged
youtube = ["google-auth", "google-auth-oauthlib", "google-api-python-client", "pyasn1"]  # unchanged
```

- `torch`'s declaration and cu128 source pin are untouched; it simply stops being
  pulled by a _main_ dependency.
- `torch` is listed explicitly in `rerank` (mirroring the `ml` extra) to document intent
  and guard against a future where `sentence-transformers` drops its hard torch dep.
- `poetry lock` is regenerated. Expectation: **no version changes** — only the
  optional/extras metadata shifts. The plan verifies the lock diff contains no dependency
  version drift (only `sentence-transformers`/`torch` moving to extra-gated, and the
  `markers`/`extras` bookkeeping that follows).

### Install-profile invariant

The design hinges on keeping every **deployment** image's contents identical. **Two
images run the pipeline** — the operator worker and the OSS standalone — and both opt
into `rerank`; CI and everything else omit it.

| Install site                                                                                                    | Today                                                                                     | After                                             | Net effect             |
| --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------- | ---------------------- |
| **Worker** (`Dockerfile.worker`, builds `worker`/`pipeline-bot`/`prefect-worker` in `docker-compose.local.yml`) | `--only main --extras "profiling youtube"` → sentence-transformers + cu128 torch via main | `--only main --extras "profiling youtube rerank"` | **identical contents** |
| **OSS standalone** (`docker-compose.yml` → `cofounder`, `src/cofounder_agent/Dockerfile`)                       | `--only main` → sentence-transformers + cu128 torch via main                              | `--only main --extras "rerank"`                   | **identical contents** |
| **CI** (`unit-tests` / `integration-db` / `benchmarks`)                                                         | `poetry install --no-root` → pulls cu128 torch                                            | `poetry install --no-root` → lean, no torch       | **the win**            |
| **Host dev** (`scripts/bootstrap.sh`)                                                                           | `poetry install --no-root` → sentence-transformers via main                               | `poetry install --no-root --extras rerank`        | **parity preserved**   |
| **SDXL** (`scripts/Dockerfile.sdxl`)                                                                            | own CUDA base image (`pytorch/pytorch:2.9.1-cuda12.8`), pip-installs diffusers directly   | unchanged                                         | unaffected             |
| **auto-embed / brain / wan / voice / backup**                                                                   | own context; pip-install minimal deps or own pyproject (no torch)                         | unchanged                                         | unaffected             |

### Files touched

1. **`src/cofounder_agent/pyproject.toml`** — the `rerank` extra; remove
   `sentence-transformers` from main. Update the adjacent explanatory comment block.
2. **`src/cofounder_agent/poetry.lock`** — regenerated (`poetry lock`, no version drift).
3. **Both pipeline Dockerfiles** — add `rerank` to the poetry-install extras **plus a
   build-time fail-fast assert** `python -c "import sentence_transformers"` so a dropped
   extra fails the _build_, not silently at runtime:
   - `src/cofounder_agent/Dockerfile.worker` (operator worker/pipeline-bot/prefect-worker):
     `--extras "profiling youtube"` → `--extras "profiling youtube rerank"` at line 115;
     add the assert after the existing pyroscope/youtube asserts (lines 129/135).
   - `src/cofounder_agent/Dockerfile` (OSS standalone `cofounder` service in
     `docker-compose.yml`): `--only main` → `--only main --extras "rerank"` at line 55;
     add the same assert.
4. **`.github/workflows/unit-tests.yml`** — delete the two torch-justified disk-reclaim
   steps (guarded `!vars.CI_RUNNER`, lines ~128 and ~263); update the stale
   "sentence-transformers → torch (multi-GB wheels)" comments.
5. **`.github/workflows/integration-db.yml`** — delete the "Free disk space for the full
   poetry env" step (line ~100); update its torch comment.
6. **`.github/workflows/benchmarks.yml`** — the benchmarks run endpoint-latency tests via
   the FastAPI `TestClient` (not the reranker), so the lean install is correct. Delete its
   "Free disk space" step (lines 74–80). No `--extras` needed.
7. **`scripts/bootstrap.sh`** — add `--extras rerank` to both `poetry install` calls
   (lines 134/137) to preserve host parity.
8. **Dev docs** (`README.md`, `docs/operations/local-development-setup.md`,
   `src/cofounder_agent/README.md`) — note the `--extras rerank` /
   `pip install -e "src/cofounder_agent[rerank]"` opt-in.
9. **Tests** — expected zero changes; the plan runs the two affected files in the lean
   env and adds an `importorskip` only if one actually needs it.

## Error handling / fail-loud

- **Build-time:** the new `import sentence_transformers` assert in `Dockerfile.worker`
  converts a missed extra into a red build (consistent with `feedback_no_silent_defaults`).
- **Runtime (already exists):** `rag_engine.py:641` lazy-imports `CrossEncoder` and logs
  an actionable dep+setting hint on failure, degrading to passthrough rather than crashing.

## Testing & verification (evidence before "done")

1. **Lean env locally:** install with the new config and no `rerank` extra →
   assert `python -c "import torch"` **fails** (proves it's gone) → run the full unit
   suite green.
2. **Worker env:** `poetry install --extras "profiling youtube rerank"` →
   assert `import sentence_transformers` and `import torch` succeed.
3. **CI proves itself:** this PR's own `unit-tests` + `integration-db` runs exercise the
   lean install. Green = proven against real runners.
4. **Worker image build:** `docker compose -f docker-compose.local.yml build
prefect-worker` passes, including the new fail-fast assert.

## Success metrics

- CI `Install dependencies` step: peak disk drops from multi-GB to a few hundred MB; the
  `rm -rf` reclaim steps are removed and CI still passes.
- Install step wall-time decreases (torch wheel download + unpack eliminated).
- Captured before/after in the PR description.

## Rollout

- Single PR against `Glad-Labs/glad-labs-stack` (source of truth), per
  `feedback_all_changes_via_pr`. CI green is the merge gate.
- After merge: rebuild the worker image (`docker compose ... build prefect-worker`,
  standing authority per `feedback_rebuild_authority`).
- The public mirror inherits the lean install automatically — a strict improvement there
  (it currently pays the same torch tax on `ubuntu-latest`).

## Install-site classification (resolved during planning)

All install sites were enumerated and classified before the plan was written:

- **`src/cofounder_agent/Dockerfile`** — **NOT** an unused coordinator. It is the OSS
  standalone `cofounder` pipeline service in `docker-compose.yml` ("the simplest way to
  run the pipeline"). It runs the reranker → **gets `--extras rerank`** (table above).
- **`scripts/Dockerfile.sdxl`** — `FROM pytorch/pytorch:2.9.1-cuda12.8` base image,
  pip-installs diffusers directly; does not consume the cofounder lock. Unaffected.
- **`scripts/Dockerfile.auto-embed`** — pip-installs its own minimal deps (asyncpg/httpx/
  pydantic/apscheduler), no torch, embeddings via Ollama HTTP. Unaffected.
- **`src/cofounder_agent/poindexter/pyproject.toml`** — CLI package; deps are
  `click`/`asyncpg`/`httpx` only, no ML libs. Unaffected.
- **`benchmarks.yml`** — endpoint-latency tests via `TestClient`, not the reranker. Lean
  install correct.

## Open items (not blockers)

1. **Issue routing** (`feedback_check_issue_routing_first`) — file the tracking issue;
   this is OSS-product CI/dependency structure, so likely `poindexter` (public). Confirm
   at PR time.

## Follow-up (separate work) — slim the worker to CPU torch

Approved as a **separate** change (not folded into this CI fix), because it is a
_substitutive_ change (cu128 → cpu) rather than this fix's _subtractive_ one, and doing
it without eroding lock-authority means re-homing the cu128 pin.

Evidence gathered this session that de-risks the follow-up:

- **The worker has no GPU passthrough** (no `driver: nvidia` reservation in its
  `docker-compose.local.yml` service) **and no `diffusers`** (`--only main`, no `ml`
  extra). So `torch.cuda.is_available()` is `False` in the worker and there is no
  in-process GPU path — the cu128 build is ~2 GB of dead weight; CPU torch is
  functionally equivalent there.
- **The SDXL container is self-contained:** [`scripts/Dockerfile.sdxl`](../../../scripts/Dockerfile.sdxl)
  is `FROM pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime` and pip-installs diffusers
  directly — it does **not** consume the cofounder lock's torch. So the cofounder cu128
  pin's only real consumer is the **in-process diffusers fallback** (host + `--extras ml`
  - a GPU), not the SDXL container.

Follow-up scope: confirm whether the in-process diffusers path is still live and where
cu128 must remain, then make **CPU the cofounder-default torch** with cu128 retained only
for that path. Lands the ~2 GB worker-image saving (and likely lets host-dev go
CPU-default too). Its own spec.
