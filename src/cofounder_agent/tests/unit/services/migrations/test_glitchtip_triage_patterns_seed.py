"""Contract test for the ``glitchtip_triage_auto_resolve_patterns`` baseline seed.

Pins two fixes from the 2026-07-12 pager-fatigue investigation
(glad-labs-stack#2402):

1. The shipped ``^CancelledError$`` rule (reason: "Scheduler shutdown
   propagation") must carry an EXPLICIT ``max_count``. Omitting it silently
   bounds the rule to the #304 safety default (50) — real installs exceed
   that within days of routine cancellations, which permanently breaks the
   rule (``_match_rule`` requires ``count <= max_count`` to match) and
   re-exposes exactly the noise the rule exists to suppress. Live evidence:
   GlitchTip issue 870 sat at count=51, one over the silent ceiling,
   re-alerting every cycle it was re-evaluated as eligible.

2. A new ``^\\[operator_notifier\\]`` / ``ignore`` rule must exist. Without
   it, ``operator_notifier.py``'s own ``logger.error/critical("[operator_
   notifier] %s", text)`` call — which fires on every delivered page — gets
   captured by the brain daemon's Sentry logging integration as a fresh
   GlitchTip issue, and this probe re-alerts on it: a structural double-page
   for an alert already delivered directly. Live evidence: GlitchTip issue
   929 (compose-drift, level=fatal) delivered 8 genuine Telegram pages, each
   one a duplicate of compose_drift_probe's own direct notify_operator call.

Hermetic by design (pure text/JSON parse, no app imports) — same style as
``test_operator_url_probe_overrides_seed.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# Below this, a resolve rule with no explicit max_count silently degrades —
# see glitchtip_triage_probe.py's #304 DEFAULT_RESOLVE_MAX_COUNT_DEFAULT (50).
# 1000 gives ample real-world headroom (issue 870 grew ~26/day; at that rate
# 1000 is ~38 days of runway) while staying a finite, revisitable ceiling.
MIN_SAFE_CANCELLED_ERROR_CEILING = 500


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    seeds_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "migrations"
        / "0000_baseline.seeds.sql"
    )
    return seeds_path.read_text(encoding="utf-8")


def _rules(seeds_text: str) -> list[dict]:
    """Extract + parse the glitchtip_triage_auto_resolve_patterns seed value.

    This row uses Postgres ``E'...'`` escaped-string syntax (unlike the
    plain ``'...'``/``''``-doubled style used elsewhere in the same file),
    because its regex patterns need literal backslashes. The only escape
    sequence present is ``\\\\`` -> ``\\`` (a doubled backslash), so a
    straight replace is a faithful emulation of Postgres's E''-unescaping
    for this content.
    """
    m = re.search(
        r"VALUES \('glitchtip_triage_auto_resolve_patterns', E'(.*?)', 'monitoring'",
        seeds_text,
        re.DOTALL,
    )
    assert m is not None, "glitchtip_triage_auto_resolve_patterns seed row missing"
    unescaped = m.group(1).replace("\\\\", "\\")
    return json.loads(unescaped)  # also asserts the seed is valid JSON


def test_cancelled_error_rule_has_explicit_high_ceiling(baseline_seeds_text: str) -> None:
    """The shipped CancelledError resolve rule must not rely on the silent
    #304 default — glad-labs-stack#2402 (issue 870 sat stuck at count=51,
    one over the implicit ceiling, re-alerting every eligible cycle)."""
    rules = _rules(baseline_seeds_text)
    rule = next(
        (r for r in rules if r.get("title_pattern") == "^CancelledError$"), None
    )
    assert rule is not None, "^CancelledError$ rule missing from baseline"
    assert rule.get("action") == "resolve"
    max_count = rule.get("max_count")
    assert max_count is not None, (
        "CancelledError rule must set an explicit max_count — an omitted "
        "one silently bounds to 50 (the #304 default), which real installs "
        "exceed within days, permanently breaking the rule."
    )
    assert max_count >= MIN_SAFE_CANCELLED_ERROR_CEILING, (
        f"CancelledError max_count={max_count} is too low a ceiling — "
        f"want >= {MIN_SAFE_CANCELLED_ERROR_CEILING} for real-world headroom."
    )


def test_operator_notifier_self_capture_is_ignored(baseline_seeds_text: str) -> None:
    """A GlitchTip issue whose title is operator_notifier's own outbound
    page (captured via the brain Sentry logging integration) must be
    ignored, not re-alerted — glad-labs-stack#2402 (issue 929, compose-drift,
    delivered 8 genuine duplicate Telegram pages before this fix)."""
    rules = _rules(baseline_seeds_text)
    rule = next(
        (r for r in rules if "operator_notifier" in r.get("title_pattern", "")),
        None,
    )
    assert rule is not None, "operator_notifier self-capture rule missing from baseline"
    assert rule.get("action") == "ignore", (
        "must be 'ignore', not 'resolve' — this is a mechanical echo of an "
        "already-delivered alert, not a GlitchTip issue needing triage/"
        "closure, and 'ignore' rules are exempt from the #304 ceiling."
    )

    pattern = re.compile(rule["title_pattern"])
    # Real title captured live during the 2026-07-12 investigation.
    real_title = (
        "[operator_notifier] \U0001F6A8 Compose drift PERSISTS — "
        "host-recover cap reached (1 service(s))"
    )
    assert pattern.search(real_title), (
        f"pattern {rule['title_pattern']!r} does not match a real captured "
        f"operator_notifier title: {real_title!r}"
    )
    # Anchored to the start — must not match unrelated titles that merely
    # mention operator_notifier mid-string (over-broad match risk).
    assert not pattern.search("some unrelated title referencing operator_notifier in prose"), (
        f"pattern {rule['title_pattern']!r} must be start-anchored, not a "
        "substring match"
    )
