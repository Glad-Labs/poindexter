#!/usr/bin/env python3
"""Suggest `settings_defaults.METADATA` annotations for unannotated keys.

`app_settings.owner` / `value_type` (poindexter#756) drive the settings API
(poindexter#956) and the zero-reader orphan probe. New keys land in `DEFAULTS`
without annotations, so coverage decays unless someone re-derives it. This
script does the derivation from the tree instead of by hand, and prints entries
ready to paste into `METADATA`.

Run from the repo root::

    python scripts/suggest_settings_metadata.py            # summary + entries
    python scripts/suggest_settings_metadata.py --verify   # audit existing METADATA

WHY THE RULES ARE WHAT THEY ARE — each was validated against real values before
being trusted, and the tempting shortcuts are all wrong:

* ``owner`` is emitted ONLY when exactly one non-excluded source file references
  the key. A wrong owner is worse than none: it sends an operator to the wrong
  module and looks authoritative doing it. Ambiguous keys get no owner.
* Seeders/appliers must be excluded or they win every key. ``settings_defaults``,
  ``operator_overrides``, migrations, seeds and tests MENTION every key without
  reading any.
* ``value_type`` comes from the key's real VALUE, not its name. The name lies:
  ``allow_paid_base_url`` is a boolean, ``affiliate_redirect_base_url`` is
  ``'/go'``, several ``*_seconds`` are floats, and ``nomic-embed-text`` is a
  model with no ``:`` or ``/``.
* The one name signal that survived checking is ``*_enabled`` -> boolean
  (83/83 of non-empty ones at the time of writing). It is used only to type
  keys whose value is empty.
* An empty value with no proven name signal gets NO type. ``''`` is the unset
  sentinel across every type, so guessing there fabricates data.
* ``csv`` requires confirming the CONSUMER splits on ``','``. Prose that merely
  contains a comma (a disclosure sentence, a negative prompt) is a ``string``.

Nothing here writes to the DB or edits source; it prints suggestions for a human
to review. Values must still be sanity-checked before pasting.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "src" / "cofounder_agent"
sys.path.insert(0, str(BACKEND))

from services.settings_defaults import DEFAULTS, METADATA  # noqa: E402

# Paths that name keys without consuming them.
EXCLUDE = re.compile(
    r"(settings_defaults\.py|/migrations/|(^|/)tests/|test_|settings_categories\.py"
    r"|operator_overrides\.py|conftest\.py|/console/|settings_routes\.py|cli/settings\.py)"
)

VALID_TYPES = {
    "string", "boolean", "integer", "float",
    "url", "model", "csv", "json", "duration",
}

# Keys whose reader was individually confirmed to split the value on ','.
# Add to this only after checking the consumer — not on the value's appearance.
VERIFIED_CSV = {
    "gpu_evictable_process_pattern",
    "qa_allow_first_person_niches",
    "tts_model_name_families",
}


def infer_value_type(key: str, value: str) -> str | None:
    """Type from the real default value. Returns None when nothing is provable."""
    v = (value or "").strip()
    if v.lower() in ("true", "false"):
        return "boolean"
    if key.endswith("_enabled"):
        return "boolean"  # the one validated name signal
    if not v:
        return None  # '' is the unset sentinel for every type — do not guess
    if re.fullmatch(r"-?\d+", v):
        return "integer"
    if re.fullmatch(r"-?\d*\.\d+", v):
        return "float"
    if v.startswith(("http://", "https://", "ws://", "wss://")):
        return "url"
    if v.startswith(("{", "[")):
        return "json"
    if key in VERIFIED_CSV:
        return "csv"
    is_model_slot = key.endswith("_model") or "_model_" in key or key.startswith("model_")
    if is_model_slot and not re.fullmatch(r"-?[\d.]+", v):
        return "model"
    return "string"


def scan_readers() -> dict[str, set[str]]:
    """key -> {module stems that reference it}, excluding seeders and tests."""
    keys = set(DEFAULTS) | set(METADATA)
    hits: dict[str, set[str]] = defaultdict(set)
    sources = list(BACKEND.rglob("*.py")) + list((REPO_ROOT / "brain").rglob("*.py"))
    for path in sources:
        rel = str(path.relative_to(REPO_ROOT))
        if EXCLUDE.search(rel) or "node_modules" in rel or path.stem in ("__init__", "main"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in re.finditer(r"""["']([a-z][a-z0-9_.]{4,})["']""", text):
            if match.group(1) in keys:
                hits[match.group(1)].add(path.stem)
    return hits


def _owner_matches(owner: str, readers: set[str]) -> bool:
    """Does `owner` plausibly name one of the modules that read the key?

    Two naming conventions coexist and both are legitimate, so an exact stem
    match is too strict:

    * conceptual subsystem labels, hand-curated — `multi_model_qa` owns
      `qa_content_originality.py`; `brain_psu_watchdog` is a component inside
      `brain_daemon.py`
    * literal file stems, machine-derived

    Substring matching in either direction catches the overlapping cases
    (`plan_image_markers` vs `content_plan_image_markers`). A genuinely
    conceptual label with no textual overlap still won't match — which is why
    the caller reports those as REVIEW rather than as an error.
    """
    return any(owner == r or owner in r or r in owner for r in readers)


def verify(hits: dict[str, set[str]]) -> int:
    """Audit existing METADATA.

    Only mechanically-decidable problems fail the run. Owner mismatches are
    advisory: a conceptual label that no filename echoes is not wrong, just
    unverifiable from here, and failing on it would train readers to ignore
    this check — the noisy-scanner trap.
    """
    bad_type = [
        (k, m["value_type"])
        for k, m in METADATA.items()
        if m.get("value_type") and m["value_type"] not in VALID_TYPES
    ]
    orphan_dep = [k for k, m in METADATA.items() if m.get("deprecated") and not m.get("superseded_by")]
    owned = {k: m["owner"] for k, m in METADATA.items() if m.get("owner")}
    unmatched = [k for k, o in owned.items() if not _owner_matches(o, hits.get(k, set()))]
    no_reader = [k for k in unmatched if not hits.get(k)]

    print(f"METADATA entries                 : {len(METADATA)}")
    print(f"  ERROR  invalid value_type      : {len(bad_type)} {bad_type[:5]}")
    print(f"  ERROR  deprecated, no successor: {len(orphan_dep)} {orphan_dep[:5]}")
    print(f"  review owner not textually matched: {len(unmatched)}"
          f" (conceptual labels are fine — spot-check, don't bulk-edit)")
    print(f"         ...of which no reader found at all: {len(no_reader)} {no_reader[:5]}")
    return 1 if (bad_type or orphan_dep) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="audit existing METADATA instead")
    args = ap.parse_args()

    hits = scan_readers()
    if args.verify:
        return verify(hits)

    suggestions: dict[str, dict[str, str]] = {}
    for key in sorted(DEFAULTS):
        if key in METADATA:
            continue
        entry: dict[str, str] = {}
        owners = hits.get(key, set())
        if len(owners) == 1:
            entry["owner"] = next(iter(owners))
        value_type = infer_value_type(key, DEFAULTS[key])
        if value_type:
            entry["value_type"] = value_type
        if entry:
            suggestions[key] = entry

    covered = sum(1 for k in DEFAULTS if k in METADATA)
    print(f"DEFAULTS keys        : {len(DEFAULTS)}")
    print(f"  already annotated  : {covered}")
    print(f"  new suggestions    : {len(suggestions)}")
    print(f"  still unannotated  : {len(DEFAULTS) - covered - len(suggestions)}")
    print(f"  types              : {dict(Counter(e.get('value_type') for e in suggestions.values()))}")
    if not suggestions:
        return 0
    print("\n# paste into METADATA after reviewing:")
    for key, entry in suggestions.items():
        body = ", ".join(f"'{f}': '{entry[f]}'" for f in ("owner", "value_type") if f in entry)
        print(f"    '{key}': {{{body}}},")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
