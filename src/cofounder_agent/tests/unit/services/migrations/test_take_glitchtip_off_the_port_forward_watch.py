"""Migration 20261003_031940: GlitchTip comes off the port-forward watch.

GlitchTip's host port is published on loopback only now. The brain's
port-forward probe reaches host ports at ``host.docker.internal``, the Docker
bridge gateway, which a loopback-only port never answers, so a remaining entry
would page on every cycle. The fresh-install seed must not carry it either, and
the compose file must keep the port off the LAN.
"""

from __future__ import annotations

import importlib.util
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[6]
_MIGRATIONS_DIR = _REPO / "src" / "cofounder_agent" / "poindexter" / "services" / "migrations"
_MIGRATION = (
    _MIGRATIONS_DIR
    / "20261003_031940_take_glitchtip_off_the_port_forward_watch_now_that_its_port_is_loopback_only.py"
)
_BASELINE_SEEDS = _MIGRATIONS_DIR / "0000_baseline.seeds.sql"
_COMPOSE = _REPO / "docker-compose.local.yml"
_KEY = "docker_port_forward_watch_list"
_GT = {"container": "poindexter-glitchtip-web", "port": 8000, "host_port": 8080, "path": "/api/0/"}
_OTHERS = [
    {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
    {"container": "poindexter-grafana", "port": 3000, "path": "/api/health"},
]


def _load():
    spec = importlib.util.spec_from_file_location("_mig_glitchtip_watch", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Table:
    """A one-table stand-in for app_settings behind pool.acquire()."""

    def __init__(self, value: str | None):
        self.rows = {} if value is None else {_KEY: value}
        self.writes = 0

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetchval(self, sql, key):
        assert "SELECT value FROM app_settings" in sql
        return self.rows.get(key)

    async def execute(self, sql, key, value):
        assert sql.startswith("UPDATE app_settings SET value = $2")
        self.rows[key] = value
        self.writes += 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestTakeGlitchtipOffThePortForwardWatch:
    async def test_removes_only_the_glitchtip_entry_and_keeps_order(self):
        table = _Table(json.dumps([_OTHERS[0], _GT, _OTHERS[1]]))
        await _load().up(table)
        assert json.loads(table.rows[_KEY]) == _OTHERS

    async def test_is_a_no_op_without_the_entry_or_the_row(self):
        table = _Table(json.dumps(_OTHERS))
        await _load().up(table)
        assert table.writes == 0
        empty = _Table(None)
        await _load().up(empty)
        assert empty.writes == 0 and empty.rows == {}

    async def test_leaves_a_value_that_is_not_a_json_list_alone(self):
        table = _Table("{not json")
        await _load().up(table)
        assert table.writes == 0 and table.rows[_KEY] == "{not json"

    async def test_down_restores_the_entry_once(self):
        table = _Table(json.dumps(_OTHERS))
        mod = _load()
        await mod.down(table)
        await mod.down(table)
        assert json.loads(table.rows[_KEY]) == [*_OTHERS, _GT]
        assert table.writes == 1


@pytest.mark.unit
def test_the_fresh_install_seed_does_not_watch_glitchtip():
    line = next(
        ln for ln in _BASELINE_SEEDS.read_text().splitlines()
        if ln.startswith("INSERT INTO app_settings") and f"'{_KEY}'" in ln
    )
    value = re.search(rf"'{_KEY}', '(\[.*?\])'", line)
    assert value is not None
    containers = {e["container"] for e in json.loads(value.group(1))}
    assert "poindexter-glitchtip-web" not in containers
    assert "poindexter-grafana" in containers  # the rest of the list survived


@pytest.mark.unit
def test_compose_publishes_glitchtip_on_loopback_by_default():
    service = yaml.safe_load(_COMPOSE.read_text())["services"]["glitchtip-web"]
    assert service["ports"] == ["${GLITCHTIP_BIND_ADDRESS:-127.0.0.1}:8080:8000"]
