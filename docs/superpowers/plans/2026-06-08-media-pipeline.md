# `media_pipeline` Implementation Plan (Stage 2)

> **For agentic workers:** execute task-by-task with TDD. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build the Stage-2 `media_pipeline` graph_def template that renders + distributes media from the persisted Stage-1 scripts, per [docs/architecture/video-pipeline-redesign.md](../../architecture/video-pipeline-redesign.md) (epic [poindexter#689](https://github.com/Glad-Labs/poindexter/issues/689)).

**Architecture:** A second LangGraph `graph_def` template (`media_pipeline`), seeded into `pipeline_templates` and run by `TemplateRunner` exactly like `canonical_blog`. Stage 1 persists scripts/shot-lists (done: #1226/#1233); Stage 2 loads them and renders deterministically — a re-render never re-invents prompts (root fix for #674/#675).

**Tech Stack:** LangGraph graph_def, atom registry (filesystem-discovered atoms under `modules/content/atoms/`), `pipeline_templates` DB table, FFmpeg compositor, SDXL/Pexels sources, Speaches TTS + Whisper ASR.

---

## Plan decomposition (this doc covers Plan 2; 3–8 are the roadmap)

Each plan is one PR off `origin/main`, TDD + contract tests, landed before the next starts.

| Plan            | Scope                                                                                                                                             | Key issues | External infra to verify           |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ---------------------------------- |
| **2 (this PR)** | `media_pipeline` graph_def **spine**: `media.load_scripts` atom + spec module + seed migration (dormant — no live trigger yet)                    | #674 spine | none — fully unit/compile testable |
| 3               | Director emits **long + short shot-lists** (`source_hint`, `narration_offset_s`, `aspect`); extend `Shot` schema                                  | #517-core  | none (schema + LLM-mocked)         |
| 4               | `FFmpegLocalCompositor` render engine — per-shot source select (slideshow/Pexels) + compose + ambient mix + 16:9/9:16 profiles                    | #675/#679  | ffmpeg, SDXL, Pexels               |
| 5               | One-ASR pass — captions (.srt burn-in) + fidelity QA diff                                                                                         | #676       | Whisper/Speaches                   |
| 6               | `media_qa` atom (frame human-detection, caption-presence, A/V sync) + `qa.audio`                                                                  | #1193      | audio model                        |
| 7               | Per-piece **Gate 1** + per-asset tiered **Gate 2** state machines + voice rotation + **Stage-2 trigger wiring** (approval → `media_pipeline` run) | #677/#531  | none (state machine, mocked)       |
| 8               | Distribution: YouTube long + Shorts (#682 fix) + reconciliation watchdog demotion + render Grafana (#678)                                         | #682/#678  | YouTube API                        |

**Pacing note:** Plans 4–6 + 8 require real external services (ffmpeg, Pexels, Whisper, YouTube) that can't be fully validated in a venv-less worktree — those land with thorough mocked unit tests + a manual verification checklist, and are confirmed against the live stack on Matt's PC.

---

## Plan 2 — the spine

`media_pipeline` is seeded **dormant**: an `active=true` row in `pipeline_templates` with no dispatcher calling `TemplateRunner.run("media_pipeline", …)` yet (the trigger lands in Plan 7). Seeding a row with no caller is a behavior no-op in prod — the same way `canonical_blog_spec` was seeded before its cutover (#355).

`media.load_scripts` is the Stage-2 entry atom: given a `task_id`, it reads the persisted Stage-1 artifacts from `pipeline_versions.stage_data['task_metadata']` (the source of truth — `posts.metadata` does NOT hold the scripts) and surfaces them as graph state for the downstream render nodes (added in Plans 3–8).

### Files

- Create: `src/cofounder_agent/services/media_pipeline_spec.py`
- Create: `src/cofounder_agent/modules/content/atoms/media_load_scripts.py`
- Create: `src/cofounder_agent/services/migrations/20260608_120000_seed_media_pipeline_graph_def.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_media_load_scripts.py`
- Test: `src/cofounder_agent/tests/unit/services/migrations/test_seed_media_pipeline_graph_def.py`
- Test: extend `src/cofounder_agent/tests/integration/test_graphdef_pipeline.py` (spec compiles)

### Task 1 — `media.load_scripts` atom (TDD)

- [ ] RED: `test_media_load_scripts_reads_task_metadata` — mock pool `fetchrow` returns `{"task_metadata": {...}}`; assert the 5 Stage-1 keys come back.
- [ ] RED: `test_media_load_scripts_handles_missing_metadata` — `fetchrow` → None → empty defaults (no raise).
- [ ] RED: `test_media_load_scripts_requires_task_id` — missing `task_id` raises `ValueError`.
- [ ] GREEN: create `media_load_scripts.py` (auto-discovered under `modules/content/atoms/`).

### Task 2 — `media_pipeline` spec + compile (TDD)

- [ ] RED: add `test_media_pipeline_spec_compiles` to `test_graphdef_pipeline.py` — `_validate_spec` ok + `build_graph_from_spec(...).compile()`.
- [ ] GREEN: create `media_pipeline_spec.py` (`MEDIA_PIPELINE_GRAPH_DEF`, pure data — no heavy imports so the migration can import it under migrations-smoke).

### Task 3 — seed migration (TDD)

- [ ] RED: `test_seed_media_pipeline_graph_def` — mock pool; `up()` issues `INSERT … pipeline_templates … 'media_pipeline' … ON CONFLICT (slug) DO UPDATE`; `down()` deletes the row.
- [ ] GREEN: create the migration (imports only `MEDIA_PIPELINE_GRAPH_DEF` + stdlib).
- [ ] Verify: `python scripts/ci/migrations_lint.py` passes (no collision / interface).
