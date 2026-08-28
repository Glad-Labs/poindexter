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
        "ops_firefighter_model": "ollama/granite4.2:3b",
        "ops_firefighter_llm_exclude_regex": "(?i)(ollama|gpu|vram)",
    }
    pool.set_fetchval(lambda sql, args: store.get(args[0]))
    cfg = await R.load_firefighter_config(pool)
    assert cfg["llm_longtail_enabled"] is True
    assert cfg["min_repeats"] == 2
    assert cfg["min_age_minutes"] == 10
    assert cfg["min_confidence"] == 0.6
    assert cfg["model"] == "ollama/granite4.2:3b"
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
    assert cfg["model"] == "ollama/granite4.2:3b"
    assert cfg["llm_exclude_regex"]  # non-empty circular-dependency guard


# ---------------------------------------------------------------------------
# Cross-checks against settings_defaults.DEFAULTS
# ---------------------------------------------------------------------------
#
# `load_firefighter_config` hardcodes a fallback for every knob it reads, used
# on a fresh or partially-seeded DB. That makes it a FOURTH home for default
# values, alongside the three seed sources `scripts/ci/settings_seed_value_drift_lint.py`
# already guards — and nothing was checking it. The 2026-08-27 licence sweep
# retired `ollama/llama3.2:3b` from every other default and left this one
# behind (its repo-wide grep was truncated, so `brain/` was never checked),
# shipping a non-permissively-licensed model tag to the public mirror in a file
# the sweep believed it had cleared. These tests close that gap.


def _defaults_value(key: str) -> str:
    """Read one key out of settings_defaults.DEFAULTS by AST.

    Parsed rather than imported: `brain/` deliberately depends on nothing from
    `src/cofounder_agent/` at runtime (the brain image ships asyncpg/httpx only),
    and importing the module here would couple the two.
    """
    import ast
    from pathlib import Path

    defaults_py = (
        Path(__file__).resolve().parents[3] / "services" / "settings_defaults.py"
    )
    tree = ast.parse(defaults_py.read_text(encoding="utf-8"))
    for node in tree.body:
        target_is_defaults = False
        value = None
        if isinstance(node, ast.Assign):
            target_is_defaults = any(
                isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in node.targets
            )
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_is_defaults = node.target.id == "DEFAULTS"
            value = node.value
        if target_is_defaults and isinstance(value, ast.Dict):
            for k, v in zip(value.keys, value.values, strict=False):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == key
                    and isinstance(v, ast.Constant)
                ):
                    return str(v.value)
    raise AssertionError(f"{key!r} not found in settings_defaults.DEFAULTS")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cfg_field,settings_key",
    [
        ("model", "ops_firefighter_model"),
        ("llm_exclude_regex", "ops_firefighter_llm_exclude_regex"),
    ],
)
async def test_brain_fallback_matches_settings_defaults(cfg_field, settings_key):
    """The brain's fallback must equal the seeded default for the same key.

    Drift here is silent and only bites a fresh/partial DB — precisely the
    install least able to notice. It is also how a retired model tag survives a
    licence audit: `settings_defaults.py` says one thing, the brain says
    another, and only the brain runs when the row is missing.
    """
    pool = FakePool()
    pool.set_fetchval(lambda sql, args: None)  # nothing seeded
    cfg = await R.load_firefighter_config(pool)
    assert cfg[cfg_field] == _defaults_value(settings_key), (
        f"brain fallback for {settings_key!r} has drifted from "
        "settings_defaults.DEFAULTS — a fresh DB would behave differently from "
        "a seeded one."
    )


@pytest.mark.asyncio
async def test_brain_firefighter_model_fallback_is_permissively_licensed():
    """`brain/` is NOT stripped by scripts/sync-to-github.sh, so this default
    ships to Glad-Labs/poindexter.

    Asserts the value the brain ACTUALLY resolves on an unseeded DB, not
    `DEFAULTS`. Those are equal today, and the drift test above keeps them so —
    but reading DEFAULTS here would make this test blind to the one case the
    drift test cannot see: both literals changed together to the same
    non-permissive tag. Same allowlist as
    tests/unit/test_settings_defaults_firefighter.py; verify a new entry with
    `ollama show --license` rather than assuming, and never re-admit a
    community licence.
    """
    permissive = {
        "ollama/granite4.2:3b",  # IBM Granite 4.2 — Apache-2.0
        "ollama/granite4.2:8b",  # Apache-2.0
        "ollama/qwen2.5:7b",     # Apache-2.0
        "ollama/phi4:14b",       # MIT
    }
    pool = FakePool()
    pool.set_fetchval(lambda sql, args: None)  # nothing seeded
    cfg = await R.load_firefighter_config(pool)
    assert cfg["model"] in permissive
