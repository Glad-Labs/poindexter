"""Unit tests for services/preview_links.py: the operator's /preview/{token} link.

The link is for the operator's DEVICE (a phone on the tailnet). The stored
base was the retired Windows node's tailnet IP after the Pop!_OS migration, so
every approval link was dead for months; these tests pin the one resolver
every operator-facing consumer builds the link through.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from poindexter.services.preview_links import (
    WORKER_API_PORT,
    operator_preview_base_url,
    operator_preview_url,
)


class _Cfg:
    """SiteConfig / platform.config shaped: ``.get(key, default)``."""

    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


class _BoomCfg:
    def get(self, key, default=None):
        raise RuntimeError("settings cache unavailable")


@pytest.mark.unit
class TestOperatorPreviewBaseUrl:
    def test_explicit_base_wins(self):
        cfg = _Cfg(
            preview_base_url="http://box.example.ts.net:8002",
            operator_service_host="ignored.example",
        )
        assert operator_preview_base_url(cfg) == "http://box.example.ts.net:8002"

    def test_explicit_base_is_trimmed(self):
        cfg = _Cfg(preview_base_url="  https://preview.example.com/  ")
        assert operator_preview_base_url(cfg) == "https://preview.example.com"

    def test_empty_base_derives_from_operator_service_host(self):
        """One setting moves both the Grafana worker links and this link."""
        cfg = _Cfg(preview_base_url="", operator_service_host="box.example.ts.net")
        assert operator_preview_base_url(cfg) == f"http://box.example.ts.net:{WORKER_API_PORT}"

    def test_nothing_set_is_localhost(self):
        """A fresh install: right for a browser on the Docker host."""
        assert operator_preview_base_url(_Cfg()) == "http://localhost:8002"
        assert operator_preview_base_url(None) == "http://localhost:8002"

    @pytest.mark.parametrize(
        "bad_host",
        ["http://box.example", "box.example:8002", "box.example/path", "two words"],
    )
    def test_malformed_host_falls_back_loudly(self, bad_host, caplog):
        cfg = _Cfg(operator_service_host=bad_host)
        with caplog.at_level(logging.WARNING):
            assert operator_preview_base_url(cfg) == "http://localhost:8002"
        assert "not a bare hostname" in caplog.text

    def test_config_read_failure_does_not_raise(self, caplog):
        """The approval notification must still go out with a working local
        link, and the failure is logged."""
        with caplog.at_level(logging.WARNING):
            assert operator_preview_base_url(_BoomCfg()) == "http://localhost:8002"
        assert "settings cache unavailable" in caplog.text


@pytest.mark.unit
class TestOperatorPreviewUrl:
    def test_builds_the_preview_path(self):
        cfg = _Cfg(preview_base_url="http://box.example.ts.net:8002/")
        assert operator_preview_url(cfg, "abc123") == (
            "http://box.example.ts.net:8002/preview/abc123"
        )

    @pytest.mark.parametrize("token", [None, "", "   "])
    def test_no_token_no_link(self, token):
        assert operator_preview_url(_Cfg(preview_base_url="http://x"), token) == ""


def _approval_queue_sql() -> str:
    root = Path(__file__).resolve()
    for parent in root.parents:
        board = parent / "infrastructure" / "grafana" / "dashboards" / "pipeline-merged.json"
        if board.is_file():
            break
    else:  # pragma: no cover - repo layout changed
        pytest.fail("pipeline-merged.json not found above the test file")

    def walk(panels):
        for panel in panels:
            yield panel
            yield from walk(panel.get("panels") or [])

    data = json.loads(board.read_text(encoding="utf-8"))
    for panel in walk(data["panels"]):
        for target in panel.get("targets") or []:
            sql = target.get("rawSql") or ""
            if "'/preview/'" in sql:
                return sql
    pytest.fail("no Grafana panel builds a /preview/ link any more")


@pytest.mark.unit
def test_grafana_approval_queue_mirrors_the_derivation():
    """The approval-queue panel builds the same link in SQL. If it read only
    preview_base_url, an install that leaves it empty (the baseline default)
    would get a relative /preview/ link, which Grafana resolves against its own
    port and 404s."""
    sql = _approval_queue_sql()
    assert "'preview_base_url'" in sql
    assert "'operator_service_host'" in sql
    assert "'localhost'" in sql
    assert f":{WORKER_API_PORT}'" in sql
