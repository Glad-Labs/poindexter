"""Host-service liveness, and the freshness rule that makes it honest.

``brain_knowledge`` probe rows are written ``ON CONFLICT DO UPDATE``, so a row
OUTLIVES the daemon that writes it. Reading one back without aging it would mean
a stopped brain daemon renders Ollama permanently green on the Services page and
the System Map — strictly worse than the dark-but-honest node it replaced, and
the same trap that let a retention policy prune nothing for months behind a green
panel (docs/architecture/retention-backlog.md).

These tests pin that a stale row is never reported as its last known status.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from poindexter.services.host_service_health import (
    DEFAULT_STALENESS_SECONDS,
    HOST_SERVICE_PROBES,
    classify,
    get_host_service_health,
)


class _FakePool:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.queries: list[tuple] = []

    async def fetch(self, sql, *args):
        self.queries.append((sql, args))
        return self._rows


def _row(entity: str, payload: dict, age_seconds: float, *, naive: bool = False):
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    if naive:
        ts = ts.replace(tzinfo=None)
    return {"entity": entity, "value": json.dumps(payload), "updated_at": ts}


# ── the freshness rule, in isolation ──────────────────────────────────────


def test_a_fresh_passing_probe_is_ok() -> None:
    assert classify({"ok": True}, 30.0, 900) == "ok"


def test_a_fresh_failing_probe_is_err() -> None:
    assert classify({"ok": False}, 30.0, 900) == "err"


@pytest.mark.parametrize("was_ok", [True, False])
def test_a_stale_row_reports_stale_and_never_its_last_status(was_ok: bool) -> None:
    """The whole point. A dead writer must not leave a green runtime behind."""
    assert classify({"ok": was_ok}, 901.0, 900) == "stale"


def test_a_row_exactly_at_the_threshold_is_still_trusted() -> None:
    # Strictly greater-than, so a probe landing right on the boundary each
    # cycle doesn't flap between ok and stale.
    assert classify({"ok": True}, 900.0, 900) == "ok"


def test_never_probed_is_unknown_not_healthy() -> None:
    assert classify(None, None, 900) == "unknown"
    assert classify({"ok": True}, None, 900) == "unknown"


def test_unknown_and_stale_are_distinct() -> None:
    """A fresh install and a dead daemon are different problems."""
    assert classify(None, None, 900) != classify({"ok": True}, 10_000.0, 900)


# ── the query path ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_probe_surfaces_with_detail_and_age() -> None:
    pool = _FakePool([_row("probe.ollama_models", {"ok": True, "model_count": 13}, 42.0)])
    out = await get_host_service_health(pool)

    svc = out["services"]["ollama"]
    assert svc["status"] == "ok"
    assert svc["detail"] == "13 models"
    assert svc["probe"] == "ollama_models"
    assert 40 <= svc["age_seconds"] <= 45
    assert svc["checked_at"]


@pytest.mark.asyncio
async def test_stale_row_does_not_report_ok() -> None:
    pool = _FakePool(
        [
            _row(
                "probe.ollama_models",
                {"ok": True, "model_count": 13},
                DEFAULT_STALENESS_SECONDS + 60,
            )
        ]
    )
    out = await get_host_service_health(pool)
    assert out["services"]["ollama"]["status"] == "stale"


@pytest.mark.asyncio
async def test_missing_row_is_reported_not_omitted() -> None:
    """Absent must be distinguishable from not-asked-about."""
    out = await get_host_service_health(_FakePool([]))
    assert set(out["services"]) == set(HOST_SERVICE_PROBES)
    assert out["services"]["ollama"]["status"] == "unknown"
    assert out["services"]["ollama"]["age_seconds"] is None


@pytest.mark.asyncio
async def test_malformed_json_is_an_absent_signal_not_a_healthy_one() -> None:
    pool = _FakePool(
        [
            {
                "entity": "probe.ollama_models",
                "value": "{not json",
                "updated_at": datetime.now(timezone.utc),
            }
        ]
    )
    out = await get_host_service_health(pool)
    assert out["services"]["ollama"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_naive_timestamps_are_treated_as_utc_not_as_a_huge_age() -> None:
    """asyncpg can hand back a naive datetime; subtracting it raw would throw,
    and coercing it to local time would make a fresh probe look hours old."""
    pool = _FakePool(
        [_row("probe.ollama_models", {"ok": True, "model_count": 3}, 20.0, naive=True)]
    )
    out = await get_host_service_health(pool)
    assert out["services"]["ollama"]["status"] == "ok"
    assert out["services"]["ollama"]["age_seconds"] < 60


@pytest.mark.asyncio
async def test_staleness_window_is_caller_tunable() -> None:
    pool = _FakePool([_row("probe.ollama_models", {"ok": True}, 120.0)])
    assert (await get_host_service_health(pool, staleness_seconds=60))["services"]["ollama"][
        "status"
    ] == "stale"
    assert (await get_host_service_health(pool, staleness_seconds=600))["services"]["ollama"][
        "status"
    ] == "ok"


@pytest.mark.asyncio
async def test_only_health_probe_rows_are_queried() -> None:
    pool = _FakePool([])
    await get_host_service_health(pool)
    sql, args = pool.queries[0]
    assert "source = 'health_probe'" in sql
    assert "attribute = 'health_status'" in sql
    # Bound, not interpolated, and scoped to the probes we actually map.
    assert sorted(args[0]) == sorted(f"probe.{p}" for p in HOST_SERVICE_PROBES.values())
    assert "probe.ollama_models" in args[0]


def test_liveness_probe_is_tags_not_embedding() -> None:
    """`ollama_embedding` legitimately reports skipped_gpu_busy while the
    pipeline holds the GPU lock — busy is not down, so it must not drive the
    console's status badge."""
    assert HOST_SERVICE_PROBES["ollama"] == "ollama_models"


# ── the second, vision-pinned Ollama (2026-09-20) ─────────────────────────
#
# An install that pins its judge to a second endpoint was running the model
# that grades every article and every frame behind NO probe at all. Adding one
# has to stay silent on the majority of installs that have only one Ollama —
# a probe that pages about an endpoint nobody configured is noise that teaches
# people to ignore probes.


def test_the_vision_instance_is_mapped() -> None:
    assert HOST_SERVICE_PROBES["ollama-vision"] == "ollama_vision_models"


@pytest.mark.asyncio
async def test_not_configured_is_omitted_not_rendered_grey() -> None:
    """A single-Ollama install must not carry a permanently grey second row."""
    pool = _FakePool(
        [
            _row("probe.ollama_models", {"ok": True, "model_count": 13}, 30.0),
            _row(
                "probe.ollama_vision_models",
                {"ok": True, "status": "not_configured"},
                30.0,
            ),
        ]
    )
    out = await get_host_service_health(pool)
    assert "ollama-vision" not in out["services"]
    assert out["services"]["ollama"]["status"] == "ok"


@pytest.mark.asyncio
async def test_a_configured_vision_instance_surfaces() -> None:
    pool = _FakePool(
        [
            _row("probe.ollama_models", {"ok": True, "model_count": 13}, 30.0),
            _row(
                "probe.ollama_vision_models",
                {"ok": True, "status": "ok", "model_count": 1},
                30.0,
            ),
        ]
    )
    out = await get_host_service_health(pool)
    vision = out["services"]["ollama-vision"]
    assert vision["status"] == "ok"
    assert vision["detail"] == "1 model"
    assert vision["label"], "a synthesized console row needs a description"


@pytest.mark.asyncio
async def test_a_configured_but_unreachable_vision_instance_is_an_error() -> None:
    """Configured-and-down is exactly what this probe exists to catch."""
    pool = _FakePool(
        [
            _row(
                "probe.ollama_vision_models",
                {"ok": False, "status": "unreachable", "detail": "connection refused"},
                30.0,
            )
        ]
    )
    out = await get_host_service_health(pool)
    assert out["services"]["ollama-vision"]["status"] == "err"


@pytest.mark.asyncio
async def test_a_stale_vision_row_is_stale_not_omitted() -> None:
    """Omission is only for not-configured. A stale reading still reports."""
    pool = _FakePool(
        [
            _row(
                "probe.ollama_vision_models",
                {"ok": True, "status": "ok", "model_count": 1},
                DEFAULT_STALENESS_SECONDS + 60,
            )
        ]
    )
    out = await get_host_service_health(pool)
    assert out["services"]["ollama-vision"]["status"] == "stale"
