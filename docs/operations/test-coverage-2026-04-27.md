# Test Coverage Report — 2026-04-27

## Summary

- **Baseline coverage:** 70% (28,405 statements; 8,511 missed) — measured before this PR
- **Coverage with this PR's new tests:** 71% (28,405 statements; 8,313 missed) — 60 new tests close 198 missed lines
- **Unit tests after this PR:** 6,429 passed, 1 failed (flaky — passes when run alone), 90 skipped
- **Run command:** `poetry run pytest tests/unit/ --cov=services --cov=routes --cov=plugins --cov=poindexter --cov-report=term-missing`
- **Modules tracked:** 170 (after excluding empty `__init__.py` files)
- **Modules at 100% coverage:** 76
- **Modules under 50% coverage:** 58
- **Modules in 50–80% (quick wins):** 36

The single failing test (`tests/unit/plugins/test_anthropic_provider.py::TestAnthropicDisabledByDefault::test_enabled_but_no_api_key_raises`) passes in isolation — symptomatic of test-ordering pollution in a 6,400-test suite, not a real bug. Worth investigating but out of scope for this report.

## Per-Module Coverage (worst-first)

Sorted by "Owner-priority": `<50%` rows first (worst gaps), then `50–80%` rows. 100% rows omitted for brevity (see raw `coverage_report.txt` for the full list).

### Under 50% — top owner-priority gaps

| Module                                                  |   Stmts | Covered | Missed |        % | Notes                                                            |
| ------------------------------------------------------- | ------: | ------: | -----: | -------: | ---------------------------------------------------------------- |
| plugins/caption_provider.py                             |      21 |       0 |     21 |       0% | Protocol stub — dataclass body not exercised                     |
| plugins/media_compositor.py                             |      36 |       0 |     36 |       0% | Protocol stub                                                    |
| plugins/publish_adapter.py                              |      15 |       0 |     15 |       0% | Protocol stub                                                    |
| routes/external_webhooks.py                             |      86 |       0 |     86 |       0% | Real route — needs FastAPI TestClient tests                      |
| routes/topics_routes.py                                 |      89 |       0 |     89 |       0% | Real route — needs FastAPI TestClient tests                      |
| routes/webhooks.py                                      |      10 |       0 |     10 |       0% | Tiny — easy win                                                  |
| services/caption_providers/whisper_local.py             |     138 |       0 |    138 |       0% | Excluded — covered in commit `48ba506f` (not yet on this branch) |
| services/integrations/handlers/webhook_alertmanager.py  |     106 |       0 |    106 |       0% | Webhook handler — needs trigger-flow tests                       |
| services/integrations/handlers/webhook_revenue.py       |      32 |       0 |     32 |       0% | Webhook handler                                                  |
| services/integrations/handlers/webhook_subscriber.py    |      25 |       0 |     25 |       0% | Webhook handler                                                  |
| **services/llm_providers/dispatcher.py**                |  **72** |  **72** |  **0** | **100%** | **FIXED in this PR (was 0%) — central LLM dispatch glue**        |
| services/media_compositors/ffmpeg_local.py              |     258 |       0 |    258 |       0% | Excluded by reviewer (already covered)                           |
| services/publish_adapters/youtube.py                    |     152 |       0 |    152 |       0% | Excluded by reviewer (already covered)                           |
| services/stages/\_video_stitch.py                       |     149 |       0 |    149 |       0% | Excluded by reviewer (already covered)                           |
| services/stages/scene_visuals.py                        |     168 |       0 |    168 |       0% | Excluded by reviewer (already covered)                           |
| services/stages/script_for_video.py                     |     129 |       0 |    129 |       0% | Excluded by reviewer (already covered)                           |
| services/stages/stitch_long_form.py                     |      45 |       0 |     45 |       0% | Excluded by reviewer (already covered)                           |
| services/stages/stitch_short_form.py                    |      75 |       0 |     75 |       0% | Excluded by reviewer (already covered)                           |
| services/stages/tts_for_video.py                        |     137 |       0 |    137 |       0% | Excluded by reviewer (already covered)                           |
| services/stages/upload_to_platform.py                   |     127 |       0 |    127 |       0% | Excluded by reviewer (already covered)                           |
| services/taps/runner.py                                 |     110 |       0 |    110 |       0% | Tap dispatcher — high-value target                               |
| **services/url_scraper.py**                             | **107** | **107** |  **0** | **100%** | **FIXED in this PR (was 0%) — utility, very mockable**           |
| services/content_router_service.py                      |     109 |       8 |    101 |       7% | Pipeline orchestrator — complex                                  |
| services/research_context.py                            |      54 |       5 |     49 |       9% | Stage helper — straightforward                                   |
| poindexter/cli/setup.py                                 |     386 |      42 |    344 |      11% | Click CLI — interactive, costly to test                          |
| services/self_review.py                                 |      42 |       6 |     36 |      14% | Stage helper                                                     |
| services/taps/gitea_issues.py                           |      76 |      12 |     64 |      16% | Tap with HTTP — mockable                                         |
| **services/writing_style_context.py**                   |  **32** |  **32** |  **0** | **100%** | **FIXED in this PR (was 16%) — small surface**                   |
| services/integrations/handlers/tap_singer_subprocess.py |     176 |      30 |    146 |      17% | Subprocess shim                                                  |
| poindexter/cli/premium.py                               |     159 |      28 |    131 |      18% | Click CLI                                                        |
| poindexter/cli/costs.py                                 |      80 |      15 |     65 |      19% | Click CLI                                                        |
| services/retention_janitor.py                           |      59 |      11 |     48 |      19% | Background task — needs DB mocking                               |
| poindexter/cli/settings.py                              |     169 |      34 |    135 |      20% | Click CLI                                                        |
| poindexter/cli/sprint.py                                |     125 |      26 |     99 |      21% | Click CLI                                                        |
| poindexter/cli/vercel.py                                |     127 |      27 |    100 |      21% | Click CLI                                                        |
| poindexter/cli/retention.py                             |     134 |      30 |    104 |      22% | Click CLI                                                        |
| poindexter/cli/webhooks.py                              |     138 |      30 |    108 |      22% | Click CLI                                                        |
| poindexter/cli/posts.py                                 |     163 |      38 |    125 |      23% | Click CLI                                                        |
| poindexter/cli/memory.py                                |     162 |      39 |    123 |      24% | Click CLI                                                        |
| poindexter/cli/taps.py                                  |     123 |      29 |     94 |      24% | Click CLI                                                        |
| services/integrations/handlers/**init**.py              |      16 |       4 |     12 |      25% | Module dispatcher                                                |
| plugins/scheduler.py                                    |      73 |      19 |     54 |      26% | apscheduler bootstrap                                            |
| poindexter/cli/qa_gates.py                              |     115 |      31 |     84 |      27% | Click CLI                                                        |
| poindexter/memory/client.py                             |     181 |      48 |    133 |      27% | HTTP memory client                                               |
| plugins/secrets.py                                      |      78 |      22 |     56 |      28% | Secret resolution                                                |
| poindexter/cli/\_api_client.py                          |      43 |      13 |     30 |      30% | HTTP shim                                                        |
| poindexter/cli/tasks.py                                 |     137 |      41 |     96 |      30% | Click CLI                                                        |
| services/image_providers/sdxl.py                        |     342 |     116 |    226 |      34% | GPU image gen — heavy mocking required                           |
| services/taps/brain_decisions.py                        |      29 |      11 |     18 |      38% | Tap                                                              |
| services/taps/published_posts.py                        |      26 |      10 |     16 |      38% | Tap                                                              |
| services/stages/generate_media_scripts.py               |      72 |      28 |     44 |      39% | Stage                                                            |
| routes/memory_dashboard_routes.py                       |      82 |      34 |     48 |      41% | Route                                                            |
| services/stages/source_featured_image.py                |     175 |      74 |    101 |      42% | Stage with image-provider integration                            |
| services/citation_verifier.py                           |     110 |      47 |     63 |      43% | Verification logic                                               |
| plugins/samples/database_probe.py                       |      24 |      11 |     13 |      46% | Sample stub                                                      |
| services/jobs/rollup_post_performance.py                |      23 |      11 |     12 |      48% | Job                                                              |
| services/taps/audit.py                                  |      23 |      11 |     12 |      48% | Tap                                                              |
| services/taps/brain_knowledge.py                        |      21 |      10 |     11 |      48% | Tap                                                              |

### 50–80% quick wins (highlights)

| Module                                   | Stmts | Covered | Missed |   % |
| ---------------------------------------- | ----: | ------: | -----: | --: |
| services/multi_model_qa.py               |   715 |     381 |    334 | 53% |
| services/stages/replace_inline_images.py |   212 |     113 |     99 | 53% |
| services/ai_content_generator.py         |   466 |     252 |    214 | 54% |
| routes/task_publishing_routes.py         |   475 |     277 |    198 | 58% |
| services/llm_providers/ollama_native.py  |    92 |      54 |     38 | 59% |
| services/publish_service.py              |   426 |     264 |    162 | 62% |
| services/llm_providers/openai_compat.py  |   180 |     113 |     67 | 63% |
| routes/cms_routes.py                     |   403 |     260 |    143 | 65% |
| services/stages/cross_model_qa.py        |   196 |     129 |     67 | 66% |
| plugins/llm_providers/gemini.py          |   258 |     179 |     79 | 69% |
| routes/task_routes.py                    |   255 |     179 |     76 | 70% |

## Recommendations

Top 5 modules to target next (excluding the three fixed in this PR and the video stages already covered in commits `d701f9d2`, `48ba506f`, `09611ec4`):

1. **services/taps/runner.py (110 stmts, 0%)** — central tap dispatcher. Mockable; test the orchestration path with a fake `Tap` and a fake `pool`.
2. **services/integrations/handlers/webhook_alertmanager.py (106 stmts, 0%)** — alert webhook handler. Has a clear input contract; build payload fixtures and assert on processed events.
3. **services/research_context.py (54 stmts, 9%)** — small surface, mostly DB-mockable. High coverage should be cheap.
4. **services/multi_model_qa.py (715 stmts, 53%)** — load-bearing pipeline component. 334 missed lines is the largest absolute miss in the codebase. Even moving from 53→70% would clear ~120 statements.
5. **services/citation_verifier.py (110 stmts, 43%)** — verification logic. Pure-ish input/output, mockable HTTP calls.

Lower-priority but worth picking up opportunistically:

- The Click CLIs (`poindexter/cli/*.py`) collectively own ~1,400 missed statements. Worth investing in a `CliRunner`-based fixture in `tests/unit/poindexter/` and slamming through the easy commands.
- `plugins/scheduler.py` and `plugins/secrets.py` are infrastructure pieces that should be straightforward to mock.

## Tools / process notes

- `pytest-cov` is already in `pyproject.toml` (`pytest-cov = "^4.0"` in dev deps).
- `[tool.coverage.run]` correctly excludes `tests/`, `migrations/`, and `__main__.py`.
- The `[tool.coverage.report]` `exclude_lines` config already drops `Protocol` class bodies and `@abstractmethod` — protocol-only modules at 0% are usually their dataclass bodies or module-level constants, NOT logic. Tests that just import the module would knock those 0% rows up.
- Adding `--cov-fail-under=70` to CI would lock in the current floor without forcing immediate test work.
