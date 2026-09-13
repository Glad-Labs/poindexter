"""A crash-looping Restart= unit sits in `activating`, never `failed` (poindexter#1048)."""
from __future__ import annotations

import pytest

from poindexter.services import prometheus_rule_builder as rb


@pytest.mark.unit
def test_restart_loop_rule_exists_and_is_shaped_like_its_sibling():
    rule = rb.DEFAULT_RULES["PoindexterSystemdUnitRestartLooping"]
    sibling = rb.DEFAULT_RULES["PoindexterSystemdUnitFailed"]
    assert 'state="activating"' in rule["expr"] and 'name=~"poindexter.*"' in rule["expr"]
    assert rule["for"] == "10m" and rule["severity"] == "critical" and rule["enabled"] is True
    assert rule["group"] == sibling["group"] and rule["category"] == sibling["category"]
