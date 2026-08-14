"""Egress-guard primitives (Glad-Labs/poindexter#1011).

These live in a normal module rather than in ``conftest.py`` on purpose.
pytest imports a conftest under its own rootdir-derived module name, so a test
that does ``from tests.unit.conftest import X`` gets a SECOND, unequal class
object — and ``pytest.raises(X)`` then cannot catch the exception the guard
actually raised. That is the same dual-module-identity trap that broke
``test_litellm_langfuse_callback`` via ``importlib.reload``
(glad-labs-stack#3155): if two paths reach one file, its classes stop being
each other.

conftest imports from here; tests import from here; both get one class.
"""

from __future__ import annotations

import os
from pathlib import Path

BASELINE_PATH = Path(__file__).parent / "network_egress_baseline.txt"

# Report mode prints offenders instead of failing them.
#
# WHY IT EXISTS: CI runs unit tests as ~13 separate per-directory pytest steps,
# and a failing step halts the job — so enforcing reveals only the FIRST step's
# offenders, and each fix uncovers the next layer. Regenerating the baseline by
# iterating through that is a guessing loop. In report mode every step runs to
# completion and prints its egress, so one CI run yields the whole list.
#
#     EGRESS_GUARD_MODE=report pytest tests/unit/...
#
# grep the output for EGRESS_REPORT_PREFIX to rebuild the baseline. Remember the
# result is per-environment — merge host and CI, per-file max (see the baseline
# file header).
EGRESS_REPORT_PREFIX = "[egress-guard] EGRESS"


def guard_is_enforcing() -> bool:
    return os.environ.get("EGRESS_GUARD_MODE", "enforce").strip().lower() != "report"


def report_sink() -> Path:
    """Where report mode appends its findings.

    A FILE, not stderr: pytest captures stdio and discards it for passing tests,
    so a printed report vanishes in exactly the mode where every test passes.
    A file also survives xdist — each worker is its own process, and the
    controller reads the merged file back at terminal summary.
    """
    return Path(os.environ.get("EGRESS_REPORT_FILE", "/tmp/egress_report.txt"))


def record_egress(nodeid: str, host: object, port: object) -> None:
    """Append one offender line. Best-effort: a broken sink must not fail a run
    that is, by definition, only gathering information."""
    try:
        with report_sink().open("a", encoding="utf-8") as fh:
            fh.write(f"{EGRESS_REPORT_PREFIX} {nodeid} -> {host}:{port}\n")
    except OSError:
        pass


class UnitTestNetworkEgress(BaseException):
    """A unit test opened a network connection.

    Derives from **BaseException, not Exception** — and that is load-bearing,
    not style. The code this guard watches is largely best-effort network code
    wrapped in broad ``except Exception`` handlers (this repo baselines 108 of
    them). An ``Exception`` subclass gets swallowed by the very code under test,
    the connection attempt is absorbed, and the test passes green — a guard that
    cannot fail on the paths it most needs to watch.

    Measured: with an ``AssertionError`` base, un-baselining
    ``test_operator_notifier.py`` (which really does open a TLS connection to
    api.telegram.org) still produced ``26 passed``. Changing the base to
    ``BaseException`` made it fail, correctly, on the connect.

    ``pytest.raises(UnitTestNetworkEgress)`` still works: naming a
    BaseException subclass explicitly catches it.
    """


def load_egress_baseline(path: Path | None = None) -> dict[str, int]:
    """Parse ``<count> <path>`` lines. Blank lines and ``#`` comments ignored.

    Returns ``{repo_relative_test_path: allowed_test_count}``. A malformed
    count is skipped rather than raising: a corrupt baseline must not take the
    whole suite down, and the guard failing OPEN here is the safe direction —
    it re-blocks whatever the bad line was trying to allow.
    """
    target = path or BASELINE_PATH
    allowed: dict[str, int] = {}
    if not target.exists():
        return allowed
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        count, _, rel = line.partition(" ")
        try:
            allowed[rel.strip()] = int(count)
        except ValueError:
            continue
    return allowed
