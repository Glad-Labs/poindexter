# CI rerank-extra (keep cu128 torch out of CI) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `sentence-transformers` an opt-in `rerank` extra so CI's default `poetry install` stops pulling the ~2.5 GB cu128 CUDA torch wheel on GPU-less runners, while both pipeline deployment images keep it.

**Architecture:** `sentence-transformers` is the only main-path puller of `torch` (which is pinned to the CUDA-only `pytorch-cu128` source). Mark it `optional = true` and reference it from a new `rerank` extra. The two images that run the pipeline (operator worker + OSS standalone) add `--extras rerank`; CI, host-dev opt-in via bootstrap, everything else omits it. Lazy/guarded torch imports in production code mean CI test collection is unaffected.

**Tech Stack:** Poetry 2.2.1, Python 3.13, Docker, GitHub Actions, pytest.

**Spec:** [`docs/superpowers/specs/2026-06-23-ci-rerank-extra-design.md`](../specs/2026-06-23-ci-rerank-extra-design.md)

## Global Constraints

- **Do NOT change** the `torch` declaration or its `source = "pytorch-cu128"` pin — it is load-bearing for prod image-gen on the RTX 5090. Only its _reachability_ changes (optional, no longer pulled by a main dep).
- **Both pipeline images stay byte-identical** in contents: operator worker (`Dockerfile.worker`) and OSS standalone (`Dockerfile`) must still install `sentence-transformers` + cu128 `torch` via `--extras rerank`.
- **No version drift in `poetry.lock`** — only the optional/extras metadata for `sentence-transformers`/`torch` may change; no transitive version bumps.
- **New extra name:** `rerank` (lowercase). Members: `["sentence-transformers", "torch"]`.
- **Fail loud:** each pipeline Dockerfile gets a build-time `import sentence_transformers` assert (consistent with the repo's existing pyroscope/youtube build asserts and `feedback_no_silent_defaults`).
- **All work on the branch → one PR** against `Glad-Labs/glad-labs-stack`; CI green is the merge gate (`feedback_all_changes_via_pr`, `feedback_ci_is_the_review_gate`).
- **Worktree poetry note:** if `poetry` is flaky in this worktree, per `reference_borrowed_venv_when_poetry_broken` fall back to a complete `poindexter-backend-*` venv + `PYTHONPATH=src/cofounder_agent` for pytest. `poetry lock` itself only needs the `poetry` binary + network.
- **Out of scope:** the worker → CPU-torch image slim (separate follow-up spec); the root-level dev-harness `pyproject.toml` (it declares no `sentence-transformers`, so its default install is already torch-free); pytest-testmon.

---

## File Structure

| File                                                                                       | Responsibility                                               | Task |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------ | ---- |
| `src/cofounder_agent/pyproject.toml`                                                       | Declare `sentence-transformers` optional; add `rerank` extra | 1    |
| `src/cofounder_agent/poetry.lock`                                                          | Regenerated lock reflecting the extra                        | 1    |
| `src/cofounder_agent/Dockerfile.worker`                                                    | Operator worker image — add `rerank` extra + assert          | 2    |
| `src/cofounder_agent/Dockerfile`                                                           | OSS standalone image — add `rerank` extra + assert           | 2    |
| `.github/workflows/unit-tests.yml`                                                         | Remove 2 torch disk hacks + stale comments                   | 3    |
| `.github/workflows/integration-db.yml`                                                     | Remove torch disk hack + stale comment                       | 3    |
| `.github/workflows/benchmarks.yml`                                                         | Remove disk hack                                             | 3    |
| `scripts/bootstrap.sh`                                                                     | Host-dev parity — add `rerank` extra                         | 4    |
| `README.md`, `docs/operations/local-development-setup.md`, `src/cofounder_agent/README.md` | Document the `--extras rerank` opt-in                        | 4    |

---

## Task 1: Make `sentence-transformers` optional + add the `rerank` extra

**Files:**

- Modify: `src/cofounder_agent/pyproject.toml` (line 223 dependency; the `[project.optional-dependencies]` block ~lines 259–265)
- Modify: `src/cofounder_agent/poetry.lock` (regenerated)
- Verify against: `src/cofounder_agent/tests/unit/services/test_rag_engine.py`, `src/cofounder_agent/tests/unit/cli/test_media_cli.py`

**Interfaces:**

- Produces: a poetry extra named `rerank` whose members are `sentence-transformers` + `torch`. Tasks 2 and 4 consume this exact name in `--extras` strings.

- [ ] **Step 1: Establish the BEFORE baseline — confirm the default install pulls torch**

Run (from repo root):

```bash
cd src/cofounder_agent && poetry install --no-root --no-interaction && python -c "import torch, sys; print('torch present:', torch.__file__)"
```

Expected: torch imports successfully (this is the state we're eliminating). Note the wheel path / size. If the worktree env can't run a full install, instead inspect the lock to confirm torch is in the default (non-optional) set:

```bash
cd src/cofounder_agent && grep -n "name = \"torch\"" poetry.lock
```

Expected: a `[[package]]` block for `torch` with no `optional = true` constraint gating it out of the default install.

- [ ] **Step 2: Mark `sentence-transformers` optional**

In `src/cofounder_agent/pyproject.toml`, change the dependency declaration (currently line 223):

```toml
# was:
sentence-transformers = "^3.4"
# to:
sentence-transformers = { version = "^3.4", optional = true }
```

Leave the explanatory comment block above it intact, but append one sentence:

```toml
# Made optional (poindexter rerank extra, 2026-06-23): pulled only by the
# `rerank` extra so CI's default install skips it (and its transitive cu128
# torch). The worker + OSS standalone images opt in via --extras rerank.
```

- [ ] **Step 3: Add the `rerank` extra**

In the `[project.optional-dependencies]` block, add the `rerank` line directly under `ml` (keep the others unchanged):

```toml
[project.optional-dependencies]
ml = ["torch", "transformers", "diffusers"]
# rerank — the LlamaIndex cross-encoder reranker stack. sentence-transformers
# pulls torch transitively; torch is listed explicitly to document intent.
# The two pipeline images (worker + OSS standalone) install this; CI does not.
rerank = ["sentence-transformers", "torch"]
profiling = ["pyroscope-io"]
```

- [ ] **Step 4: Regenerate the lock**

Run:

```bash
cd src/cofounder_agent && poetry lock --no-interaction
```

If `--no-update` is supported by this Poetry (2.2.1) and the run bumps unrelated versions, re-run with it to constrain churn:

```bash
cd src/cofounder_agent && poetry lock --no-update --no-interaction
```

- [ ] **Step 5: Verify the lock diff has no version drift**

Run:

```bash
git -C ../.. diff --stat src/cofounder_agent/poetry.lock
git -C ../.. diff src/cofounder_agent/poetry.lock | grep -E "^\+.*version = " | head -40
```

Expected: changes confined to `sentence-transformers` / `torch` `optional`/`extras`/`markers` metadata and the `[extras]` table gaining a `rerank` entry. **No** `version = "..."` line should change for any unrelated package. If an unrelated version moved, revert the lock and re-run Step 4 with `--no-update`.

- [ ] **Step 6: Verify the lean install no longer pulls torch**

Run in a throwaway environment (do NOT pollute the dev venv):

```bash
python -m venv /tmp/lean-check && /tmp/lean-check/bin/pip install -q poetry==2.2.1
cd src/cofounder_agent && VIRTUAL_ENV=/tmp/lean-check poetry install --no-root --no-interaction
/tmp/lean-check/bin/python -c "import importlib.util as u; print('torch importable:', u.find_spec('torch') is not None); print('sentence_transformers importable:', u.find_spec('sentence_transformers') is not None)"
```

Expected: `torch importable: False` and `sentence_transformers importable: False`.

(On Windows, substitute `/tmp/lean-check/Scripts/python.exe` and `Scripts\pip.exe`. If venv-in-tmp is impractical in the worktree, the lock-diff check in Step 5 plus the CI run in Task 5 are the authoritative proofs — note that in the commit.)

- [ ] **Step 7: Verify the affected unit tests still pass with the lib absent**

Run the two test files that reference torch/sentence-transformers, against the lean env:

```bash
cd src/cofounder_agent && VIRTUAL_ENV=/tmp/lean-check poetry run pytest tests/unit/services/test_rag_engine.py tests/unit/cli/test_media_cli.py -q --tb=short -p no:cacheprovider
```

Expected: PASS (with some tests skipped via `importorskip`). If either file ERRORS at collection because of an unguarded import, add a `pytest.importorskip("torch")` / `importorskip("sentence_transformers")` at the top of the offending module and re-run. (Per the spec's grep, none should need it — `test_rag_engine.py` already guards at line 19 — but verify, don't assume.)

- [ ] **Step 8: Verify the `rerank` extra DOES pull both libs**

Run in a second throwaway env:

```bash
python -m venv /tmp/rerank-check && /tmp/rerank-check/bin/pip install -q poetry==2.2.1
cd src/cofounder_agent && VIRTUAL_ENV=/tmp/rerank-check poetry install --no-root --no-interaction --extras rerank
/tmp/rerank-check/bin/python -c "import torch, sentence_transformers; print('rerank extra OK:', torch.__version__, sentence_transformers.__version__)"
```

Expected: both import; prints the torch + sentence-transformers versions.

- [ ] **Step 9: Commit**

```bash
git add src/cofounder_agent/pyproject.toml src/cofounder_agent/poetry.lock
git commit -m "$(cat <<'EOF'
build(deps): make sentence-transformers an opt-in rerank extra

torch is pinned to the cu128 CUDA source and was pulled into every
install transitively via the sentence-transformers main dep. Mark
sentence-transformers optional and add a `rerank` extra so CI's default
poetry install skips torch entirely on GPU-less runners. Pipeline images
opt back in via --extras rerank (Task 2).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `rerank` to both pipeline Dockerfiles + fail-fast asserts

**Files:**

- Modify: `src/cofounder_agent/Dockerfile.worker:115` (+ assert after line 135)
- Modify: `src/cofounder_agent/Dockerfile:55` (+ assert after line 55)

**Interfaces:**

- Consumes: the `rerank` extra from Task 1.

- [ ] **Step 1: Worker image — add the extra**

In `src/cofounder_agent/Dockerfile.worker`, change the install line (currently line 115):

```dockerfile
# was:
    poetry install --no-root --only main --no-interaction --extras "profiling youtube"
# to:
    poetry install --no-root --only main --no-interaction --extras "profiling youtube rerank"
```

- [ ] **Step 2: Worker image — add the fail-fast assert**

In `src/cofounder_agent/Dockerfile.worker`, after the existing youtube assert (the `RUN python -c "import googleapiclient..."` block ending ~line 135), add:

```dockerfile
# Fail-fast verification — confirm the rerank stack landed. If `[rerank]`
# was silently dropped, the LlamaIndex cross-encoder reranker
# (services/rag_engine.py) would ImportError at first use and degrade to
# passthrough. Assert at build time instead.
RUN python -c "import sentence_transformers, torch; print('[BUILD] rerank extra OK — sentence_transformers + torch available')"
```

- [ ] **Step 3: OSS standalone image — add the extra**

In `src/cofounder_agent/Dockerfile`, change the install line (currently line 55):

```dockerfile
# was:
    poetry install --no-interaction --no-ansi --only main
# to:
    poetry install --no-interaction --no-ansi --only main --extras "rerank"
```

- [ ] **Step 4: OSS standalone image — add the fail-fast assert**

In `src/cofounder_agent/Dockerfile`, immediately after the `poetry install` `RUN` block (after line 55, before the Playwright chromium step), add:

```dockerfile
# Fail-fast verification — confirm the rerank stack landed (mirror of the
# worker image). A dropped `[rerank]` extra silently degrades reranking to
# passthrough at runtime; fail the build instead.
RUN python -c "import sentence_transformers, torch; print('[BUILD] rerank extra OK — sentence_transformers + torch available')"
```

- [ ] **Step 5: Verify the worker image builds (install layer + assert)**

Run:

```bash
docker compose -f docker-compose.local.yml build prefect-worker
```

Expected: build succeeds; the log shows `[BUILD] rerank extra OK — sentence_transformers + torch available`. (Heavy — pulls cu128 torch. If a full build is impractical at execution time, at minimum lint the Dockerfile syntax with `docker build --check -f src/cofounder_agent/Dockerfile.worker src/cofounder_agent` and rely on the post-merge worker rebuild; note which was done in the commit.)

- [ ] **Step 6: Verify the OSS standalone image builds**

Run:

```bash
docker compose -f docker-compose.yml build cofounder
```

Expected: build succeeds; the log shows `[BUILD] rerank extra OK — ...`.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/Dockerfile.worker src/cofounder_agent/Dockerfile
git commit -m "$(cat <<'EOF'
build(docker): install rerank extra in both pipeline images

The worker and OSS-standalone images run the cross-encoder reranker, so
they opt back into sentence-transformers + cu128 torch via --extras rerank
(now optional after the previous commit). Build-time import asserts fail
the build loudly if the extra is ever dropped.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Make the three CI workflows lean (remove torch disk hacks)

**Files:**

- Modify: `.github/workflows/unit-tests.yml` (the two `Free … disk` steps + stale comments)
- Modify: `.github/workflows/integration-db.yml` (the `Free disk space for the full poetry env` step + comment)
- Modify: `.github/workflows/benchmarks.yml` (the `Free disk space` step)

**Interfaces:**

- Consumes: the lean default install from Task 1 (these deletions are only safe because torch is no longer installed).

- [ ] **Step 1: unit-tests.yml — delete the first disk-reclaim step**

In `.github/workflows/unit-tests.yml`, delete the entire `- name: Free runner disk space` step (the `if: ${{ !vars.CI_RUNNER }}` block with the `sudo rm -rf /usr/local/lib/android …` run, ~lines 128–138, including its preceding comment block ~lines 121–127 that justifies it by the forked services suite filling disk).

- [ ] **Step 2: unit-tests.yml — delete the second disk-reclaim step**

Delete the `- name: Free up runner disk` step (the `if: ${{ steps.changes.outputs.needs_tests == 'true' && !vars.CI_RUNNER }}` block, ~lines 263–269) and its preceding justification comment (~lines 255–262 referencing "sentence-transformers -> torch (multi-GB wheels)").

- [ ] **Step 3: unit-tests.yml — verify no other text still claims torch is installed**

Run:

```bash
grep -nE "sentence-transformers|torch|No space left|rm -rf /usr/local/lib/android" .github/workflows/unit-tests.yml
```

Expected: no remaining references to the torch disk pressure. If a leftover comment mentions it, update it to reflect that CI now installs the lean (torch-free) env.

- [ ] **Step 4: integration-db.yml — delete its disk-reclaim step**

In `.github/workflows/integration-db.yml`, delete the `- name: Free disk space for the full poetry env` step (the `sudo rm -rf …` run ~lines 100–103) and trim its comment (~lines 93–99) that cites "sentence-transformers pulls torch (multi-GB wheels)". Run:

```bash
grep -nE "sentence-transformers|multi-GB|No space left|android" .github/workflows/integration-db.yml
```

Expected: no remaining torch-disk references.

- [ ] **Step 5: benchmarks.yml — delete its disk-reclaim step**

In `.github/workflows/benchmarks.yml`, delete the `- name: Free disk space` step (lines 74–80, the `sudo rm -rf /usr/local/lib/android /opt/ghc …` block). Run:

```bash
grep -nE "Free disk|android|ghc|dotnet" .github/workflows/benchmarks.yml
```

Expected: no matches.

- [ ] **Step 6: Validate the three workflows are still valid YAML**

Run:

```bash
python -c "import yaml,sys; [yaml.safe_load(open(f)) for f in ['.github/workflows/unit-tests.yml','.github/workflows/integration-db.yml','.github/workflows/benchmarks.yml']]; print('all three parse OK')"
```

Expected: `all three parse OK`. (Deeper proof — the jobs actually passing on the lean install — comes from this PR's own CI in Task 5.)

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/unit-tests.yml .github/workflows/integration-db.yml .github/workflows/benchmarks.yml
git commit -m "$(cat <<'EOF'
ci: drop torch disk-reclaim hacks now that CI installs lean

The `rm -rf android/.NET/GHC` steps in unit-tests, integration-db, and
benchmarks existed solely to make room for the ~2.5GB cu128 torch wheel.
With sentence-transformers now an opt-in extra, the default poetry install
is torch-free and the disk pressure is gone.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Host-dev parity (bootstrap.sh) + docs

**Files:**

- Modify: `scripts/bootstrap.sh:134` and `:137`
- Modify: `README.md`, `docs/operations/local-development-setup.md`, `src/cofounder_agent/README.md`

**Interfaces:**

- Consumes: the `rerank` extra from Task 1.

- [ ] **Step 1: bootstrap.sh — add the extra to both install branches**

In `scripts/bootstrap.sh`, change both `poetry install` calls (currently lines 134 and 137) so the host dev environment keeps reranking parity:

```bash
# was (both branches):
    poetry install --no-root
# to:
    poetry install --no-root --extras rerank
```

Add a one-line comment above the `if command -v poetry` block:

```bash
# --extras rerank keeps the local dev env's cross-encoder reranker working
# (sentence-transformers + torch). CI installs lean; the host opts in here.
```

- [ ] **Step 2: Verify bootstrap.sh is still valid shell**

Run:

```bash
bash -n scripts/bootstrap.sh && echo "bootstrap.sh syntax OK"
```

Expected: `bootstrap.sh syntax OK`.

- [ ] **Step 3: Document the opt-in in the three READMEs**

In each of `README.md`, `docs/operations/local-development-setup.md`, and `src/cofounder_agent/README.md`, find the existing local-install instruction (`poetry install` or `pip install -e src/cofounder_agent`) and add a sentence immediately after it:

```markdown
> The cross-encoder reranker (`sentence-transformers` + `torch`) is an opt-in extra so
> CI and lean installs skip the multi-GB CUDA wheel. To run it locally, install with the
> `rerank` extra: `poetry install --extras rerank` (or `pip install -e "src/cofounder_agent[rerank]"`).
```

- [ ] **Step 4: Verify the doc edits landed and render**

Run:

```bash
grep -rn "extras rerank\|\[rerank\]" README.md docs/operations/local-development-setup.md src/cofounder_agent/README.md
```

Expected: one match per file.

- [ ] **Step 5: Commit**

```bash
git add scripts/bootstrap.sh README.md docs/operations/local-development-setup.md src/cofounder_agent/README.md
git commit -m "$(cat <<'EOF'
docs: document the rerank extra opt-in + keep host-dev parity

bootstrap.sh installs --extras rerank so a fresh host keeps the working
cross-encoder reranker it had when sentence-transformers was a main dep.
Local-dev docs note the `poetry install --extras rerank` opt-in.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Full verification, metrics capture, and PR

**Files:** none (integration + rollout)

- [ ] **Step 1: Run the full backend unit suite against the lean env**

Drive pytest the same way CI does (per-dir), against the lean install:

```bash
cd src/cofounder_agent && VIRTUAL_ENV=/tmp/lean-check poetry run pytest tests/unit/services/ -q --tb=short -n 4 --dist loadfile -p no:cacheprovider
cd src/cofounder_agent && VIRTUAL_ENV=/tmp/lean-check poetry run pytest tests/unit/cli/ tests/unit/routes/ tests/unit/utils/ -q --tb=short -p no:cacheprovider
```

Expected: PASS (skips allowed). If the worktree poetry env is unusable, use the repo-root venv + `PYTHONPATH=src/cofounder_agent` fallback (`reference_worktree_test_invocation`). Any new failure traced to a missing torch/sentence-transformers import is a Task-1 regression — fix with an `importorskip` in that test and note it.

- [ ] **Step 2: Capture the before/after metric for the PR body**

Record the install-size delta for the PR description. From the two throwaway envs created in Task 1:

```bash
du -sh /tmp/lean-check/lib/python*/site-packages 2>/dev/null | tail -1   # lean (no torch)
du -sh /tmp/rerank-check/lib/python*/site-packages 2>/dev/null | tail -1 # rerank (with cu128 torch)
```

Expected: the lean tree is hundreds of MB smaller (no torch + CUDA libs). Note both numbers.

- [ ] **Step 3: Confirm the whole branch is committed and clean**

Run:

```bash
git -C ../.. status --short && git -C ../.. log --oneline origin/main..HEAD
```

Expected: clean tree; the log shows the spec commit + Tasks 1–4 commits (5 commits).

- [ ] **Step 4: File the tracking issue (routing)**

Per `feedback_check_issue_routing_first`, this is OSS-product CI/dependency structure → file on the public `poindexter` repo unless it touches operator-only infra (it doesn't). Confirm with:

```bash
gh issue create --repo Glad-Labs/poindexter --title "CI: move rerank stack to opt-in extra (skip cu128 torch on GPU-less runners)" --body "<summary + link to spec/plan>"
```

(If routing is ambiguous at execution time, ask before filing.)

- [ ] **Step 5: Push the branch and open the PR**

```bash
git push -u origin HEAD
gh pr create --repo Glad-Labs/glad-labs-stack --base main \
  --title "ci: make rerank an opt-in extra so CI skips cu128 torch" \
  --body "$(cat <<'EOF'
## What
`sentence-transformers` becomes an opt-in `rerank` extra. CI's default
`poetry install` is now torch-free; the worker + OSS-standalone images opt
back in via `--extras rerank`. Removes the `rm -rf android/.NET/GHC` disk
hacks from unit-tests / integration-db / benchmarks.

## Why
The cu128 CUDA torch wheel (~2.5 GB) was being installed on GPU-less CI
runners purely as a transitive of the sentence-transformers main dep.

## Verification
- Lean install: torch + sentence_transformers absent (site-packages −<N> MB).
- `--extras rerank`: both present (build-time asserts in both Dockerfiles).
- Both pipeline images build green with the assert.
- Full unit suite green on the lean env.

Spec: docs/superpowers/specs/2026-06-23-ci-rerank-extra-design.md
Plan: docs/superpowers/plans/2026-06-23-ci-rerank-extra.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Watch CI to green; merge when the gate passes**

```bash
gh pr checks --repo Glad-Labs/glad-labs-stack --watch
```

Expected: `unit-tests` (lean install, no disk hacks) and `integration-db` pass — this is the authoritative proof the lean install works on real runners. Merge per `feedback_ci_is_the_review_gate`. After merge, rebuild the worker image (`docker compose -f docker-compose.local.yml build prefect-worker`) per standing authority.

---

## Self-Review (completed)

**Spec coverage:** dependency change → Task 1; both pipeline-image edits (worker + OSS standalone) → Task 2; three lean CI workflows → Task 3; bootstrap parity + docs → Task 4; fail-loud asserts → Task 2 Steps 2/4; testing/verification → Tasks 1, 2, 5; success metrics → Task 5 Step 2; rollout + issue routing → Task 5 Steps 4–6. All spec sections map to a task.

**Placeholder scan:** every step has an exact command + expected output and exact file/line targets. The two genuinely conditional points (a test needing `importorskip`; a heavy Docker build being impractical at execution time) carry explicit instructions for both branches rather than a TODO.

**Type/name consistency:** the extra is named `rerank` everywhere (Task 1 defines it; Tasks 2 and 4 consume the literal string `rerank` in `--extras`); the build assert string is identical in both Dockerfiles.
