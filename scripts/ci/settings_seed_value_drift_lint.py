#!/usr/bin/env python3
"""CI guard: catch app_settings seed sources that disagree on a key's VALUE.

Sibling to ``settings_seed_drift_lint.py``, which guards a different failure
(a migration-deleted key that a seed file resurrects). This one closes the gap
poindexter#819 named and did not close:

    settings_seed_drift_lint only catches re-seeded deleted keys, **not value
    drift between the two seed sources** -- which is how this slipped through
    the baseline squash.

Why it matters
==============

Three files seed ``app_settings``, all with ``INSERT ... ON CONFLICT (key) DO
NOTHING``. First writer wins, so a disagreement means the loser's value is
simply unreachable -- and which one loses depends on the install path:

  * ``docker compose up`` on an empty DB -> the brain daemon seeds first
    (``worker`` declares ``depends_on: brain-daemon: service_healthy``, and
    ``seed_loader`` does its own ``CREATE TABLE IF NOT EXISTS``), so
    **brain > baseline > settings_defaults**.
  * ``poindexter setup`` -> migrations + ``seed_all_defaults`` run before any
    container, so **baseline > settings_defaults** and the brain seed no-ops.

That made fresh-install values nondeterministic by container topology. This
guard makes the sources agree, so the precedence stops mattering.

The rule
========

* ``settings_defaults.py::DEFAULTS`` and ``0000_baseline.seeds.sql`` are two
  encodings of **the same thing** (the reference default). They must agree
  exactly on every overlapping key -- there is no legitimate reason to differ,
  so there is no escape hatch for this half.
* ``brain/seed_app_settings.json`` is **a different thing**: its ``_meta``
  declares ``"tier": "free"``, and its lower limits are the free/paid product
  boundary, not drift. It may diverge from the baseline -- but only where
  declared in ``TIER_POLICY`` below, with a reason.

Comparison is **exact-string**, deliberately. Normalising JSON or floats would
let ``300`` vs ``300.0`` and whitespace-only drift persist, which is exactly the
noise that hides a real diff. Precedent: the prompt-fallback drift guard uses
the same exact-``==`` approach for the same "two encodings of one value" problem.

Known interaction with the next squash
======================================

A fold-forward baseline regeneration re-reads a LIVE DB, so it will re-import
operator values and red this check. **That is the guard working** -- it is the
whole reason it exists. See ``docs/operations/migrations.md`` for the runbook
step.

Static only -- no DB, no project imports -- so it runs in CI.
Exit 0 = clean, 1 = drift found.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SVC = REPO / "src" / "cofounder_agent" / "services"
BASELINE_SEEDS = SVC / "migrations" / "0000_baseline.seeds.sql"
DEFAULTS_PY = SVC / "settings_defaults.py"
BRAIN_SEED = REPO / "brain" / "seed_app_settings.json"

# Keys where brain/seed_app_settings.json is ALLOWED to differ from the
# baseline, because the brain seed is a deliberate free-tier profile
# (`_meta.tier == "free"`) rather than another encoding of the reference
# default. Every entry needs a reason -- this dict is the only written record
# of where the free/paid boundary actually sits, so a bare key is a bug.
#
# This does NOT excuse code<->baseline drift. Those two must always agree.
TIER_POLICY: dict[str, str] = {
    # --- free-tier throughput + spend caps -------------------------------
    "daily_post_limit": "free tier ships a deliberately lower post cap",
    "max_posts_per_day": "free tier ships a deliberately lower post cap",
    "daily_spend_limit_usd": "free tier ships a tighter daily spend cap",
    "monthly_spend_limit_usd": "free tier ships a tighter monthly spend cap",
    "max_approval_queue": "free tier ships a shallower approval queue",
    "prometheus.threshold.monthly_spend_warning_usd": (
        "warning threshold tracks the free tier's lower monthly spend cap"
    ),
    # --- free-tier quality bars ------------------------------------------
    "qa_final_score_threshold": (
        "free tier ships a lower QA bar; the tuned bar is the paid Quick Start "
        "Guide's territory per the brain seed's _meta"
    ),
    "min_curation_score": "free tier ships a lower curation bar (see qa_final_score_threshold)",
    "content_validator_warning_reject_threshold": (
        "free tier tolerates fewer validator warnings before rejecting"
    ),
    # --- bootstrap identity placeholders ---------------------------------
    # The brain seed's purpose is "minimum viable config to make the pipeline
    # runnable -- docker compose up, visit /api/health, submit a task", so it
    # ships working placeholders. The reference seed leaves identity empty for
    # the operator to fill via `poindexter setup`.
    "site_name": "brain seeds a runnable placeholder; the reference seed leaves identity empty",
    "site_url": "brain seeds a runnable placeholder; the reference seed leaves identity empty",
    "site_domain": "brain seeds a runnable placeholder; the reference seed leaves identity empty",
    "public_site_url": "brain seeds a runnable placeholder; the reference seed leaves identity empty",
    "company_name": "brain seeds a runnable placeholder; the reference seed leaves identity empty",
    "privacy_email": "brain seeds a runnable placeholder; the reference seed leaves identity empty",
    "support_email": "brain seeds a runnable placeholder; the reference seed leaves identity empty",
}

_SEED_ROW_RE = re.compile(
    r"INSERT INTO app_settings \(key, value,.*?VALUES \('([^']+)', '((?:[^']|'')*)'",
    re.S,
)


def _defaults() -> dict[str, str]:
    """key -> value from the DEFAULTS dict literal in settings_defaults.py."""
    tree = ast.parse(DEFAULTS_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value, named = node.value, any(
                isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in node.targets
            )
        elif isinstance(node, ast.AnnAssign):
            value, named = node.value, (
                isinstance(node.target, ast.Name) and node.target.id == "DEFAULTS"
            )
        else:
            continue
        if named and isinstance(value, ast.Dict):
            out: dict[str, str] = {}
            for k, v in zip(value.keys, value.values, strict=True):
                if (
                    isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                    and isinstance(v, ast.Constant)
                    and isinstance(v.value, str)
                ):
                    out[k.value] = v.value
            return out
    return {}


def _baseline() -> dict[str, str]:
    """key -> value from the baseline seed SQL (un-escaping SQL's '' -> ')."""
    text = BASELINE_SEEDS.read_text(encoding="utf-8")
    return {
        m.group(1): m.group(2).replace("''", "'") for m in _SEED_ROW_RE.finditer(text)
    }


def _brain() -> dict[str, str]:
    """key -> value from the brain's free-tier seed JSON."""
    if not BRAIN_SEED.exists():
        return {}
    data = json.loads(BRAIN_SEED.read_text(encoding="utf-8"))
    return {
        s["key"]: str(s["value"])
        for s in data.get("settings", [])
        if isinstance(s, dict) and "key" in s and "value" in s
    }


def main() -> int:
    code, baseline, brain = _defaults(), _baseline(), _brain()

    # 1. code <-> baseline: must always agree. No escape hatch.
    code_drift = sorted(
        k for k in set(code) & set(baseline) if code[k] != baseline[k]
    )

    # 2. brain <-> baseline: may differ only where declared.
    brain_drift = sorted(
        k
        for k in set(brain) & set(baseline)
        if brain[k] != baseline[k] and k not in TIER_POLICY
    )

    # 3. TIER_POLICY entries that no longer describe a real divergence. Without
    #    this the allowlist silently rots into a list of claims that stopped
    #    being true, and the next reader trusts it.
    stale = sorted(
        k
        for k in TIER_POLICY
        if k not in (set(brain) & set(baseline)) or brain.get(k) == baseline.get(k)
    )

    if not (code_drift or brain_drift or stale):
        overlap = len(set(code) & set(baseline))
        print(
            f"settings-seed-value-drift: OK ({overlap} code/baseline keys agree, "
            f"{len(TIER_POLICY)} declared free-tier divergence(s))"
        )
        return 0

    print("settings-seed-value-drift: DRIFT\n")

    if code_drift:
        print(
            f"  {len(code_drift)} key(s): settings_defaults.py disagrees with "
            "0000_baseline.seeds.sql."
        )
        print(
            "  These encode the SAME thing (the reference default) — pick one "
            "value and set it in both.\n"
        )
        for k in code_drift:
            print(f"    - {k}")
            print(f"        settings_defaults.py : {code[k]!r}")
            print(f"        baseline.seeds.sql   : {baseline[k]!r}")
        print()

    if brain_drift:
        print(
            f"  {len(brain_drift)} key(s): brain/seed_app_settings.json disagrees "
            "with the baseline and is not declared."
        )
        print(
            "  The brain seed is the free tier (_meta.tier=free) and MAY differ — "
            "but say so: add a TIER_POLICY entry with a reason. If the divergence "
            "is not deliberate, fix the brain seed instead.\n"
        )
        for k in brain_drift:
            print(f"    - {k}")
            print(f"        brain (free tier)  : {brain[k]!r}")
            print(f"        baseline.seeds.sql : {baseline[k]!r}")
        print()

    if stale:
        print(f"  {len(stale)} stale TIER_POLICY entry/entries — the key no longer")
        print("  diverges (or is no longer in both sources). Remove the entry.\n")
        for k in stale:
            print(f"    - {k}: {TIER_POLICY[k]}")
        print()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
