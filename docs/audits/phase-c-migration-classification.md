# Phase C migration flatten — classification

**Date:** 2026-05-28
**Phase:** C (61 timestamped migrations → baseline)
**Predecessor:** Phase A (169 legacy migrations → baseline, 2026-05-08, Glad-Labs/poindexter#30)

Each timestamped migration under `src/cofounder_agent/services/migrations/`
was classified into one of:

- **schema** — pure DDL (CREATE/ALTER/DROP TABLE/COLUMN/INDEX/CONSTRAINT).
  Captured by the `pg_dump --schema-only` of `flatten_old`; lands in
  `0000_baseline.schema.sql` automatically.
- **seed** — pure idempotent INSERT into `app_settings` / `qa_gates` /
  `niches` / `content_validator_rules`. Captured by `pg_dump --data-only`
  of the seed tables on `flatten_old`; lands in `0000_baseline.seeds.sql`
  via the convert-seeds script (drops serial-id + timestamps for
  `app_settings`, preserves UUID-keyed inserts elsewhere).
- **data_scrub** — one-shot data mutation against existing rows
  (`regexp_replace`, value rewrite, etc.). Prod already ran these; they
  do NOT need to land in the new baseline since a fresh DB never had
  the legacy data to scrub.
- **mixed** — schema + seed (split between the two SQL files via the
  pg_dump capture).

The schema-only and seed-only captures inherently include the cumulative
effect of every schema and seed migration; data_scrubs leave no trace
in the dump because they only mutated existing rows.

## Classification table

| File                                                                                                | Class         |
| --------------------------------------------------------------------------------------------------- | ------------- |
| 20260509_125415_unify_approval_history.py                                                           | schema        |
| 20260509_130047_drop_pipeline_reviews.py                                                            | schema        |
| 20260509_175447_add_publishing_adapters.py                                                          | schema + seed |
| 20260509_203928_seed_cost_tier_model_mappings.py                                                    | seed          |
| 20260509_220000_seed_lane_b_misc_keys.py                                                            | seed          |
| 20260509_222554_seed_thinking_model_substrings.py                                                   | seed          |
| 20260510_013927_recreate_experiments_with_key_schema.py                                             | schema        |
| 20260510_014520_add_posts_id_gen_random_uuid_default.py                                             | schema        |
| 20260510_022034_seed_deepeval_g_eval_and_faithfulness.py                                            | seed          |
| 20260510_030530_seed_guardrails_rails.py                                                            | seed          |
| 20260510_032959_seed_ragas_rail.py                                                                  | seed          |
| 20260510_040315_seed_rag_engine_master_switch.py                                                    | seed          |
| 20260510_044707_seed_default_template_slug.py                                                       | seed          |
| 20260510_065631_drop_experiments_tables.py                                                          | schema        |
| 20260510_073230_fix_docker_port_forward_watch_list.py                                               | data_scrub    |
| 20260510_091348_seed_backup_watcher_sentinel_dir.py                                                 | seed          |
| 20260510_145955_add_host_ports_to_watch_list.py                                                     | seed/scrub    |
| 20260510_150600_extend_url_probe_skip_keys.py                                                       | seed/scrub    |
| 20260510_152609_url_probe_per_target_overrides.py                                                   | seed          |
| 20260510_182824_seed_prefect_cutover_flag.py                                                        | seed          |
| 20260512_032900_redact_leaked_app_settings_secrets.py                                               | data_scrub    |
| 20260512_125846_create_sensor_samples_register_corsair_csv_tap.py                                   | schema + seed |
| 20260512_182304_seed_igdb_topic_source.py                                                           | seed          |
| 20260512_213806_seed_unresolved_placeholder_validator_rule.py                                       | seed          |
| 20260512_215741_seed_discord_bot_health_probe_settings.py                                           | seed          |
| 20260512_220316_seed_mcp_http_probe_settings.py                                                     | seed          |
| 20260513_161559_default_prefect_orchestration_to_true.py                                            | data_scrub    |
| 20260513_181343_create_module_schema_migrations_table_for_module_v1_phase_2.py                      | schema        |
| 20260516_091017_seed_scene_visuals_max_concurrent.py                                                | seed          |
| 20260516_201048_refresh_post_stage4_descriptions.py                                                 | data_scrub    |
| 20260519_134736_niches_default_media_to_generate.py                                                 | schema + seed |
| 20260519_191744_backfill_posts_word_count_reading_time.py                                           | data_scrub    |
| 20260519_211809_niches_default_template_slug.py                                                     | schema        |
| 20260520_003551_backfill_posts_featured_image_data.py                                               | data_scrub    |
| 20260520_011341_extend_operator_url_probe_skip_keys_with_mcpvoice_double_page_surfaces.py           | seed/scrub    |
| 20260520_091534_bump_alert_dedup_suppress_window_to_120m_for_grafana_1h_repeat_interval.py          | seed/scrub    |
| 20260520_140806_discord_ops_row_use_secret_key_ref_instead_of_embedded_url.py                       | data_scrub    |
| 20260520_172353_dev_diary_niche_prompt_post_content_use_public_poindexter_repo_not_private_stack.py | data_scrub    |
| 20260520_174023_strip_remaining_autolink_style_glad_labs_stack_urls_from_dev_diary_posts.py         | data_scrub    |
| 20260520_175633_strip_inline_markdown_links_to_glad_labs_stack_from_dev_diary_posts.py              | data_scrub    |
| 20260522_212507_lower_glitchtip_triage_alert_threshold_to_surface_novel_issues.py                   | seed/scrub    |
| 20260526_135306_seed_prefect_stuck_flow_probe_app_settings.py                                       | seed          |
| 20260526_165828_seed_ops_triage_writer_model_gemma3_non_thinking.py                                 | seed          |
| 20260526_214206_add_video_server_url_to_operator_url_probe_target_overrides.py                      | seed/scrub    |
| 20260527_015444_rename_cost_guard_keys_and_drop_orphan_daily_budget.py                              | seed + scrub  |
| 20260527_024058_silence_openclaw_probe_pending_upstream_fix.py                                      | seed/scrub    |
| 20260527_034229_seed_sdxl_enabled_app_setting.py                                                    | seed          |
| 20260527_134748_drop_orphan_video_scene_visuals_max_concurrent_app_setting_after_stage_deletion.py  | data_scrub    |
| 20260527_180559_add_unsubscribe_token_to_newsletter_subscribers.py                                  | schema        |
| 20260527_183209_add_retry_count_to_pipeline_tasks_for_stale_sweep.py                                | schema        |
| 20260527_233118_create_media_approvals_table_for_per_medium_distribution_gate.py                    | schema        |
| 20260528_001023_add_quality_signals_columns_to_media_approvals.py                                   | schema        |
| 20260528_003606_add_video_shot_list_column_to_posts.py                                              | schema        |
| 20260528_021920_backfill_pipeline_task_id_on_posts_metadata.py                                      | data_scrub    |
| 20260528_040918_repoint_video_server_url_to_slideshow_port.py                                       | data_scrub    |
| 20260528_090821_seed_prefect_pending_flow_threshold.py                                              | seed          |
| 20260528_131401_scrub_private_repo_urls_from_dev_diary_posts_recurrence_after_2026_05_20_strip.py   | data_scrub    |
| 20260528_135212_retire_deterministic_compositor_dead_writer_rag_modes.py                            | data_scrub    |
| 20260528_163748_drop_writer_rag_mode_columns_retire_writer_rag_modes_namespace.py                   | schema        |
| 20260528_165428_drop_dead_giteaforgejo_schema_post_decommission_cleanup.py                          | schema        |
| 20260528_170956_drop_dead_giteaforgejo_schema_phase_a2_single_word_names.py                         | schema        |

## Capture method

```bash
# 1. apply the existing baseline + 61 timestamped migrations to flatten_old
DATABASE_URL="postgres://.../flatten_old" python scripts/ci/migrations_smoke.py

# 2. dump the resulting schema
pg_dump --schema-only --no-owner --no-privileges flatten_old > dump_old.sql

# 3. sanitize for baseline conventions (CREATE TABLE -> IF NOT EXISTS,
#    DROP TRIGGER IF EXISTS scaffolding, header/footer strip, etc.)
python tmp/sanitize_dump.py dump_old.sql 0000_baseline.schema.sql

# 4. dump the seed-table rows + convert to ON CONFLICT DO NOTHING idempotency
pg_dump --data-only --column-inserts --no-owner --no-privileges \
    -t app_settings -t qa_gates -t niches \
    -t content_validator_rules -t niche_goals -t niche_sources \
    flatten_old > seeds_old.sql
python tmp/convert_seeds.py seeds_old.sql 0000_baseline.seeds.sql

# 5. apply the new flattened baseline alone to flatten_new and parity-check
DATABASE_URL="postgres://.../flatten_new" python scripts/ci/migrations_smoke.py
pg_dump --schema-only --no-owner --no-privileges flatten_new > dump_new.sql
diff <(python tmp/normalize_for_diff.py dump_old.sql -) \
     <(python tmp/normalize_for_diff.py dump_new.sql -)
# expected: zero diff (parity holds)
```

## Parity result

- Schema dump (normalized): **0 diff lines** between `flatten_old` and `flatten_new`.
- `app_settings` seeded rows (key,value,category,description,is_secret,is_active): **0 diff lines** (695 rows each side).
- `qa_gates` seeded rows (by name): **0 diff lines** (12 rows each side).
- `content_validator_rules` seeded rows (by name): **0 diff lines** (23 rows each side).
- `niches` seeded rows (by slug,name,active): **0 diff lines** (1 row each side).

The 61 timestamped migration files were deleted with `git rm`; the
`schema_migrations` table on a fresh DB now records exactly one applied
migration (`0000_baseline.py`) instead of 62.
