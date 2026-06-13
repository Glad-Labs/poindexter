# Design: `docs/reference/services.md` generator + drift guard

**Date:** 2026-06-13
**Status:** Approved (design); pending spec review
**Author:** Claude (Opus 4.8) with Matt

## Problem

`docs/reference/services.md` is a hand-maintained catalog of
`src/cofounder_agent/services/`. It has drifted badly: ~24 of the ~85 `.py`
files it references no longer exist by name (separate from the intentional
"deleted services" appendix). Root causes:

- The 6 modular DB files (`admin_database.py`, `content_database.py`,
  `embeddings_database.py`, `tasks_database.py`, `users_database.py`,
  `writing_style_database.py`) consolidated into `database_service.py`
  (+ `database_mixin.py`).
- The 5 image services (`image_generation_config.py`,
  `image_generation_runner.py`, `image_prompt_builder.py`,
  `image_selection_service.py`, plus the config file) restructured into the
  `services/image_providers/` package + `image_service.py` /
  `image_captioner.py` / `image_decision_agent.py` + content atoms/stages.
- Files deleted outright (`notifications_service.py`, `paging_helpers.py`,
  `html_sanitizer.py`, `slugify_service.py`, `quality_checker.py`,
  `rag_embeddings_service.py`, `vector_similarity_search.py`,
  `media_script_generator.py`, `transcription_service.py`,
  `handle_task_status_change.py`, `stateless_decision_handler.py`,
  `idle_worker.py`).
- The content-pipeline code physically moved from `services/` into
  `modules/content/` (Phase 3 migration, 2026-06-04). The doc still points
  `multi_model_qa.py`, `content_validator.py`, etc. at `services/` — and so do
  the task brief and CLAUDE.md narrative.
- The "Pipeline stages (12)" section is pre-#362: it claims stages live under
  the deleted `services/stages/` tree and lists monolithic nodes
  (`generate_content.py`, `replace_inline_images.py`, `finalize_task.py`) that
  were decomposed into atoms. The authoritative node list is now 36 nodes in
  `services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF`.

The drift recurs because the doc is a **hand-copy** of two facts that change
often — _which files exist_ and _the pipeline node order_. Re-fixing it by hand
guarantees re-drift.

## Goals

1. Bring `services.md` to 100% accuracy against the current
   `services/` + `modules/content/` trees.
2. Add a generator so it stops re-drifting — the doc becomes a **projection**
   of authoritative sources, not a hand-copy. Mirror the spirit of
   `scripts/regen-app-settings-doc.py`.
3. Prevent future drift with a CI guard.

## Non-goals

- Editing CLAUDE.md (it already self-corrects `services/` paths with an inline
  note; out of scope — stay in lane).
- Scanning `modules/finance/` or other private overlays (outside the two
  trees; no redaction logic needed).
- Cataloging `services/migrations/` (schema deltas with their own runner +
  `migrations.md`).

## Decisions (resolved with Matt)

1. **Coverage: exhaustive, grouped by package.** Every tracked
   service/atom/stage `.py` listed under its package heading with a
   docstring-derived one-liner. Reverses the old "skimmable subset" framing.
   Justification: only exhaustive coverage makes existence-drift structurally
   impossible, and it serves the doc's real audience (LLM "what's responsible
   for X" lookups). 99% docstring coverage makes the output genuinely useful.

2. **Drift prevention: PR-time blocking check.** Because the source is the repo
   itself (not the live DB, as app-settings is), drift can only appear in a PR
   that moves/renames/deletes a file. A `git diff --exit-code` check after
   regeneration catches it at the moment it's introduced — no Postgres, no
   GitHub App token, works on forked PRs. The heavy nightly-PR machinery of
   `regen-app-settings-doc.yml` exists only because _its_ source lives outside
   the repo; we don't inherit that complexity.

### Consequence of decision 2: the artifact must be deterministic

A PR-time `git diff` check requires the generated file to be a **pure
deterministic function of tracked source** — no clocks, no env, no DB.
Therefore, diverging from app-settings:

- **No date stamp.** The current "Last Updated / last generated" lines are the
  very thing that goes stale and lies. Git history is the real timestamp. Drop
  them; replace with a static "generated; run the script to refresh" banner.
  (This also removes the need for the `resolved_stamp` / `REGEN_DATE_OVERRIDE`
  seam that app-settings needs.)
- **No exact line counts.** They would churn on every code edit and fire the
  drift check constantly. Drop the current "~250 lines" hints. (Optional future
  add: coarse size _buckets_ XS/S/M/L/XL that change only when a file crosses a
  threshold — deferred unless requested.)

## Deliverables

1. `scripts/regen-services-doc.py` — the generator.
2. `docs/reference/services.md` — regenerated (fixes all current drift by
   construction).
3. `.github/workflows/regen-services-doc.yml` — PR-time blocking drift check.
4. `src/cofounder_agent/tests/unit/scripts/test_regen_services_doc.py` — unit
   tests.

## Generator design

Structured as **pure render functions + a thin `main()`** so the render logic
is unit-testable without IO (the app-settings generator couldn't do this — its
render needs a live Postgres; ours needs only a list of files).

### Source A — the file trees

- Discover via `git ls-files` over `src/cofounder_agent/services/**/*.py` and
  `src/cofounder_agent/modules/content/**/*.py` (matches the "verify against
  git ls-files" instruction; excludes untracked/ignored files).
- Exclude `services/migrations/**` and `modules/content/migrations/**`.
- Exclude empty/stub `__init__.py`; keep `__init__.py` that carries a real
  module docstring (e.g. `pipeline_templates/__init__.py`).
- Per file: `summary = first_docstring_line(source)` — `ast.get_docstring`,
  first non-empty line, internal whitespace collapsed, pipes escaped,
  truncated to ~140 chars. Missing docstring → `—`.
- Group by package label: top-level `services/*.py` → `services/ (top-level)`;
  everything else → its containing directory (e.g. `modules/content/atoms/`).

### Source B — the pipeline node list

- Load `CANONICAL_BLOG_GRAPH_DEF` by **file path via importlib** (not package
  import) so it pulls zero LangGraph/template_runner deps — honoring that
  spec's stated "pure data, NO imports" design and keeping the generator (and
  its test) dependency-light.
- Walk `edges` from `entry` to `END` to render the 36 nodes in true execution
  order (robust against `nodes`-list reordering).
- **Compute** the composition counts from atom-id prefixes
  (`stage.` / `content.` / `qa.` / `seo.` / `atoms.`) → `10 / 12 / 12 / 1 / 1`.
  Because the section renders the live spec, a removed node (e.g.
  `qa.guardrails`, #730) can never reappear.

### Curated, deterministic tail

- `_DELETED_SERVICES`: a list-of-records constant (name, when, note) ported
  from the current doc's appendix. **Each entry verified genuinely absent via
  `git ls-files` during implementation** before inclusion. Preserves useful
  "don't grep for this" history through regeneration.
- Static intro banner + "Conventions" block as string constants. The generator
  owns the entire file (like app-settings) — to change the prose, change the
  script. **The ported intro/Conventions prose gets an accuracy pass during
  implementation**, not a verbatim copy: the current Conventions block has its
  own drift (e.g. "Never import from `stages/*`" points at the deleted
  `services/stages/` tree; `decision_service` / `error_handler` references need
  a `git ls-files` check).

### Output document structure

```
# Poindexter Services Reference

> Auto-generated by scripts/regen-services-doc.py — do not edit by hand.
> Run the script to refresh; CI fails if this file is out of date.
> <N> files across <M> packages (migrations excluded).

[intro: what this catalog is / how to use it]

## Table of contents
- [services/ (top-level)](#...) (146 files)
- ...

## services/ (top-level)
| File | Summary |
| ---- | ------- |
| `database_service.py` | PostgreSQL Database Service Coordinator |
| ...

## modules/content/atoms/
...

---

## Content pipeline (canonical_blog graph_def) — 36 nodes
[10 stage.* + 12 content.* + 12 qa.* + 1 seo.* + 1 atoms.approval_gate,
 computed; numbered list in edge order: verify_task → … → evaluate_auto_publish]

---

## What's NOT in this catalog (deleted services)
[rendered from _DELETED_SERVICES]

---

## Conventions
[static block]
```

### `main()`

Discover → read sources → render → write `docs/reference/services.md` as UTF-8
with `newline="\n"` → print a summary line (files shown, packages, excluded
counts). Fail loud if `git ls-files` returns nothing (per
`feedback_no_silent_defaults`).

## CI workflow

`.github/workflows/regen-services-doc.yml`:

- Triggers: `pull_request` on paths `src/cofounder_agent/services/**`,
  `src/cofounder_agent/modules/content/**`, `scripts/regen-services-doc.py`,
  `docs/reference/services.md`; plus `workflow_dispatch`.
- `permissions: contents: read`.
- Steps: checkout → `actions/setup-python@… 3.13` → (no deps; pure stdlib) →
  `python scripts/regen-services-doc.py` →
  `git diff --exit-code -- docs/reference/services.md` || fail with a message
  telling the contributor to run the script and commit.
- ~15s, no services block, no secrets — so it runs on forked PRs too.
- Whether this becomes a **required** check is a branch-protection setting Matt
  owns (current required set is minimal: test-backend + migrations-smoke). The
  workflow goes red on drift regardless; promotion to required is his call and
  noted, not assumed.

## Test plan

`test_regen_services_doc.py` — load the hyphen-named script via importlib
(reuse the existing test's loader pattern; **no `brain.bootstrap`/asyncpg stub
needed** since the generator has no DB import). Cover the pure functions:

- `first_docstring_line`: normal extraction; multiline (takes first non-empty);
  missing docstring → `—`; pipe in docstring is escaped; long line truncated.
- `group_by_package`: top-level vs subdir bucketing.
- `render_catalog`: table header + row shape; pipe-escaping; deterministic
  ordering.
- `render_pipeline_section` against the **real** `CANONICAL_BLOG_GRAPH_DEF`:
  36 nodes; prefix counts `10/12/12/1/1`; `qa.guardrails` absent;
  order follows edges (`verify_task` first, `evaluate_auto_publish` last).
- Idempotence: rendering the full doc twice yields byte-identical output.

The "generated == committed" invariant is enforced by the CI job (git diff),
mirroring how app-settings lets the workflow own end-to-end verification.

## Risks / notes

- **Docstring quality:** 99% coverage, first lines sampled and clean. The 2
  files without docstrings (`telemetry.py`, one empty `__init__.py`) get `—` /
  are skipped. Improving a weak catalog entry = improving that file's docstring
  (a feature: description co-located with code).
- **Doc length:** ~368 rows across 26 packages. Grouped + ToC keeps it
  navigable; completeness is the point.
- **Public mirror:** `docs/reference/` ships to the public Poindexter mirror.
  Service docstrings describe code, not operator PII; the two scanned trees are
  public. No redaction tier needed (unlike app-settings, which leaked a bank
  balance). If a private module is ever added under these paths, revisit.
- The broader-effort plan referenced in the task
  (`docs/superpowers/plans/2026-06-13-documentation-cleanup.md`) does not exist;
  this spec stands alone.

## Verified facts (2026-06-13, via `git ls-files` + AST probes)

- 407 tracked `.py` across 26 packages; 368 excluding 39 migration files.
- 99% module-docstring coverage (405/407).
- `CANONICAL_BLOG_GRAPH_DEF` = 36 nodes (10 stage / 12 content / 12 qa / 1 seo /
  1 atoms.approval_gate) — matches CLAUDE.md.
- Content files now under `modules/content/`: `multi_model_qa.py`,
  `content_validator.py`, `quality_service.py`, `auto_publish_gate.py`,
  `ai_content_generator.py`, `internal_link_coherence.py`.
