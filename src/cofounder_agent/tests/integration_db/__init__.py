"""Integration tests that run against a real Postgres (GH#21).

Gated behind ``pytest -m integration_db``. The default pytest invocation
runs only unit + integration (mock-based) tests; this tier only runs
when explicitly requested so the fast unit suite stays fast.

The harness in conftest.py:
  - creates a disposable DB named ``poindexter_test_<uuid>`` on the
    existing postgres-local instance at session start
  - runs every migration in order against it
  - loads a small fixture set (posts, categories, app_settings,
    fact_overrides, embeddings) for realistic tests
  - hands out asyncpg pools + transaction-rolled-back sessions to tests
  - drops the test DB on session teardown

Add new tests here whenever you're testing a code path that the
mock-heavy unit suite can't cover — things that depend on real SQL,
real constraint enforcement, or real migrations.
"""
