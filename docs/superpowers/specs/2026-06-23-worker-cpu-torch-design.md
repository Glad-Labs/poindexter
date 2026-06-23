# Slim the cofounder pipeline images to CPU-only torch

- **Date:** 2026-06-23
- **Status:** Design — approved, proceeding to plan + implementation
- **Author:** Matt + Claude (brainstorming session)
- **Scope:** the follow-up captured in
  [`2026-06-23-ci-rerank-extra-design.md`](2026-06-23-ci-rerank-extra-design.md)
  ("Follow-up (separate work) — slim the worker to CPU torch"). One focused
  dependency change across the two cofounder pipeline images plus the root
  dev-harness pyproject.

## Problem

Both pipeline images install a ~2.5–3 GB **CUDA** build of `torch` (`2.11.0+cu128`)
that no part of either container can use:

- The **worker** image (`Dockerfile.worker` → `worker` / `pipeline-bot` /
  `prefect-worker`) and the **OSS standalone** image (`Dockerfile` → `cofounder`)
  both opt into `--extras rerank`, which pulls `sentence-transformers` → `torch`,
  and torch's only registered source is `pytorch-cu128`.
- Neither container has GPU passthrough, and neither installs `diffusers` (no
  `ml` extra). So `torch.cuda.is_available()` is already `False` in them today —
  the only torch consumer is the **CPU** cross-encoder reranker (#1882).

The CUDA build is therefore dead weight: on Linux it drags in **18 transitive
`nvidia-*`/`cuda-*` packages** (`cuda-toolkit 12.8.1`, `nvidia-cudnn-cu12`, the
cublas/cufft/cusparse/nccl family) gated `(extra == "ml" or extra == "rerank")
and platform_system == "Linux"`. CPU torch (`2.11.0+cpu`, ~200 MB, zero nvidia
deps) is functionally identical for the reranker.

### Why this was deferred from #1891

#1891 was a _subtractive_ change (stop pulling torch into CI). This is a
_substitutive_ one (cu128 → cpu) that touches the prod GPU/SDXL story, so it got
its own spec. The crux #1891 flagged: torch is pinned `source = "pytorch-cu128"`,
so giving the worker CPU torch either (a) erodes lock-authority (pip-installing
torch outside poetry — the drift class behind a 28 h Prefect outage) or (b)
needs re-homing the pin. This spec picks (b), done cleanly.

## Investigation findings (the crux, resolved)

The brief's premise was "cu128's only real consumer is the in-process diffusers
SDXL fallback." The code says that fallback is **unwired everywhere**:

1. **Two SDXL paths exist** in `services/image_service.py::generate_image`:
   - **Strategy 1** (lines 1012–1098): HTTP POST to `sdxl_server_url`
     (`host.docker.internal:9836`) — the self-contained `sdxl-server` container
     (`scripts/Dockerfile.sdxl`, `FROM pytorch/pytorch:2.9.1-cuda12.8`,
     pip-installs its **own** torch + diffusers; does **not** consume the
     cofounder lock).
   - **Strategy 2** (lines 1100+): in-process diffusers, gated by
     `DIFFUSERS_AVAILABLE` (the `ml` extra) **and** a GPU.
2. **Nothing installs `--extras ml`** — not the worker/OSS Dockerfiles, not
   `bootstrap.sh`, not any `docker-compose*.yml` service. Verified by grep across
   `*.yml/*.sh/*.toml/*.md/*.ps1/Dockerfile*`.
3. The documented enablement instruction (`utils/startup_manager.py:900`,
   "`pip install -r scripts/requirements-ml.txt`") points at a file that
   **does not exist**.
4. `modules/content/stages/source_featured_image.py:228–235` states it
   authoritatively: _"The worker container no longer installs the `ml` extras
   (diffusers + torch + sentence-transformers moved out to dedicated
   containers), so `sdxl_available` is permanently False… The real SDXL path
   goes through … HTTP POST to `sdxl_server_url`."_

**Conclusion.** The in-process diffusers path is not live in any deployed
surface. The cu128 that actually renders SDXL lives in the `sdxl-server`
container with its own torch. cu128 in the _cofounder lock_ serves nothing
live — so "re-home cu128 for the in-process path" is really "retire a dead pin
and let cu128 live where it's already exercised (the SDXL container)."

### Runtime-invariance (the core safety argument)

`torch.cuda.is_available()` already returns `False` in the worker today (no GPU
passthrough). CPU torch still exposes the `torch.cuda` namespace (reporting zero
devices), so every existing `is_available()` guard takes the identical `False`
branch. **The image gets ~2 GB smaller; runtime behavior is byte-identical.**

## Decision

**Drop cu128 from the cofounder lock entirely.** Add a `pytorch-cpu` explicit
source and pin torch to it. One torch, one source, no two-torch ambiguity, no
lock-erosion. cu128 stays solely in `scripts/Dockerfile.sdxl`. Confirmed by Matt
(brainstorming, 2026-06-23): the in-process path is retired in favour of the
container; `--extras ml` becoming a _CPU_ diffusers fallback is acceptable (it's
strictly more useful than today's GPU-only, never-installed extra).

### Approaches considered

- **A — CPU as the single cofounder-lock torch (chosen).** cu128 leaves the
  cofounder pyproject; lives only in the SDXL container. Clean, honest graph,
  fixes the footgun. `--extras ml` resolves CPU torch.
- **B — two-source split (cu128 for `--extras ml`, cpu otherwise).** Rejected:
  Poetry locks exactly one `(version, source)` for torch per environment; the
  `ml`/`rerank` extras aren't mutually exclusive, so a source split is ambiguous
  and risks the same broken two-torch lock that bit #1891 — brittle across the
  Poetry 1.8.5 / 2.x version skew across the images.
- **C — CPU override at the Dockerfile layer (pip over poetry).** Rejected: the
  explicit lock-erosion / drift anti-pattern the brief warned against.

## Design

### Dependency change — `src/cofounder_agent/pyproject.toml`

```toml
# [[tool.poetry.source]] — replace the cu128 block with cpu
[[tool.poetry.source]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
priority = "explicit"
```

```toml
# [tool.poetry.dependencies] — was:
#   torch = { version = ">=2.7", source = "pytorch-cu128", optional = true }
torch = { version = ">=2.7,<2.12", source = "pytorch-cpu", optional = true }
```

- The `<2.12` bound is the footgun fix: during #1891 a fresh `poetry lock` tried
  to pull a newly-published torch 2.12.x (CUDA-13) and split it from the pinned
  2.11.0. Bounding prevents silent CUDA-major bumps on re-lock.
- The `ml` and `rerank` extras are **untouched** — torch resolves `+cpu` through
  both. `ml` becomes a functional CPU diffusers fallback.
- The cu128/5090 comment block (≈lines 88–94, 420–429) is rewritten to explain
  the CPU default and that cu128 lives in `scripts/Dockerfile.sdxl`.

### Same re-home in the root dev-harness — `pyproject.toml`

The root (`package-mode = false`, its own `poetry.lock` from Poetry 2.4.1)
carries the identical `torch = { ">=2.7", source = "pytorch-cu128" }` (line 24) +
source block (lines 64–67). Apply the same swap + `<2.12` bound + comment update
for consistency, and regenerate the root lock.

### Fail-fast guard — both Dockerfiles

Strengthen the existing rerank assert (`Dockerfile.worker:142`, `Dockerfile:61`)
to confirm the CPU build, turning a future accidental cu128 re-lock into a red
build (consistent with `feedback_no_silent_defaults`):

```dockerfile
RUN python -c "import sentence_transformers, torch; \
assert torch.version.cuda is None, f'expected CPU torch, got CUDA {torch.version.cuda}'; \
print('[BUILD] rerank OK — CPU torch', torch.__version__)"
```

No `--extras` line changes — the swap is purely pyproject + lock.

### Stale-reference cleanup — `utils/startup_manager.py:900`

Reword the warmup hint away from the non-existent `scripts/requirements-ml.txt`
to the real options: `poetry install --extras ml` (CPU in-process diffusers) or
the `sdxl-server` container (GPU).

### Expected lock diff (cofounder)

Mechanical and verifiable:

1. `torch` `2.11.0+cu128` → `2.11.0+cpu` (source `pytorch-cu128` → `pytorch-cpu`;
   new file hashes).
2. The **18 `nvidia-*`/`cuda-*` package blocks removed** (the CPU wheel has no
   nvidia deps).
3. The torch `[package.source]` reference updated.
4. **No other dependency version drift.** The plan asserts exactly this.

(No `torchvision`/`torchaudio` in the cofounder lock, so nothing else needs the
source treatment.)

### Files touched

1. `src/cofounder_agent/pyproject.toml` — source block, torch pin, comments.
2. `src/cofounder_agent/poetry.lock` — regenerated (cpu torch, 18 nvidia/cuda
   blocks gone, no other drift).
3. `pyproject.toml` (root) — source block, torch pin, comments.
4. `poetry.lock` (root) — regenerated.
5. `src/cofounder_agent/Dockerfile.worker` — strengthen the rerank assert (CPU).
6. `src/cofounder_agent/Dockerfile` — strengthen the rerank assert (CPU).
7. `src/cofounder_agent/utils/startup_manager.py` — fix the stale warmup hint.
8. Dev docs (`README.md`, `docs/operations/local-development-setup.md`,
   `src/cofounder_agent/README.md`) — update any cu128/GPU-torch setup note to
   reflect CPU-default + cu128-in-SDXL-container. (Plan greps; updates only what
   actually references it.)

## Error handling / fail-loud

- **Build-time:** the strengthened `torch.version.cuda is None` assert reddens
  the build if cu128 ever sneaks back into an image.
- **Runtime (already exists):** `rag_engine.py:641` lazy-imports `CrossEncoder`
  and degrades to passthrough with an actionable log on failure; `image_service`
  Strategy-1 HTTP path returns gracefully on transport errors.

## Testing & verification (evidence before "done")

1. **Lock diff:** regenerate both locks; confirm cofounder diff is _only_ the
   torch source/version line + removal of the 18 nvidia/cuda blocks (+ torch
   sub-dep marker churn), no unrelated version drift.
2. **`docker build --check`** on both Dockerfiles (lint the Dockerfile syntax /
   stage graph).
3. **Real build** of `prefect-worker` (`docker compose -f docker-compose.local.yml
build prefect-worker`) **and** the OSS `cofounder` image, including the new
   CPU assert.
4. **In-image proof:** `torch.__version__` ends `+cpu`; `torch.version.cuda is
None`; **`from sentence_transformers import CrossEncoder`** imports and
   instantiates `cross-encoder/ms-marco-MiniLM-L-6-v2` on CPU.
5. **Image size** before/after recorded in the PR (~2 GB delta expected).
6. **Unit suite:** the two torch-touching files (`tests/unit/services/
test_rag_engine.py`, `tests/unit/cli/test_media_cli.py`) green; broader unit
   run green.

## Risks

1. **Poetry 1.8.5 vs lock-version 2.1 (pre-existing).** The OSS `Dockerfile`
   pins `poetry==1.8.5`, but the lock is lock-version 2.1 (Poetry 2.2.1/2.4.1).
   1.8.5 predates 2.1 and may refuse the lock. Not introduced by this change
   (the lock is already 2.1), but step 3's real OSS build will definitively
   expose it. **If it fails, fold in a one-line poetry bump** (matching the
   worker/brain images, which already use 2.x) rather than pre-emptively
   widening scope.
2. **CPU wheel availability.** `torch 2.11.x+cpu` for cp313 on linux + win_amd64
   must exist on the cpu index — high confidence (the index mirrors every
   release); the regenerate-lock step proves it.
3. **Behavior parity.** None expected — the reranker already runs on CPU (#1882)
   and `torch.cuda.is_available()` is already `False` in the worker; the swap
   only removes unused CUDA libs.

## Rollout

- Single PR against `Glad-Labs/glad-labs-stack` (source of truth), per
  `feedback_all_changes_via_pr`. CI green is the merge gate.
- After merge: rebuild the worker image (`docker compose -f
docker-compose.local.yml build prefect-worker`, standing authority per
  `feedback_rebuild_authority`).
- The public `poindexter` mirror inherits the slimmer install automatically.

## Open items (not blockers)

1. **Issue routing** (`feedback_check_issue_routing_first`) — file the tracking
   issue; OSS-product CI/dependency structure → likely `poindexter` (public).
   Confirm at PR time.

## Implementation outcome (2026-06-23)

Implemented and verified the same day. Notes that supersede the design-time
estimates above:

- **Saving is much larger than the "~2 GB" estimate.** Modern torch externalizes
  the CUDA runtime into separate `nvidia-*` wheels (plus `triton`), so the lock
  dropped **19** CUDA-only packages, not "~2 GB of torch." Measured on a real
  `Dockerfile.worker` build (Python 3.13-slim), cu128 vs cpu:
  - site-packages `du`: **7.9 GB → 2.2 GB** (~5.7 GB off the Python payload;
    torch+nvidia+triton 6.4 GB → CPU torch 0.72 GB).
  - full image (`docker image inspect .Size`): **12.7 GB → 2.2 GB**.
  - The strengthened assert printed `[BUILD] rerank extra OK — CPU torch
2.11.0+cpu`; in-image: `torch 2.11.0+cpu | cuda None | is_available False |
CrossEncoder import OK`. The poetry-install layer fell from 199 s (cu128
    download) to 58 s.
- **Lock diff was exactly the predicted shape** in both `src/cofounder_agent/`
  and the root: torch `2.11.0+cu128 → 2.11.0+cpu`, the 18 `nvidia-*`/`cuda-*`
  blocks + `triton` removed, zero other version drift. `poetry check --lock`: all
  set.
- **OSS standalone image build deferred — pre-existing, unrelated breakage.**
  The `cofounder` image (`src/cofounder_agent/Dockerfile`) does not build for
  three reasons that predate and are independent of this change: (1)
  `poetry==1.8.5` cannot read the PEP-621 `[project]` table; (2) it installs the
  root package (no `--no-root`) but `.dockerignore` excludes the `README.md`
  poetry needs for metadata; (3) `packages = [{ from = ".." }]` reaches outside
  the `src/cofounder_agent` build context. The CPU-torch change still applies to
  that image automatically via the lock, and its `Dockerfile` carries the same
  CPU assert. The build fix is its own task (spawned 2026-06-23) — Risk #1 above
  turned out to be the tip of a larger pre-existing problem, so it was
  **not** folded into this dependency PR.
- **Unit suite** (`test_rag_engine.py`, `test_media_cli.py`) deferred to CI (the
  review gate per `feedback_ci_is_the_review_gate`): this host is Python 3.11
  (lock resolves for 3.13), and the worker image already proves the reranker
  imports on CPU torch. CI runs both files on the lean (torch-free) install.
