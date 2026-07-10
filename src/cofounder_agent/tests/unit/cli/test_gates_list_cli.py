"""CLI render test for the honest `poindexter gates list` output."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.approval import gates_list_command

pytestmark = pytest.mark.unit


def _canned_rows():
    return [
        {
            "gate_name": "awaiting_approval",
            "enabled": True,
            "mechanism": "default",
            "wired_into": "post_pipeline (every post)",
            "setting_key": None,
            "pending_count": 2,
            "auto_publish_threshold": "0",
            "require_human_approval": "true",
            "armed_niches": ["dev_diary"],
        },
        {
            "gate_name": "final_publish_approval",
            "enabled": False,
            "mechanism": "imperative-hold",
            "wired_into": "scheduled_publisher",
            "setting_key": "pipeline_gate_final_publish_approval",
            "pending_count": 0,
        },
    ]


def test_gates_list_renders_default_section_and_table():
    dummy_pool = AsyncMock()
    dummy_pool.close = AsyncMock()
    with (
        patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=dummy_pool),
        ),
        patch(
            "poindexter.cli.approval._make_site_config",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "services.approval_service.list_gates",
            new=AsyncMock(return_value=_canned_rows()),
        ),
    ):
        result = CliRunner().invoke(gates_list_command, [])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "DEFAULT PUBLISH GATE" in out
    assert "awaiting_approval" in out
    assert "2 pending" in out
    assert "dev_diary" in out
    assert "CONFIGURABLE GATES" in out
    assert "WIRED INTO" in out
    assert "final_publish_approval" in out
    assert "scheduled_publisher" in out


def test_gates_list_json_is_superset():
    dummy_pool = AsyncMock()
    dummy_pool.close = AsyncMock()
    with (
        patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=dummy_pool),
        ),
        patch(
            "poindexter.cli.approval._make_site_config",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "services.approval_service.list_gates",
            new=AsyncMock(return_value=_canned_rows()),
        ),
    ):
        result = CliRunner().invoke(gates_list_command, ["--json"])

    assert result.exit_code == 0, result.output
    import json as _json

    payload = _json.loads(result.output)
    # Backcompat: original keys still present on every row.
    for row in payload:
        assert "gate_name" in row
        assert "enabled" in row
        assert "pending_count" in row
