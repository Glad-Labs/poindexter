"""Contract test for the ``operator_url_probe_target_overrides`` baseline seed.

Pins two things the brain's operator URL probe (``brain/operator_url_probe.py``)
depends on:

1. ``cloudflare_beacon_url`` carries an ``alive_codes`` override that counts the
   page-views beacon Worker's by-design 405 (it is POST-only — see
   ``infrastructure/cloudflare/page-views-beacon``) as "alive", while a real 5xx
   still reads as down. Without it the probe pages every ~15 min on a *healthy*
   beacon — the false positive this seed fixes. Authoritative beacon-outage
   detection is ``ProbeCloudflareBeaconJob`` (POST health ping + gauge); this
   override just stops the generic probe from double-alerting on the 405.

2. The full override key set stays reconciled with prod. These entries have
   historically drifted because one-shot timestamped migrations
   (``20260608_012805`` and siblings) added them without folding back into the
   baseline, so a fresh install silently got fewer overrides than prod. If a
   future baseline regen drops any migration-sourced key, this fails loud.

Hermetic by design (pure text/JSON parse, no app imports) so it can't flake on
PYTHONPATH/harness differences — same style as ``test_video_server_url_seed``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# The override map must seed exactly these keys (mirrors prod). Several were
# added by timestamped migrations and MUST stay folded into the baseline or a
# fresh install drifts from prod: data_fabric_loki_url / data_fabric_tempo_url /
# podcast_tts_base_url (20260608_012805) and mcp_http_probe_recovery_url.
EXPECTED_OVERRIDE_KEYS = {
    "cloudflare_beacon_url",
    "data_fabric_loki_url",
    "data_fabric_tempo_url",
    "google_sitemap_ping_url",
    "indexnow_ping_url",
    "mcp_http_probe_recovery_url",
    "podcast_tts_base_url",
    "storage_public_url",
    "video_server_url",
    "voice_agent_claude_code_host_brain_url",
    "voice_agent_stt_base_url",
    "voice_agent_tts_base_url",
}


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    seeds_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "migrations"
        / "0000_baseline.seeds.sql"
    )
    return seeds_path.read_text(encoding="utf-8")


def _sql_string_value(seeds_text: str, key: str) -> str | None:
    """Return the raw value seeded for ``key`` (un-escaping SQL ``''`` -> ``'``).

    Parses the SQL single-quoted literal directly rather than with a greedy
    regex, because the override JSON contains both single quotes (escaped as
    ``''``) and semicolons inside ``reason`` strings.
    """
    marker = f"('{key}', '"
    start = seeds_text.find(marker)
    if start == -1:
        return None
    j = start + len(marker)  # first char of the value (just past the opening quote)
    buf: list[str] = []
    while j < len(seeds_text):
        c = seeds_text[j]
        if c == "'":
            if seeds_text[j + 1 : j + 2] == "'":  # '' -> literal '
                buf.append("'")
                j += 2
                continue
            break
        buf.append(c)
        j += 1
    return "".join(buf)


def _overrides(seeds_text: str) -> dict:
    raw = _sql_string_value(seeds_text, "operator_url_probe_target_overrides")
    assert raw is not None, "operator_url_probe_target_overrides seed row missing"
    return json.loads(raw)  # also asserts the seed is valid JSON


def _parse_alive_codes(spec: str) -> list[range]:
    """Inline mirror of ``brain.operator_url_probe._parse_alive_codes`` (kept
    local so the test stays import-free)."""
    out: list[range] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.append(range(int(lo), int(hi) + 1))
        else:
            out.append(range(int(part), int(part) + 1))
    return out or [range(200, 400)]


def _alive(entry: dict, code: int) -> bool:
    return any(code in r for r in _parse_alive_codes(entry.get("alive_codes", "")))


def test_beacon_override_silences_405_but_not_5xx(baseline_seeds_text: str) -> None:
    """Beacon is POST-only; HEAD/GET 405 means alive. The seed must mark 405
    (and a real 204 POST) alive while keeping 5xx a real outage — else the probe
    either pages on a healthy beacon or goes blind to a dead one."""
    entry = _overrides(baseline_seeds_text).get("cloudflare_beacon_url")
    assert entry is not None, (
        "cloudflare_beacon_url override missing from baseline — the brain probe "
        "will page every ~15 min on the healthy POST-only beacon Worker (405)."
    )
    assert _alive(entry, 405), "405 must read as alive (beacon is POST-only by design)"
    assert _alive(entry, 204), "204 (a real beacon POST) must read as alive"
    assert not _alive(entry, 500), "500 must still read as a real outage"
    assert not _alive(entry, 503), "503 must still read as a real outage"


def test_override_key_set_matches_prod(baseline_seeds_text: str) -> None:
    """Guard the historical baseline<->prod drift: migration-sourced override
    entries must stay folded into the baseline so fresh installs match prod."""
    keys = set(_overrides(baseline_seeds_text))
    assert keys == EXPECTED_OVERRIDE_KEYS, (
        f"override key set drifted from prod. "
        f"missing={EXPECTED_OVERRIDE_KEYS - keys} extra={keys - EXPECTED_OVERRIDE_KEYS}. "
        "If you added an override via a timestamped migration, fold it into "
        "0000_baseline.seeds.sql too."
    )


def test_overrides_seed_is_idempotent(baseline_seeds_text: str) -> None:
    """ON CONFLICT DO NOTHING so a baseline replay never clobbers an operator's
    runtime-tuned override map. Line-anchored (not ``[^;]``) because the JSON
    value legitimately contains semicolons."""
    match = re.search(
        r"^INSERT INTO app_settings[^\n]*?'operator_url_probe_target_overrides'[^\n]*$",
        baseline_seeds_text,
        re.MULTILINE,
    )
    assert match is not None, "operator_url_probe_target_overrides INSERT not found"
    assert "ON CONFLICT (key) DO NOTHING" in match.group(0), (
        "overrides INSERT missing ON CONFLICT clause — a baseline replay would "
        "clobber operator-tuned overrides"
    )
