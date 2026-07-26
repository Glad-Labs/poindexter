import pytest
from brain.remediation import rules as R

from tests.unit.brain._remediation_fakes import FakePool


@pytest.mark.asyncio
async def test_match_rule_exact_alertname():
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [
        {"id": 1, "alertname": "WorkerDown", "match_regex": None,
         "action_name": "restart_container", "params": '{"container": "poindexter-worker"}',
         "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None},
    ])
    rule = await R.match_rule(pool, alertname="WorkerDown", fingerprint="fp1")
    assert rule is not None
    assert rule["action_name"] == "restart_container"
    assert rule["params"] == {"container": "poindexter-worker"}  # JSON string coerced to dict


@pytest.mark.asyncio
async def test_match_rule_regex_over_fingerprint():
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [
        {"id": 2, "alertname": None, "match_regex": "topic.batch.stuck",
         "action_name": "run_auto_remediate", "params": {},
         "max_attempts_per_window": 2, "window_minutes": 30, "verify_after_seconds": 90},
    ])
    rule = await R.match_rule(pool, alertname="Whatever", fingerprint="topic_batch_stuck:glad-labs")
    assert rule is not None
    assert rule["action_name"] == "run_auto_remediate"
    assert rule["max_attempts_per_window"] == 2


@pytest.mark.asyncio
async def test_match_rule_none_when_no_match():
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [])
    assert await R.match_rule(pool, alertname="X", fingerprint="y") is None


@pytest.mark.asyncio
async def test_circuit_breaker_trips_at_threshold():
    pool = FakePool()
    pool.set_fetchval(lambda sql, args: 3)  # 3 prior attempts
    assert await R.circuit_breaker_tripped(
        pool, fingerprint="fp", action_name="restart_container",
        max_attempts=3, window_minutes=60,
    ) is True


@pytest.mark.asyncio
async def test_circuit_breaker_open_below_threshold():
    pool = FakePool()
    pool.set_fetchval(lambda sql, args: 1)
    assert await R.circuit_breaker_tripped(
        pool, fingerprint="fp", action_name="restart_container",
        max_attempts=3, window_minutes=60,
    ) is False


@pytest.mark.asyncio
async def test_global_rate_cap():
    pool = FakePool()
    pool.set_fetchval(lambda sql, args: 10)
    assert await R.global_rate_exceeded(pool, max_actions_per_hour=10) is True
    pool.set_fetchval(lambda sql, args: 4)
    assert await R.global_rate_exceeded(pool, max_actions_per_hour=10) is False


@pytest.mark.asyncio
async def test_load_config_parses_types():
    pool = FakePool()
    store = {
        "ops_firefighter_enabled": "true",
        "ops_firefighter_max_attempts_per_window": "3",
        "ops_firefighter_window_minutes": "60",
        "ops_firefighter_verify_after_seconds": "120",
        "ops_firefighter_max_actions_per_hour": "10",
        "ops_firefighter_action_allowlist": "restart_container, run_auto_remediate",
    }
    pool.set_fetchval(lambda sql, args: store.get(args[0]))
    cfg = await R.load_firefighter_config(pool)
    assert cfg["enabled"] is True
    assert cfg["max_attempts_per_window"] == 3
    assert cfg["action_allowlist"] == ["restart_container", "run_auto_remediate"]


@pytest.mark.asyncio
async def test_load_config_parses_llm_longtail_knobs():
    """Plan B knobs — the LLM long-tail selector gates (persistence, confidence,
    exclusion) all resolve through the same one-cycle config snapshot."""
    pool = FakePool()
    store = {
        "ops_firefighter_enabled": "true",
        "ops_firefighter_llm_longtail_enabled": "true",
        "ops_firefighter_min_repeats": "2",
        "ops_firefighter_min_age_minutes": "10",
        "ops_firefighter_min_confidence": "0.6",
        "ops_firefighter_model": "ollama/llama3.2:3b",
        "ops_firefighter_llm_exclude_regex": "(?i)(ollama|gpu|vram)",
    }
    pool.set_fetchval(lambda sql, args: store.get(args[0]))
    cfg = await R.load_firefighter_config(pool)
    assert cfg["llm_longtail_enabled"] is True
    assert cfg["min_repeats"] == 2
    assert cfg["min_age_minutes"] == 10
    assert cfg["min_confidence"] == 0.6
    assert cfg["model"] == "ollama/llama3.2:3b"
    assert "ollama" in cfg["llm_exclude_regex"]


@pytest.mark.asyncio
async def test_load_config_llm_longtail_defaults_when_unset():
    """Missing rows fall back to the seeded defaults, not crashes — a fresh DB
    (or a partial seed) still yields a usable, conservative config."""
    pool = FakePool()
    pool.set_fetchval(lambda sql, args: None)  # nothing seeded
    cfg = await R.load_firefighter_config(pool)
    assert cfg["llm_longtail_enabled"] is True
    assert cfg["min_repeats"] == 2
    assert cfg["min_age_minutes"] == 10
    assert cfg["min_confidence"] == 0.6
    assert cfg["model"] == "ollama/llama3.2:3b"
    assert cfg["llm_exclude_regex"]  # non-empty circular-dependency guard
