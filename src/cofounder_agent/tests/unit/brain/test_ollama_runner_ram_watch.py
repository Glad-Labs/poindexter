"""Ollama runner host-RAM recycle watch (brain/ollama_runner_ram_watch.py).

The sibling of sidecar_ram_watch that reaches what it cannot: ollama runs as a
HOST systemd unit here, so cadvisor never sees it and `docker restart` cannot
touch it. What grows is llama-server's host-RAM prompt cache (re-measured
2026-09-25; first read as a ~6.9 MiB/request leak on 2026-08-28): 96 KiB per
cached token, capped at 8 GiB of KV, and it plateaued at 10.6 GB on
2026-09-25 with image data riding above the cap.

Two dangerous failures, pulling opposite ways. A FALSE IDLE recycles a runner
mid-request and costs a QA rail its answer (plus a 40-85 s reload). A FALSE
BUSY is what 2026-09-25 was: a GPU-0 render held the whole-box lock read for
2.5 h and the GPU-1 judge's runner grew to 10.6 GB behind it.
"""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from poindexter.brain import ollama_runner_ram_watch as ow


class _Pool:
    """Minimal asyncpg-pool stand-in: app_settings reads, audit_log writes, and
    a pg_locks table (``locks``) that the lock query filters by key."""

    def __init__(
        self,
        settings: dict | None = None,
        gpu_lock_held: bool = False,
        locks: list[dict] | None = None,
        lock_error: Exception | None = None,
    ):
        self._settings = settings or {}
        self._gpu_lock_held = gpu_lock_held
        self._locks = list(locks or [])
        self._lock_error = lock_error
        self.lock_queries: list[list[int]] = []
        self.execute = AsyncMock()

    async def fetchval(self, sql: str, *args):
        if "pg_locks" in sql:
            return self._gpu_lock_held
        key = args[0] if args else None
        return self._settings.get(key)

    async def fetch(self, sql: str, *args):
        assert "pg_locks" in sql, sql
        if self._lock_error is not None:
            raise self._lock_error
        wanted = [int(k) for k in args[0]]
        self.lock_queries.append(sorted(wanted))
        return [row for row in self._locks if row["key"] in wanted]


ENABLED = {
    ow.ENABLED_KEY: "true",
    ow.TARGETS_KEY: "ollama-vision.service|4|http://h:11435|qwen3-vl:30b",
}

# The operator stack as of 2026-09-25: device scoping on, the judge pinned to
# GPU 1, renders and the primary on GPU 0.
NODE = "pop-os"
BASE = ow.GPU_ADVISORY_LOCK_KEY
GPU0 = ow.device_lock_key(NODE, 0)
GPU1 = ow.device_lock_key(NODE, 1)
SCOPED = {
    **ENABLED,
    ow.GPU_LOCK_PER_DEVICE_KEY: "true",
    ow.GPU_LOCK_NODE_ID_KEY: NODE,
    ow.GPU_LOCK_SCOPES_KEY: '{"render": [0], "qa_judge": [1], "llm_primary": [0]}',
    ow.OLLAMA_BASE_URL_KEY: "http://h:11434",
    ow.OLLAMA_VISION_BASE_URL_KEY: "http://h:11435",
}
RENDER = "poindexter-gpu:video:media_render:cc260343:pid42"


def _lock(key: int, mode: str = "ExclusiveLock", granted: bool = True, holder: str = "x") -> dict:
    return {"key": key, "mode": mode, "granted": granted, "holder": holder}


def _render_on_gpu0(holder: str = RENDER) -> list[dict]:
    """What pg_locks held 13:31-16:04 EDT on 2026-09-25: a device-scoped render
    takes the base key SHARED and GPU 0's key exclusively."""
    return [_lock(BASE, "ShareLock", holder=holder), _lock(GPU0, holder=holder)]


def _run(coro):
    return asyncio.run(coro)


def _summary(pool, **kw):
    kw.setdefault("gpu_lock_fn", AsyncMock(return_value=False))
    kw.setdefault(
        "mem_fn",
        lambda url: {"ollama-vision.service": {"anon_gb": 9.0, "cpu_percent": 0.0}},
    )
    kw.setdefault("recycle_fn", lambda e, m, n: (True, "unloaded and re-pinned"))
    kw.setdefault("now_fn", lambda: 10_000.0)
    return _run(ow.run_ollama_runner_ram_watch_probe(pool, **kw))


@pytest.fixture(autouse=True)
def _clean_state():
    ow._reset_recycle_state()
    yield
    ow._reset_recycle_state()


class TestTargetParsing:
    """`unit:watermark:endpoint:model` — both tail fields carry their own
    colons, which a naive split(':') destroys."""

    def test_keeps_the_url_scheme_and_the_model_tag_intact(self):
        parsed = ow.parse_targets("ollama-vision.service|4|http://h:11435|qwen3-vl:30b")
        assert parsed == [
            ("ollama-vision.service", 4.0, "http://h:11435", "qwen3-vl:30b")
        ]

    def test_shipped_default_parses(self):
        """The default is a real target, not an illustration — if it stops
        parsing, the probe silently watches nothing."""
        assert len(ow.parse_targets(ow.DEFAULT_TARGETS)) == 1

    @pytest.mark.parametrize(
        "raw", ["ollama-vision.service|4", "u|notanumber|http://h:1|m", "", "|||"]
    )
    def test_malformed_entries_are_skipped_not_fatal(self, raw):
        assert ow.parse_targets(raw) == []

    def test_a_bad_entry_does_not_take_down_its_neighbours(self):
        parsed = ow.parse_targets("junk, ollama-vision.service|4|http://h:11435|m:1")
        assert [p[0] for p in parsed] == ["ollama-vision.service"]


class TestMetricParsing:
    def test_reads_anon_and_cpu_per_unit(self):
        text = (
            '# HELP ollama_runner_anon_bytes x\n'
            'ollama_runner_anon_bytes{unit="ollama-vision.service"} 10737418240\n'
            'ollama_runner_cpu_percent{unit="ollama-vision.service"} 3.5\n'
        )
        stats = ow.parse_runner_stats(text)
        assert stats["ollama-vision.service"]["anon_gb"] == pytest.approx(10.0)
        assert stats["ollama-vision.service"]["cpu_percent"] == pytest.approx(3.5)

    def test_missing_cpu_is_absent_not_zero(self):
        """CPU is a rate and is absent on the exporter's first scrape. Defaulting
        it to 0.0 would read as 'idle' and let the probe recycle a busy runner
        during that window."""
        text = 'ollama_runner_anon_bytes{unit="u"} 1073741824\n'
        assert "cpu_percent" not in ow.parse_runner_stats(text)["u"]


class TestIdleGating:
    """A false idle costs a live QA rail its answer plus an ~85s reload."""

    def test_recycles_when_over_watermark_and_idle(self):
        out = _summary(_Pool(ENABLED))
        assert out["status"] == "recycled"
        assert out["anon_gb"] == 9.0

    def test_defers_while_a_gpu_session_holds_the_lock(self):
        # gpu_lock_fn must be passed explicitly: _summary defaults it, so the
        # pool's own value would never reach the probe.
        out = _summary(_Pool(ENABLED), gpu_lock_fn=AsyncMock(return_value=True))
        assert out["status"] == "deferred"
        assert "scheduler lock" in out["detail"]

    def test_defers_when_the_lock_state_is_unknown(self):
        """Unprovable counts as busy (#3094) — an unreadable gate must never
        be read as permission."""
        out = _summary(_Pool(ENABLED), gpu_lock_fn=AsyncMock(return_value=None))
        assert out["status"] == "deferred"

    def test_defers_when_the_runner_cpu_is_busy(self):
        out = _summary(
            _Pool(ENABLED),
            mem_fn=lambda url: {
                "ollama-vision.service": {"anon_gb": 9.0, "cpu_percent": 80.0}
            },
        )
        assert out["status"] == "deferred"
        assert "CPU" in out["detail"]

    def test_defers_when_cpu_is_unknown(self):
        out = _summary(
            _Pool(ENABLED),
            mem_fn=lambda url: {"ollama-vision.service": {"anon_gb": 9.0}},
        )
        assert out["status"] == "deferred"
        assert "CPU unknown" in out["detail"]

    def test_cpu_gate_is_not_redundant_with_the_gpu_lock(self):
        """Requests that never take the GPU scheduler lock (a warm-up ping, a
        direct curl) still peg the runner. With the lock free and CPU busy the
        probe must still defer, or the second gate is decoration."""
        out = _summary(
            _Pool(ENABLED, gpu_lock_held=False),
            mem_fn=lambda url: {
                "ollama-vision.service": {"anon_gb": 9.0, "cpu_percent": 95.0}
            },
        )
        assert out["status"] == "deferred"


class TestWatermarkAndCooldown:
    def test_under_watermark_does_nothing(self):
        out = _summary(
            _Pool(ENABLED),
            mem_fn=lambda url: {
                "ollama-vision.service": {"anon_gb": 0.3, "cpu_percent": 0.0}
            },
        )
        assert out["status"] == "under_watermark"

    def test_a_fresh_runner_is_under_the_shipped_watermark(self):
        """0.30 GB measured on a fresh runner vs a 4 GB default watermark. If a
        change ever puts the watermark below a fresh runner, the probe would
        recycle in a loop and never converge."""
        watermark = ow.parse_targets(ow.DEFAULT_TARGETS)[0][1]
        assert watermark > 0.30

    def test_the_incident_footprint_would_have_tripped(self):
        """9.35 GB was the measured 2026-08-28 footprint. A watermark above it
        would mean shipping a probe that watches the incident it was written
        for (the stable-audio lesson from 2026-08-27)."""
        assert ow.parse_targets(ow.DEFAULT_TARGETS)[0][1] < 9.35

    def test_the_watermark_sits_below_the_prompt_cache_cap(self):
        """What grows is llama-server's prompt cache, capped at 8 GiB of KV
        (--cache-ram's default). A text-only workload plateaus at about the
        cap, so a watermark at or above it would never fire on one; vision
        entries carry ~46 MiB each above it, which is how 2026-09-25 reached
        10.6 GB."""
        assert ow.parse_targets(ow.DEFAULT_TARGETS)[0][1] < 8.0

    def test_cooldown_blocks_a_second_recycle(self):
        pool = _Pool({**ENABLED, ow.COOLDOWN_MINUTES_KEY: "120"})
        assert _summary(pool, now_fn=lambda: 10_000.0)["status"] == "recycled"
        again = _summary(pool, now_fn=lambda: 10_060.0)
        assert again["status"] == "under_watermark"
        assert "cooldown" in again["detail"]

    def test_cooldown_expires(self):
        pool = _Pool({**ENABLED, ow.COOLDOWN_MINUTES_KEY: "10"})
        assert _summary(pool, now_fn=lambda: 10_000.0)["status"] == "recycled"
        assert _summary(pool, now_fn=lambda: 10_000.0 + 601)["status"] == "recycled"


class TestFailureModes:
    def test_disabled_by_default_setting(self):
        out = _summary(_Pool({}))
        assert out["status"] == "disabled"

    def test_ships_disabled(self):
        """Other installs run ollama differently; restarting someone's LLM
        endpoint uninvited is a bad default."""
        assert ow.DEFAULT_ENABLED is False

    def test_unreachable_exporter_recycles_nothing(self):
        """Cannot tell a healthy runner from a leaking one — so do nothing, and
        say so, rather than treat 'no data' as 'no problem'."""
        out = _summary(_Pool(ENABLED), mem_fn=lambda url: None)
        assert out["ok"] is False
        assert out["status"] == "exporter_unreachable"

    def test_absent_unit_is_not_an_error(self):
        """No runner = the model is not loaded = nothing to reclaim."""
        out = _summary(_Pool(ENABLED), mem_fn=lambda url: {})
        assert out["status"] == "under_watermark"

    def test_recycle_failure_is_reported_and_not_stamped(self):
        pool = _Pool(ENABLED)
        out = _summary(pool, recycle_fn=lambda e, m, n: (False, "connection refused"))
        assert out["ok"] is False
        assert out["status"] == "recycle_failed"
        # A failed attempt must not start the cooldown, or a broken endpoint
        # would be retried once every two hours instead of every cycle.
        assert "ollama-vision.service" not in ow._last_recycle_monotonic
        # ...and the finding must say so, not promise a retry "after the cooldown".
        body = " ".join(str(c.args) for c in pool.execute.await_args_list)
        assert "next cycle" in body
        assert "retried after" not in body

    def test_only_the_fattest_is_recycled_per_cycle(self):
        pool = _Pool(
            {
                **ENABLED,
                ow.TARGETS_KEY: (
                    "ollama-vision.service|4|http://h:11435|m:1,"
                    "ollama-primary.service|4|http://h:11434|m:2"
                ),
            }
        )
        out = _summary(
            pool,
            mem_fn=lambda url: {
                "ollama-vision.service": {"anon_gb": 9.0, "cpu_percent": 0.0},
                "ollama-primary.service": {"anon_gb": 12.0, "cpu_percent": 0.0},
            },
        )
        assert out["unit"] == "ollama-primary.service"


class TestUrlSchemeGuard:
    """Both URLs come from app_settings. urlopen honours file:// and friends,
    so a typo would silently read a local file and parse it as Prometheus text
    (or POST a recycle at it) instead of failing loudly."""

    @pytest.mark.parametrize("url", ["http://h:9835/metrics", "https://h/metrics"])
    def test_http_urls_pass(self, url):
        assert ow._require_http_url(url, "x") == url

    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "ftp://h/x", "/just/a/path", ""]
    )
    def test_other_schemes_are_rejected(self, url):
        with pytest.raises(ValueError):
            ow._require_http_url(url, "x")

    def test_a_bad_exporter_url_is_a_clean_no_recycle(self):
        """The guard must surface as 'unreachable' — nothing recycled — rather
        than propagating and killing the whole probe cycle."""
        pool = _Pool({**ENABLED, ow.EXPORTER_URL_KEY: "file:///etc/passwd"})
        out = _run(
            ow.run_ollama_runner_ram_watch_probe(
                pool,
                gpu_lock_fn=AsyncMock(return_value=False),
                recycle_fn=lambda e, m, n: (True, "ok"),
                now_fn=lambda: 1.0,
            )
        )
        assert out["status"] == "exporter_unreachable"
        assert out["ok"] is False


def test_gpu_lock_key_matches_the_worker_constant():
    """The brain duplicates this int64 by hand — pin it to the worker.

    This probe gates on `pg_advisory_lock(GPU_ADVISORY_LOCK_KEY)` being free
    before recycling an ollama runner. The brain runs stdlib + asyncpg and
    cannot import the worker package, so the value is copied. A copy that
    drifts does not raise — it reads "GPU idle" during a render and recycles a
    live model server mid-generation.

    Added 2026-08-28 by `scripts/ci/gpu_lock_key_contract_lint.py`, which
    found this duplicate unpinned.
    """
    from poindexter.services.gpu_scheduler import GPU_ADVISORY_LOCK_KEY

    assert ow.GPU_ADVISORY_LOCK_KEY == GPU_ADVISORY_LOCK_KEY


class TestRepinContext:
    """The re-pin loads at pinned_llm_endpoint_num_ctx (2026-09-25).

    Ollama reloads a resident model for any other num_ctx. The re-pin used to
    send none, so it loaded at the instance default (32768 on the 24 GB card)
    while the rails run at 16384, and every recycle cost two reloads: its own,
    then the next rail call's. Four times on 2026-09-25 alone.
    """

    def test_the_probe_repins_at_the_configured_size(self):
        seen: list[tuple[str, str, int]] = []
        pool = _Pool({**ENABLED, ow.PINNED_NUM_CTX_KEY: "24576"})

        out = _summary(pool, recycle_fn=lambda e, m, n: seen.append((e, m, n)) or (True, "ok"))

        assert out["status"] == "recycled"
        assert seen == [("http://h:11435", "qwen3-vl:30b", 24576)]

    def test_an_unset_row_repins_at_the_declared_default(self):
        """The brain may run before any worker has seeded the key."""
        seen: list[int] = []
        _summary(_Pool(ENABLED), recycle_fn=lambda e, m, n: seen.append(n) or (True, "ok"))
        assert seen == [ow.DEFAULT_PINNED_NUM_CTX]

    def test_the_recycled_finding_records_the_size(self):
        pool = _Pool({**ENABLED, ow.PINNED_NUM_CTX_KEY: "16384"})
        _summary(pool)
        params = [c.args for c in pool.execute.await_args_list]
        assert any("16384" in str(p) for p in params), params

    @staticmethod
    def _capture_posts(monkeypatch) -> list[dict]:
        bodies: list[dict] = []

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self):
                return b"{}"

        def _urlopen(req, timeout=None):
            import json as _json

            bodies.append(_json.loads(req.data.decode()))
            return _Resp()

        monkeypatch.setattr(ow.urllib.request, "urlopen", _urlopen)
        return bodies

    def test_recycle_runner_unloads_then_repins_at_num_ctx(self, monkeypatch):
        bodies = self._capture_posts(monkeypatch)

        ok, detail = ow.recycle_runner("http://h:11435", "qwen3-vl:30b", 16384)

        assert ok is True
        assert "num_ctx=16384" in detail
        unload, repin = bodies
        assert unload == {"model": "qwen3-vl:30b", "keep_alive": 0}
        assert repin["keep_alive"] == -1, "a re-pin that can be evicted is not a pin"
        assert repin["options"] == {"num_ctx": 16384}, (
            "without num_ctx the re-pin loads at the instance default and the "
            "next rail call reloads the model at its own size"
        )

    def test_recycle_runner_with_zero_leaves_the_size_to_the_instance(self, monkeypatch):
        bodies = self._capture_posts(monkeypatch)

        ow.recycle_runner("http://h:11435", "qwen3-vl:30b", 0)

        assert "options" not in bodies[1]


def test_pinned_ctx_key_and_default_match_the_worker():
    """The brain copies the worker's key and default by hand (it runs stdlib +
    asyncpg). A drifted key re-pins at the declared default forever; a drifted
    default re-pins at a size no rail asks for — each is the double reload this
    replaced, silently."""
    from poindexter.services.llm_providers.dispatcher import PINNED_NUM_CTX_KEY
    from poindexter.services.settings_defaults import DEFAULTS

    assert ow.PINNED_NUM_CTX_KEY == PINNED_NUM_CTX_KEY
    assert ow.DEFAULT_PINNED_NUM_CTX == int(DEFAULTS[PINNED_NUM_CTX_KEY])


class TestLockScopeResolution:
    """Which advisory keys the gate reads for a target. Every doubt reads the
    whole box, which can only defer a recycle, never permit one mid-call."""

    @staticmethod
    def _resolve(endpoint: str = "http://h:11435", **over):
        cfg = {
            "per_device_enabled": True,
            "scopes_raw": SCOPED[ow.GPU_LOCK_SCOPES_KEY],
            "node_id": NODE,
            "primary_url": "http://h:11434",
            "vision_url": "http://h:11435",
        }
        cfg.update(over)
        return ow.resolve_lock_scope(endpoint, **cfg)

    def test_the_judge_endpoint_reads_gpu_1_only(self):
        scope = self._resolve()
        assert scope.keys == (GPU1,)
        assert scope.label == "GPU 1 (qa_judge)"

    def test_the_primary_endpoint_reads_the_primarys_card(self):
        scope = self._resolve("http://h:11434")
        assert scope.keys == (GPU0,)
        assert scope.label == "GPU 0 (llm_primary)"

    def test_an_empty_primary_row_means_the_default_primary_url(self):
        scope = self._resolve("http://localhost:11434", primary_url="")
        assert scope.label == "GPU 0 (llm_primary)"

    def test_an_empty_scope_row_means_the_default_map(self):
        assert self._resolve(scopes_raw="").keys == (GPU1,)

    def test_a_widened_judge_scope_reads_both_cards(self):
        scope = self._resolve(
            scopes_raw='{"render": [0], "qa_judge": [1, 0], "llm_primary": [0]}'
        )
        assert scope.keys == tuple(sorted((GPU0, GPU1)))
        assert scope.label == "GPU 0, 1 (qa_judge)"

    def test_a_role_on_no_card_reads_no_card_key(self):
        """An empty list is the lock's "occupies no GPU": no card key can cover
        the target, so only a whole-box session can."""
        scope = self._resolve(scopes_raw='{"qa_judge": []}')
        assert scope.keys == ()

    def test_loopback_spellings_name_the_same_instance(self):
        scope = self._resolve(
            "http://LOCALHOST:11435/", vision_url="http://host.docker.internal:11435"
        )
        assert scope.keys == (GPU1,)

    def test_the_primary_wins_a_tie(self):
        """A vision URL pointing back at the primary names the primary's
        server, on the primary's cards."""
        scope = self._resolve("http://h:11434", vision_url="http://h:11434")
        assert scope.label == "GPU 0 (llm_primary)"

    @pytest.mark.parametrize(
        ("endpoint", "over", "why"),
        [
            ("http://h:11435", {"per_device_enabled": False}, "gpu_lock_per_device_enabled is off"),
            ("http://h:11435", {"node_id": ""}, "gpu_lock_node_id is unset"),
            ("http://h:11435", {"node_id": "   "}, "gpu_lock_node_id is unset"),
            ("http://h:11435", {"scopes_raw": "not json"}, "unparseable"),
            ("http://h:11435", {"scopes_raw": "[0, 1]"}, "unparseable"),
            ("http://h:11435", {"scopes_raw": '{"qa_judge": 1}'}, "unparseable"),
            ("http://h:11435", {"scopes_raw": '{"render": [0]}'}, "missing from gpu_lock_scopes"),
            ("http://elsewhere:11435", {}, "is neither"),
            ("http://h:11435", {"vision_url": ""}, "is neither"),
        ],
    )
    def test_every_doubt_reads_the_whole_box(self, endpoint, over, why):
        scope = self._resolve(endpoint, **over)
        assert scope.keys is None
        assert scope.label.startswith("every GPU")
        assert why in scope.label


class TestLockRows:
    """Which pg_locks rows make the target busy."""

    def test_a_render_on_another_card_leaves_the_judge_free(self):
        assert ow.lock_rows_busy(_render_on_gpu0(), (GPU1,)) == []

    def test_a_session_on_the_judges_card_is_busy(self):
        rows = [_lock(BASE, "ShareLock", holder="cap"), _lock(GPU1, holder="cap")]
        assert ow.lock_rows_busy(rows, (GPU1,)) == ["cap"]

    def test_a_queued_session_counts_and_says_so(self):
        """Queued behind a holder means about to call the judge."""
        rows = [_lock(GPU1, granted=False, holder="cap")]
        assert ow.lock_rows_busy(rows, (GPU1,)) == ["cap (queued)"]

    def test_an_exclusive_base_session_claims_every_card(self):
        """An unscoped caller (scoping off in its process, no node id, the
        brain's own health probes) takes the base key exclusively, and so
        claims the judge's card too."""
        rows = [_lock(BASE, "ExclusiveLock", holder="brain")]
        assert ow.lock_rows_busy(rows, (GPU1,)) == ["brain"]

    def test_a_shared_base_row_alone_is_not_busy(self):
        """Every scoped holder takes the base key shared; the card key says
        where it is, and here it is not on the judge's card."""
        assert ow.lock_rows_busy([_lock(BASE, "ShareLock")], (GPU1,)) == []

    def test_the_whole_box_read_counts_any_base_row(self):
        assert ow.lock_rows_busy(_render_on_gpu0(), None) == [RENDER]

    def test_an_untagged_holder_is_still_a_holder(self):
        assert ow.lock_rows_busy([_lock(GPU1, holder="")], (GPU1,)) == [
            "an untagged session"
        ]


class TestGpuLockHeldQuery:
    def test_it_asks_for_the_base_key_and_the_targets_card_keys(self):
        pool = _Pool()
        assert _run(ow.gpu_lock_held(pool, ow.LockScope((GPU1,), "GPU 1 (qa_judge)"))) is False
        assert pool.lock_queries == [sorted([BASE, GPU1])]

    def test_the_default_is_the_whole_box_read(self):
        pool = _Pool(locks=_render_on_gpu0())
        assert _run(ow.gpu_lock_held(pool)) is True
        assert pool.lock_queries == [[BASE]]

    def test_a_query_failure_is_unknown_not_free(self):
        pool = _Pool(lock_error=RuntimeError("pool closed"))
        assert _run(ow.gpu_lock_held(pool, ow.LockScope((GPU1,), "x"))) is None

    def test_the_holder_is_logged(self, caplog):
        pool = _Pool(locks=[_lock(GPU1, holder="poindexter-gpu:ollama:caption_image:ab:pid3")])
        with caplog.at_level(logging.INFO, logger=ow.logger.name):
            _run(ow.gpu_lock_held(pool, ow.LockScope((GPU1,), "GPU 1 (qa_judge)")))
        assert "caption_image" in caplog.text
        assert "GPU 1 (qa_judge)" in caplog.text


class TestScopedIdleGate:
    """The real lock read end to end, against the lock sets of 2026-09-25."""

    @staticmethod
    def _probe(pool, **kw):
        kw.setdefault("gpu_lock_fn", None)  # None = the real, scoped read
        return _summary(pool, **kw)

    def test_a_render_on_gpu0_no_longer_defers_the_gpu1_judge(self):
        """The incident: a render held GPU 0 13:31-16:04 EDT and the judge's
        runner reached 10.6 GB behind the whole-box read."""
        pool = _Pool(SCOPED, locks=_render_on_gpu0())
        out = self._probe(
            pool,
            mem_fn=lambda url: {"ollama-vision.service": {"anon_gb": 10.6, "cpu_percent": 0.0}},
        )
        assert out["status"] == "recycled"
        assert out["lock_scope"] == "GPU 1 (qa_judge)"

    def test_a_session_on_the_judges_card_defers(self):
        locks = [
            *_render_on_gpu0(),
            _lock(BASE, "ShareLock", holder="cap"),
            _lock(GPU1, holder="cap"),
        ]
        out = self._probe(_Pool(SCOPED, locks=locks))
        assert out["status"] == "deferred"
        assert "scheduler lock covering GPU 1 (qa_judge)" in out["detail"]

    def test_a_whole_box_session_defers(self):
        locks = [_lock(BASE, "ExclusiveLock", holder="poindexter-gpu:brain:content_gen:pid9")]
        assert self._probe(_Pool(SCOPED, locks=locks))["status"] == "deferred"

    def test_with_scoping_off_any_session_still_defers(self):
        """Every caller takes the base key exclusively then, and the gate reads
        the whole box exactly as it did before device scoping."""
        pool = _Pool(
            {**SCOPED, ow.GPU_LOCK_PER_DEVICE_KEY: "false"},
            locks=[_lock(BASE, holder=RENDER)],
        )
        out = self._probe(pool)
        assert out["status"] == "deferred"
        assert "gpu_lock_per_device_enabled is off" in out["detail"]

    def test_an_unresolvable_scope_reads_the_whole_box(self):
        """No node id: no card keys can be derived, so a GPU-0 render defers
        the recycle again, exactly as before this fix."""
        pool = _Pool({**SCOPED, ow.GPU_LOCK_NODE_ID_KEY: ""}, locks=_render_on_gpu0())
        out = self._probe(pool)
        assert out["status"] == "deferred"
        assert pool.lock_queries == [[BASE]]

    def test_unpinning_the_judge_restores_the_deferral(self):
        """qa_judge widened to [0, 1] says the judge may share GPU 0 with the
        render, so the render must defer the recycle again."""
        scopes = '{"render": [0], "qa_judge": [0, 1], "llm_primary": [0]}'
        pool = _Pool({**SCOPED, ow.GPU_LOCK_SCOPES_KEY: scopes}, locks=_render_on_gpu0())
        assert self._probe(pool)["status"] == "deferred"

    def test_the_cpu_gate_still_defers_during_a_gpu0_render(self):
        """QA rails and qa_shot_vision take no lock (#2646), so the runner's
        CPU is what sees them mid-request, render or no render."""
        pool = _Pool(SCOPED, locks=_render_on_gpu0())
        out = self._probe(
            pool,
            mem_fn=lambda url: {"ollama-vision.service": {"anon_gb": 9.0, "cpu_percent": 85.0}},
        )
        assert out["status"] == "deferred"
        assert "CPU" in out["detail"]

    def test_a_lock_query_failure_defers(self):
        out = self._probe(_Pool(SCOPED, lock_error=RuntimeError("pool closed")))
        assert out["status"] == "deferred"
        assert "unknown" in out["detail"]

    def test_the_lock_gate_off_skips_the_read(self):
        pool = _Pool(
            {**SCOPED, ow.REQUIRE_GPU_LOCK_FREE_KEY: "false"},
            locks=[_lock(GPU1, holder="cap")],
        )
        out = self._probe(pool)
        assert out["status"] == "recycled"
        assert pool.lock_queries == []
        # and the finding does not claim a lock proof it never made
        body = " ".join(str(c.args) for c in pool.execute.await_args_list)
        assert "lock gate is off" in body

    def test_the_recycled_finding_names_the_scope_it_read(self):
        pool = _Pool(SCOPED, locks=_render_on_gpu0())
        self._probe(pool)
        params = [c.args for c in pool.execute.await_args_list]
        assert any("GPU 1 (qa_judge)" in str(p) for p in params), params

    def test_a_deferral_is_logged_with_its_reason(self, caplog):
        """The heartbeat records only ok/issue, so a deferral nobody logs is
        indistinguishable from a probe with nothing to do."""
        pool = _Pool(SCOPED, locks=[_lock(GPU1, holder="poindexter-gpu:ollama:caption_image:ab:pid3")])
        with caplog.at_level(logging.INFO, logger=ow.logger.name):
            self._probe(pool)
        assert "deferred" in caplog.text
        assert "GPU 1 (qa_judge)" in caplog.text


class TestLockScopeMatchesTheWorker:
    """Every mirrored piece, pinned to the worker original.

    The brain cannot import the worker package, so these are copies, and a
    drifted copy raises nothing: the gate reads keys no caller takes and calls
    a busy judge idle. The worker side runs on a SiteConfig keyed by the
    BRAIN's setting-key constants with non-default values, so a drifted key
    name makes the worker fall back to its default and the comparison fails.
    """

    JUDGE_URL = "http://host.docker.internal:11435"
    JUDGE_MODEL = "qwen3-vl:30b-a3b-instruct"

    def _values(self, scopes: str) -> dict[str, str]:
        return {
            ow.GPU_LOCK_PER_DEVICE_KEY: "true",  # worker default: false
            ow.GPU_LOCK_NODE_ID_KEY: "node-a",  # worker default: hostname or ""
            ow.GPU_LOCK_SCOPES_KEY: scopes,
            ow.OLLAMA_BASE_URL_KEY: "http://host.docker.internal:11434",
            ow.OLLAMA_VISION_BASE_URL_KEY: self.JUDGE_URL,  # worker default: ""
            "plugin.llm_provider.litellm": json.dumps(
                {"config": {"model_api_base_overrides": {f"ollama/{self.JUDGE_MODEL}": self.JUDGE_URL}}}
            ),
        }

    @staticmethod
    def _worker(monkeypatch, values: dict[str, str]):
        from poindexter.services import gpu_scheduler as gs
        from poindexter.services.site_config import SiteConfig

        cfg = SiteConfig(initial_config=dict(values))
        monkeypatch.setattr(gs, "_sc", lambda: cfg)
        return gs

    def _brain_scope(self, values: dict[str, str], endpoint: str) -> ow.LockScope:
        # Read through the brain's own setting keys, as the probe does.
        cfg = _run(ow._read_lock_scope_config(_Pool(values)))
        return ow.resolve_lock_scope(endpoint, **cfg)

    # Non-default cards everywhere, so the shipped defaults cannot mask a drift.
    SPLIT = '{"render": [2], "qa_judge": [3], "llm_primary": [2]}'

    def test_the_judge_endpoint_reads_the_keys_a_judge_caller_takes(self, monkeypatch):
        values = self._values(self.SPLIT)
        gs = self._worker(monkeypatch, values)
        scope = self._brain_scope(values, self.JUDGE_URL)
        assert list(scope.keys) == gs.resolve_lock_keys("ollama", self.JUDGE_MODEL)
        assert gs.ollama_host_devices(self.JUDGE_URL) == frozenset({3})

    def test_the_primary_endpoint_reads_the_keys_a_primary_caller_takes(self, monkeypatch):
        values = self._values(self.SPLIT)
        gs = self._worker(monkeypatch, values)
        scope = self._brain_scope(values, "http://host.docker.internal:11434")
        assert list(scope.keys) == gs.resolve_lock_keys("ollama", "some-writer:latest")

    @pytest.mark.parametrize("owner", ["video", "image_gen"])
    def test_a_render_takes_none_of_the_judges_keys(self, monkeypatch, owner):
        values = self._values(self.SPLIT)
        gs = self._worker(monkeypatch, values)
        scope = self._brain_scope(values, self.JUDGE_URL)
        assert not set(scope.keys) & set(gs.resolve_lock_keys(owner, None))

    def test_a_widened_judge_shares_a_key_with_renders(self, monkeypatch):
        values = self._values('{"render": [2], "qa_judge": [2, 3], "llm_primary": [2]}')
        gs = self._worker(monkeypatch, values)
        scope = self._brain_scope(values, self.JUDGE_URL)
        assert set(scope.keys) & set(gs.resolve_lock_keys("video", None))

    def test_with_scoping_off_the_worker_takes_only_the_base_key(self, monkeypatch):
        """...which the brain's whole-box read is built for."""
        values = {**self._values(self.SPLIT), ow.GPU_LOCK_PER_DEVICE_KEY: "false"}
        gs = self._worker(monkeypatch, values)
        assert gs.resolve_lock_keys("ollama", self.JUDGE_MODEL) == [gs.GPU_ADVISORY_LOCK_KEY]
        assert self._brain_scope(values, self.JUDGE_URL).keys is None

    def test_device_key_derivation(self):
        from poindexter.services import gpu_scheduler as gs

        for node in ("pop-os", "node-a", "node:with:colons", ""):
            for card in (0, 1, 2, 7):
                assert ow.device_lock_key(node, card) == gs.device_lock_key(node, card)

    def test_the_derivation_matches_what_prod_pg_locks_showed(self):
        """Read off pg_locks live: GPU 1 during the 2026-08-30 rollout, GPU 0
        under a video_director call on 2026-09-25."""
        assert (GPU0, GPU1) == (10_738_779_002, 11_124_470_800)

    def test_default_scope_map(self):
        from poindexter.services.gpu_scheduler import DEFAULT_GPU_LOCK_SCOPES

        assert ow.DEFAULT_GPU_LOCK_SCOPES == DEFAULT_GPU_LOCK_SCOPES

    def test_default_primary_url(self):
        from poindexter.services.bootstrap_defaults import DEFAULT_OLLAMA_URL

        assert ow.DEFAULT_OLLAMA_URL == DEFAULT_OLLAMA_URL

    @pytest.mark.parametrize(
        "url",
        [
            "http://host.docker.internal:11435",
            "HTTP://Host.Docker.Internal:11435/",
            "http://localhost:11435",
            "http://127.0.0.1:11434/",
            "  http://h:1  ",
            "http://10.0.0.5:11435",
            "",
            None,
        ],
    )
    def test_canonical_url(self, url):
        from poindexter.services import gpu_scheduler as gs

        assert ow.canonical_base_url(url) == gs._canonical_base_url(url)

    @pytest.mark.parametrize(
        ("primary", "vision", "endpoint"),
        [
            ("http://h:11434", "http://h:11435", "http://h:11435"),
            ("http://h:11434", "http://h:11435", "http://h:11434"),
            ("http://h:11434", "http://h:11434", "http://h:11434"),
            ("http://h:11434", "", "http://h:11435"),
            ("", "http://h:11435", "http://localhost:11434"),
            ("http://localhost:11434", "http://127.0.0.1:11435", "http://host.docker.internal:11435/"),
            ("http://h:11434", "http://h:11435", "http://other:11435"),
        ],
    )
    def test_endpoint_role(self, monkeypatch, primary, vision, endpoint):
        gs = self._worker(
            monkeypatch,
            {ow.OLLAMA_BASE_URL_KEY: primary, ow.OLLAMA_VISION_BASE_URL_KEY: vision},
        )
        brain = ow.endpoint_role(endpoint, primary_url=primary, vision_url=vision)
        assert brain == gs.ollama_host_role(endpoint)
