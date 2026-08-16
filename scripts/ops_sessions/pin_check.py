"""pin-check — daily probe that every OPS_OLLAMA_MODEL_* pin is satisfiable.

The detection gap behind stack#3163: a session's model pin can be
unsatisfiable for weeks and the only detector was the session itself
failing at its own fire time (03:00, asleep hours) — one iteration per
day, and before #3154 the failure was silent as well. The testfix pin sat
missing for over two weeks after the Pop!_OS fleet re-pull skipped the
one model no interactive workflow uses.

This session runs the same ``preflight_model_pins`` check the LLM
sessions now run at startup, but on its own daily timer at a humane hour
(12:30 local) — so a broken pin pages the operator the same day it
breaks, with the ``ollama pull`` remedy, instead of surfacing as a 03:00
failure log. Deterministic tier: one HTTP GET, no DB, no LLM call, no
commits.

The pin set comes from ``_common.MODEL_PINS`` — the single registry the
preflight and this probe share, so a future pin added there is covered
automatically.
"""
from __future__ import annotations

import sys

import _common as c


def main() -> int:
    log = c.get_logger("pin-check")
    pins = c.MODEL_PINS
    try:
        c.preflight_model_pins(*pins.values())
    except c.OllamaUnavailable as exc:
        log.error("pin check failed: %s", exc)
        c.notify_fail(
            "ops model pin(s) unsatisfiable",
            f"{exc}\n\nPins checked: "
            + ", ".join(f"{k}={v!r}" for k, v in pins.items())
            + "\nSessions that depend on them will fail at their next fire "
            "until this is fixed.",
            "pin_check",
        )
        return 1
    log.info(
        "all %d model pin(s) present on %s: %s",
        len(pins), c.OPS_OLLAMA_URL,
        ", ".join(f"{k}={v!r}" for k, v in pins.items()),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
