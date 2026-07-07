"""Lock the ``brain-ollama-down`` alert predicate to an accurate local-inference signal.

Provisioning files:
``infrastructure/grafana/provisioning/alerting/alert-rules.yml.tmpl`` (source of truth)
``infrastructure/grafana/provisioning/alerting/alert-rules.yml``      (rendered — what Grafana provisions)

WHY THIS TEST EXISTS

The "Ollama Unresponsive" rule is a *proxy* liveness check: when tasks are
pending but no local inference landed in ``cost_logs`` within the window, it
assumes Ollama is down. Its predicate drifted twice:

1. FALSE POSITIVE (the bug this test was born from). The rule filtered
   ``provider = 'ollama'``. But since the 2026-05-16 LiteLLM router cutover,
   local inference is logged ``provider = 'litellm'`` (the real Ollama model
   lands in the ``model`` column). The old literal matched zero rows, so the
   rule fired on *every* active task while Ollama was busy — training the
   operator (and the brain's reasoner) to ignore it.

2. FALSE NEGATIVE (the trap a naive fix falls into). Simply swapping to
   ``provider = 'litellm'`` is wrong: LiteLLM is a *router*. If Ollama dies and
   LiteLLM falls back to a cloud model, it still writes a ``provider='litellm'``
   row — now with ``cost_usd > 0`` — and the alert would stay silent through a
   real outage.

The accurate predicate keys on the *backend cost*, which ``cost_guard`` already
stamps: local (``is_local``) calls are recorded at ``cost_usd = 0``; cloud calls
carry real per-token dollars. So "local LLM inference is flowing" is:

    cost_type = 'inference' AND cost_usd = 0
      AND provider IN ('litellm', 'ollama', 'ollama_native')

``cost_usd = 0`` excludes a cloud fallback (defends against #2); the provider set
is router-agnostic (defends against #1) and keeps the check on the LLM path so a
local Whisper caption (``provider='caption.speaches'``, also $0) can't mask a
dead Ollama.

This test pins that predicate so neither drift can recur silently, and ties the
rendered YAML to its template so an edit to one without the other is caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_ALERTING_DIR = (
    REPO_ROOT / "infrastructure" / "grafana" / "provisioning" / "alerting"
)
ALERT_RULES_YML = _ALERTING_DIR / "alert-rules.yml"
ALERT_RULES_TMPL = _ALERTING_DIR / "alert-rules.yml.tmpl"

OLLAMA_RULE_UID = "brain-ollama-down"

# The router-agnostic, local-only fragment both files must carry. Kept as one
# string so the test fails loudly if a reviewer trims a provider or drops the
# cost_usd guard.
LOCAL_INFERENCE_FRAGMENT = (
    "cost_type = 'inference' AND cost_usd = 0 "
    "AND provider IN ('litellm', 'ollama', 'ollama_native')"
)


def _ollama_rule() -> dict:
    assert ALERT_RULES_YML.is_file(), f"missing provisioning file: {ALERT_RULES_YML}"
    data = yaml.safe_load(ALERT_RULES_YML.read_text(encoding="utf-8"))
    for group in data["groups"]:
        for rule in group.get("rules", []):
            if rule.get("uid") == OLLAMA_RULE_UID:
                return rule
    raise AssertionError(
        f"{OLLAMA_RULE_UID!r} not found in {ALERT_RULES_YML} — if the rule was "
        "renamed, update OLLAMA_RULE_UID here."
    )


def _ollama_rawsql() -> str:
    rule = _ollama_rule()
    for datum in rule.get("data", []):
        raw = datum.get("model", {}).get("rawSql")
        if raw:
            return raw
    raise AssertionError(f"{OLLAMA_RULE_UID!r} has no rawSql query to assert on")


def test_ollama_alert_counts_local_zero_cost_inference() -> None:
    """The rendered rule must count local ($0) LLM inference, not a provider literal."""
    raw = _ollama_rawsql()
    assert LOCAL_INFERENCE_FRAGMENT in raw, (
        f"{OLLAMA_RULE_UID!r} rawSql lost the accurate local-inference predicate.\n"
        f"expected fragment: {LOCAL_INFERENCE_FRAGMENT!r}\n"
        f"got: {raw!r}"
    )


def test_ollama_alert_requires_cost_usd_zero() -> None:
    """cost_usd = 0 is what distinguishes local Ollama from a paid cloud fallback.

    Without it, a LiteLLM cloud fallback (cost_usd > 0) would satisfy the rule
    and mask a dead Ollama — the false-negative trap.
    """
    raw = _ollama_rawsql()
    assert "cost_usd = 0" in raw, (
        f"{OLLAMA_RULE_UID!r} rawSql dropped the cost_usd = 0 guard; a paid cloud "
        f"fallback through LiteLLM would now keep the alert silent. got: {raw!r}"
    )


def test_ollama_alert_does_not_use_bare_provider_ollama() -> None:
    """The brittle ``provider = 'ollama'`` literal is the drift that broke this alert."""
    raw = _ollama_rawsql()
    assert "provider = 'ollama'" not in raw, (
        f"{OLLAMA_RULE_UID!r} rawSql reintroduced ``provider = 'ollama'`` — no row "
        "has carried that tag since the 2026-05-16 LiteLLM cutover, so the alert "
        f"would false-fire on every active task. got: {raw!r}"
    )


def test_ollama_alert_template_and_rendered_stay_consistent() -> None:
    """The predicate must live in BOTH the template and the rendered YAML.

    ``render_grafana_alerts.py`` regenerates the ``.yml`` from the ``.tmpl`` in
    prod, but the committed ``.yml`` is what fresh installs provision and what CI
    reads — so editing one file without the other is a silent staleness bug.
    """
    assert ALERT_RULES_TMPL.is_file(), f"missing template: {ALERT_RULES_TMPL}"
    tmpl_text = ALERT_RULES_TMPL.read_text(encoding="utf-8")
    assert LOCAL_INFERENCE_FRAGMENT in tmpl_text, (
        "the alert-rules.yml.tmpl template is missing the accurate local-inference "
        "predicate — the rendered .yml and its source template have drifted. "
        f"expected fragment: {LOCAL_INFERENCE_FRAGMENT!r}"
    )
    assert LOCAL_INFERENCE_FRAGMENT in _ollama_rawsql(), (
        "the rendered alert-rules.yml is missing the predicate present in the "
        "template — re-render via render_grafana_alerts or mirror the edit."
    )
