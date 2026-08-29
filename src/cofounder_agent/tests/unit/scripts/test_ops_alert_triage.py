from __future__ import annotations

import inspect
import logging
import sys
from pathlib import Path

import pytest


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import alert_triage as at  # noqa: E402

# Captured before the autouse stub below can shadow it, so the tests that
# are ABOUT this lookup exercise the real implementation.
_REAL_TRACKED_ELSEWHERE = at.tracked_elsewhere_alertnames


@pytest.fixture(autouse=True)
def _stub_pin_preflight(monkeypatch):
    """main() now preflights the model pin (stack#3163) with a real HTTP GET;
    stub it so these tests exercise the classification path, not the
    operator box's live Ollama. The preflight has its own tests in
    test_ops_common.py."""
    monkeypatch.setattr(at.c, "preflight_model_pins", lambda *m, **k: None)


@pytest.fixture(autouse=True)
def _no_tracked_elsewhere(monkeypatch):
    """Default the cross-repo "already concluded" lookup to empty.

    main() consults it on every run, so without this every main() test would
    have to stub a gh call it does not care about. Tests that are ABOUT that
    lookup override it explicitly.
    """
    monkeypatch.setattr(at, "tracked_elsewhere_alertnames", lambda: set())


class _FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess (returncode + stdout only)."""

    def __init__(self, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout


def test_prompt_includes_alert_and_probe():
    p = at.build_classification_prompt("BrainDaemonStale", 4, 38, "dispatched ok", "def probe(): ...")
    assert "BrainDaemonStale" in p
    assert "def probe()" in p


def test_prompt_includes_paged_vs_total_so_llm_sees_the_suppression_ratio():
    # A perfectly-deduped alert (203 rows, 4 actually delivered) must not read
    # the same as a genuinely-broken one to the classifier — both counts need
    # to be visible in the prompt text, not collapsed into one number.
    p = at.build_classification_prompt("docker_port_forward_restart_skipped", 4, 203, "sent", "")
    assert "4" in p
    assert "203" in p


def test_noisy_alerts_query_gates_on_paged_not_raw_row_count():
    # Regression guard for the false-positive class #2395 documented: a fully
    # suppressed/deduped alert must not trip the noise threshold just because
    # alert_dispatcher recorded a row for every suppressed repeat.
    sql = inspect.getsource(at._noisy_alerts)
    assert "FILTER (WHERE dispatch_result NOT LIKE 'suppressed%')" in sql
    assert "HAVING COUNT(*) FILTER" in sql


def test_parse_classification_valid_json():
    raw = '{"classification": "probe_bug", "reason": "dedup broken", "suspect_file": "brain/x_probe.py"}'
    out = at.parse_classification(raw)
    assert out["classification"] == "probe_bug"
    assert out["suspect_file"] == "brain/x_probe.py"


def test_parse_classification_normalizes_and_defaults():
    out = at.parse_classification('{"classification": "REAL_FAILURE"}')
    assert out["classification"] == "real_failure"
    assert out["reason"] == ""


def test_parse_classification_bad_json_raises():
    with pytest.raises(ValueError):
        at.parse_classification("not json")


@pytest.mark.parametrize(
    "raw",
    [
        '{"classification": "\\"probe_bug\\""}',    # value wrapped in double quotes
        '{"classification": "\'probe_bug\'"}',      # ... or single quotes
        '{"classification": " \\"PROBE_BUG\\" "}',  # ... plus padding and case
    ],
)
def test_parse_classification_strips_quotes_copied_into_the_value(raw):
    """A quoted value must still classify.

    The system prompt spells the enum as ``"probe_bug"|"real_failure"``, and
    models periodically copy those quotes into the value itself. The result is
    well-formed JSON that parses cleanly and then matches NEITHER branch in
    ``main()`` — so a probe-bug issue is silently never filed, with no error
    anywhere. Seen from granite4.2:3b during the 2026-08-27 triage-pin
    bake-off; treat it as general model behaviour, not one model's quirk.
    """
    assert at.parse_classification(raw)["classification"] == "probe_bug"


def _fake_run(alerts, *, firing=(), days=7):
    """Serve main()'s two asyncio_run calls in order: alerts, then quiet_context."""
    seq = iter([alerts, (days, set(firing))])

    def run(coro):
        coro.close()  # never awaited — avoids a "coroutine never awaited" warning
        return next(seq)

    return run


def _issue(number, alertname):
    return {"number": number, "title": f"probe bug: {alertname} paged 6x/24h (6x total, 0 dedup-suppressed)"}


# --------------------------------------------------------------------------
# Title parsing — the dedupe key in both directions
# --------------------------------------------------------------------------

@pytest.mark.parametrize("alertname", [
    "PoindexterSystemdUnitFailed",
    "gpu_scheduler:gpu_lock_timeout",
    "modules.content.multi_model_qa:critic_model_collision",
])
def test_alertname_round_trips_through_the_issue_title(alertname):
    """Dotted/colonned alertnames must survive the title grammar intact."""
    assert at.alertname_from_title(_issue(1, alertname)["title"]) == alertname


def test_alertname_from_title_ignores_unrelated_issues():
    """Hand-written issues must never be mistaken for this session's output."""
    assert at.alertname_from_title("Refactor the image service") == ""
    assert at.alertname_from_title("⚠️ poindexter mirror sync FAILED") == ""


# --------------------------------------------------------------------------
# Reading open issues — two gh traps that would silently un-fix this
# --------------------------------------------------------------------------

def test_open_probe_issues_returns_only_this_sessions_issues(monkeypatch):
    payload = (
        '[{"number": 1, "title": "probe bug: AlertOne paged 6x/24h (6x total, 0 dedup-suppressed)"},'
        ' {"number": 2, "title": "Refactor something unrelated"}]'
    )
    monkeypatch.setattr(at.c, "gh", lambda *_a: _FakeProc(0, payload))
    found = at.open_probe_issues()
    assert [i["number"] for i in found] == [1]


def test_open_probe_issues_never_uses_the_search_index(monkeypatch):
    """`--search` lags writes by minutes, so it can miss an issue that exists —
    which files a duplicate AND leaves a resolved one open."""
    calls = []
    monkeypatch.setattr(at.c, "gh", lambda *a: (calls.append(a), _FakeProc(0, "[]"))[1])
    at.open_probe_issues()
    assert "--search" not in calls[0]


def test_open_probe_issues_passes_an_explicit_limit(monkeypatch):
    """`gh issue list` silently truncates at 30."""
    calls = []
    monkeypatch.setattr(at.c, "gh", lambda *a: (calls.append(a), _FakeProc(0, "[]"))[1])
    at.open_probe_issues()
    assert "--limit" in calls[0]
    assert int(calls[0][calls[0].index("--limit") + 1]) >= 100


@pytest.mark.parametrize("proc", [_FakeProc(1, ""), _FakeProc(0, "not json")])
def test_open_probe_issues_returns_none_when_github_cannot_be_read(monkeypatch, proc):
    """None, not [] — callers must tell 'nothing open' from 'could not tell'."""
    monkeypatch.setattr(at.c, "gh", lambda *_a: proc)
    assert at.open_probe_issues() is None


# --------------------------------------------------------------------------
# Closing on quiet
# --------------------------------------------------------------------------

def test_close_resolved_closes_a_quiet_alert_and_spares_a_firing_one(monkeypatch):
    calls = []
    monkeypatch.setattr(at.c, "gh", lambda *a: (calls.append(a), _FakeProc(0, ""))[1])
    log = logging.getLogger("test-alert-triage")

    closed = at.close_resolved(
        [_issue(10, "QuietOne"), _issue(11, "StillFiring")],
        firing={"StillFiring"},
        days=7,
        log=log,
    )

    assert closed == {"QuietOne"}
    closes = [a for a in calls if a[:2] == ("issue", "close")]
    assert [a[2] for a in closes] == ["10"]
    assert any(a[:2] == ("issue", "comment") for a in calls), "closing should say why"


def test_close_resolved_closes_nothing_when_everything_is_still_firing(monkeypatch):
    monkeypatch.setattr(at.c, "gh", lambda *_a: pytest.fail("must not touch a firing alert's issue"))
    assert at.close_resolved(
        [_issue(10, "Loud")], firing={"Loud"}, days=7,
        log=logging.getLogger("test-alert-triage"),
    ) == set()


# --------------------------------------------------------------------------
# main()
# --------------------------------------------------------------------------

def test_main_skips_unparseable_reply_without_crashing_the_sweep(monkeypatch):
    """A garbled model reply must skip that alert, not abort the run.

    parse_classification raises ValueError on non-JSON. Uncaught, that exits
    main() with a traceback and NO notify_fail — the "exception nobody catches
    pages nobody" shape.
    """
    replies = iter([
        "I think this is probably a probe bug!",  # not JSON at all
        '{"classification": "probe_bug", "reason": "r", "suspect_file": "f.py"}',
    ])
    filed = []

    monkeypatch.setattr(at.c, "get_logger", lambda _n: logging.getLogger("test-alert-triage"))
    monkeypatch.setattr(at.c, "asyncio_run", _fake_run([
        {"alertname": "GarbledFirst", "dispatch_result": "sent", "n_paged": 9, "n_total": 9},
        {"alertname": "GoodSecond", "dispatch_result": "sent", "n_paged": 7, "n_total": 7},
    ], firing={"GarbledFirst", "GoodSecond"}))
    monkeypatch.setattr(at, "open_probe_issues", lambda: [])
    monkeypatch.setattr(at.c, "ollama_chat", lambda *_a, **_k: next(replies))
    monkeypatch.setattr(at.c, "gh", lambda *args: (filed.append(args), _FakeProc(0, ""))[1])

    assert at.main() == 0
    assert len(filed) == 1
    assert "GoodSecond" in " ".join(filed[0])


def test_main_skips_already_tracked_alert_without_classifying_or_filing(monkeypatch):
    def boom_ollama(*a, **k):
        raise AssertionError("an already-tracked alert must not be re-classified")

    monkeypatch.setattr(at.c, "get_logger", lambda _n: logging.getLogger("test-alert-triage"))
    monkeypatch.setattr(at.c, "asyncio_run", _fake_run(
        [{"alertname": "FinanceMercuryPollStale", "dispatch_result": "sent", "n_paged": 19, "n_total": 19}],
        firing={"FinanceMercuryPollStale"},
    ))
    monkeypatch.setattr(at, "open_probe_issues", lambda: [_issue(7, "FinanceMercuryPollStale")])
    monkeypatch.setattr(at.c, "ollama_chat", boom_ollama)
    monkeypatch.setattr(at.c, "gh", lambda *a: pytest.fail("must not file a duplicate"))

    assert at.main() == 0


def test_main_classifies_and_files_when_nothing_open_yet(monkeypatch):
    filed = []
    monkeypatch.setattr(at.c, "get_logger", lambda _n: logging.getLogger("test-alert-triage"))
    monkeypatch.setattr(at.c, "asyncio_run", _fake_run(
        [{"alertname": "NewNoisyAlert", "dispatch_result": "sent", "n_paged": 8, "n_total": 8}],
        firing={"NewNoisyAlert"},
    ))
    monkeypatch.setattr(at, "open_probe_issues", lambda: [])
    monkeypatch.setattr(
        at.c, "ollama_chat",
        lambda *_a, **_k: '{"classification": "probe_bug", "reason": "r", "suspect_file": "f.py"}',
    )
    monkeypatch.setattr(at.c, "gh", lambda *args: (filed.append(args), _FakeProc(0, ""))[1])

    assert at.main() == 0
    (call,) = filed
    assert "NewNoisyAlert" in " ".join(call)
    assert "paged 8x/24h" in " ".join(call)


def test_main_title_distinguishes_paged_from_total_when_dedup_partially_worked(monkeypatch):
    # An alert that mostly dedups but still pages a handful of times must file
    # with the REAL (paged) count in the title, not the raw row count.
    filed = []
    monkeypatch.setattr(at.c, "get_logger", lambda _n: logging.getLogger("test-alert-triage"))
    monkeypatch.setattr(at.c, "asyncio_run", _fake_run(
        [{"alertname": "PoindexterHostMemoryThrashing", "dispatch_result": "sent",
          "n_paged": 11, "n_total": 87}],
        firing={"PoindexterHostMemoryThrashing"},
    ))
    monkeypatch.setattr(at, "open_probe_issues", lambda: [])
    monkeypatch.setattr(
        at.c, "ollama_chat",
        lambda *_a, **_k: '{"classification": "probe_bug", "reason": "r", "suspect_file": "f.py"}',
    )
    monkeypatch.setattr(at.c, "gh", lambda *args: (filed.append(args), _FakeProc(0, ""))[1])

    assert at.main() == 0
    assert "paged 11x/24h" in " ".join(filed[0])


def test_main_closes_a_quiet_issue_and_can_refile_it_in_the_same_run(monkeypatch):
    """The suppression fix, end to end.

    A stale open issue makes the filing guard skip its alert. So an alert that
    was fixed long ago and has genuinely come back must get its old issue
    closed AND a fresh one filed in the SAME run — otherwise the return is
    invisible for another whole day, or forever if it keeps re-firing.
    """
    calls = []
    monkeypatch.setattr(at.c, "get_logger", lambda _n: logging.getLogger("test-alert-triage"))
    # The alert is noisy again now, but had NOT fired inside the quiet window.
    monkeypatch.setattr(at.c, "asyncio_run", _fake_run(
        [{"alertname": "ReturnedFromTheDead", "dispatch_result": "sent", "n_paged": 9, "n_total": 9}],
        firing=set(),
    ))
    monkeypatch.setattr(at, "open_probe_issues", lambda: [_issue(42, "ReturnedFromTheDead")])
    monkeypatch.setattr(
        at.c, "ollama_chat",
        lambda *_a, **_k: '{"classification": "probe_bug", "reason": "r", "suspect_file": "f.py"}',
    )
    monkeypatch.setattr(at.c, "gh", lambda *args: (calls.append(args), _FakeProc(0, ""))[1])

    assert at.main() == 0
    verbs = [a[:2] for a in calls]
    assert ("issue", "close") in verbs, "the stale issue must be closed"
    assert ("issue", "create") in verbs, "and a fresh one filed in the same run"
    assert verbs.index(("issue", "close")) < verbs.index(("issue", "create"))


def test_main_files_and_closes_nothing_when_github_is_unreadable(monkeypatch):
    """Fail-safe: without the issue list we cannot tell tracked from untracked,
    and filing blind resumes the duplicate-issue spam this guard exists to stop."""
    notified = []
    monkeypatch.setattr(at.c, "get_logger", lambda _n: logging.getLogger("test-alert-triage"))
    monkeypatch.setattr(at.c, "asyncio_run", _fake_run(
        [{"alertname": "Whatever", "dispatch_result": "sent", "n_paged": 9, "n_total": 9}],
    ))
    monkeypatch.setattr(at, "open_probe_issues", lambda: None)
    monkeypatch.setattr(at.c, "notify_fail", lambda *a: notified.append(a))
    monkeypatch.setattr(at.c, "ollama_chat", lambda *a, **k: pytest.fail("must not classify"))
    monkeypatch.setattr(at.c, "gh", lambda *a: pytest.fail("must not touch GitHub"))

    assert at.main() == 1
    assert notified, "an unreadable GitHub must page, not pass silently"


# --------------------------------------------------------------------------
# "Tracked elsewhere" — closing is not enough for an alert that keeps firing
# --------------------------------------------------------------------------

def test_tracked_elsewhere_reads_closed_labelled_issues(monkeypatch):
    calls = []
    payload = '[{"title": "probe bug: AlertOne paged 9x/24h (9x total, 0 dedup-suppressed)"}]'
    monkeypatch.setattr(at.c, "gh", lambda *a: (calls.append(a), _FakeProc(0, payload))[1])
    assert _REAL_TRACKED_ELSEWHERE() == {"AlertOne"}
    q = calls[0]
    assert "closed" in q, "must look at CLOSED issues — the open ones are a different question"
    assert at.TRACKED_ELSEWHERE_LABEL in q


@pytest.mark.parametrize("proc", [_FakeProc(1, ""), _FakeProc(0, "not json")])
def test_tracked_elsewhere_fails_toward_filing(monkeypatch, proc):
    """A duplicate issue is a nuisance; silently never filing is the failure
    this session exists to prevent. So an unreadable lookup files anyway."""
    monkeypatch.setattr(at.c, "gh", lambda *_a: proc)
    assert _REAL_TRACKED_ELSEWHERE() == set()


def test_main_does_not_refile_a_still_firing_alert_tracked_elsewhere(monkeypatch):
    """The loop this closes.

    `critic_model_collision` was closed as a duplicate of poindexter#1013 and
    re-filed within the hour (#3476) because it is real and still firing 40x/48h
    — so "has it gone quiet?" is never true for it. Labelled closed issues stop
    that without silencing the alert itself.
    """
    monkeypatch.setattr(at.c, "get_logger", lambda _n: logging.getLogger("test-alert-triage"))
    monkeypatch.setattr(at.c, "asyncio_run", _fake_run(
        [{"alertname": "modules.content.multi_model_qa:critic_model_collision",
          "dispatch_result": "sent", "n_paged": 10, "n_total": 10}],
        firing={"modules.content.multi_model_qa:critic_model_collision"},
    ))
    monkeypatch.setattr(at, "open_probe_issues", lambda: [])
    monkeypatch.setattr(
        at, "tracked_elsewhere_alertnames",
        lambda: {"modules.content.multi_model_qa:critic_model_collision"},
    )
    monkeypatch.setattr(at.c, "ollama_chat", lambda *a, **k: pytest.fail("must not re-classify"))
    monkeypatch.setattr(at.c, "gh", lambda *a: pytest.fail("must not re-file a concluded alert"))

    assert at.main() == 0


def test_a_quiet_close_still_refiles_when_the_alert_returns(monkeypatch):
    """The two close reasons must stay distinct: only the LABELLED ones are
    permanently concluded. A quiet-closed alert that comes back must re-file."""
    calls = []
    monkeypatch.setattr(at.c, "get_logger", lambda _n: logging.getLogger("test-alert-triage"))
    monkeypatch.setattr(at.c, "asyncio_run", _fake_run(
        [{"alertname": "CameBack", "dispatch_result": "sent", "n_paged": 9, "n_total": 9}],
        firing={"CameBack"},
    ))
    monkeypatch.setattr(at, "open_probe_issues", lambda: [])
    monkeypatch.setattr(at, "tracked_elsewhere_alertnames", lambda: set())
    monkeypatch.setattr(
        at.c, "ollama_chat",
        lambda *_a, **_k: '{"classification": "probe_bug", "reason": "r", "suspect_file": "f.py"}',
    )
    monkeypatch.setattr(at.c, "gh", lambda *args: (calls.append(args), _FakeProc(0, ""))[1])

    assert at.main() == 0
    assert ("issue", "create") in [a[:2] for a in calls]
