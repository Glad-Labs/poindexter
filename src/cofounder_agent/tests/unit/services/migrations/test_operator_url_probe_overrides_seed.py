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
# plugin.caption_provider.speaches.base_url routes to speaches /health like the
# other three speaches-backed keys; without it the operator-url probe HEADs the
# bare /v1 base path (404) and false-pages on a healthy caption sidecar.
# api_base_url / grafana_url / internal_api_base_url /
# plugin.tts_provider.chatterbox.base_url (glad-labs-stack#2395/#2397) route to
# each service's compose-DNS /health endpoint instead of a host-published port
# (localhost:3000, localhost:8002) that wedges from inside the brain container
# even when the backend is healthy on the compose net — the dominant operator
# pager-fatigue source found 2026-07-12 (178 Telegram + 178 Discord pages/24h).
EXPECTED_OVERRIDE_KEYS = {
    "api_base_url",
    "cloudflare_beacon_url",
    "data_fabric_loki_url",
    "data_fabric_tempo_url",
    "google_sitemap_ping_url",
    "grafana_url",
    "indexnow_ping_url",
    "internal_api_base_url",
    "mcp_http_probe_recovery_url",
    "plugin.caption_provider.speaches.base_url",
    "plugin.tts_provider.chatterbox.base_url",
    "podcast_tts_base_url",
    "storage_public_url",
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


def test_data_fabric_overrides_probe_compose_dns(baseline_seeds_text: str) -> None:
    """Tempo + Loki probe overrides must target the compose-network service
    name, not ``localhost`` / ``host.docker.internal``.

    The data_fabric clients and Grafana's datasources reach these backends over
    the compose net (``tempo:3200`` / ``loki:3100``). Probing the host-published
    port instead means a wedged host-port proxy — Tempo healthy, traces still
    flowing to Grafana over the compose net, but ``localhost:3200`` returning an
    empty reply — pages the operator every ~15 min on a false positive. Compose-
    DNS probing tests the path real consumers use and only fails when the backend
    is actually down. Regression guard for that false-page class.
    """
    overrides = _overrides(baseline_seeds_text)
    expected_probe_urls = {
        "data_fabric_tempo_url": "http://tempo:3200/ready",
        "data_fabric_loki_url": "http://loki:3100/ready",
    }
    for key, want in expected_probe_urls.items():
        entry = overrides.get(key)
        assert entry is not None, f"{key} override missing from baseline"
        got = entry.get("probe_url")
        assert got == want, (
            f"{key} probe_url must be {want!r} (compose DNS), got {got!r}. "
            "localhost / host.docker.internal routes the probe through the "
            "host-published-port proxy, which false-pages when only that proxy "
            "is wedged while the backend is healthy on the compose net."
        )
        assert "localhost" not in got and "host.docker.internal" not in got, (
            f"{key} probe_url must not target the host-port path: {got!r}"
        )


def test_speaches_backed_overrides_probe_health(baseline_seeds_text: str) -> None:
    """Every speaches-backed base_url override must probe /health, not bare /v1.

    speaches serves its OpenAI-compatible API under ``/v1/*`` (``/v1/audio/
    transcriptions`` for the caption provider, ``/v1/audio/speech`` for TTS) but
    has no GET handler on the bare ``/v1`` base path, so a HEAD/GET there 404s.
    The caption key was missing this override and false-paged the operator-url
    probe on a healthy speaches sidecar; this guards all four speaches keys so
    the gap can't reopen. ``/health`` is the container's own liveness endpoint
    (200) — the same one docker-compose healthchecks.
    """
    overrides = _overrides(baseline_seeds_text)
    speaches_keys = (
        "plugin.caption_provider.speaches.base_url",
        "podcast_tts_base_url",
        "voice_agent_stt_base_url",
        "voice_agent_tts_base_url",
    )
    for key in speaches_keys:
        entry = overrides.get(key)
        assert entry is not None, f"{key} override missing from baseline"
        assert entry.get("probe_url") == "http://speaches:8000/health", (
            f"{key} must probe speaches /health (200), got {entry.get('probe_url')!r}. "
            "The bare /v1 base path 404s and false-pages the operator-url probe."
        )


def test_service_base_overrides_probe_compose_dns_health(baseline_seeds_text: str) -> None:
    """chatterbox / grafana / worker API-base overrides must probe the
    compose-network service name's health endpoint, not localhost /
    host.docker.internal (glad-labs-stack#2395/#2397).

    These app_settings hold host-published-port URLs (``localhost:3000``,
    ``localhost:8002``) that wedge intermittently from inside the brain
    container (``RemoteProtocolError: server disconnected`` / ``ConnectError``)
    even though the backend is healthy on the compose net — this was the
    dominant operator pager-fatigue source (178 Telegram + 178 Discord
    deliveries/24h, 2026-07-12). chatterbox's OpenAI-compat ``/v1`` base also
    has no GET handler (bare HEAD/GET 404s). Regression guard: same shape as
    the data_fabric/speaches overrides above — if this baseline entry is ever
    dropped or repointed at a host-published port, the false-page class
    reopens.
    """
    overrides = _overrides(baseline_seeds_text)
    expected_probe_urls = {
        "plugin.tts_provider.chatterbox.base_url": "http://chatterbox:8000/health",
        "grafana_url": "http://grafana:3000/api/health",
        "api_base_url": "http://worker:8002/api/health",
        "internal_api_base_url": "http://worker:8002/api/health",
    }
    for key, want in expected_probe_urls.items():
        entry = overrides.get(key)
        assert entry is not None, f"{key} override missing from baseline"
        got = entry.get("probe_url")
        assert got == want, (
            f"{key} probe_url must be {want!r} (compose DNS), got {got!r}. "
            "localhost / host.docker.internal routes the probe through the "
            "host-published-port proxy, which false-pages when only that proxy "
            "is wedged while the backend is healthy on the compose net."
        )
        assert "localhost" not in got and "host.docker.internal" not in got, (
            f"{key} probe_url must not target the host-port path: {got!r}"
        )


def test_overridden_surfaces_are_monitored_not_skiplisted(baseline_seeds_text: str) -> None:
    """Surfaces with a probe override must not ALSO be skip-listed, or they're muted.

    A key in BOTH operator_url_probe_skip_keys AND the override map is a
    contradiction: ``collect_app_setting_urls`` drops skip-listed keys before the
    override is ever consulted, so the skip-list wins and the surface goes
    unmonitored. These were migrated off the skip-list (keeping their overrides)
    so they're actually probed — the four speaches keys via /health, the three
    ping/bucket surfaces via alive_codes 200-499 — per the no-silencing rule.
    Guard so none can be silently re-muted.

    ``voice_agent_claude_code_host_brain_url`` is deliberately excluded: its
    /healthz host service (voice_brain_host on :8123) is intermittent, so it
    stays skip-listed on purpose.
    """
    skip_raw = _sql_string_value(baseline_seeds_text, "operator_url_probe_skip_keys") or ""
    skip_keys = {s.strip() for s in skip_raw.split(",") if s.strip()}
    overrides = _overrides(baseline_seeds_text)
    monitored = (
        "plugin.caption_provider.speaches.base_url",
        "podcast_tts_base_url",
        "voice_agent_stt_base_url",
        "voice_agent_tts_base_url",
        "google_sitemap_ping_url",
        "indexnow_ping_url",
        "storage_public_url",
    )
    for key in monitored:
        assert key not in skip_keys, (
            f"{key} is skip-listed AND has a probe override — the skip-list wins, "
            f"so the surface is muted, not monitored. Remove it from "
            f"operator_url_probe_skip_keys."
        )
        assert overrides.get(key) is not None, (
            f"{key} must keep its override so un-skipping it actually monitors it "
            f"(not a bare 404/405 false page)."
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
