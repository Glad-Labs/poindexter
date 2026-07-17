# app_settings seed reconciliation + drift ratchet

**Date:** 2026-07-17
**Status:** design — pending review
**Related:** poindexter#819 (same class, fixed as a one-off), stack#2133, #1696/#1706, #2386, #1869 (Phase F squash), #1700

## Problem

Three files seed `app_settings`, all with `INSERT ... ON CONFLICT (key) DO NOTHING`.
They disagree on **46 keys**. Because first-writer-wins, the loser's value is
unreachable on a fresh install — including values that CLAUDE.md documents as
deliberate.

This is the second occurrence of the class. poindexter#819 (2026-07-02) fixed
`rag_source_filter` ('' vs 'posts') one key at a time and its own body named the
missing guard:

> `settings_seed_drift_lint` only catches re-seeded deleted keys, **not value
> drift between the two seed sources** — which is how this slipped through the
> baseline squash.

The guard was never built. #819's drift caused a real incident (unfiltered RAG
corpus → ops-log transcripts reproduced into drafts). Fixing 46 more keys without
the guard just resets the clock.

## Findings

### 1. The real precedence is three-deep, and it varies by install path

`brain/seed_app_settings.json` (81 keys) is applied by the brain daemon as
"boot-controller phase 1". It calls `CREATE TABLE IF NOT EXISTS app_settings`
itself (`brain/seed_loader.py::_ensure_app_settings_table`), so there is no
chicken-and-egg with migrations. And `docker-compose.local.yml` /
`docker-compose.consumer.yml` both declare `worker → depends_on: brain-daemon:
service_healthy`.

| install path                         | order                                                         | effective precedence                                                         |
| ------------------------------------ | ------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `docker compose up` on an empty DB   | brain boots first, seeds 81 keys; worker then runs migrations | **brain > baseline > code**                                                  |
| `poindexter setup` (documented flow) | migrations + `seed_all_defaults` run before any container     | **baseline > code** (brain no-ops: table non-empty, `REQUIRED_KEYS` present) |

**Fresh-install values currently depend on which install path the operator took.**
That is the deepest bug here — it is not reproducible drift, it is
nondeterministic-by-topology.

### 2. The drift runs in two directions — the squash is the injector

The Phase F/G squashes regenerate `baseline.seeds.sql` **fold-forward from a live
DB**, so they capture whatever prod held that day, overwriting hand-maintained
decisions in either direction:

| key                    | code                | baseline         | live prod           | direction                      |
| ---------------------- | ------------------- | ---------------- | ------------------- | ------------------------------ |
| `electricity_rate_kwh` | `0.16`              | `0.2579`         | `0.2579`            | squash captured operator state |
| `image_model`          | `z_image_turbo`     | `sdxl_lightning` | `z_image_turbo`     | squash froze a **stale** value |
| `podcast_tts_format`   | `mp3`               | `wav`            | `mp3`               | squash froze a **stale** value |
| `topic_dedup_engine`   | `content_embedding` | `word_overlap`   | `content_embedding` | squash froze a **stale** value |

Git history confirms the mechanism: #1700 set both files to `z_image_turbo`; the
Phase F squash (`9576dcb01`) regenerated the seed from a DB still holding
`sdxl_lightning`; #2386 then updated only `settings_defaults.py`.

**Consequence for design:** no blanket "code wins" or "seed wins" rule is correct.
Each key needs a reason.

### 3. `brain/seed_app_settings.json` is an intentional product tier, not drift

Its `_meta` block:

```json
{"name": "Poindexter core seed", "tier": "free", "version": "1",
 "last_updated": "2026-04-18",
 "description": "Free-tier starter settings... minimum viable config to make the
  pipeline runnable. For the optimized production seed (site-specific models,
  tuned QA workflows, image styles), see the premium Quick Start Guide..."}
```

Its lower values (`daily_post_limit` 1 vs 4, `max_posts_per_day` 3 vs 8,
`qa_final_score_threshold` 70 vs 80) are the **free/paid product boundary**.
Forcing three-way agreement would collapse the free tier into the operator
profile and erase the gap `feedback_prompt_quality_gap` and
`project_monetization` depend on.

Caveat: `poindexter premium activate` (`poindexter/cli/premium.py`) sets
`premium_*` keys only — it applies **no seed overlay**. The tier split is real;
`seed_loader.py`'s docstring claim that "the CLI applies [an overlay] on top" is
inaccurate and gets corrected.

### 4. Severe: `podcast_tts_format` seeds `wav`

Per #1696/#1706, `wav` is the **unrecoverable** Speaches failure mode — only the
first segment's RIFF header is valid and `ffmpeg -c copy` remux _cannot_ repair
it (it reads the first RIFF chunk and truncates). This single TTS path feeds both
the podcast episode and the video narration track. **Fresh installs ship silently
truncated audio on both.** `modules/content/stages/generate_media_scripts.py:148`
independently carries `sc.get("podcast_tts_format", "wav")` as an inline fallback
— a fifth disagreeing value, inside production code.

### 5. `sentry_dsn` leaks an operator credential into the public repo

`baseline.seeds.sql:593` seeds
`sentry_dsn = 'http://31fbc77a-…@glitchtip-web:8000/1'` — the operator's real
GlitchTip DSN. It is `is_secret=false`, which is how it survived the squash's
secret filter; `scripts/sync-to-github.sh` does not strip `baseline.seeds.sql`,
and `scripts/ci/check_public_mirror_safety.py` has no DSN pattern.

**Severity, stated accurately:** the host `glitchtip-web` is a compose-internal
DNS name, so the DSN is not externally postable — this is not a live credential
compromise. The functional harm is the real one: CLAUDE.md documents the Sentry
SDK as auto-initialising _whenever `sentry_dsn` is set_, so **every fresh OSS
install boots an SDK shipping errors at a container that does not exist for
them.** The brain seed already has `''` correctly.

### 6. Agreement is not correctness — a real class, but my headline example was wrong

> **CORRECTION (2026-07-17, post-merge).** This finding originally claimed
> `pipeline_writer_model` / `pipeline_fallback_model` = `ollama/gemma3:27b` was
> "unanimous but dead" and used it to justify a new sibling pinned-model check.
> **That was false and is retracted.** `gemma3:27b` is the **sanctioned OSS
> default**, chosen deliberately in stack#2018 (2026-06-30) _because_ it is
> publicly pullable; the README documents `ollama pull gemma3:27b` and rates it
> "16 GB — Writer, fallback." Its unanimity across all three sources is
> **correct**. I conflated "not in Matt's fleet" with "dead": Matt's fleet runs
> `gemma-4-31B-it-qat` via the **operator overlay** (`operator_overrides.
OPERATOR_MODEL_PINS`, stripped from the mirror, conditional-upsert over the OSS
> base) — the two-tier split working as designed, documented in
> `project-oss-vs-operator-model-defaults`. No writer/fallback swap is warranted.

The underlying principle still holds — a value unanimous across all sources
passes the ratchet without the ratchet vouching for its _correctness_ — but:

- **The sibling check already exists.** `tests/unit/services/test_oss_seed_model_hygiene.py`
  guards the real failure (an operator-private tag leaking into the OSS seeds);
  its `_OPERATOR_PRIVATE_MODEL_TAGS` list deliberately excludes `gemma3:27b`. No
  new check is needed — see `feedback_trace_orphaned_mechanisms_before_designing_fix`.
- **The one genuine unanimous-but-dead value is `pipeline.stages.order`** — seeded
  by baseline + brain with the _pre-atom_ stage list (`generate_content`,
  `cross_model_qa`, `replace_inline_images` — all deleted at the #355 atom
  cutover) and with **zero readers** (the StageRunner that consumed it was
  deleted 2026-05-16). That's a dead seed row, retired in the follow-up cleanup,
  not a model-pin problem.

So the ratchet is necessary but not sufficient — the existing hygiene test, not a
new one, covers the model half; the follow-up removes the one true fossil.

## Design

### Resolution rule

Two sources encode _the same thing_; one encodes _a different thing_:

- `settings_defaults.py::DEFAULTS` and `0000_baseline.seeds.sql` are two encodings
  of **the reference default**. They must be **byte-identical** on every
  overlapping key. There is no case where they should legitimately differ.
- `brain/seed_app_settings.json` is **the free tier**. It may differ — but only
  where the divergence is declared and reasoned.

This collapses 46 ad-hoc calls into one rule:

```
code == baseline                       ALWAYS (26 conflicts must resolve)
brain may differ  IFF  key in TIER_POLICY   (20 conflicts triaged)
```

Where a `brain` divergence is _not_ tier policy, it is a correctness bug and the
brain seed is fixed to match.

### Per-key resolution (26 code↔baseline)

Each resolution names its reason. "Minimal / opt-in" posture per operator
decision: OFF where a key requires external infrastructure, an optional poetry
extra, or exceeds the consumer VRAM target.

| key                                 | resolve to                             | why                                                                                                                                                                                         |
| ----------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enable_pyroscope`                  | `false`                                | optional `profiling` extra, no Windows wheels (#2133). Brain already agrees.                                                                                                                |
| `rag_rerank_enabled`                | `false`                                | optional `rerank` extra / sentence-transformers (#2133)                                                                                                                                     |
| `disable_auth_for_dev`              | `false`                                | defense-in-depth; not a live vuln (needs `development_mode` too, #1219) but the public baseline should not ship one flag-flip from a bypass                                                 |
| `development_mode`                  | `false`                                | baseline wins; code `''` is an AST-extraction artifact                                                                                                                                      |
| `podcast_tts_format`                | `mp3`                                  | `wav` is unrecoverable corruption (#1696/#1706)                                                                                                                                             |
| `image_model`                       | `z_image_turbo`                        | #2386 bake-off; seed is stale, prod agrees with code                                                                                                                                        |
| `topic_dedup_engine`                | `content_embedding`                    | seed is stale, prod agrees with code                                                                                                                                                        |
| `electricity_rate_kwh`              | `0.16`                                 | `0.2579` is operator state; `jobs/update_utility_rates.py` maintains the live value from EIA. Matches both inline call-site defaults.                                                       |
| `local_llm_api_url`                 | `http://host.docker.internal:11434`    | **baseline wins** — worker is containerized, Ollama is on the host; code's `localhost` only works host-run                                                                                  |
| `voice_agent_ollama_url`            | `http://host.docker.internal:11434/v1` | as above                                                                                                                                                                                    |
| `langfuse_host`                     | `http://langfuse-web:3000`             | **baseline wins** — compose service DNS is correct for a containerized worker                                                                                                               |
| `voice_agent_livekit_url`           | `ws://livekit:7880`                    | as above                                                                                                                                                                                    |
| `image_negative_prompt`             | _(the prompt)_                         | **baseline wins** — code `''` is an extraction artifact; brain agrees with baseline                                                                                                         |
| `image_styles`                      | _(the JSON)_                           | **baseline wins** — `''` would break style rotation outright (the bug #1700 fixed)                                                                                                          |
| `niche_goal_descriptions`           | _(the JSON)_                           | **baseline wins** — `''` is an artifact                                                                                                                                                     |
| `ragas_judge_model`                 | `ollama/phi4:14b`                      | **baseline wins** — `''` is an artifact                                                                                                                                                     |
| `vision_alt_model`                  | `ollama/qwen3-vl:30b`                  | litellm-canonical form; prod agrees with baseline                                                                                                                                           |
| `newsletter_enabled`                | `false`                                | requires Resend config                                                                                                                                                                      |
| `podcast_tts_enabled`               | `false`                                | requires a Speaches service                                                                                                                                                                 |
| `voice_agent_whisper_model`         | `base`                                 | consumer-safe; `medium` is an operator upgrade                                                                                                                                              |
| `self_consistency_enabled`          | `false`                                | N× LLM calls per rail — heavy for 8–16 GB; the rail's own docstring says "Default off"                                                                                                      |
| `enable_writer_self_review`         | `true`                                 | **baseline wins** — pure local compute, one extra call, and a live `writer_self_review` node in the graph_def. No external dep ⇒ posture rule does not apply.                               |
| `self_consistency_threshold`        | `0.55`                                 | prod-validated tuning, not posture                                                                                                                                                          |
| `niche_internal_rag_per_kind_limit` | `4`                                    | prod-validated tuning                                                                                                                                                                       |
| `voice_agent_vad_stop_secs`         | `0.4`                                  | prod-validated tuning                                                                                                                                                                       |
| `niche_ollama_chat_timeout_seconds` | `300.0`                                | canonicalize; `300` vs `300.0` parse identically                                                                                                                                            |
| `tts_acronym_replacements`          | _(canonical JSON)_                     | semantically identical, whitespace-only drift — pick one serialization                                                                                                                      |
| `tts_pronunciations`                | _(union)_                              | genuinely different content: code has ~45 entries, baseline ~20; baseline uniquely has `pgvector`; `CI/CD` differs (`"See Eye See Dee"` vs `"CI CD"`). Merge, preferring code's expansions. |

Two keys are 3-way (code + baseline + brain). Their code↔baseline halves resolve
under the same rule; brain's value stays as declared tier policy:

| key                       | code    | baseline | brain   | code↔baseline resolves to | why                                                                                                                                                                                                                                                                |
| ------------------------- | ------- | -------- | ------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `max_approval_queue`      | `3`     | `100`    | `10`    | **`100`**                 | not a posture key (no external dep, no VRAM cost) — it caps how many posts may sit awaiting approval. `100` is prod-validated; code's `3` is a stale extraction artifact that would throttle a fresh install to 3 queued posts. Brain's `10` is the free-tier cap. |
| `monthly_spend_limit_usd` | `100.0` | `10.0`   | `20.00` | **`10.0`**                | a _safety_ cap, so the minimal posture means fail-closed (`feedback_no_paid_apis`: local default, cloud behind cost_guard caps). Brain's `20.00` is the free-tier cap.                                                                                             |

### `TIER_POLICY` allowlist (brain divergences that are intentional)

Mirrors the existing `ALLOWLIST` pattern in `settings_seed_drift_lint.py` — a
dict of `key → one-line reason`. Covers the free-tier limits
(`daily_post_limit`, `max_posts_per_day`, `daily_spend_limit_usd`,
`monthly_spend_limit_usd`, `max_approval_queue`,
`prometheus.threshold.monthly_spend_warning_usd`), the free-tier quality bars
(`qa_final_score_threshold`, `min_curation_score`,
`content_validator_warning_reject_threshold`), and the bootstrap identity
placeholders (`site_name`, `site_url`, `site_domain`, `public_site_url`,
`company_name`, `privacy_email`, `support_email` — brain ships runnable
placeholders, baseline ships `''`).

**The allowlist is the deliverable, not a workaround.** It makes the free/paid
boundary explicit in code instead of implicit in a JSON nobody re-reads, so the
next squash cannot silently collapse the tier either.

### The ratchet — `scripts/ci/settings_seed_value_drift_lint.py`

New sibling to `settings_seed_drift_lint.py` (which stays — it guards a different
failure: migration-deleted keys that a seed resurrects).

- Static only: AST-parses `DEFAULTS`, regex-parses the seed SQL, JSON-parses the
  brain seed. No DB, no project imports — same constraint as its sibling, so it
  runs in CI.
- Asserts `DEFAULTS[k] == baseline[k]` for every overlapping key.
- Asserts `brain[k] == baseline[k]` for every overlapping key **unless** `k` is in
  `TIER_POLICY`.
- Fails with the key, all three values, and the remediation.
- Wired into the required `migrations-smoke` check, alongside its sibling.

**Comparison is exact-string, not normalized.** Precedent:
`reference_prompt_fallback_drift_guard_newline` uses an exact-`==` guard for the
same "two encodings of one value" problem. Normalizing JSON/floats would let
`300` vs `300.0` and whitespace-only JSON drift persist, which is precisely the
noise that hides a real diff. Exact-match forces one canonical serialization; the
cost is that reformatting one file reds CI until the other matches — that is the
guard working.

**Known interaction with the next squash:** a fold-forward regeneration will
reintroduce operator values and the ratchet will red the squash PR. That is
intended and is the whole point — but it means the squash runbook needs a step.
`docs/operations/migrations.md` gets it.

### Sibling check — already exists (Finding 6, corrected)

> **CORRECTION.** This section originally proposed a new "pinned models must
> exist" check. It is not needed: `tests/unit/services/test_oss_seed_model_hygiene.py`
> already guards the real failure (an operator-private model tag leaking into the
> OSS seeds). The value that prompted this — `gemma3:27b` — is the correct OSS
> default, not a dead pin (see Finding 6). No new sibling check is built.

## Scope / PR split

**PR 1 — `sentry_dsn` leak (fast, independent).**
`sentry_dsn` → `''` in `baseline.seeds.sql`; add a DSN pattern to
`check_public_mirror_safety.py` so the `is_secret=false` mechanism cannot leak the
next one; test. Ships without waiting on the 46-key review.

**PR 2 — reconciliation + ratchet.**
The 26 code↔baseline resolutions; brain correctness fixes (`pipeline_critic_model`
→ `ollama/phi4:14b`, `enable_pyroscope` already correct); `TIER_POLICY` allowlist;
the new lint + CI wiring; the two dangerous inline call-site defaults
(`generate_media_scripts.py` `wav`, `image_service.py`/`_image_models.py`
`sdxl_lightning`); docs.

**PR 3 — follow-up cleanup (scope corrected 2026-07-17).**
The originally-planned "free-tier profile revalidation" evaporated: its
centerpiece (swap `pipeline_writer_model` / `pipeline_fallback_model` off
`gemma3:27b`) rested on the false premise retracted in Finding 6 — `gemma3:27b`
is the correct OSS default and stays. The sibling check already exists. What
actually remains is minor cleanup: retire the `pipeline.stages.order` fossil
(zero readers, pre-atom stage list) from both seed sources; fix the
`seed_loader.py` docstring (it claims `premium activate` applies a seed overlay
— it does not, it only sets `premium_*` keys); fix stale prose in
`anti-hallucination.md` (says the critic default is `gemma3:27b`; it is
`phi4:14b`). (`image_generation_model` was already consistent; the brain-seed
mojibake fix and the duplicate-`ImageModel`-enum consolidation are spawned as
their own tasks.)

## Testing

- `tests/unit/scripts/test_settings_seed_value_drift_lint.py` — clean tree passes;
  synthetic code↔baseline divergence fails; synthetic brain divergence fails;
  a `TIER_POLICY` key diverging passes; allowlisting a _non_-diverging key fails
  (stale-entry guard, so the allowlist cannot rot).
- The lint runs green against the real tree as the reconciliation's own
  acceptance test — it is the assertion that all 46 are resolved.
- `test_check_public_mirror_safety.py` — DSN pattern catches a seeded DSN,
  ignores `''` and a bare `sentry_dsn` key name.
- Existing `tests/unit/services/test_tts_service.py` already asserts the mp3
  default; extend to the `generate_media_scripts` inline path.

## Docs

- `docs/operations/migrations.md` — the squash runbook step: after a fold-forward
  regeneration, run the value-drift lint and reconcile before merging.
- `CLAUDE.md` — correct the "new `app_settings` keys belong in
  `settings_defaults.py`" claim, which is true for _new_ keys but silently false
  for the 337 keys the baseline also seeds. State the precedence and the rule.
- `docs/architecture/services/site_config.md` — the three-source precedence.

## Out of scope

- **Sweeping every inline `site_config.get(key, default)` call site.** A fourth
  disagreeing surface, and a real epic. Only the two dangerous ones are fixed
  here (both are Finding 4 / Finding 2 keys).
- **Generating one seed source from the other.** More durable, but the operator
  decision is agreement + a guard, not deleting a seed home
  (`feedback_seed_data_in_baseline_not_new_migrations` treats both as legitimate).
  The ratchet makes the duplication enforced-consistent, which buys most of it.
- **Re-tiering the free profile's limits.** PR 3 revalidates for _correctness_
  (dead pins, fossils); the `daily_post_limit=1` style limits are product
  decisions and stay as-is.

## Follow-up (decided 2026-07-17, separate from PRs 1–3)

The operator asked whether to go further: **no defaults in code at all, every
missing config fails loud.** Decided against, in favour of a narrower fix — the
reasoning is worth recording because the instinct was right and the lever wasn't.

**"No defaults" conflates two orthogonal questions.** _Where does a default live?_
(single-source-of-truth) and _may this key be absent?_ (required-vs-optional) do
not trade against each other. You can have one home for defaults AND fail-loud on
the ~20 keys that matter.

**The fail-loud primitive already exists and is 5% adopted.** `SiteConfig.require()`
raises `RuntimeError` with remediation; `get()`'s docstring already points at it
("For optional settings only. Use require() for required settings"). Adoption:
**17 production callers vs 356 `get(key, default)` call sites.** Per
`feedback_trace_orphaned_mechanisms_before_designing_fix`, re-wire it rather than
build a new mechanism. `seed_loader.REQUIRED_KEYS` (9 boot-critical keys) is
already the required/optional taxonomy in embryo.

**The real defect is `get()`'s signature:**

```python
def get(self, key: str, default: str = "") -> str:
```

`''` is the unset sentinel (`feedback_app_settings_value_not_null`), so a bare
`get("foo")` on a **missing** key returns `''` — indistinguishable from a key
present-and-deliberately-empty. Nothing _can_ fail loud, because the signature
erases the distinction before the caller sees it. Structurally identical to the
`self_consistency` fail-open fixed in #2655 the same day: _if the neutral value
you return on failure is inside the value's own domain, it's a lie the
type-checker can't catch._ Note `SettingsService.get` already has the right shape
(`default: str | None = None -> str | None`) — the two config seams disagree, and
the one reached through DI is the lossy one.

**Why full no-defaults backfires:** 1,242 live keys (CLAUDE.md's 1,090 is stale),
most of them `*.threshold.*` tuning knobs where a default is correct; it removes
the runnable free tier (`brain/seed_app_settings.json`'s entire purpose); and a
_seeded row_ is discoverable from the console / MCP / Grafana while a _code
default_ is invisible to all of them — deleting `settings_defaults.py` would make
~397 keys undiscoverable without grepping source, against
`feedback_design_for_llm_consumers`.

**Decided scope (PR 4, after PRs 2–3):**

1. Drop `default: str = ""` from `SiteConfig.get` so a caller must either pass an
   explicit default (declaring the key optional) or call `require()`. Lint-enforceable.
2. Grow `REQUIRED_KEYS` deliberately and route those keys to `require()`.
3. **Deferred:** merging `settings_defaults.py` into the baseline seed. They are
   not redundant — 734 vs 692 keys, only 337 overlapping — so neither generates
   cleanly from the other today. Revisit after 1–2. This would obsolete the
   code↔baseline half of the ratchet, which is an argument for landing the
   ratchet first: it makes the 337-key overlap safe to reason about meanwhile.

**Also fix while there:** `settings_defaults.py`'s docstring claims unseeded keys
are "inserted lazily by SettingsService the first time the worker queries them."
That is stale — `SettingsService.get` does not insert. It is the stated rationale
for the module's existence, so it should not stay wrong.

## Risk

**Matt's prod is untouched.** All three seeders are `ON CONFLICT DO NOTHING` and
all 46 rows already exist in the live DB, so no value is rewritten. The blast
radius is fresh installs only — which is exactly the population that is currently
broken.
