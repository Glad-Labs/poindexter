"""Unit tests for services/metrics_exporter.py.

Verifies that refresh_metrics() populates every gauge/histogram even
when individual queries fail, and that the new Gitea #238 metrics
(postgres latency, ollama model count, embeddings-missing-posts,
approval queue) respond to the expected DB shapes.

All DB + HTTP I/O is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_gauges():
    """Zero out the single-value Gauges between tests. Histograms
    accumulate across the session since prometheus_client doesn't
    expose a clean reset; tests assert ``> 0`` rather than a specific
    value, so accumulation is fine."""
    from services import metrics_exporter as mx

    for g in (
        mx.WORKER_UP,
        mx.POSTGRES_CONNECTED,
        mx.PG_CONNECTIONS_USED,
        mx.PG_CONNECTIONS_MAX,
        mx.OLLAMA_REACHABLE,
        mx.OLLAMA_MODEL_COUNT,
        mx.EMBEDDINGS_MISSING_POSTS,
        mx.APPROVAL_QUEUE_LENGTH,
        mx.AUTO_CANCELLED_TOTAL,
        mx.UNAPPLIED_MIGRATIONS_COUNT,
        mx.POSTS_PUBLISHED,
    ):
        g.set(0)
    # Labeled gauges — clear the series so each test starts absent.
    mx.BRAIN_CYCLE_HEARTBEAT_TIMESTAMP.clear()
    mx.POSTS_TOTAL.clear()  # poindexter#576
    yield


def _make_pool(fetchval_responses, fetch_responses):
    """Build an AsyncMock pool whose acquire() context returns a conn
    that serves up fetchval/fetch responses from the provided queues."""
    pool = MagicMock()

    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=fetchval_responses)
    conn.fetch = AsyncMock(side_effect=fetch_responses)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


@pytest.mark.unit
@pytest.mark.asyncio
class TestRefreshMetrics:
    async def test_worker_up_always_set(self):
        from services import metrics_exporter as mx

        # fetchval queue: SELECT 1, heartbeat-epoch (#524), pg_stat_activity,
        # max_connections, embeddings-gap, approval-queue, auto-cancelled,
        # applied-migrations.
        pool, _ = _make_pool([1, 1_700_000_000.0, 50, 300, 0, 0, 0, 0], [[], []])
        with patch(
            "services.metrics_exporter.httpx.AsyncClient"
        ) as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("no ollama")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        # _value is the prometheus_client Gauge internal counter.
        assert mx.WORKER_UP._value.get() == 1  # type: ignore[attr-defined]

    async def test_postgres_latency_recorded_on_success(self):
        from services import metrics_exporter as mx

        # Capture the current observation count so we can assert that
        # refresh_metrics observed exactly one more sample. Reading via
        # collect() avoids poking at private attributes that vary across
        # prometheus_client versions.
        def _latency_count() -> float:
            families = list(mx.POSTGRES_QUERY_LATENCY.collect())
            for sample in families[0].samples:
                if sample.name.endswith("_count"):
                    return sample.value
            return 0.0

        before = _latency_count()

        pool, _ = _make_pool([1, 1_700_000_000.0, 50, 300, 0, 0, 0, 0], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        after = _latency_count()
        assert after == before + 1
        assert mx.POSTGRES_CONNECTED._value.get() == 1  # type: ignore[attr-defined]

    async def test_postgres_connected_zero_on_query_failure(self):
        from services import metrics_exporter as mx

        # SELECT 1 raises — downstream queries still get to run via
        # their own try/except.
        pool = MagicMock()
        conn = MagicMock()
        conn.fetchval = AsyncMock(side_effect=RuntimeError("conn dead"))
        conn.fetch = AsyncMock(return_value=[])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=ctx)

        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.POSTGRES_CONNECTED._value.get() == 0  # type: ignore[attr-defined]

    async def test_ollama_model_count_set_from_api_tags(self):
        from services import metrics_exporter as mx

        pool, _ = _make_pool([1, 1_700_000_000.0, 50, 300, 0, 0, 0, 0], [[], []])

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json = MagicMock(
            return_value={"models": [{"name": "gemma3:27b"}, {"name": "nomic-embed-text"}]}
        )
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_ctx = MagicMock()
        fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
        fake_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("services.metrics_exporter.httpx.AsyncClient", return_value=fake_ctx):
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.OLLAMA_REACHABLE._value.get() == 1  # type: ignore[attr-defined]
        assert mx.OLLAMA_MODEL_COUNT._value.get() == 2  # type: ignore[attr-defined]

    async def test_ollama_model_count_zero_when_tags_empty(self):
        """"Ollama up but no models" — the scenario Gitea #238 flagged."""
        from services import metrics_exporter as mx

        pool, _ = _make_pool([1, 1_700_000_000.0, 50, 300, 0, 0, 0, 0], [[], []])

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json = MagicMock(return_value={"models": []})
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_ctx = MagicMock()
        fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
        fake_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("services.metrics_exporter.httpx.AsyncClient", return_value=fake_ctx):
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.OLLAMA_REACHABLE._value.get() == 1  # type: ignore[attr-defined]
        assert mx.OLLAMA_MODEL_COUNT._value.get() == 0  # type: ignore[attr-defined]

    async def test_embeddings_missing_posts_reflects_gap(self):
        from services import metrics_exporter as mx

        # fetchval queue: SELECT 1 → 1, heartbeat-epoch (#524),
        # pg_stat_activity → 50, max_connections → 300, embeddings-gap → 5,
        # queue → 2, auto-cancelled → 0, applied-migrations → 0.
        pool, _ = _make_pool([1, 1_700_000_000.0, 50, 300, 5, 2, 0, 0], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.EMBEDDINGS_MISSING_POSTS._value.get() == 5  # type: ignore[attr-defined]

    async def test_approval_queue_length_reflects_count(self):
        from services import metrics_exporter as mx

        # fetchval queue (post-rebase, GH-90 + GH-92 + GH-227):
        #   SELECT 1 → 1
        #   heartbeat-epoch (#524)
        #   pg_stat_activity → 50, max_connections → 300 (GH-92)
        #   embeddings-gap → 0
        #   queue → 7
        #   auto-cancelled → 0 (GH-90)
        #   applied-migrations → 0 (GH-227)
        pool, _ = _make_pool([1, 1_700_000_000.0, 50, 300, 0, 7, 0, 0], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.APPROVAL_QUEUE_LENGTH._value.get() == 7  # type: ignore[attr-defined]

    async def test_posts_published_gauge_matches_published_status_only(self):
        """poindexter#576: the Cost & Analytics dashboard read 23
        ("posts published") while the DB had 91 published — it was
        selecting the ``archived`` label (23) off the multi-label
        ``poindexter_posts_total`` gauge instead of ``published`` (91).

        Pin the semantics: the dedicated ``poindexter_posts_published``
        gauge equals the ``published`` count and nothing else, and the
        labeled gauge keeps ``published`` and ``archived`` as distinct
        series so a correct consumer can never conflate them.
        """
        from services import metrics_exporter as mx

        def _posts_total_label(status: str) -> float:
            """Read poindexter_posts_total{status=...} via collect() so we
            don't poke version-specific private child internals."""
            for family in mx.POSTS_TOTAL.collect():
                for sample in family.samples:
                    if sample.labels.get("status") == status:
                        return sample.value
            return float("nan")

        # The exact status distribution from the live DB in poindexter#576:
        #   published=91, rejected=143, archived=23, draft=1.
        posts_rows = [
            {"status": "published", "n": 91},
            {"status": "rejected", "n": 143},
            {"status": "archived", "n": 23},
            {"status": "draft", "n": 1},
        ]
        # fetch queue: [embeddings-by-source_table=[], posts-by-status=rows]
        pool, _ = _make_pool(
            [1, 1_700_000_000.0, 50, 300, 0, 0, 0, 0],
            [[], posts_rows],
        )
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        # The dedicated gauge tracks ONLY published — not archived (23).
        assert mx.POSTS_PUBLISHED._value.get() == 91  # type: ignore[attr-defined]
        assert mx.POSTS_PUBLISHED._value.get() != 23  # type: ignore[attr-defined]

        # The labeled gauge keeps published and archived as separate series.
        assert _posts_total_label("published") == 91
        assert _posts_total_label("archived") == 23

    async def test_posts_total_clears_stale_status_between_refreshes(self):
        """poindexter#576: a labeled Gauge is never auto-reset, so a status
        that drops to zero rows must be cleared or its series freezes at the
        last value. Verify a status present in refresh #1 disappears in
        refresh #2 once it has no rows."""
        from services import metrics_exporter as mx

        def _has_label(status: str) -> bool:
            for family in mx.POSTS_TOTAL.collect():
                for sample in family.samples:
                    if sample.labels.get("status") == status:
                        return True
            return False

        # Refresh #1: a "draft" series exists.
        pool1, _ = _make_pool(
            [1, 1_700_000_000.0, 50, 300, 0, 0, 0, 0],
            [[], [{"status": "published", "n": 90}, {"status": "draft", "n": 5}]],
        )
        with patch("services.metrics_exporter.httpx.AsyncClient") as mc:
            mc.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool1, "http://localhost:11434")
        assert _has_label("draft") is True

        # Refresh #2: the drafts were all published — no "draft" rows now.
        pool2, _ = _make_pool(
            [1, 1_700_000_000.0, 50, 300, 0, 0, 0, 0],
            [[], [{"status": "published", "n": 95}]],
        )
        with patch("services.metrics_exporter.httpx.AsyncClient") as mc:
            mc.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool2, "http://localhost:11434")

        assert _has_label("draft") is False  # stale series cleared
        assert mx.POSTS_PUBLISHED._value.get() == 95  # type: ignore[attr-defined]

    async def test_auto_cancelled_total_reflects_event_count(self):
        """GH-90 AC #4: sweeper cancellations are exposed as
        ``poindexter_pipeline_auto_cancelled_total``. Value comes from
        ``COUNT(*) FROM pipeline_tasks WHERE auto_cancelled_at IS NOT NULL``
        (poindexter#366 phase 2 moved this off pipeline_events) —
        persistent across worker restarts so short-window rate()
        queries stay useful."""
        from services import metrics_exporter as mx

        # fetchval queue (8 values, current refresh_metrics order):
        #   SELECT 1, heartbeat-epoch (#524), pg_used, pg_max,
        #   embeddings-gap, queue, cancelled=42, applied-migrations=0.
        pool, _ = _make_pool([1, 1_700_000_000.0, 0, 100, 0, 0, 42, 0], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.AUTO_CANCELLED_TOTAL._value.get() == 42  # type: ignore[attr-defined]

    async def test_pg_connections_used_and_max_emitted(self):
        """GH-92: Prometheus scrape must surface server-side connection
        utilization so the 80%-threshold alert can fire before the pool
        exhausts ``max_connections``."""
        from services import metrics_exporter as mx

        # 8-value fetchval queue: SELECT 1, heartbeat-epoch (#524),
        # pg_used=127, pg_max=300, gap=0, queue=0, cancelled=0,
        # applied-migrations=0.
        pool, _ = _make_pool([1, 1_700_000_000.0, 127, 300, 0, 0, 0, 0], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.PG_CONNECTIONS_USED._value.get() == 127  # type: ignore[attr-defined]
        assert mx.PG_CONNECTIONS_MAX._value.get() == 300  # type: ignore[attr-defined]

    async def test_pg_connections_query_failure_leaves_gauges_untouched(self):
        """If pg_stat_activity or current_setting() raise (unlikely but
        possible on a permission-stripped role), /metrics must not 500 —
        we just leave the gauge at its last-known value."""
        from services import metrics_exporter as mx

        pool = MagicMock()
        conn = MagicMock()
        # SELECT 1 → 1, heartbeat-epoch (#524) → epoch, pg_stat_activity
        # raises (its own try/except eats that + skips the max_conn
        # fetchval), then gap/queue/cancelled/migrations.
        conn.fetchval = AsyncMock(
            side_effect=[
                1,
                1_700_000_000.0,
                RuntimeError("permission denied"),
                0,
                0,
                0,
                0,
            ]
        )
        conn.fetch = AsyncMock(return_value=[])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=ctx)

        # Pre-seed so we can prove the gauges weren't clobbered to 0.
        mx.PG_CONNECTIONS_USED.set(42)
        mx.PG_CONNECTIONS_MAX.set(100)

        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            # Should NOT raise even when pg_stat_activity fails.
            await mx.refresh_metrics(pool, "http://localhost:11434")

        # Gauges stayed at their pre-set values — the try/except ate the error.
        assert mx.PG_CONNECTIONS_USED._value.get() == 42  # type: ignore[attr-defined]
        assert mx.PG_CONNECTIONS_MAX._value.get() == 100  # type: ignore[attr-defined]

    async def test_pg_connections_metrics_appear_in_exposition(self):
        """Smoke-check that the new gauges actually render in the
        ``/metrics`` text body consumed by Prometheus."""
        from services import metrics_exporter as mx

        pool, _ = _make_pool([1, 1_700_000_000.0, 88, 300, 0, 0, 0, 0], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        body, _ = mx.render_exposition()
        text = body.decode("utf-8")
        assert "pg_connections_used" in text
        assert "pg_connections_max" in text

    async def test_unapplied_migrations_count_zero_when_all_applied(self):
        """GH-227: when schema_migrations row count >= on-disk migration
        files, the gauge sits at 0 (clean steady state)."""
        from pathlib import Path

        from services import metrics_exporter as mx
        from services import migrations as _migrations_pkg

        # Match the actual on-disk count so the gauge converges to 0.
        migrations_dir = Path(_migrations_pkg.__file__).parent
        on_disk = sum(
            1 for p in migrations_dir.glob("*.py") if p.name != "__init__.py"
        )

        # 8-value queue: SELECT 1, heartbeat-epoch (#524), pg_used, pg_max,
        # gap, queue, cancelled, applied=on_disk.
        pool, _ = _make_pool([1, 1_700_000_000.0, 50, 300, 0, 0, 0, on_disk], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.UNAPPLIED_MIGRATIONS_COUNT._value.get() == 0  # type: ignore[attr-defined]

    async def test_unapplied_migrations_count_positive_on_drift(self):
        """GH-227: when applied < on-disk, the gauge surfaces the gap so
        Alertmanager can alert that the worker is on a stale schema."""
        from pathlib import Path

        from services import metrics_exporter as mx
        from services import migrations as _migrations_pkg

        migrations_dir = Path(_migrations_pkg.__file__).parent
        on_disk = sum(
            1 for p in migrations_dir.glob("*.py") if p.name != "__init__.py"
        )
        # Pretend the worker has applied 3 fewer migrations than the
        # files on disk.
        applied = max(on_disk - 3, 0)

        # 8-value queue matching the current refresh_metrics fetchval
        # order (SELECT 1, heartbeat-epoch (#524), pg_used, pg_max, gap,
        # queue, cancelled, applied).
        pool, _ = _make_pool([1, 1_700_000_000.0, 50, 300, 0, 0, 0, applied], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.UNAPPLIED_MIGRATIONS_COUNT._value.get() == on_disk - applied  # type: ignore[attr-defined]

    async def test_unapplied_migrations_count_floored_at_zero(self):
        """GH-227: ghost rows (schema_migrations row count > on-disk file
        count) clamp to 0, never negative — the alert is "stale schema",
        not "ghost rows"."""
        from pathlib import Path

        from services import metrics_exporter as mx
        from services import migrations as _migrations_pkg

        migrations_dir = Path(_migrations_pkg.__file__).parent
        on_disk = sum(
            1 for p in migrations_dir.glob("*.py") if p.name != "__init__.py"
        )
        # Pretend schema_migrations has 5 more rows than there are files
        # on disk (legal but odd — manual inserts, removed files).
        applied_inflated = on_disk + 5

        pool, _ = _make_pool(
            [1, 1_700_000_000.0, 50, 300, 0, 0, 0, applied_inflated], [[], []]
        )
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.UNAPPLIED_MIGRATIONS_COUNT._value.get() == 0  # type: ignore[attr-defined]

    async def test_unapplied_migrations_metric_appears_in_exposition(self):
        """Smoke-check that the new gauge actually renders in the
        ``/metrics`` exposition text Prometheus scrapes."""
        from services import metrics_exporter as mx

        pool, _ = _make_pool([1, 1_700_000_000.0, 50, 300, 0, 0, 0, 0], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        body, _ = mx.render_exposition()
        text = body.decode("utf-8")
        assert "poindexter_unapplied_migrations_count" in text


@pytest.mark.unit
class TestMetricsRefreshErrorCounter:
    """Audit H2b — per-phase refresh-failure counter.

    Each refresh_metrics block has its own try/except so one failing query
    can't fail the whole scrape, but those used to log only at DEBUG — and
    when a phase throws its gauge holds the last value (stale). _note_refresh
    _error now also increments a per-phase counter so the staleness is
    countable + alertable instead of invisible.
    """

    def test_note_refresh_error_increments_phase(self):
        from prometheus_client import REGISTRY

        from services import metrics_exporter as mx

        name = "poindexter_metrics_refresh_errors_total"
        before = REGISTRY.get_sample_value(name, {"phase": "unit_probe"}) or 0.0
        mx._note_refresh_error("unit_probe", RuntimeError("boom"))
        after = REGISTRY.get_sample_value(name, {"phase": "unit_probe"}) or 0.0
        assert after == before + 1.0

    def test_exposed_series_is_total_suffixed(self):
        # Pins the series name the PoindexterMetricsRefreshErrors alert
        # queries. If prometheus_client ever changes its ``_total`` suffixing
        # this fails loudly here, instead of the alert silently going dead.
        from prometheus_client import REGISTRY

        from services import metrics_exporter as mx

        mx._note_refresh_error("name_probe", ValueError("x"))
        assert (
            REGISTRY.get_sample_value(
                "poindexter_metrics_refresh_errors_total",
                {"phase": "name_probe"},
            )
            is not None
        )

    @pytest.mark.asyncio
    async def test_failing_phase_increments_counter(self):
        from prometheus_client import REGISTRY

        from services import metrics_exporter as mx

        name = "poindexter_metrics_refresh_errors_total"
        before = REGISTRY.get_sample_value(name, {"phase": "postgres"}) or 0.0

        # A pool whose acquire() raises makes the postgres block (and the
        # other DB blocks) throw. refresh_metrics must still complete and the
        # postgres phase counter must tick.
        pool = MagicMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("db gone"))
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=ctx)

        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("no ollama")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        after = REGISTRY.get_sample_value(name, {"phase": "postgres"}) or 0.0
        assert after >= before + 1.0


def _heartbeat_series_value(mx):
    """Return the brain-heartbeat gauge value from the exposition, or None
    if the series is absent (the dead-man's-switch trigger condition)."""
    for family in mx.BRAIN_CYCLE_HEARTBEAT_TIMESTAMP.collect():
        for sample in family.samples:
            if sample.name == "poindexter_brain_cycle_heartbeat_timestamp_seconds":
                return sample.value
    return None


@pytest.mark.unit
@pytest.mark.asyncio
class TestBrainCycleHeartbeat:
    """#524 — the delivery-plane dead-man's-switch heartbeat gauge.

    The gauge must (a) carry the epoch of the latest brain.cycle_heartbeat
    row when one exists, and (b) be ABSENT from the exposition entirely on
    no-row / DB-error so ``absent(...)`` can fire and the staleness check
    isn't frozen at a stale value.
    """

    async def test_gauge_set_from_latest_heartbeat_epoch(self):
        from services import metrics_exporter as mx

        epoch = 1_700_000_123.0
        # fetchval order: SELECT 1, heartbeat-epoch, pg_used, pg_max, gap,
        # queue, cancelled, applied.
        pool, _ = _make_pool([1, epoch, 50, 300, 0, 0, 0, 0], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert _heartbeat_series_value(mx) == epoch
        body, _ = mx.render_exposition()
        text = body.decode("utf-8")
        # A real data sample line must be present (not just HELP/TYPE).
        sample_lines = [
            ln
            for ln in text.splitlines()
            if ln.startswith("poindexter_brain_cycle_heartbeat_timestamp_seconds{")
        ]
        assert sample_lines, "heartbeat sample line missing from exposition"

    async def test_heartbeat_query_targets_real_audit_log_schema(self):
        """Guard the SQL against schema drift: audit_log's timestamp column
        is ``timestamp`` (NOT ``created_at``). A wrong column silently errors
        every scrape → the gauge is always cleared → the dead-man's switch
        fires a CONSTANT false alarm. (This caught a real bug in the original
        implementation, which mocked fetchval and never hit the schema.)
        """
        from services import metrics_exporter as mx

        pool, conn = _make_pool([1, 1_700_000_000.0, 50, 300, 0, 0, 0, 0], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        sqls = [c.args[0] for c in conn.fetchval.call_args_list if c.args]
        hb = [s for s in sqls if "brain.cycle_heartbeat" in s]
        assert hb, "heartbeat query was never issued"
        q = hb[0]
        assert "audit_log" in q
        assert "created_at" not in q, "audit_log has no created_at column"
        assert '"timestamp"' in q, "must read the real (quoted) timestamp column"

    async def test_gauge_absent_when_no_heartbeat_row(self):
        """No brain.cycle_heartbeat row yet → MAX("timestamp") is NULL →
        the series must be cleared so absent() fires (not emitted as 0)."""
        from services import metrics_exporter as mx

        # Pre-seed a value to prove it gets cleared on the None result.
        mx.BRAIN_CYCLE_HEARTBEAT_TIMESTAMP.labels(source="audit_log").set(123.0)

        # heartbeat fetchval → None (no rows).
        pool, _ = _make_pool([1, None, 50, 300, 0, 0, 0, 0], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert _heartbeat_series_value(mx) is None
        # The HELP/TYPE metadata lines still render (prometheus_client keeps
        # registered metric metadata), but NO data SAMPLE line may exist —
        # that absence is what makes ``absent()`` fire. A sample line starts
        # with the metric name + ``{`` (labels) or a space; metadata lines
        # start with ``# HELP`` / ``# TYPE``.
        text = body.decode("utf-8") if (body := mx.render_exposition()[0]) else ""
        sample_lines = [
            ln
            for ln in text.splitlines()
            if ln.startswith("poindexter_brain_cycle_heartbeat_timestamp_seconds")
        ]
        assert sample_lines == []

    async def test_gauge_absent_on_db_error(self):
        """If the heartbeat query raises, the series must be cleared (not
        left at a stale value) so the dead-man's switch can fire."""
        from services import metrics_exporter as mx

        mx.BRAIN_CYCLE_HEARTBEAT_TIMESTAMP.labels(source="audit_log").set(999.0)

        pool = MagicMock()
        conn = MagicMock()
        # SELECT 1 → 1, heartbeat query RAISES, then the rest succeed so
        # the wider refresh still completes (per-block try/except posture).
        conn.fetchval = AsyncMock(
            side_effect=[1, RuntimeError("audit_log gone"), 50, 300, 0, 0, 0, 0]
        )
        conn.fetch = AsyncMock(return_value=[])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=ctx)

        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert _heartbeat_series_value(mx) is None


def _skip_pool(fetch_rows, *, window_val=None):
    """Fake pool for refresh_qa_rail_skip_ratio: one fetchval (window
    size from app_settings) + one fetch (the per-rail skip/pass rows)."""
    pool = MagicMock()
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=window_val)
    conn.fetch = AsyncMock(return_value=fetch_rows)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _skip_ratio_value(mx, reviewer):
    """Return the gauge value for one reviewer, or None if the series is
    absent (a healthy rail emits no series → no alert)."""
    for family in mx.QA_RAIL_SKIP_RATIO.collect():
        for sample in family.samples:
            if sample.labels.get("reviewer") == reviewer:
                return sample.value
    return None


@pytest.mark.unit
@pytest.mark.asyncio
class TestQaRailSkipRatio:
    """poindexter#553 — the rail-skip-rate alert's metric. The gauge is the
    fraction of the last N QA passes in which a rail was skipped; the
    QaRailFullySkipped Prometheus rule fires when it reaches 1.0 (a rail
    skipping 100% of recent passes — e.g. ragas_eval with empty
    research_context, a disabled master flag, or an unresolvable judge)."""

    async def test_ratio_is_one_when_rail_skipped_every_pass(self):
        """Synthetic 'alert fires' case: ragas_eval skipped in all 20 of the
        last 20 passes → ratio 1.0 → QaRailFullySkipped fires."""
        from services import metrics_exporter as mx

        pool, _ = _skip_pool([
            {"reviewer": "ragas_eval", "skips": 20.0, "passes": 20},
        ])
        await mx.refresh_qa_rail_skip_ratio(pool, window_passes=20)
        assert _skip_ratio_value(mx, "ragas_eval") == 1.0

    async def test_partial_skip_ratio(self):
        from services import metrics_exporter as mx

        pool, _ = _skip_pool([
            {"reviewer": "deepeval_faithfulness", "skips": 5.0, "passes": 20},
        ])
        await mx.refresh_qa_rail_skip_ratio(pool, window_passes=20)
        assert _skip_ratio_value(mx, "deepeval_faithfulness") == 0.25

    async def test_ratio_capped_at_one(self):
        """More skips than passes (an in-flight pass's skip lands in the
        window) must clamp to 1.0, never exceed it."""
        from services import metrics_exporter as mx

        pool, _ = _skip_pool([
            {"reviewer": "ragas_eval", "skips": 25.0, "passes": 20},
        ])
        await mx.refresh_qa_rail_skip_ratio(pool, window_passes=20)
        assert _skip_ratio_value(mx, "ragas_eval") == 1.0

    async def test_no_passes_emits_no_series(self):
        """No qa_pass_completed rows in the window → no denominator → no
        series (can't have a 100% skip rate with nothing to measure)."""
        from services import metrics_exporter as mx

        pool, _ = _skip_pool([])
        await mx.refresh_qa_rail_skip_ratio(pool, window_passes=20)
        assert _skip_ratio_value(mx, "ragas_eval") is None

    async def test_recovered_rail_series_cleared(self):
        """A rail that stopped skipping must lose its series so the alert
        resolves — the gauge is cleared each refresh."""
        from services import metrics_exporter as mx

        pool, _ = _skip_pool([{"reviewer": "ragas_eval", "skips": 20.0, "passes": 20}])
        await mx.refresh_qa_rail_skip_ratio(pool, window_passes=20)
        assert _skip_ratio_value(mx, "ragas_eval") == 1.0
        # Next refresh: ragas recovered (no skip rows) → series gone.
        pool2, _ = _skip_pool([])
        await mx.refresh_qa_rail_skip_ratio(pool2, window_passes=20)
        assert _skip_ratio_value(mx, "ragas_eval") is None

    async def test_window_size_read_from_app_settings_with_default(self):
        """window_passes defaults to the app_settings key; an empty-string
        sentinel (app_settings.value is NOT NULL) falls back to 20."""
        from services import metrics_exporter as mx

        # '' is the unset sentinel — must NOT crash int(), must default to 20.
        pool, conn = _skip_pool([], window_val="")
        await mx.refresh_qa_rail_skip_ratio(pool)
        # The skip query was issued with the default window of 20.
        assert conn.fetch.call_args.args[1] == 20

    async def test_sql_targets_real_audit_log_schema(self):
        """Guard against schema drift: the gauge SQL must read the two QA
        event types and the quoted ``timestamp`` column (audit_log has no
        created_at). A wrong column silently errors every scrape → the
        gauge is always empty → the alert can never fire."""
        from services import metrics_exporter as mx

        pool, conn = _skip_pool([{"reviewer": "ragas_eval", "skips": 1.0, "passes": 1}])
        await mx.refresh_qa_rail_skip_ratio(pool, window_passes=10)
        sql = conn.fetch.call_args.args[0]
        assert "qa_pass_completed" in sql
        assert "qa_reviewer_skipped" in sql
        assert '"timestamp"' in sql
        assert "created_at" not in sql
        assert "details->>'reviewer'" in sql

    async def test_sql_excludes_master_flag_off_skips(self):
        """The SQL WHERE clause must exclude skips whose reason contains
        'master rail flag off' so intentionally-disabled rails (ragas_enabled=false,
        deepeval_enabled=false, guardrails_enabled=false) don't drive
        QaRailFullySkipped. The filter is in the SQL; this test confirms the
        clause is present so it can't be accidentally removed.

        The mock here simulates the DB returning 0 rows after the SQL
        filter eliminates all master-flag-off skips for a disabled rail,
        matching what prod returns for ragas_eval / guardrails_* today.
        """
        from services import metrics_exporter as mx

        # DB returns nothing for a fully-disabled rail (all its skips had
        # reason='ragas_enabled=false (master rail flag off ...)' — filtered by SQL).
        pool, conn = _skip_pool([])
        await mx.refresh_qa_rail_skip_ratio(pool, window_passes=20)
        assert _skip_ratio_value(mx, "ragas_eval") is None

        sql = conn.fetch.call_args.args[0]
        assert "master rail flag off" in sql, (
            "SQL must filter out master-flag-off skips to prevent "
            "QaRailFullySkipped from firing on intentionally-disabled rails"
        )

    async def test_sql_excludes_structured_skip_types(self):
        """#1181 drift guard: the skip-ratio SQL must exclude every skip_type
        in modules.content.multi_model_qa.SKIP_TYPES_EXCLUDED_FROM_RATIO via a
        structured ``details->>'skip_type'`` filter — not a prose substring
        match. If a new intentional skip_type is added there but the SQL isn't
        updated, that rail's intentional skips would drive QaRailFullySkipped.
        """
        from modules.content.multi_model_qa import (
            SKIP_TYPES_EXCLUDED_FROM_RATIO,
        )
        from services import metrics_exporter as mx

        pool, conn = _skip_pool([{"reviewer": "ragas_eval", "skips": 1.0, "passes": 1}])
        await mx.refresh_qa_rail_skip_ratio(pool, window_passes=10)
        sql = conn.fetch.call_args.args[0]
        assert "skip_type" in sql
        for skip_type in SKIP_TYPES_EXCLUDED_FROM_RATIO:
            assert f"'{skip_type}'" in sql, (
                f"skip-ratio SQL must exclude skip_type={skip_type!r} "
                "(SKIP_TYPES_EXCLUDED_FROM_RATIO drifted from the SQL literals)"
            )

    async def test_appears_in_exposition(self):
        from services import metrics_exporter as mx

        pool, _ = _skip_pool([{"reviewer": "ragas_eval", "skips": 20.0, "passes": 20}])
        await mx.refresh_qa_rail_skip_ratio(pool, window_passes=20)
        text = mx.render_exposition()[0].decode("utf-8")
        assert "poindexter_qa_rail_skip_ratio" in text


@pytest.mark.unit
def test_render_exposition_returns_text_format():
    """Smoke check: /metrics endpoint returns bytes + the standard
    Prometheus text content-type so Alertmanager can scrape it."""
    from services.metrics_exporter import render_exposition

    body, content_type = render_exposition()
    assert isinstance(body, bytes)
    assert "text/plain" in content_type


@pytest.mark.unit
def test_exporter_registers_social_adapter_counters_at_import():
    """poindexter#455 follow-up: ``social_poster`` is otherwise imported lazily
    (only from ``publish_service`` at publish time), so its adapter counters
    were ABSENT from ``/metrics`` after every worker restart until the first
    social post — gapping any ``rate()`` / ``increase()`` panel on them across
    each restart boundary.

    ``metrics_exporter`` imports the singletons so they register on the default
    REGISTRY the moment ``/metrics`` is first served. This pins that wiring:
    the identity check fails loud if an unused-import cleanup removes it, and
    the exposition check proves the families render without any post happening.
    """
    from services import metrics_exporter as mx
    from services import social_poster as sp

    # Same singleton objects — proves the exporter imported them, not a copy.
    assert mx.SOCIAL_ADAPTER_POSTS_TOTAL is sp.SOCIAL_ADAPTER_POSTS_TOTAL
    assert mx.SOCIAL_ADAPTER_ERRORS_TOTAL is sp.SOCIAL_ADAPTER_ERRORS_TOTAL

    # Both families render on /metrics with no _bump_metric / post call.
    text = mx.render_exposition()[0].decode("utf-8")
    assert "poindexter_social_adapter_posts_total" in text
    assert "poindexter_social_adapter_errors_total" in text


# ---------------------------------------------------------------------------
# Module v1 metric-contribution loop (Glad-Labs/poindexter#490 / #565).
# The exporter calls each registered Module's optional
# ``refresh_module_metrics(pool)`` at scrape time. The loop must be generic
# (no per-module knowledge), await an awaitable result, skip modules without
# the hook, and never let one module's failure break the scrape.
# ---------------------------------------------------------------------------


class _ModWithAsyncHook:
    def __init__(self):
        self.called_with = None

    async def refresh_module_metrics(self, pool):
        self.called_with = pool


class _ModWithSyncAwaitableHook:
    """Hook is a sync method returning an awaitable (a module that does
    ``return refresh_my_metrics(pool)`` rather than ``async def``)."""

    def __init__(self):
        self.called_with = None

    def refresh_module_metrics(self, pool):
        called = {}

        async def _coro():
            called["pool"] = pool
            self.called_with = pool

        return _coro()


class _ModWithoutHook:
    pass


class _ModThatRaises:
    def refresh_module_metrics(self, pool):
        raise RuntimeError("boom")


@pytest.mark.unit
async def test_module_metrics_loop_invokes_async_and_awaitable_hooks():
    from services import metrics_exporter as mx

    async_mod = _ModWithAsyncHook()
    sync_mod = _ModWithSyncAwaitableHook()
    pool = MagicMock()

    with patch("plugins.registry.get_modules", return_value=[async_mod, sync_mod]):
        await mx._refresh_module_metrics(pool)

    assert async_mod.called_with is pool
    assert sync_mod.called_with is pool


@pytest.mark.unit
async def test_module_metrics_loop_skips_modules_without_hook():
    from services import metrics_exporter as mx

    good = _ModWithAsyncHook()
    pool = MagicMock()

    # A module lacking the hook must be silently skipped (not an error).
    with patch(
        "plugins.registry.get_modules", return_value=[_ModWithoutHook(), good]
    ):
        await mx._refresh_module_metrics(pool)

    assert good.called_with is pool


@pytest.mark.unit
async def test_module_metrics_loop_isolates_a_failing_hook():
    """One module raising must not abort the loop nor propagate — /metrics
    must keep serving."""
    from services import metrics_exporter as mx

    good = _ModWithAsyncHook()
    pool = MagicMock()

    with patch(
        "plugins.registry.get_modules",
        return_value=[_ModThatRaises(), good],
    ):
        # Must not raise.
        await mx._refresh_module_metrics(pool)

    # The good module after the failing one still ran.
    assert good.called_with is pool


@pytest.mark.unit
async def test_module_metrics_loop_tolerates_registry_failure():
    from services import metrics_exporter as mx

    pool = MagicMock()
    with patch("plugins.registry.get_modules", side_effect=RuntimeError("nope")):
        # Registry import/call failure must be swallowed.
        await mx._refresh_module_metrics(pool)
