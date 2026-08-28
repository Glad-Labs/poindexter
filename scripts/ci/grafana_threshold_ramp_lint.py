#!/usr/bin/env python3
# scan-floor-exempt: has its own `if not files` floor
"""CI lint: no Grafana threshold ramp may repeat the previous step's colour.

WHY

A threshold ramp encodes severity by colour. When two adjacent steps share
a colour, crossing that threshold produces **no visible change** — the panel
advertises four severity levels and delivers three. Nothing errors, the panel
renders, the numbers are real; the escalation is simply invisible.

This was fleet-wide, not a one-off. Glad-Labs/poindexter#1028 found 23 ramps
across 6 boards, in shapes that repeat far too regularly to be hand-authored
(``blue,yellow,orange,orange``, ``orange,orange,yellow,blue``, ``blue,blue``)
— the fingerprint of a past ``red->orange`` / ``green->blue`` sweep that
collapsed adjacent steps wherever the ramp already used the replacement
colour. A sweep like that is exactly the kind of change a ratchet should
have caught at PR time, which is why this script exists.

WHAT IT CHECKS

Every ``thresholds.steps`` array reachable from a dashboard panel — both
``fieldConfig.defaults`` and every per-field ``fieldConfig.overrides`` entry,
recursing into ``panels[].panels[]`` rows via
``lib_grafana_panels.walk_panels``. A step whose ``color`` equals the
previous step's ``color`` is a failure.

FIXING A FAILURE

Two legitimate shapes, and picking between them is a judgement call about
the panel, not a mechanical swap:

  * The ramp really does have N levels — give the top step a distinct
    colour. House convention (set on the Database board, #3414):
        ascending-is-worse   blue -> yellow -> orange -> dark-red
        descending-is-worse  dark-red -> orange -> yellow -> blue
    ``dark-red`` rather than plain ``red`` is deliberate: the operator is
    red-green deficient (deuteranomaly), where red-vs-orange is a
    *compressed* pair. dark-red also separates on luminance, which is the
    intact channel.

  * The ramp does not have N levels — **delete the redundant step.** An
    informational counter (in-flight tasks, queue depth) often has no
    severity distinction between 0 and 1, and inventing a colour for one
    is worse than admitting the ramp has two levels.

Do not silence this lint. A duplicate colour is always one of the two cases
above.

LOCAL USAGE

    python3 scripts/ci/grafana_threshold_ramp_lint.py
    python3 scripts/ci/grafana_threshold_ramp_lint.py infrastructure/grafana/dashboards/

Unlike ``grafana_panels_lint.py`` this needs no datasource — it is a pure
JSON structure check, so it runs anywhere.

SEE ALSO

  * scripts/ci/grafana_panels_lint.py — validates panel *queries* (needs live
    datasources; note its PROMETHEUS_URL default is 9090 but this stack runs
    Prometheus on 9091).
  * Glad-Labs/poindexter#1028 — the sweep this ratchet locks in.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_grafana_panels import iter_root_panels, walk_panels  # noqa: E402


def _ramps(panel: dict):
    """Yield (where, steps) for every thresholds block on a panel."""
    field_config = panel.get("fieldConfig") or {}
    defaults = (field_config.get("defaults") or {}).get("thresholds") or {}
    if defaults.get("steps"):
        yield "defaults", defaults["steps"]
    for override in field_config.get("overrides") or []:
        where = (override.get("matcher") or {}).get("options")
        # A byRegexp/byType matcher's options can be a dict; stringify for the
        # message rather than assuming a scalar.
        label = where if isinstance(where, str) else json.dumps(where)
        for prop in override.get("properties") or []:
            if prop.get("id") == "thresholds" and (prop.get("value") or {}).get("steps"):
                yield label, prop["value"]["steps"]


def main() -> int:
    args = sys.argv[1:]
    target = (
        Path(args[0])
        if args
        else Path(__file__).resolve().parents[2] / "infrastructure" / "grafana" / "dashboards"
    )
    if not target.is_dir():
        print(f"ERROR: not a directory: {target}", file=sys.stderr)
        return 2

    files = sorted(target.glob("*.json"))
    if not files:
        print(f"ERROR: no dashboards under {target}", file=sys.stderr)
        return 2

    failures: list[str] = []
    checked = 0
    for path in files:
        board = json.loads(path.read_text(encoding="utf-8"))
        for root in iter_root_panels(board):
            for panel in walk_panels(root):
                for where, steps in _ramps(panel):
                    checked += 1
                    colors = [s.get("color") for s in steps]
                    for idx, (prev, cur) in enumerate(
                    zip(colors, colors[1:], strict=False), start=1
                ):
                        if prev != cur:
                            continue
                        failures.append(
                            f"  {path.name} panel={panel.get('id')} "
                            f"({panel.get('title')!r}) [{where}]: step {idx} repeats "
                            f"{cur!r} — {','.join(str(c) for c in colors)}"
                        )

    print(f"[grafana-ramp-lint] checked {checked} threshold ramp(s) across {len(files)} board(s)")
    if failures:
        print(f"\n{len(failures)} ramp step(s) repeat the previous colour:\n")
        print("\n".join(failures))
        print(
            "\nFAIL: an invisible threshold crossing. Give the step a distinct "
            "colour, or delete it if the ramp does not really have that level. "
            "See this script's docstring for the house convention.",
            file=sys.stderr,
        )
        return 1
    print("[grafana-ramp-lint] clean — every ramp step changes colour.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
