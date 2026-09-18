"""Tell us when the langchain-community pin can finally be lifted (poindexter#840).

`pyproject.toml` holds `langchain-community = ">=0.4,<0.4.2"` for one reason:
ragas 0.4.x hard-imports `langchain_community.chat_models.vertexai` at
`from ragas import evaluate` time while declaring `langchain-community="*"`.
That module was removed in langchain-community 0.4.2, so the pairing
ImportErrors and the whole Ragas QA rail dies — deployed workers lost it
2026-06-29 → 07-11, surfacing only as misleading "judge or embedding backend
likely unreachable" skips (#839).

The pin is a hold on someone else's bug, and holds rot: nothing tells us when
it stops being necessary, so it outlives its reason and quietly blocks every
later langchain-community fix. This is the convergence watchdog for it.

It asserts the pin is STILL NEEDED. When a future ragas drops the import, this
test fails — and that failure is the notification, arriving on the dependabot
bump that makes the change relevant rather than on a calendar reminder nobody
set. The failure message says exactly what to do.

Verified 2026-09-18: ragas 0.4.3 is the newest release on PyPI and
`ragas/llms/base.py:12` still carries the import, so the pin stands.
"""

from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.unit

_REMOVED_MODULE = "langchain_community.chat_models.vertexai"


def _ragas_root() -> pathlib.Path | None:
    try:
        import ragas
    except Exception:  # noqa: BLE001 — optional `qa` extra; absence is not a failure
        return None
    return pathlib.Path(ragas.__file__).parent


def test_pin_is_still_required_or_tell_us_to_lift_it():
    root = _ragas_root()
    if root is None:
        pytest.skip("ragas not installed (optional `qa` extra) — nothing to judge")

    offenders = [
        f"{p.relative_to(root)}:{i}"
        for p in root.rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1)
        if _REMOVED_MODULE in line
    ]

    assert offenders, (
        "ragas no longer imports "
        f"`{_REMOVED_MODULE}` — the reason for the "
        "`langchain-community = '>=0.4,<0.4.2'` pin in "
        "src/cofounder_agent/pyproject.toml is GONE.\n\n"
        "This test failing IS the signal (poindexter#840). To lift it:\n"
        "  1. widen the pin (drop the <0.4.2 upper bound),\n"
        "  2. `poetry lock` + reinstall the `qa` extra,\n"
        "  3. run a live Ragas smoke — `from ragas import evaluate` must "
        "import AND a real rail pass must score, since the 2026-06 failure "
        "showed as a degraded rail, not an ImportError at boot,\n"
        "  4. delete this test and the pin comment together.\n\n"
        "Do NOT just delete this test to get green."
    )


def test_the_pinned_pairing_actually_imports():
    """The pin is only worth holding if it works. Guards the other direction:
    a langchain-community inside the allowed range that still breaks ragas."""
    if _ragas_root() is None:
        pytest.skip("ragas not installed (optional `qa` extra)")
    try:
        from ragas import evaluate  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"`from ragas import evaluate` failed under the pinned pairing: "
            f"{type(exc).__name__}: {exc}. The Ragas QA rail is dead in this "
            "environment — that is the #839 outage shape, which surfaces as "
            "'judge or embedding backend likely unreachable' skips rather "
            "than anything naming the real cause."
        )
