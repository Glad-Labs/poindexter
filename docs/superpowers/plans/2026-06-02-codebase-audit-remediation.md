# Codebase-Audit Remediation — Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement each batch task-by-task. Steps use checkbox (`- [ ]`) syntax.
>
> **This is a master/index plan.** The spec (55 audit issues) spans 11 independent subsystems, so per the writing-plans scope-check it is decomposed into 11 batch-plans, each a standalone PR that produces working, testable software. Detailed bite-sized TDD steps are authored per batch at execution time.

**Goal:** Resolve all 55 issues from the 2026-06-02 codebase audit (`codebase-audit` label) across the two trackers, via reviewable themed PRs that keep CI green.

**Architecture:** Single monorepo (`glad-labs-stack`). All code — including fixes for `poindexter`-tracked issues — lands here in `src/cofounder_agent/`, `brain/`, `infrastructure/`, `docs/`, `web/`, root configs. `poindexter` is a force-rebuilt public mirror (`scripts/sync-to-github.sh` on push to `origin/main`); it cannot take code PRs. So every fix is a commit on a `glad-labs-stack` branch → PR → on merge the OSS subset auto-mirrors and the `poindexter` issues are closed by reference.

**Tech Stack:** Python 3.12 / FastAPI / asyncpg / Prefect / LangGraph (backend); Next.js 16 App Router (frontend); Poetry, pytest, Jest, Playwright; Docker Compose; GitHub Actions.

---

## Execution constraints (apply to every batch)

- **Branching:** work on `claude/nifty-franklin-76119c` (current) or per-batch branches off it. **Never push `main`; never merge.** Output = commits + PR(s) to `Glad-Labs/glad-labs-stack`. Human/automation merges.
- **Cross-repo issue closing:** a `glad-labs-stack` PR can `Closes #NNN` only for `glad-labs-stack` issues. For `poindexter` issues, reference them in the PR body and close them manually (`gh issue close N --repo Glad-Labs/poindexter`) once merged+mirrored.
- **CI gates** (must stay green): `test-backend` / unit-tests, `migrations-smoke`, `migrations-lint`, `grafana-panels-lint`, public-mirror-safety, link-rot, Mintlify. Run the relevant one locally before each PR.
- **Known gotchas (from memory):**
  - **Prettier pre-commit hook mangles markdown prose containing `*` glob tokens** — keep globs like `qa.*` out of reflowed `.md` prose (use code spans), and re-read committed `.md`.
  - **ESLint pre-commit hook is broken (eslint 10 vs plugin-react)** — frontend commits may need `--no-verify`; JS lint is hook-only (no CI), Vercel ignores it.
  - **`web/public-site/` is gitignored-but-tracked** — Grep/ripgrep silently skip it; use Read/Glob/Select-String. lint-staged warns but commits work.
  - **migrations-smoke runs in a LIGHT env** (no langchain/langgraph) — a new migration must import only light modules (`services/__init__` light; `pipeline_templates/__init__` heavy). Graph_def seed migrations import pure-data spec modules only.
  - **Worker bind-mounts the live `main` checkout** — do NOT deploy; these are PRs, not prod pushes.
- **Verification before "done":** run the batch's named command and paste real output. Evidence before assertions (verification-before-completion skill).

---

## Risk / judgment calls (decide before the relevant batch)

1. **#615 approve/reject params as query-string → body is a BREAKING change.** Callers: CLI, MCP server, public-site, Playwright specs. **Decision: preserve backward compatibility** — accept an optional Pydantic `Body(...)` model AND keep the existing query params working (read body-first, fall back to query). Do not hard-break.
2. **#606 / #608 auth changes** touch the security boundary. Full TDD: write failing tests pinning the desired default-deny behavior first. #608 is compose-only (no unit test) — verify with `docker compose config` that the var is now required.
3. **#623 decompose `publish_post_from_task` (941 lines)** is high-risk pure refactor. Characterization tests first (pin current behavior), then extract phases, re-run. No behavior change.
4. **#621 delete 7 "dead" modules** — re-confirm zero non-test importers at execution time (`grep -rn "import <mod>"`), and that none is a plugin entry-point in `pyproject.toml`, before deleting. `task_failure_alerts.py` is in this list AND referenced by #647/#642 — resolve as deletion (drop the exc_info sub-item if the module goes).
5. **#642/#647 umbrellas** contain items that overlap feature batches (exception-leak, probes auth, task tests). Land each sub-item in its most natural batch; close the umbrella when all boxes are checked.

---

## Batch sequencing (safe / high-confidence → risky / cross-cutting)

| #   | Batch (PR)               | Issues                                                | Risk     | Local verification                                                  |
| --- | ------------------------ | ----------------------------------------------------- | -------- | ------------------------------------------------------------------- |
| 1   | Documentation accuracy   | #609 #610 #638 #639 #640 #968 + Next-ver part of #979 | LOW      | re-read; `ls routes/*.py`; node-count from spec                     |
| 2   | Database integrity       | #625 #626 #627 #628 #629                              | MED      | `migrations_lint`, `migrations_smoke`, pytest embeddings/content_db |
| 3   | Testing scaffolding      | #616 #617 #618 #641 #647 #977                         | LOW      | run the new tests; workflow lint                                    |
| 4   | Fail-loud observability  | #611 #612 #613 #632 #633 #634                         | MED      | pytest per module                                                   |
| 5   | Performance              | #619 #620 #644                                        | MED      | pytest; behavior-unchanged                                          |
| 6   | Infra / DevOps / deps    | #607 #630 #631 #645 #646                              | LOW-MED  | `docker compose config`; pyproject parse                            |
| 7   | Accessibility            | #972 #973 #974 #975 #976 #978                         | LOW-MED  | `npm --prefix web/public-site run build`                            |
| 8   | Frontend correctness     | #967 #969 #970 #971 + #979 (perf/pgpass)              | MED      | public-site build; Read/Glob (not Grep)                             |
| 9   | Code-quality / dead-code | #621 #622 #623 #643                                   | MED-HIGH | full pytest; importer checks                                        |
| 10  | API contracts            | #614 #624 #635 #637                                   | MED      | pytest routes                                                       |
| 11  | Auth & security          | #606 #608 #615 #636 #642                              | HIGH     | pytest oauth/middleware; compose config                             |

Batches 1–8 are largely independent (different files) and can proceed with confidence. 9–11 are the cross-cutting / breaking / security-sensitive ones held for last, each with characterization tests first.

---

## Per-batch detail

### Batch 1 — Documentation accuracy (LOW · no tests · verify-then-edit)

**Files:** `src/cofounder_agent/README.md` (ships to OSS mirror → fixes #609/#610/#638/#639/#640), `CLAUDE.md` (business-only → #968 + Next ver), `web/public-site/README.md` (Next ver).

- #609 remove the `agents/` block (dir confirmed absent).
- #610 replace `api_token` bullet with OAuth 2.1 client-credentials flow.
- #638 `12 nodes` → exact count (verify from `canonical_blog_spec.py`; ~21–22).
- #639 move `modules/` out of the `services/` subtree to top-level.
- #640 route count `31/30` → `20` (verified: 20 files).
- #968 CLAUDE.md `18 nodes (13 stage + 5 qa)` → real count + `caption_images` + 3-node `seo.*` chain; fix the numbered chain from node 9 on.
- #979(doc) `Next.js 15` → `Next.js 16` in CLAUDE.md + public-site README (pkg pins `^16.2.6`).
  **Verify:** re-read each edited region; confirm node count by reading `canonical_blog_spec.py`. Watch the prettier-glob gotcha.
  **PR:** `docs: correct backend README + CLAUDE.md drift (audit batch 1)`.

### Batch 2 — Database integrity (MED · migrations + unit tests · TDD)

**Files:** new `services/migrations/<ts>_drop_redundant_idx_posts_slug.py` (#627), `<ts>_add_posts_pipeline_task_id_index.py` (#628); modify `services/embeddings_db.py` (#625 add `chunk_index` param; #626 deterministic/scoped fetch); `services/content_db.py` (#629 wrap post+tags in `conn.transaction()`).

- Migrations: idempotent (`DROP INDEX IF EXISTS` / `CREATE INDEX IF NOT EXISTS`), light-env-safe imports, generated via `scripts/new-migration.py`.
- #625/#626/#629: write failing unit tests first.
  **Verify:** `python scripts/ci/migrations_lint.py`; `python scripts/ci/migrations_smoke.py`; `poetry run pytest tests/unit/services/test_embeddings_db.py tests/unit/services/test_content_db.py -q`.

### Batch 3 — Testing scaffolding (LOW · adds tests/CI)

**Files:** new `tests/unit/services/test_post_pipeline_actions.py` additions (#616), `tests/unit/routes/test_oauth_routes.py` (#617), `tests/integration/test_graphdef_pipeline.py` (#618), edit `.github/workflows/unit-tests.yml` (#641), new `test_auto_publish.py` + `test_topics_routes.py` + `test_voice_routes.py` + gate OR-bleed test (#647), new `.github/workflows/playwright-e2e.yml` (#977).

- These tests pin current correct behavior; #616/#617/#618 are the P1 regression guards. Land BEFORE the batches that touch the same code (4, 9, 11) so refactors are guarded.
  **Verify:** run each new test; `actionlint`/yaml parse the workflows.

### Batch 4 — Fail-loud observability (MED · TDD)

**Files:** `services/cost_guard.py` (#611), `services/jobs/detect_anomalies.py` (#612), `services/jobs/findings_alert_router.py` (#613), `services/post_pipeline_actions.py` (#632), `services/integrations/operator_notify.py` (#633), `services/metrics_exporter.py` (#634).

- Each: failing test that asserts the loud/correct behavior on the error path, then fix. #613 watermark: assert `max_id == first_failed_id - 1`. #634: wire `TASKS_CREATED.inc()` at the creation site or delete + fix docstring (decide at execution by checking the creation path).
  **Verify:** `poetry run pytest` for each touched module.

### Batch 5 — Performance (MED · behavior-preserving)

**Files:** `services/tasks_db.py` (#619 projection), `services/static_export_service.py` (#620 single scan + feed LIMIT), `services/metrics_exporter.py` + `routes/task_status_routes.py` + `routes/cms_routes.py` + `routes/settings_routes.py` + `routes/task_publishing_routes.py` (#644 umbrella).

- Keep outputs identical; assert via existing/added tests that response shape is unchanged.
  **Verify:** `poetry run pytest tests/unit/services/test_tasks_db.py tests/unit/services/test_static_export_service.py -q` + route tests.

### Batch 6 — Infra / DevOps / deps (LOW-MED)

**Files:** new root `.env.example` (#607), `docker-compose.local.yml` (#630 healthcheck, #646 gitea/glitchtip), `docker-compose.yml` (#646 mem limits), `brain/Dockerfile` (#631 sha256), `src/cofounder_agent/Dockerfile.worker` (#646 stderr), `pyproject.toml` + `src/cofounder_agent/pyproject.toml` + `src/LICENSE.md` (#645).

- #607: ensure `.env.example` is NOT stripped by the sync filter (and remove its `git rm` line) so the OSS quickstart works.
  **Verify:** `docker compose -f docker-compose.yml config -q`; `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"`; pin SHA from official docker release.

### Batch 7 — Accessibility (LOW-MED · web/public-site)

**Files:** `web/public-site/app/{layout.js,search/page.jsx,not-found.tsx,error.tsx,legal/layout.tsx,preview/[token]/page.tsx}` (#972 main, #973 footer/banner), listing pages + `packages/brand/src/components/Card.jsx` (#974 h2), `app/search/page.jsx` (#975 aria-live), `packages/brand/src/tokens/colors.css` or usages (#976 contrast), `components/{NewsletterModal.tsx,CookieConsentBanner.jsx}` + `app/posts/[slug]/page.tsx` + buttons (#978).
**Verify:** `npm --prefix web/public-site run build`; manual landmark/heading review. Use Read/Glob, not Grep.

### Batch 8 — Frontend correctness (MED · web/public-site)

**Files:** the 6 cache-tag files (#967), `app/not-found.tsx` (#969 → server component via `lib/posts.ts`), `app/posts/[slug]/page.tsx` (#970 single BlogPosting), delete `lib/api-fastapi.js` after inlining `getImageURL` (#971), `next.config.js` (#979 optimizePackageImports), `docker-compose.local.yml` pgpass guard (#979).
**Verify:** `npm --prefix web/public-site run build`; confirm `revalidateTag('posts')` now invalidates all surfaces.

### Batch 9 — Code-quality / dead-code (MED-HIGH · refactor)

**Files:** delete 7 modules + tests (#621, re-confirm zero importers), shared gate module for #622, decompose `publish_service.py:517` (#623, characterization tests first), #643 umbrella (extract `_strip_markdown_fence`, `configure_cloudinary`; rule-registry for `content_validator`; split `multi_model_qa.review`; delete dead `claim_next_task`/`release_task`; fail-loud migration runner; reconcile `settings_service` env fallback).
**Verify:** full `poetry run pytest tests/unit -q`; `grep -rn` importer checks before each deletion.

### Batch 10 — API contracts (MED)

**Files:** delete dead `reject_task` in `routes/task_publishing_routes.py` (#614), unify on one `ErrorResponse` and migrate `cms_routes.py` (#624), settings `offset/limit` alias (#635), analytics 200→503 (#637).
**Verify:** `poetry run pytest tests/unit/routes -q`.

### Batch 11 — Auth & security (HIGH · TDD)

**Files:** `middleware/api_token_auth.py` (#606 default-deny dev-token), `docker-compose.local.yml` (#608 required LiveKit secret), `routes/video_routes.py` (#636 auth + drop file_path), `routes/task_publishing_routes.py` (#615 body w/ back-compat), #642 umbrella (`routes/external_webhooks.py` Svix HMAC, settings secret redaction in `services/admin_db.py`/`schemas/model_converter.py`, `routes/module_probes_routes.py` auth, generic 500 detail across the listed routes).
**Verify:** `poetry run pytest tests/unit/routes/test_oauth_routes.py tests/unit/middleware -q`; `docker compose config`.

---

## Self-review (spec coverage)

All 42 OSS issues (#606–#647) and 13 business issues (#967–#979) are mapped to a batch above; the two P3 umbrellas' sub-items are distributed to their natural batches and the umbrella closed when all boxes are checked. The three highest-risk items (#615 breaking change, #606/#608 auth, #623 god-function) have explicit risk decisions. No batch depends on an undefined artifact; sequencing places guard tests (Batch 3) before the refactors that need them (4, 9, 11).
