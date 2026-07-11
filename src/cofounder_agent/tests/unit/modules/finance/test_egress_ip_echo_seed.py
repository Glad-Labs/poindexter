"""The egress-IP reflector setting is seeded by a symmetric, idempotent module
migration whose key matches the constant the probe actually reads.

A key/typo drift between the seed and ``EGRESS_IP_ECHO_URL_KEY`` would silently
kill the feature — the probe would fall back to its in-code default forever and
the operator would never be able to repoint the reflector. This guards that
invariant plus the seed's idempotency + teardown symmetry, reading the file as
text so it doesn't depend on importing a timestamp-named module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.finance.probes import EGRESS_IP_ECHO_URL_KEY

_MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "modules"
    / "finance"
    / "migrations"
    / "20260711_212344_seed_finance_egress_ip_echo_url.py"
)


@pytest.mark.unit
def test_egress_ip_echo_seed_matches_probe_key_and_is_symmetric():
    assert _MIGRATION.exists(), f"seed migration missing at {_MIGRATION}"
    src = _MIGRATION.read_text(encoding="utf-8")

    # seeds exactly the key the probe reads (drift here = feature silently dead)
    assert EGRESS_IP_ECHO_URL_KEY == "finance_egress_ip_echo_url"
    assert f"'{EGRESS_IP_ECHO_URL_KEY}'" in src

    # idempotent insert — never clobbers an operator-tuned reflector URL
    assert "ON CONFLICT (key) DO NOTHING" in src

    # symmetric teardown — the key travels with the module (down() removes it)
    assert "DELETE FROM app_settings" in src
    assert "async def up(" in src
    assert "async def down(" in src
