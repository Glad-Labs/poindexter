# Integration Tests

Two flavors:

## 1. Live-server integration tests (existing)

`test_blog_workflow.py`, `test_task_lifecycle.py`, `test_content_pipeline_e2e.py`, etc.

Assume a fully-running Poindexter stack (worker on :8002, Ollama on :11434, etc.) and hit it as if they were an external client.

**Run:**

```bash
INTEGRATION_TESTS=1 poetry run pytest -m integration
```

Each test handles its own setup inside the shared worker. State leaks between tests are possible; these are more "does the live stack respond reasonably" smoke tests than true isolation tests.

## 2. Real-services harness (new, scaffold for GitHub #21)

`conftest_real_services.py` + `test_harness_smoke.py`.

Designed for tests that need **real Postgres + real Ollama**, but **strict isolation from Matt's operating database**. Uses a separate `poindexter_test` DB on the same Postgres instance. Tables can be truncated between tests for a clean slate.

**Run:**

```bash
INTEGRATION_TESTS=1 REAL_SERVICES_TESTS=1 poetry run pytest -m integration \
  tests/integration/test_harness_smoke.py
```

Both env vars are required — the second gate exists so the harness never runs by accident on a machine that only enables the first gate.

### Fixtures

| Fixture                | Scope    | Purpose                                                 |
| ---------------------- | -------- | ------------------------------------------------------- |
| `ensure_test_database` | session  | Creates `poindexter_test` DB + installs pgvector        |
| `real_pool`            | session  | asyncpg pool on the test DB                             |
| `migrations_applied`   | session  | Applies `init_test_schema.sql` + creates `app_settings` |
| `clean_test_tables`    | function | Truncates all public tables before each test            |
| `real_ollama_url`      | session  | Verifies Ollama is reachable; returns base URL          |
| `real_ollama`          | function | httpx.AsyncClient pre-configured for Ollama             |

### Why this exists

This is the Phase A0 prerequisite for the plugin-architecture refactor (GitHub #64). Without a real-services test bed, we can't answer "did we change pipeline behavior?" before and after a refactor. Existing unit tests validate mocks (GitHub #30); they don't help when the question is "does the pipeline still produce posts."

The harness is intentionally small right now. Phase B+ tests expand it:

- Canonical blog_post golden snapshot tests
- Plugin discovery round-trip tests
- Auto-embed Taps regression coverage
- Brain probe → Prometheus metric correspondence tests

### Adding a test

```python
import pytest
from tests.integration.conftest_real_services import requires_real_services

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, requires_real_services]


async def test_my_pipeline_stage(clean_test_tables, real_ollama):
    async with clean_test_tables.acquire() as conn:
        # DB writes go to poindexter_test, not poindexter_brain.
        ...

    resp = await real_ollama.post("/api/generate", json={...})
    # Hits the host Ollama with real latency + real models.
    ...
```

### Isolation guarantees

- Test DB is `poindexter_test`, always separate from `poindexter_brain`.
- `test_main_database_untouched` asserts the admin DSN doesn't point at the operating DB.
- `clean_test_tables` only truncates the test DB.
- Ollama is shared with the host (no isolation possible); tests that pull or modify models are responsible for cleanup.
