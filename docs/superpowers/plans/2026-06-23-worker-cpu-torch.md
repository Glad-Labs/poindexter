# CPU-only torch for cofounder pipeline images — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cu128 (CUDA) torch pin in the cofounder lock with CPU torch so the worker + OSS standalone images shed ~2 GB each; cu128 stays only in the self-contained `sdxl-server` container.

**Architecture:** Re-home torch's Poetry source from `pytorch-cu128` to a new `pytorch-cpu` explicit source and bound it `<2.12`, in both `src/cofounder_agent/pyproject.toml` and the root dev-harness `pyproject.toml`; regenerate both locks. Strengthen the existing Dockerfile rerank asserts to fail the build if torch is ever CUDA again. No `--extras` line changes — the swap is purely pyproject + lock.

**Tech Stack:** Poetry 2.x (sources, extras, lock), Docker/BuildKit, pytest, PyTorch CPU wheels.

## Global Constraints

- **No version drift.** The cofounder lock diff must be ONLY: `torch 2.11.0+cu128 → 2.11.0+cpu` (source swap) + removal of the 18 `nvidia-*`/`cuda-*` package blocks + torch's own `[package.source]`/marker churn. Any other package version change is a red flag — investigate before committing.
- **cu128 stays in `scripts/Dockerfile.sdxl` only.** Do not touch that file; it has its own CUDA base image and torch.
- **torch pin is exactly** `{ version = ">=2.7,<2.12", source = "pytorch-cpu", optional = true }` in both pyprojects.
- **New source is exactly** `name = "pytorch-cpu"`, `url = "https://download.pytorch.org/whl/cpu"`, `priority = "explicit"`.
- **All changes via PR** against `Glad-Labs/glad-labs-stack`; CI green is the gate (`feedback_all_changes_via_pr`).
- **Fail loud, no silent defaults** (`feedback_no_silent_defaults`) — the build-time CPU assert is mandatory.
- Build contexts: both images build from `./src/cofounder_agent`. Worker service in `docker-compose.local.yml` = `prefect-worker` (also `worker`/`pipeline-bot`); OSS service in `docker-compose.yml` = `cofounder`.

---

### Task 1: Cofounder pyproject → CPU torch + regenerated lock

**Files:**

- Modify: `src/cofounder_agent/pyproject.toml` (torch pin ~line 94; source block ~lines 420-429)
- Regenerate: `src/cofounder_agent/poetry.lock`

**Interfaces:**

- Produces: a cofounder lock pinning `torch 2.11.0+cpu` with no `nvidia-*`/`cuda-*` blocks. Task 6's image build + CPU assert depend on this.

- [ ] **Step 1: Edit the torch pin + its comment**

In `src/cofounder_agent/pyproject.toml`, replace the SDXL torch comment block + pin (currently lines ~88-94):

```toml
# ML/AI - Image Generation (SDXL)
# torch is the CPU build here: the two pipeline images (worker + OSS
# standalone) install it only via the `rerank` extra to drive the
# LlamaIndex cross-encoder reranker, which runs on CPU (#1882). Neither
# image has GPU passthrough or `diffusers`, so the CUDA build was ~2 GB
# of dead weight (18 transitive nvidia-*/cuda-* wheels). The cu128 build
# that actually renders SDXL on Matt's RTX 5090 lives self-contained in
# scripts/Dockerfile.sdxl (its own pytorch/pytorch:*-cuda12.8 base), not
# in this lock. <2.12 bounds the pin so a re-lock can't silently jump to
# a new CUDA-major torch (the 2.12/CUDA-13 split that bit #1891). See
# docs/superpowers/specs/2026-06-23-worker-cpu-torch-design.md.
torch = { version = ">=2.7,<2.12", source = "pytorch-cpu", optional = true }
```

- [ ] **Step 2: Edit the source block**

Replace the `[[tool.poetry.source]]` block (currently lines ~420-429):

```toml
# ===== POETRY SOURCES =====
# Explicit-priority source for the CPU-only torch wheels. The pipeline
# images install torch (via the `rerank` extra) purely for the CPU
# cross-encoder reranker, so the CPU build is correct and ~2 GB smaller
# than cu128. priority="explicit" means poetry only consults this index
# for packages that pin source = "pytorch-cpu"; everything else still
# resolves from PyPI. The CUDA (cu128) torch that drives SDXL lives in
# scripts/Dockerfile.sdxl, not here. See glad-labs-stack#334 for the
# original cu128 pin and the 2026-06-23 CPU-trim follow-up spec.
[[tool.poetry.source]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
priority = "explicit"
```

- [ ] **Step 3: Regenerate the lock**

Run: `cd src/cofounder_agent && poetry lock`
Expected: success, "Writing lock file". (Poetry 2.x preserves existing pins by default — minimal diff. Do NOT use `--regenerate`; it re-resolves everything and bloats the diff. If poetry errors that the lock is stale vs the source change, re-run `poetry lock` once more — the first pass updates source metadata.)

If poetry is unavailable/flaky in this shell, see `reference_borrowed_venv_when_poetry_broken`: borrow a complete `poindexter-backend-*` venv's poetry. The lock regen needs network to `download.pytorch.org`.

- [ ] **Step 4: Verify the lock diff is clean (the key gate)**

Run: `cd src/cofounder_agent && git --no-pager diff poetry.lock | grep -E '^\+name = |^-name = '`
Expected: shows ONLY removals of the 18 `nvidia-*`/`cuda-*` packages (`cuda-bindings`, `cuda-pathfinder`, `cuda-toolkit`, `nvidia-cublas-cu12`, `nvidia-cuda-cupti-cu12`, `nvidia-cuda-nvrtc-cu12`, `nvidia-cuda-runtime-cu12`, `nvidia-cudnn-cu12`, `nvidia-cufft-cu12`, `nvidia-cufile-cu12`, `nvidia-curand-cu12`, `nvidia-cusolver-cu12`, `nvidia-cusparse-cu12`, `nvidia-cusparselt-cu12`, `nvidia-nccl-cu12`, `nvidia-nvjitlink-cu12`, `nvidia-nvshmem-cu12`, `nvidia-nvtx-cu12`). NO `+name =` additions.

Run: `cd src/cofounder_agent && grep -n 'version = "2.11' poetry.lock | head -1 && grep -n 'download.pytorch.org/whl/cpu\|download.pytorch.org/whl/cu128' poetry.lock`
Expected: torch version line shows `2.11.0+cpu`; the source URL is now `.../whl/cpu`; NO `.../whl/cu128` remains.

If any unrelated package version changed (a `+version`/`-version` pair on a non-torch package), STOP and investigate — that violates the no-drift constraint.

- [ ] **Step 5: Prove the install + CPU reranker locally**

Run: `cd src/cofounder_agent && poetry install --no-root --extras rerank`
Expected: success.

Run: `cd src/cofounder_agent && poetry run python -c "import torch; from sentence_transformers import CrossEncoder; print('torch', torch.__version__, 'cuda', torch.version.cuda); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2'); print('reranker OK on CPU')"`
Expected: prints `torch 2.11.0+cpu cuda None` then `reranker OK on CPU` (model download may take a moment on first run). `torch.version.cuda` MUST be `None`.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/pyproject.toml src/cofounder_agent/poetry.lock
git commit -m "build(deps): CPU torch for the cofounder lock (drop cu128)

Re-home torch from pytorch-cu128 to a new pytorch-cpu explicit source and
bound it <2.12. The worker + OSS images install torch only via the rerank
extra for the CPU cross-encoder (#1882) — no GPU passthrough, no diffusers
— so the cu128 build (18 nvidia/cuda transitive wheels, ~2 GB) was dead
weight. cu128 stays in scripts/Dockerfile.sdxl. Spec:
docs/superpowers/specs/2026-06-23-worker-cpu-torch-design.md

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Root dev-harness pyproject → CPU torch + regenerated lock

**Files:**

- Modify: `pyproject.toml` (root; torch pin line ~24; source block lines ~64-67)
- Regenerate: `poetry.lock` (root)

**Interfaces:**

- Consumes: nothing from Task 1 (separate install context).
- Produces: a consistent root lock with CPU torch. No downstream task depends on it; this is for cross-file consistency + the same footgun fix.

- [ ] **Step 1: Edit the torch pin + comment**

In the root `pyproject.toml`, replace the torch comment + pin (currently lines ~18-24):

```toml
# Optional ML/AI dependencies. torch is the CPU build (sourced from the
# explicit pytorch-cpu index defined at the bottom of this file). The
# CUDA (cu128) torch that drives SDXL on the 5090 lives self-contained
# in scripts/Dockerfile.sdxl, not in this dev-harness lock — see
# Glad-Labs/glad-labs-stack#334 and the CPU-trim follow-up spec
# docs/superpowers/specs/2026-06-23-worker-cpu-torch-design.md. <2.12
# bounds the pin against a silent CUDA-major bump on re-lock.
torch = { version = ">=2.7,<2.12", source = "pytorch-cpu", optional = true }
```

- [ ] **Step 2: Edit the source block**

Replace the `[[tool.poetry.source]]` block (currently lines ~59-67):

```toml
# Explicit-priority source for the CPU-only torch wheels.
# priority="explicit" means poetry only consults this index for
# packages that pin source = "pytorch-cpu"; everything else still
# resolves from PyPI. The CUDA (cu128) torch that drives SDXL on the
# 5090 lives in scripts/Dockerfile.sdxl, not here. See
# Glad-Labs/glad-labs-stack#334 for the original cu128 pin.
[[tool.poetry.source]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
priority = "explicit"
```

- [ ] **Step 3: Regenerate the root lock**

Run: `poetry lock` (from repo root)
Expected: success. Same minimal-diff guidance as Task 1 Step 3.

- [ ] **Step 4: Verify the root lock diff**

Run: `git --no-pager diff poetry.lock | grep -E '^\+name = |^-name = '`
Expected: only `nvidia-*`/`cuda-*` removals (if the root lock carried them), no unrelated additions. The torch entry resolves `+cpu`.

Run: `grep -n 'download.pytorch.org/whl/cu128' poetry.lock || echo "no cu128 remaining (good)"`
Expected: `no cu128 remaining (good)`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml poetry.lock
git commit -m "build(deps): CPU torch for the root dev-harness lock

Mirror the cofounder CPU-torch re-home (Task 1) in the root pyproject so
both install contexts agree: pytorch-cpu source, torch>=2.7,<2.12. cu128
stays in scripts/Dockerfile.sdxl.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Strengthen the Dockerfile rerank asserts to require CPU torch

**Files:**

- Modify: `src/cofounder_agent/Dockerfile.worker` (assert ~line 142)
- Modify: `src/cofounder_agent/Dockerfile` (assert ~line 61)

**Interfaces:**

- Consumes: the CPU lock from Task 1 (the assert only passes against CPU torch).
- Produces: a build-time guard that reddens the build if torch is ever CUDA again. Task 6's real build exercises it.

- [ ] **Step 1: Edit the worker assert**

In `src/cofounder_agent/Dockerfile.worker`, replace the rerank fail-fast comment + RUN (currently lines ~137-142):

```dockerfile
# Fail-fast verification — confirm the rerank stack landed AND that torch is the
# CPU build. sentence-transformers is an opt-in `rerank` extra (it was a main dep
# until the 2026-06-23 CI torch-trim); if `[rerank]` is ever dropped from the
# --extras line above, the LlamaIndex cross-encoder reranker (services/rag_engine.py)
# would ImportError at first use and silently degrade to passthrough. The
# torch.version.cuda assertion guards the 2026-06-23 CPU-trim: this image has no
# GPU passthrough and no diffusers, so it must carry the ~2 GB-smaller CPU wheel,
# not cu128. A future re-lock that re-pulls a CUDA torch reddens the build here
# instead of silently re-bloating the image. cu128 lives in scripts/Dockerfile.sdxl.
RUN python -c "import sentence_transformers, torch; assert torch.version.cuda is None, f'expected CPU torch, got CUDA {torch.version.cuda}'; print('[BUILD] rerank extra OK — sentence_transformers + CPU torch', torch.__version__)"
```

- [ ] **Step 2: Edit the OSS assert**

In `src/cofounder_agent/Dockerfile`, replace the rerank fail-fast comment + RUN (currently lines ~57-61):

```dockerfile
# Fail-fast verification — confirm the rerank stack landed AND that torch is the
# CPU build (mirror of the worker image). sentence-transformers is an opt-in
# `rerank` extra as of the 2026-06-23 CI torch-trim; a dropped `[rerank]` extra
# silently degrades the cross-encoder reranker to passthrough at runtime. The
# torch.version.cuda assertion guards the CPU-trim — this image carries the
# ~2 GB-smaller CPU wheel (no GPU here), and a re-lock that re-pulls CUDA torch
# reddens the build instead of silently re-bloating it. cu128 lives in
# scripts/Dockerfile.sdxl.
RUN python -c "import sentence_transformers, torch; assert torch.version.cuda is None, f'expected CPU torch, got CUDA {torch.version.cuda}'; print('[BUILD] rerank extra OK — sentence_transformers + CPU torch', torch.__version__)"
```

- [ ] **Step 3: Lint both Dockerfiles**

Run: `DOCKER_BUILDKIT=1 docker build --check -f src/cofounder_agent/Dockerfile.worker src/cofounder_agent`
Expected: `Check complete, no warnings found.` (or only pre-existing warnings unrelated to the assert line).

Run: `DOCKER_BUILDKIT=1 docker build --check -f src/cofounder_agent/Dockerfile src/cofounder_agent`
Expected: same.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/Dockerfile.worker src/cofounder_agent/Dockerfile
git commit -m "build(docker): assert CPU torch in the rerank fail-fast guard

Strengthen the existing import assert in both pipeline images to require
torch.version.cuda is None, so a future re-lock that re-pulls a CUDA torch
reddens the build instead of silently re-bloating the image by ~2 GB.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Fix the stale `requirements-ml.txt` warmup hint

**Files:**

- Modify: `src/cofounder_agent/utils/startup_manager.py` (~line 900)

**Interfaces:**

- Produces: an accurate operator hint (the referenced file does not exist).

- [ ] **Step 1: Edit the hint**

In `src/cofounder_agent/utils/startup_manager.py`, replace the `ModuleNotFoundError` branch (currently ~lines 898-901):

```python
        except ModuleNotFoundError:
            logger.info("  SDXL warmup: torch not installed - SDXL disabled")
            logger.info(
                "     In-process SDXL needs the `ml` extra (poetry install "
                "--extras ml); GPU rendering runs in the sdxl-server container."
            )
            return
```

- [ ] **Step 2: Verify no stale reference remains**

Run: `grep -rn "requirements-ml.txt" src/cofounder_agent/ || echo "no stale ref (good)"`
Expected: `no stale ref (good)`.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/utils/startup_manager.py
git commit -m "fix(startup): correct the SDXL warmup hint

The warmup pointed operators at scripts/requirements-ml.txt, which does
not exist. Point at the real options: poetry install --extras ml (CPU
in-process diffusers) or the sdxl-server container (GPU).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Correct the README rerank-torch note (CPU, not CUDA)

**Files:**

- Modify: `src/cofounder_agent/README.md` (~lines 28-31)

**Interfaces:**

- Produces: accurate dev-setup copy.

- [ ] **Step 1: Edit the note**

In `src/cofounder_agent/README.md`, replace the rerank blockquote (currently lines ~28-31):

```markdown
> The cross-encoder reranker (`sentence-transformers` + `torch`, a CPU wheel) is
> an opt-in `rerank` extra. With the lean `poetry install` above the reranker
> degrades to passthrough; to run it locally use `poetry install --extras rerank`
> (or `pip install -e "src/cofounder_agent[rerank]"`). The reranker runs on CPU
> (#1882); the CUDA torch that drives SDXL lives in the sdxl-server container.
```

- [ ] **Step 2: Verify no other doc claims a CUDA rerank wheel**

Run: `grep -rn "multi-GB CUDA" docs/ src/cofounder_agent/README.md README.md || echo "none (good)"`
Expected: `none (good)`.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/README.md
git commit -m "docs: rerank torch is a CPU wheel, not multi-GB CUDA

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Integration verification — real image builds + size delta

**Files:** none (verification only; produces PR evidence).

**Interfaces:**

- Consumes: Tasks 1 (CPU lock) + 3 (CPU assert).

- [ ] **Step 1: Record the pre-change worker image size (baseline)**

Run: `docker images poindexter-prefect-worker --format '{{.Size}}' || docker images | grep -i prefect-worker`
Expected: note the current size (the cu128 image, for the before/after delta). If no local image exists, skip — the after-size still demonstrates the win against the documented ~2.5-3 GB cu128 baseline.

- [ ] **Step 2: Real build of the worker image (exercises the CPU assert)**

Run: `docker compose -f docker-compose.local.yml build prefect-worker`
Expected: build succeeds; the rerank RUN prints `[BUILD] rerank extra OK — sentence_transformers + CPU torch 2.11.0+cpu`. If it fails on the assert, the lock is still CUDA — return to Task 1.

- [ ] **Step 3: Real build of the OSS standalone image**

Run: `docker compose -f docker-compose.yml build cofounder`
Expected: build succeeds with the same `[BUILD] ... CPU torch` line.
NOTE: this image pins `poetry==1.8.5` (`Dockerfile:41`), but the lock is lock-version 2.1. If the build fails at `poetry install` with a lock-incompatibility error, bump that pin to a 2.x (e.g. `poetry==2.2.1`, matching `brain/Dockerfile`) in a dedicated commit:

```bash
git add src/cofounder_agent/Dockerfile
git commit -m "build(docker): bump OSS image poetry to 2.x for lock-version 2.1

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: In-image proof of CPU torch + working reranker**

Run: `docker compose -f docker-compose.local.yml run --rm --no-deps --entrypoint python prefect-worker -c "import torch; from sentence_transformers import CrossEncoder; print(torch.__version__, torch.version.cuda, torch.cuda.is_available()); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2'); print('reranker OK')"`
Expected: `2.11.0+cpu None False` then `reranker OK`.

- [ ] **Step 5: Record the after size + delta**

Run: `docker images poindexter-prefect-worker --format '{{.Size}}'`
Expected: ~2 GB smaller than Step 1's baseline (or clearly under the ~2.5-3 GB cu128 figure). Capture before/after for the PR description.

- [ ] **Step 6: Run the torch-touching unit tests**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_rag_engine.py tests/unit/cli/test_media_cli.py -q`
Expected: all pass (these tolerate torch absence and exercise the real path under `--extras rerank`). If poetry env is flaky, use the borrowed-venv approach (`reference_borrowed_venv_when_poetry_broken`).

(No commit — verification only.)

---

### Task 7: File the tracking issue + open the PR

**Files:** none (process).

- [ ] **Step 1: File the tracking issue**

Per `feedback_check_issue_routing_first`: this is OSS-product CI/dependency structure → file on `Glad-Labs/poindexter` (public). Title e.g. "Worker/OSS images carry cu128 torch despite no GPU — switch to CPU torch (~2 GB)". Body links the spec.

- [ ] **Step 2: Push the branch + open the PR against glad-labs-stack**

```bash
git push -u origin claude/fervent-mirzakhani-f7937f
```

Open a PR against `Glad-Labs/glad-labs-stack` `main`. PR body: the problem (cu128 dead weight), the decision (CPU re-home, cu128 stays in SDXL container), the before/after image size from Task 6, the clean lock diff, and the verification evidence. Reference the spec + the tracking issue. CI green is the merge gate.

- [ ] **Step 3: Verify CI is green, then merge** (`feedback_ci_is_the_review_gate`, `feedback_manage_prs_yourself`).

---

## Self-Review notes

- **Spec coverage:** pyproject+lock (cofounder=Task 1, root=Task 2); `<2.12` bound (Tasks 1+2); CPU fail-fast assert (Task 3); stale `requirements-ml.txt` hint (Task 4); README CUDA→CPU (Task 5); `docker build --check` + real builds + reranker import + size delta (Tasks 3+6); poetry 1.8.5/lock-2.1 risk (Task 6 Step 3); rollout/issue/PR (Task 7). All spec sections mapped.
- **No-drift gate** is the load-bearing check (Task 1 Step 4) — it's where a bad re-lock gets caught.
- **Type/name consistency:** the source name `pytorch-cpu`, URL `.../whl/cpu`, and pin `>=2.7,<2.12` are identical across Tasks 1, 2, and the Global Constraints.
