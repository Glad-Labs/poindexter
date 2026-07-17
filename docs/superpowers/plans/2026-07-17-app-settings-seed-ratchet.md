# app_settings Seed Reconciliation + Drift Ratchet Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. Execute inline, task-by-task, verifying each step. (Subagent dispatch is disabled in this repo per CLAUDE.md — do not delegate.)

**Goal:** Make the three `app_settings` seed sources agree where they must, declare where they may differ, and add a CI ratchet so the next baseline squash cannot silently re-introduce drift.

**Architecture:** `settings_defaults.py::DEFAULTS` and `0000_baseline.seeds.sql` are two encodings of _the reference default_ and must be byte-identical on every overlapping key. `brain/seed_app_settings.json` is a deliberate `tier: free` profile and may differ — but only via a declared `TIER_POLICY` allowlist. A new static lint enforces both rules and is wired into the required `migrations-smoke` check.

**Tech Stack:** Python 3.13, pytest, asyncpg (not needed — lint is static), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-17-app-settings-seed-reconciliation-design.md`

## Global Constraints

- **No DB, no project imports in the lint.** It runs in CI where neither exists. AST-parse `DEFAULTS`, regex-parse the seed SQL, JSON-parse the brain seed — same constraint as the sibling `settings_seed_drift_lint.py`.
- **Exact-string comparison, not normalized.** Precedent: `reference_prompt_fallback_drift_guard_newline`. Normalizing would let `300` vs `300.0` and whitespace-only JSON drift persist — the noise that hides a real diff.
- **Prod is never rewritten.** All seeders are `INSERT ... ON CONFLICT (key) DO NOTHING`; all 45 rows already exist live. Blast radius is fresh installs only.
- **`''` is the unset sentinel, never NULL** (`feedback_app_settings_value_not_null`). NULL crashes CI.
- **Do NOT run `scripts/ci/migrations_smoke.py` locally.** It resolves the DSN via bootstrap.toml and would hit **prod** (`reference_resolve_db_url_bootstrap_precedence`). CI runs it against a throwaway DB.
- **Test invocation in this worktree:** the worktree has no venv. Use the main checkout's poetry env with absolute paths and `-o addopts=""`:
  `"$LOCALAPPDATA/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest <ABS-PATH> -o addopts="" -q`
- **Run `check_public_mirror_safety.py` only after `git add`** — it scans `git ls-files`, so untracked files are invisible to it.
- Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Use `git commit -F -` (backticks in `-m` get shell-substituted).

## File Structure

| File                                                                            | Responsibility                                                                                         |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `scripts/ci/settings_seed_value_drift_lint.py`                                  | **new** — the ratchet. Asserts `code == baseline` always; `brain == baseline` unless in `TIER_POLICY`. |
| `src/cofounder_agent/tests/unit/scripts/test_settings_seed_value_drift_lint.py` | **new** — contract tests incl. the stale-allowlist-entry guard.                                        |
| `src/cofounder_agent/services/settings_defaults.py`                             | modify — 16 values → baseline's.                                                                       |
| `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql`               | modify — 12 values → code's.                                                                           |
| `brain/seed_app_settings.json`                                                  | modify — 1 correctness fix (`pipeline_critic_model`).                                                  |
| `.github/workflows/migrations-smoke.yml`                                        | modify — wire the lint into the required check.                                                        |
| `modules/content/stages/generate_media_scripts.py`                              | modify — inline `wav` fallback → `mp3`.                                                                |
| `services/image_service.py`, `services/image_providers/_image_models.py`        | modify — inline `sdxl_lightning` → `z_image_turbo`.                                                    |
| `docs/operations/migrations.md`                                                 | modify — squash runbook step.                                                                          |
| `CLAUDE.md`                                                                     | modify — correct the "new keys belong in settings_defaults.py" claim.                                  |

---

### Task 1: The ratchet lint + tests

**Files:**

- Create: `scripts/ci/settings_seed_value_drift_lint.py`
- Test: `src/cofounder_agent/tests/unit/scripts/test_settings_seed_value_drift_lint.py`

**Interfaces:**

- Produces: `TIER_POLICY: dict[str, str]` (key → reason), `main() -> int` (0 clean / 1 drift), `_defaults()`, `_baseline()`, `_brain()` each `-> dict[str, str]`.
- Consumes: nothing (static, stdlib only).

- [ ] **Step 1: Write the failing tests**

Model the loader on the sibling `settings_seed_drift_lint.py` (`_SEED_KEY_RE`, `_defaults_keys` AST walk). Tests build synthetic source trees in `tmp_path` and monkeypatch the module's path constants, so they never depend on the real tree's current drift state.

```python
def test_clean_tree_passes(tmp_path, monkeypatch):
    _write_tree(tmp_path, code={"a": "1"}, baseline={"a": "1"}, brain={"a": "1"})
    _point_at(monkeypatch, tmp_path)
    assert LINT.main() == 0

def test_code_baseline_divergence_fails(tmp_path, monkeypatch, capsys):
    _write_tree(tmp_path, code={"a": "1"}, baseline={"a": "2"}, brain={})
    _point_at(monkeypatch, tmp_path)
    assert LINT.main() == 1
    out = capsys.readouterr().out
    assert "a" in out and "1" in out and "2" in out

def test_brain_divergence_fails_when_not_tier_policy(tmp_path, monkeypatch):
    _write_tree(tmp_path, code={"a": "1"}, baseline={"a": "1"}, brain={"a": "9"})
    _point_at(monkeypatch, tmp_path)
    monkeypatch.setattr(LINT, "TIER_POLICY", {})
    assert LINT.main() == 1

def test_brain_divergence_passes_when_tier_policy(tmp_path, monkeypatch):
    _write_tree(tmp_path, code={"a": "1"}, baseline={"a": "1"}, brain={"a": "9"})
    _point_at(monkeypatch, tmp_path)
    monkeypatch.setattr(LINT, "TIER_POLICY", {"a": "free tier caps this lower"})
    assert LINT.main() == 0

def test_stale_tier_policy_entry_fails(tmp_path, monkeypatch, capsys):
    """An allowlist entry for a key that no longer diverges must fail, so the
    allowlist cannot rot into a list of lies."""
    _write_tree(tmp_path, code={"a": "1"}, baseline={"a": "1"}, brain={"a": "1"})
    _point_at(monkeypatch, tmp_path)
    monkeypatch.setattr(LINT, "TIER_POLICY", {"a": "no longer true"})
    assert LINT.main() == 1
    assert "stale" in capsys.readouterr().out.lower()

def test_every_tier_policy_entry_has_a_reason():
    assert all(v.strip() for v in LINT.TIER_POLICY.values())

def test_real_tree_is_clean():
    """The reconciliation's acceptance test — all 45 conflicts resolved."""
    assert LINT.main() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `<PY> -m pytest <ABS>/test_settings_seed_value_drift_lint.py -o addopts="" -q`
Expected: FAIL — `ModuleNotFoundError` / no such file. `test_real_tree_is_clean` must fail with real drift once the module exists (that failure IS the bug reproduction).

- [ ] **Step 3: Write the lint**

Key design points (full code written at implementation time, mirroring the sibling's structure):

- `REPO = Path(__file__).resolve().parents[2]`
- `_defaults()` — AST walk for the `DEFAULTS` dict, returning key→value (the sibling's `_defaults_keys` returns keys only; extend to values).
- `_baseline()` — regex `INSERT INTO app_settings \(key, value,.*?VALUES \('([^']+)', '((?:[^']|'')*)'` with `re.S`, un-escaping `''` → `'`.
- `_brain()` — `json.load(...)["settings"]` → `{s["key"]: s["value"]}`.
- Report each violation with key, both values, and which file to edit.
- Stale-entry check: any `TIER_POLICY` key that is absent from both sources or does not actually diverge → fail.

- [ ] **Step 4: Run tests — synthetic ones pass, `test_real_tree_is_clean` FAILS**

Expected: 6 pass, `test_real_tree_is_clean` FAILS listing 45 keys. That failure is the bug, reproduced by the guard. Do not fix it here.

- [ ] **Step 5: Commit**

```bash
git add scripts/ci/settings_seed_value_drift_lint.py src/cofounder_agent/tests/unit/scripts/test_settings_seed_value_drift_lint.py
git commit -F -   # "feat(ci): settings seed value-drift ratchet (red against the current tree)"
```

---

### Task 2: Resolve the 30 code↔baseline conflicts

**Files:**

- Modify: `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql` (12 keys)
- Modify: `src/cofounder_agent/services/settings_defaults.py` (16 keys + 2 canonicalized)

**Interfaces:** none — data only.

**12 keys where CODE wins → edit `baseline.seeds.sql`:**

| key                                 | seed now         | →                   | why                                                                                   |
| ----------------------------------- | ---------------- | ------------------- | ------------------------------------------------------------------------------------- |
| `disable_auth_for_dev`              | `true`           | `false`             | public baseline must not ship one flag-flip from a bypass                             |
| `electricity_rate_kwh`              | `0.2579`         | `0.16`              | operator's EIA rate; `jobs/update_utility_rates.py` maintains the live value          |
| `enable_pyroscope`                  | `true`           | `false`             | optional `profiling` extra, no Windows wheels (#2133). Also resolves the brain 3-way. |
| `image_model`                       | `sdxl_lightning` | `z_image_turbo`     | #2386 bake-off; seed is stale                                                         |
| `newsletter_enabled`                | `true`           | `false`             | requires Resend                                                                       |
| `niche_ollama_chat_timeout_seconds` | `300`            | `300.0`             | canonicalize (parse-identical)                                                        |
| `podcast_tts_enabled`               | `true`           | `false`             | requires Speaches                                                                     |
| `podcast_tts_format`                | `wav`            | `mp3`               | **wav is unrecoverable corruption** (#1696/#1706)                                     |
| `rag_rerank_enabled`                | `true`           | `false`             | optional `rerank` extra (#2133)                                                       |
| `self_consistency_enabled`          | `true`           | `false`             | N× LLM calls; rail docstring says "Default off"                                       |
| `topic_dedup_engine`                | `word_overlap`   | `content_embedding` | seed is stale; prod agrees with code                                                  |
| `voice_agent_whisper_model`         | `medium`         | `base`              | consumer-safe                                                                         |

**16 keys where BASELINE wins → edit `settings_defaults.py`:**

| key                                 | code now                    | →                                      | why                                                                           |
| ----------------------------------- | --------------------------- | -------------------------------------- | ----------------------------------------------------------------------------- |
| `development_mode`                  | `''`                        | `false`                                | `''` is an AST-extraction artifact                                            |
| `enable_writer_self_review`         | `false`                     | `true`                                 | pure local compute, live graph_def node; posture rule doesn't apply           |
| `image_negative_prompt`             | `''`                        | _(the prompt)_                         | artifact; brain agrees with baseline                                          |
| `image_styles`                      | `''`                        | _(the JSON)_                           | `''` breaks style rotation (the #1700 bug)                                    |
| `langfuse_host`                     | `''`                        | `http://langfuse-web:3000`             | compose service DNS is correct for a containerized worker                     |
| `local_llm_api_url`                 | `http://localhost:11434`    | `http://host.docker.internal:11434`    | worker is containerized, Ollama is on the host                                |
| `max_approval_queue`                | `3`                         | `100`                                  | not a posture key; `3` is a stale artifact that would throttle fresh installs |
| **`monthly_spend_limit_usd`**       | `100.0`                     | **KEEP `100.0`**                       | **CORRECTED — see below.**                                                    |
| `niche_goal_descriptions`           | `''`                        | _(the JSON)_                           | artifact                                                                      |
| `niche_internal_rag_per_kind_limit` | `5`                         | `4`                                    | prod-validated tuning                                                         |
| `ragas_judge_model`                 | `''`                        | `ollama/phi4:14b`                      | artifact                                                                      |
| `self_consistency_threshold`        | `0.7`                       | `0.55`                                 | prod-validated tuning                                                         |
| `vision_alt_model`                  | `qwen3-vl:30b`              | `ollama/qwen3-vl:30b`                  | litellm-canonical form; prod agrees                                           |
| `voice_agent_livekit_url`           | `''`                        | `ws://livekit:7880`                    | compose service DNS                                                           |
| `voice_agent_ollama_url`            | `http://localhost:11434/v1` | `http://host.docker.internal:11434/v1` | as above                                                                      |
| `voice_agent_vad_stop_secs`         | `0.2`                       | `0.4`                                  | prod-validated tuning                                                         |

**CORRECTION to the spec — `monthly_spend_limit_usd` resolves to `100.0` (code), not `10.0`.**
The spec argued `10.0` on fail-closed grounds. That is wrong: `daily_spend_limit_usd` is **`2.0` in both** code and baseline, so a `10.0` monthly cap binds after 5 days — incoherent, not conservative. `100.0` is coherent (2.0 × 30 = 60 < 100) and correctly leaves the free tier's `20.00` _below_ the reference rather than above it. So this key moves to the **CODE-wins** list: edit `baseline.seeds.sql` `10.0` → `100.0` (making 13 seed edits, 15 defaults edits).

**2 keys canonicalized manually:**

- `tts_acronym_replacements` — semantically identical, whitespace-only drift. Adopt **code's** spaced form (`{"SOC": "security operations", ...}`) in both.
- `tts_pronunciations` — genuinely different: code has ~45 entries, baseline ~20; baseline uniquely has `pgvector`; `CI/CD` differs (`"See Eye See Dee"` vs `"CI CD"`). **Union**, preferring code's expansions, plus baseline's `pgvector`. Write the merged value to both.

- [ ] **Step 1: Apply the 13 `baseline.seeds.sql` edits**
- [ ] **Step 2: Apply the 15 `settings_defaults.py` edits**
- [ ] **Step 3: Canonicalize the 2 TTS JSON values in both files**
- [ ] **Step 4: Run the lint — code↔baseline half must now be clean**

Run: `<PY> scripts/ci/settings_seed_value_drift_lint.py`
Expected: no `code != baseline` violations remain; only brain violations (18) remain.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/services/migrations/0000_baseline.seeds.sql
git commit -F -   # "fix(settings): reconcile 30 code<->baseline default conflicts"
```

---

### Task 3: TIER_POLICY allowlist + the brain correctness fix

**Files:**

- Modify: `scripts/ci/settings_seed_value_drift_lint.py` (populate `TIER_POLICY`)
- Modify: `brain/seed_app_settings.json` (`pipeline_critic_model`)

**Interfaces:** Consumes `TIER_POLICY` from Task 1.

18 brain↔baseline conflicts triage into three buckets:

**Auto-resolved by Task 2 (1):** `enable_pyroscope` — baseline → `false` now matches brain. No action.

**Correctness fix (1):** `pipeline_critic_model` — brain seeds `ollama/gemma3:27b`, **not in the fleet** and ~17GB against the 8-16GB consumer target. Fix brain → `ollama/phi4:14b` (matches code + baseline). Note `pipeline_writer_model` / `pipeline_fallback_model` are _also_ `gemma3:27b` but agree across all three sources — that's Task/PR 3, not here.

**`TIER_POLICY` (16)** — free-tier limits, quality bars, and bootstrap identity placeholders:

| key                                                                                                               | brain (free)          | baseline (reference) |
| ----------------------------------------------------------------------------------------------------------------- | --------------------- | -------------------- |
| `daily_post_limit`                                                                                                | `1`                   | `4`                  |
| `max_posts_per_day`                                                                                               | `3`                   | `8`                  |
| `daily_spend_limit_usd`                                                                                           | `1.00`                | `2.0`                |
| `monthly_spend_limit_usd`                                                                                         | `20.00`               | `100.0`              |
| `max_approval_queue`                                                                                              | `10`                  | `100`                |
| `prometheus.threshold.monthly_spend_warning_usd`                                                                  | `15.0`                | `35.0`               |
| `qa_final_score_threshold`                                                                                        | `70`                  | `80`                 |
| `min_curation_score`                                                                                              | `70`                  | `75`                 |
| `content_validator_warning_reject_threshold`                                                                      | `3`                   | `5`                  |
| `site_name` / `site_url` / `site_domain` / `public_site_url` / `company_name` / `privacy_email` / `support_email` | runnable placeholders | `''`                 |

Each entry gets a one-line reason, e.g.:

```python
TIER_POLICY: dict[str, str] = {
    "daily_post_limit": "free tier ships a deliberately lower cap (brain seed _meta.tier=free)",
    "site_name": "brain seeds a runnable placeholder so `docker compose up` works; the reference seed leaves identity empty for the operator to set",
    ...
}
```

- [ ] **Step 1: Fix `pipeline_critic_model` in `brain/seed_app_settings.json`** → `ollama/phi4:14b`
- [ ] **Step 2: Populate `TIER_POLICY` with the 16 entries + reasons**
- [ ] **Step 3: Run the full lint — must be GREEN**

Run: `<PY> scripts/ci/settings_seed_value_drift_lint.py`
Expected: `settings-seed-value-drift: OK`

- [ ] **Step 4: Run the test suite — `test_real_tree_is_clean` now passes**

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/ci/settings_seed_value_drift_lint.py brain/seed_app_settings.json
git commit -F -   # "fix(settings): declare the free-tier divergences + repoint the dead critic pin"
```

---

### Task 4: Wire the lint into CI

**Files:** Modify `.github/workflows/migrations-smoke.yml`

Add a step next to the existing `settings_seed_drift_lint` invocation (grep for it to find the exact location and style — mirror it):

```yaml
- name: Settings seed value-drift ratchet
  run: python scripts/ci/settings_seed_value_drift_lint.py
```

- [ ] **Step 1: Locate the sibling lint's step** — `grep -n "settings_seed_drift_lint" .github/workflows/migrations-smoke.yml`
- [ ] **Step 2: Add the new step immediately after it**
- [ ] **Step 3: Verify YAML parses** — `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/migrations-smoke.yml'))"`
- [ ] **Step 4: Commit**

---

### Task 5: The two dangerous inline call-site defaults

**Files:**

- Modify: `src/cofounder_agent/modules/content/stages/generate_media_scripts.py:148`
- Modify: `src/cofounder_agent/services/image_service.py:187`
- Modify: `src/cofounder_agent/services/image_providers/_image_models.py:164`
- Test: `src/cofounder_agent/tests/unit/services/test_tts_service.py` (extend)

These are a _fifth_ disagreeing surface. Only the two dangerous ones are in scope — the full inline sweep is a separate epic (spec: Out of scope).

Current:

```python
# generate_media_scripts.py:148  — reintroduces the #1696/#1706 wav corruption
suffix = (sc.get("podcast_tts_format", "wav") if sc else "wav") or "wav"
# image_service.py:187 and _image_models.py:164 — stale model
model_name = site_config.get("image_model", "sdxl_lightning")
```

- [ ] **Step 1: Write a failing test** asserting `generate_media_scripts` uses `mp3` when the key is unset
- [ ] **Step 2: Run it — verify it fails** (currently yields `wav`)
- [ ] **Step 3: Change all three inline defaults** (`wav` → `mp3`, `sdxl_lightning` → `z_image_turbo`)
- [ ] **Step 4: Run the test + `test_tts_service.py` + image tests — all pass**
- [ ] **Step 5: Commit**

---

### Task 6: Docs

**Files:** `docs/operations/migrations.md`, `CLAUDE.md`, `docs/architecture/services/site_config.md`

- [ ] **Step 1: `docs/operations/migrations.md`** — add the squash runbook step:

> After a fold-forward baseline regeneration, run `python scripts/ci/settings_seed_value_drift_lint.py` and reconcile before merging. The regen captures live operator values and **will** red this check — that is the guard working, not a bug. Resolve each key toward the reference default (or add a `TIER_POLICY` entry with a reason).

- [ ] **Step 2: `CLAUDE.md`** — correct the settings-defaults claim. It currently says new keys belong in `settings_defaults.py`; that is true only for keys the baseline does **not** seed. Document the real precedence:

> **Precedence (fresh install):** `brain/seed_app_settings.json` (81, free tier) > `0000_baseline.seeds.sql` (692) > `settings_defaults.py` (734) — all `ON CONFLICT DO NOTHING`, first writer wins. The brain seeds first because `worker` declares `depends_on: brain-daemon: service_healthy`; via `poindexter setup` the migrations run first instead, so the brain seed no-ops. New keys go in `settings_defaults.py`; a key the baseline also seeds must have **identical** values in both — enforced by `scripts/ci/settings_seed_value_drift_lint.py`.

Also correct the stale key count (CLAUDE.md says 1,090; prod has **1,242**).

- [ ] **Step 3: `docs/architecture/services/site_config.md`** — document the three-source precedence.
- [ ] **Step 4: Commit**

---

### Task 7: Full verification + PR

- [ ] **Step 1: Full unit suite for touched areas**

```bash
<PY> -m pytest <ABS>/tests/unit/scripts/ <ABS>/tests/unit/services/test_tts_service.py -o addopts="" -q
```

- [ ] **Step 2: All static lints**

```bash
<PY> scripts/ci/settings_seed_value_drift_lint.py   # new ratchet
<PY> scripts/ci/settings_seed_drift_lint.py         # sibling must stay green
<PY> scripts/ci/migrations_lint.py
git add -A && <PY> scripts/ci/check_public_mirror_safety.py   # AFTER git add
```

- [ ] **Step 3: Confirm prod is untouched** — spot-check that live values for a few reconciled keys are unchanged (they must be: `ON CONFLICT DO NOTHING`).

```bash
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -t -A -c \
  "SELECT key, value FROM app_settings WHERE key IN ('podcast_tts_format','image_model','enable_pyroscope');"
```

- [ ] **Step 4: Push + open PR** against `Glad-Labs/glad-labs-stack`, `Closes` the tracking issue.
- [ ] **Step 5: Monitor CI; merge when green** (`feedback_ci_is_the_review_gate`). Verify merge by `gh pr view --json state`, not exit code.

---

## Follow-on (not this plan)

- **PR 3 — free-tier revalidation:** `pipeline_writer_model`/`pipeline_fallback_model` off `gemma3:27b` (unanimous-but-dead, ~17GB vs the 8-16GB target); retire the `pipeline.stages.order` fossil (zero readers) from both seed sources; `image_generation_model` (brain-only `sdxl_lightning`); a sibling pinned-model-exists check; `seed_loader.py` docstring correction; refresh `_meta.last_updated`.
- **PR 4 — site_config:** drop `default: str = ""` from `SiteConfig.get`; grow `REQUIRED_KEYS` and route to `require()`. See the spec's Follow-up section.
