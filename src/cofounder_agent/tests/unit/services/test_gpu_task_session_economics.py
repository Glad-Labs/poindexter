"""``gpu_task_sessions`` records the cards a session ran on, at their real draw.

Three defects shared one row, measured on the operator box on 2026-09-25:

1. **Wrong card.** Every row said ``gpu_model = "RTX 5090"`` (a literal) and
   sampled ``pipeline_gpu_index`` (GPU 0). Once device scoping went live
   (2026-08-31), the qwen3-vl judge ran on the RTX 3090 (GPU 1): 34
   ``caption_image`` sessions in 7 days were recorded as 5090 work at the
   5090's draw.
2. **Wrong moment.** Power was one reading taken at release, after the work.
   A 51-minute render averaged 307.7 W on GPU 0 and was recorded at 36.8 W.
3. **Wrong rate.** The row read ``electricity_rate_kwh_usd``, a key nothing
   seeds, so every row was priced at the 0.12 code default while the live
   ``electricity_rate_kwh`` read 0.2883.

What these tests pin: the cards come from the same scope claim the lock uses
(and agree with the keys it takes), the figures are averaged over the hold on
exactly those cards, the model name comes from the exporter, and the price
comes from the key the rest of the cost ledger reads.
"""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import gpu_scheduler as gs
from poindexter.services.gpu_scheduler import GPUScheduler
from poindexter.services.site_config import SiteConfig
from tests.unit._nonempty import nonempty

_JUDGE_MODEL = "ollama/qwen3-vl:30b-a3b-instruct"
_WRITER_MODEL = "gemma-4-31B-it-qat:latest"

#: The operator box, as the prod rows read on 2026-09-25.
_PROD = {
    "gpu_lock_per_device_enabled": "true",
    "gpu_lock_node_id": "test-node",
    "gpu_lock_scopes": json.dumps(
        {"render": [0], "qa_judge": [1], "llm_primary": [0]}
    ),
    "pipeline_gpu_index": "0",
    "plugin.llm_provider.litellm": json.dumps(
        {"config": {"model_api_base_overrides": {
            _JUDGE_MODEL: "http://host.docker.internal:11435",
        }}}
    ),
}


@pytest.fixture
def config(monkeypatch):
    """Install a SiteConfig for the module-level helpers and the scheduler."""
    # SiteConfig.get falls back to the upper-cased env var for a missing key;
    # keep every key a test leaves unset actually unset.
    for key in (*_PROD, "electricity_rate_kwh", "electricity_rate_kwh_usd"):
        monkeypatch.delenv(key.upper(), raising=False)

    def _apply(**values):
        cfg = SiteConfig(initial_config={str(k): str(v) for k, v in values.items()})
        monkeypatch.setattr(gs, "_sc", lambda: cfg)
        return cfg

    return _apply


# --- which cards ---------------------------------------------------------------


def test_scoping_off_reads_the_pipeline_card(config):
    config(gpu_lock_per_device_enabled="false", pipeline_gpu_index="1")
    assert gs.resolve_session_devices("ollama", _JUDGE_MODEL) == [1]
    assert gs.resolve_session_devices("image_gen", None) == [1]


def test_scoping_off_is_the_default(config):
    """No scoping setting at all keeps the single pipeline card, as before."""
    config()
    assert gs.resolve_session_devices("ollama", _JUDGE_MODEL) == [0]


def test_judge_session_is_attributed_to_gpu_1(config):
    config(**_PROD)
    assert gs.resolve_session_devices("ollama", _JUDGE_MODEL) == [1]
    # Call sites pass the bare name too; the override map is prefixed.
    assert gs.resolve_session_devices("ollama", "qwen3-vl:30b-a3b-instruct") == [1]


def test_writer_and_render_sessions_are_attributed_to_gpu_0(config):
    config(**_PROD)
    assert gs.resolve_session_devices("ollama", _WRITER_MODEL) == [0]
    assert gs.resolve_session_devices("image_gen", "z_image_turbo") == [0]
    assert gs.resolve_session_devices("video", "shot_list_render") == [0]


@pytest.mark.parametrize("owner", ["", "wan", "some_new_owner"])
def test_unknown_owner_is_attributed_to_every_card(config, owner):
    """The lock takes every device key for a caller it cannot place."""
    config(**_PROD)
    assert gs.resolve_session_devices(owner, None) == [0, 1]


def test_role_missing_from_the_map_is_every_mapped_card(config):
    config(**{**_PROD, "gpu_lock_scopes": json.dumps(
        {"render": [0], "llm_primary": [2]}
    )})
    # The judge model still resolves to qa_judge, which this map lacks.
    assert gs.resolve_session_devices("ollama", _JUDGE_MODEL) == [0, 2]


def test_multi_card_role_names_every_card_of_the_role(config):
    config(**{**_PROD, "gpu_lock_scopes": json.dumps(
        {"render": [0], "qa_judge": [1], "llm_primary": [1, 0]}
    )})
    assert gs.resolve_session_devices("ollama", _WRITER_MODEL) == [0, 1]


def test_empty_scope_occupies_no_gpu(config):
    config(**{**_PROD, "gpu_lock_scopes": json.dumps(
        {"render": [0], "qa_judge": [1], "llm_primary": []}
    )})
    assert gs.resolve_session_devices("ollama", _WRITER_MODEL) == []


def test_unparseable_map_falls_back_to_the_pipeline_card(config):
    """An unreadable row is not a hardware claim, so it names no split."""
    config(**{**_PROD, "gpu_lock_scopes": "{not json", "pipeline_gpu_index": "0"})
    assert gs.resolve_session_devices("ollama", _JUDGE_MODEL) == [0]


def test_blank_map_uses_the_shipped_scopes(config):
    config(**{**_PROD, "gpu_lock_scopes": ""})
    assert gs.resolve_session_devices("ollama", _JUDGE_MODEL) == [
        int(i) for i in gs.DEFAULT_GPU_LOCK_SCOPES["qa_judge"]
    ]


@pytest.mark.parametrize(
    ("owner", "model"),
    [
        ("image_gen", "z_image_turbo"),
        ("video", "shot_list_render"),
        ("ollama", _WRITER_MODEL),
        ("ollama", _JUDGE_MODEL),
        ("wan", None),
        ("", None),
    ],
)
def test_attribution_names_exactly_the_cards_the_lock_holds(config, owner, model):
    """Derived, not hand-listed: the row's cards ARE the lock's device keys.

    If the two ever disagree, a session is costed on a card it did not hold.
    """
    config(**_PROD)
    held = set(gs.resolve_lock_keys(owner, model))
    attributed = {
        gs.device_lock_key("test-node", i)
        for i in gs.resolve_session_devices(owner, model)
    }
    assert attributed == held


# --- what the cards drew ------------------------------------------------------


class _FakePrometheus:
    """Answers the sampler's queries from per-card tables and records them."""

    def __init__(self, *, avg=None, peak=None, util=None, names=None, fail=False):
        self.avg = avg or {}
        self.peak = peak or {}
        self.util = util or {}
        self.names = names or {}
        self.fail = fail
        self.queries: list[tuple[str, str | None]] = []

    async def __call__(self, query: str, *, metric: str | None = None):
        self.queries.append((query, metric))
        if self.fail:
            return None
        if query.startswith("nvidia_gpu_info"):
            return [
                {"metric": {"gpu": str(i), "name": n, "uuid": f"GPU-{i}"},
                 "value": [0, "1"]}
                for i, n in self.names.items()
            ]
        if "max_over_time" in query:
            table = self.peak
        elif "nvidia_gpu_utilization_percent" in query:
            table = self.util
        else:
            table = self.avg
        return [
            {"metric": {"gpu": str(i)}, "value": [0, str(v)]} for i, v in table.items()
        ]


def _scheduler_with(fake: _FakePrometheus) -> GPUScheduler:
    scheduler = GPUScheduler()
    scheduler._query_prometheus_vector = fake  # type: ignore[method-assign]
    return scheduler


async def test_judge_session_samples_gpu_1_over_the_hold():
    fake = _FakePrometheus(
        avg={1: 212.4}, peak={1: 318.0}, util={1: 71.5},
        names={1: "NVIDIA GeForce RTX 3090"},
    )
    sample = await _scheduler_with(fake)._sample_session_gpus([1], 107.3)

    assert sample == gs._SessionGpuSample(
        gpu_model="NVIDIA GeForce RTX 3090",
        avg_utilization_pct=71.5,
        avg_power_watts=212.4,
        peak_power_watts=318.0,
    )
    queries = [q for q, _ in fake.queries]
    # Averaged over the session's own duration (rounded UP to whole seconds,
    # the only unit PromQL ranges take), on GPU 1 only, falling back to the
    # latest sample when the hold was shorter than a scrape.
    assert (
        'avg by (gpu) (avg_over_time(nvidia_gpu_power_draw_watts{gpu=~"1"}[108s]) '
        'or nvidia_gpu_power_draw_watts{gpu=~"1"})'
    ) in queries
    assert (
        'max by (gpu) (max_over_time(nvidia_gpu_power_draw_watts{gpu=~"1"}[108s]) '
        'or nvidia_gpu_power_draw_watts{gpu=~"1"})'
    ) in queries
    assert (
        'avg by (gpu) (avg_over_time(nvidia_gpu_utilization_percent{gpu=~"1"}[108s]) '
        'or nvidia_gpu_utilization_percent{gpu=~"1"})'
    ) in queries
    assert 'nvidia_gpu_info{gpu=~"1"}' in queries


async def test_findings_are_keyed_on_the_metric_family_not_the_expression():
    """The window changes every call; a key built from it would never dedup."""
    fake = _FakePrometheus()
    await _scheduler_with(fake)._sample_session_gpus([0], 12.0)
    assert sorted(metric for _, metric in fake.queries) == [
        "nvidia_gpu_info",
        "nvidia_gpu_power_draw_watts",
        "nvidia_gpu_power_draw_watts",
        "nvidia_gpu_utilization_percent",
    ]


@pytest.mark.parametrize("duration", [0.0, 0.2, 1.0])
async def test_window_is_at_least_one_whole_second(duration):
    fake = _FakePrometheus()
    await _scheduler_with(fake)._sample_session_gpus([0], duration)
    windowed = [q for q, _ in fake.queries if "_over_time" in q]
    for query in nonempty(windowed, "windowed queries"):
        assert "[1s]" in query


async def test_several_cards_sum_power_and_average_utilisation():
    fake = _FakePrometheus(
        avg={0: 300.0, 1: 40.0}, peak={0: 410.0, 1: 250.0},
        util={0: 90.0, 1: 10.0},
        names={0: "NVIDIA GeForce RTX 5090", 1: "NVIDIA GeForce RTX 3090"},
    )
    sample = await _scheduler_with(fake)._sample_session_gpus([1, 0], 60.0)

    assert sample.avg_power_watts == pytest.approx(340.0)
    assert sample.peak_power_watts == pytest.approx(660.0)  # an upper bound
    assert sample.avg_utilization_pct == pytest.approx(50.0)
    assert sample.gpu_model == "NVIDIA GeForce RTX 5090 + NVIDIA GeForce RTX 3090"
    assert all('gpu=~"0|1"' in q for q, _ in fake.queries)


async def test_an_unread_card_leaves_the_figure_unknown_not_partial():
    """Summing only the cards that answered would understate the session."""
    fake = _FakePrometheus(
        avg={0: 300.0}, peak={0: 410.0}, util={0: 90.0},
        names={0: "NVIDIA GeForce RTX 5090"},
    )
    sample = await _scheduler_with(fake)._sample_session_gpus([0, 1], 60.0)
    assert sample == gs._SessionGpuSample()


async def test_no_cards_means_no_gpu_figures_and_no_queries():
    fake = _FakePrometheus()
    sample = await _scheduler_with(fake)._sample_session_gpus([], 60.0)
    assert sample == gs._SessionGpuSample()
    assert fake.queries == []


async def test_prometheus_down_leaves_every_figure_unknown():
    sample = await _scheduler_with(_FakePrometheus(fail=True))._sample_session_gpus(
        [1], 60.0,
    )
    assert sample == gs._SessionGpuSample()


async def test_non_finite_sample_is_unknown():
    fake = _FakePrometheus(
        avg={1: math.nan}, peak={1: math.inf}, util={1: 20.0},
        names={1: "NVIDIA GeForce RTX 3090"},
    )
    sample = await _scheduler_with(fake)._sample_session_gpus([1], 60.0)
    assert sample.avg_power_watts is None
    assert sample.peak_power_watts is None
    assert sample.avg_utilization_pct == 20.0


async def test_exporter_without_the_info_series_records_no_name_and_says_so_once(
    monkeypatch,
):
    """An exporter image older than nvidia_gpu_info: NULL, never a guessed card."""
    monkeypatch.setattr(gs, "_warned_no_gpu_info", False)
    log = MagicMock()
    monkeypatch.setattr(gs, "logger", log)
    fake = _FakePrometheus(avg={1: 40.0}, peak={1: 45.0}, util={1: 3.0})
    scheduler = _scheduler_with(fake)

    first = await scheduler._sample_session_gpus([1], 60.0)
    second = await scheduler._sample_session_gpus([1], 60.0)

    assert first.gpu_model is None and second.gpu_model is None
    assert first.avg_power_watts == 40.0
    assert log.warning.call_count == 1
    assert "nvidia_gpu_info" in log.warning.call_args.args[0]


# --- the Prometheus read ---------------------------------------------------------


def _client_returning(*, status=200, result=None, raise_exc=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value={
        "status": "success", "data": {"resultType": "vector", "result": result or []},
    })
    client = MagicMock()
    client.is_closed = False
    if raise_exc is not None:
        client.get = AsyncMock(side_effect=raise_exc)
    else:
        client.get = AsyncMock(return_value=resp)
    return client


async def test_vector_query_returns_every_series():
    scheduler = GPUScheduler()
    series = [
        {"metric": {"gpu": "0"}, "value": [0, "300"]},
        {"metric": {"gpu": "1"}, "value": [0, "40"]},
    ]
    scheduler._http_client = _client_returning(result=series)
    assert await scheduler._query_prometheus_vector("q", metric="m") == series


async def test_vector_query_failure_emits_the_finding_under_the_family(monkeypatch):
    scheduler = GPUScheduler()
    scheduler._emit_exporter_finding = AsyncMock()  # type: ignore[method-assign]
    scheduler._http_client = _client_returning(status=503)

    result = await scheduler._query_prometheus_vector(
        'avg by (gpu) (avg_over_time(nvidia_gpu_power_draw_watts{gpu=~"1"}[42s]))',
        metric="nvidia_gpu_power_draw_watts",
    )

    assert result is None
    scheduler._emit_exporter_finding.assert_awaited_once_with(
        "nvidia_gpu_power_draw_watts", "HTTP 503",
    )


async def test_vector_query_empty_result_is_quiet():
    scheduler = GPUScheduler()
    scheduler._emit_exporter_finding = AsyncMock()  # type: ignore[method-assign]
    scheduler._http_client = _client_returning(result=[])
    assert await scheduler._query_prometheus_vector("q") == []
    scheduler._emit_exporter_finding.assert_not_awaited()


# --- the row ----------------------------------------------------------------------


def _record_session(scheduler: GPUScheduler, **overrides):
    kwargs = {
        "task_id": "task-econ-1",
        "phase": "caption_image",
        "model": _JUDGE_MODEL,
        "devices": [1],
        "started_at": datetime.now(UTC),
        "duration_seconds": 3600.0,
    }
    kwargs.update(overrides)
    return scheduler._record_task_session(**kwargs)


@pytest.fixture
def db():
    """Hermetic asyncpg boundary: capture the INSERT, open nothing."""
    conn = AsyncMock()
    with patch("asyncpg.connect", new=AsyncMock(return_value=conn)), patch(
        "poindexter.brain.bootstrap.resolve_database_url",
        return_value="postgresql://x",
    ):
        yield conn


def _insert_args(conn) -> dict:
    conn.execute.assert_awaited_once()
    args = conn.execute.await_args.args
    columns = (
        "task_id", "phase", "started_at", "duration_seconds", "gpu_model",
        "avg_utilization_pct", "avg_power_watts", "peak_power_watts",
        "kwh_consumed", "electricity_rate_kwh", "electricity_cost_usd",
        "model_name",
    )
    assert len(args) == 1 + len(columns)
    return dict(zip(columns, args[1:], strict=True))


@pytest.mark.gpu_lock_real_db
async def test_row_carries_the_sessions_own_card_and_draw(config, db):
    config(electricity_rate_kwh="0.2883")
    scheduler = GPUScheduler()
    scheduler._sample_session_gpus = AsyncMock(  # type: ignore[method-assign]
        return_value=gs._SessionGpuSample(
            gpu_model="NVIDIA GeForce RTX 3090",
            avg_utilization_pct=12.5,
            avg_power_watts=150.0,
            peak_power_watts=240.0,
        ),
    )

    await _record_session(scheduler, devices=[1], duration_seconds=3600.0)

    scheduler._sample_session_gpus.assert_awaited_once_with([1], 3600.0)
    row = _insert_args(db)
    assert row["gpu_model"] == "NVIDIA GeForce RTX 3090"
    assert row["avg_utilization_pct"] == 12.5
    # avg and peak are separate readings now, not one sample written twice.
    assert row["avg_power_watts"] == 150.0
    assert row["peak_power_watts"] == 240.0
    assert row["kwh_consumed"] == pytest.approx(0.15)
    assert row["electricity_rate_kwh"] == pytest.approx(0.2883)
    assert row["electricity_cost_usd"] == pytest.approx(0.15 * 0.2883)
    assert row["model_name"] == _JUDGE_MODEL


@pytest.mark.gpu_lock_real_db
async def test_price_comes_from_electricity_rate_kwh(config, db):
    """Not the unseeded ``electricity_rate_kwh_usd`` the row used to read."""
    config(electricity_rate_kwh="0.2883", electricity_rate_kwh_usd="9.99")
    scheduler = GPUScheduler()
    scheduler._sample_session_gpus = AsyncMock(  # type: ignore[method-assign]
        return_value=gs._SessionGpuSample(avg_power_watts=100.0),
    )
    await _record_session(scheduler)
    assert _insert_args(db)["electricity_rate_kwh"] == pytest.approx(0.2883)


@pytest.mark.gpu_lock_real_db
async def test_unknown_draw_records_no_energy_and_no_card_name(config, db):
    """Unknown stays NULL: no literal card, no kWh or cost from nothing."""
    config()
    scheduler = GPUScheduler()
    scheduler._sample_session_gpus = AsyncMock(  # type: ignore[method-assign]
        return_value=gs._SessionGpuSample(),
    )
    await _record_session(scheduler)
    row = _insert_args(db)
    assert row["gpu_model"] is None
    assert row["avg_power_watts"] is None
    assert row["kwh_consumed"] is None
    assert row["electricity_cost_usd"] is None


# --- the lock hands the row its cards --------------------------------------------


async def test_lock_records_a_judge_session_against_gpu_1(config):
    config(**_PROD)
    scheduler = GPUScheduler()
    record = AsyncMock()
    scheduler._record_task_session = record  # type: ignore[method-assign]

    async with scheduler.lock(
        "ollama", model=_JUDGE_MODEL, task_id="t-judge", phase="caption_image",
    ):
        pass

    record.assert_awaited_once()
    assert record.await_args.kwargs["devices"] == [1]
    assert record.await_args.kwargs["phase"] == "caption_image"


async def test_lock_records_the_pipeline_card_with_scoping_off(config):
    config(gpu_lock_per_device_enabled="false", pipeline_gpu_index="0")
    scheduler = GPUScheduler()
    record = AsyncMock()
    scheduler._record_task_session = record  # type: ignore[method-assign]

    async with scheduler.lock("ollama", model=_JUDGE_MODEL, task_id="t-legacy"):
        pass

    assert record.await_args.kwargs["devices"] == [0]


async def test_a_card_resolution_error_costs_the_row_never_the_release(
    config, monkeypatch,
):
    config(**_PROD)
    findings: list[dict] = []
    import poindexter.utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: findings.append(kw))
    monkeypatch.setattr(
        gs, "resolve_session_devices",
        MagicMock(side_effect=RuntimeError("scope map exploded")),
    )
    scheduler = GPUScheduler()
    record = AsyncMock()
    scheduler._record_task_session = record  # type: ignore[method-assign]

    async with scheduler.lock("ollama", model=_JUDGE_MODEL, task_id="t-boom"):
        assert scheduler.is_busy

    assert not scheduler.is_busy
    record.assert_not_awaited()
    assert [f["kind"] for f in findings] == ["gpu_task_session_write_failed"]
