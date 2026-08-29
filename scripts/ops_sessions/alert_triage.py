"""Classify noisy alerts (local Ollama) → file probe-bug issues."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/glad-labs-stack"
_SYSTEM = (
    "You are an SRE triaging a repeatedly-firing alert. Decide if the alert is a "
    "PROBE BUG (false positive / broken dedup repeating one fingerprint) or a REAL "
    "FAILURE (service down, resource exhausted). Respond with strict JSON: "
    '{"classification": "probe_bug"|"real_failure", "reason": "...", "suspect_file": "..."}'
)

NOISE_THRESHOLD = 5
WINDOW = "24 hours"

# Title grammar for the issues this session files, and the key that decides
# when a filed issue is stale enough to close itself.
_TITLE_PREFIX = "probe bug: "
_TITLE_PAGED = " paged "
QUIET_DAYS_SETTING = "alert_triage_probe_issue_quiet_days"
QUIET_DAYS_DEFAULT = 7


def build_classification_prompt(
    alertname: str, n_paged: int, n_total: int, dispatch_result: str, probe_src: str
) -> str:
    return (
        f"Alert: {alertname}\n"
        f"Delivered to operator (NOT suppressed by dedup) in the last 24h: {n_paged}\n"
        f"Total alert_events rows in the last 24h (incl. deduped/suppressed repeats): {n_total}\n"
        f"Most recent dispatch_result: {dispatch_result}\n\n"
        f"Probe source (if any):\n{probe_src or '(no probe file found)'}\n"
    )


def parse_classification(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"non-JSON classification: {raw[:120]}") from exc
    # Strip quotes around the VALUE, not just whitespace. The system prompt
    # shows the enum as `"probe_bug"|"real_failure"`, and models periodically
    # copy the quotes into the value itself -> `{"classification": "\"probe_bug\""}`.
    # That is well-formed JSON, so it parses fine and then matches neither
    # branch in main(), silently dropping a probe-bug issue that should have
    # been filed. Observed from granite4.2:3b during the 2026-08-27 pin
    # bake-off; it is a general model behaviour, not a granite quirk.
    cls = str(data.get("classification", "")).strip().strip("\"'").strip().lower()
    return {
        "classification": cls,
        "reason": str(data.get("reason", "") or ""),
        "suspect_file": str(data.get("suspect_file", "") or ""),
    }


def _probe_source(root: Path, alertname: str) -> str:
    slug = alertname.lower().replace("-", "_")
    for cand in root.glob("brain/*probe*.py"):
        if slug[:8] in cand.name.lower():
            return cand.read_text(encoding="utf-8")[:4000]
    return ""


def alertname_from_title(title: str) -> str:
    """Recover the alertname from a filed issue title; "" if it is not one.

    Titles are built in ``main`` as
    ``probe bug: <alertname> paged Nx/24h (Nx total, N dedup-suppressed)``.
    """
    if not title.startswith(_TITLE_PREFIX):
        return ""
    rest = title[len(_TITLE_PREFIX):]
    cut = rest.find(_TITLE_PAGED)
    return (rest[:cut] if cut > 0 else rest).strip()


def open_probe_issues() -> list[dict] | None:
    """Every open probe-bug issue — or None when GitHub could not be read.

    Deliberately NOT ``gh issue list --search``. The search index lags writes
    by minutes, so a search-based lookup can miss an issue that demonstrably
    exists; plain ``issue list`` reads the issues API directly and is
    read-your-writes consistent. ``--limit`` is explicit because the default
    silently truncates at 30, and a miss here is expensive in both directions:
    it files a duplicate AND leaves a resolved issue open.

    Returns None rather than [] on failure so callers can tell "nothing is
    open" from "could not tell", and skip the run instead of guessing.
    """
    proc = c.gh(
        "issue", "list", "--repo", REPO, "--state", "open",
        "--limit", "500", "--json", "number,title",
    )
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return [r for r in rows if alertname_from_title(r.get("title", ""))]


async def quiet_context() -> tuple[int, set[str]]:
    """(quiet-days threshold, alertnames that have fired inside it)."""
    rows = await c.fetch_all(
        "SELECT value FROM app_settings WHERE key = $1", QUIET_DAYS_SETTING
    )
    days = QUIET_DAYS_DEFAULT
    if rows:
        try:
            days = max(1, int(str(rows[0]["value"]).strip()))
        except (TypeError, ValueError):
            days = QUIET_DAYS_DEFAULT
    fired = await c.fetch_all(
        "SELECT DISTINCT alertname FROM alert_events "
        "WHERE received_at > NOW() - make_interval(days => $1)",
        days,
    )
    return days, {r["alertname"] for r in fired}


def close_resolved(issues: list[dict], firing: set[str], days: int, log) -> set[str]:
    """Close probe-bug issues whose alert has gone quiet. Returns their names.

    Nothing else ever closed these, and the cost is not only backlog clutter.
    A stale OPEN issue actively **suppresses this session**: the filing guard
    skips any alert that already has one, so an alert that was fixed months
    ago and then genuinely returns is never re-filed. Closing on quiet re-arms
    the filer for exactly the case it exists to catch.

    Audited 2026-08-29: of 15 open probe-bug issues, 4 had been silent for
    2-13 days with the underlying fault verifiably fixed.
    """
    closed: set[str] = set()
    for issue in issues:
        name = alertname_from_title(issue["title"])
        if not name or name in firing:
            continue
        num = str(issue["number"])
        c.gh(
            "issue", "comment", num, "--repo", REPO, "--body",
            f"Closing automatically — `{name}` has not fired in {days} days.\n\n"
            f"The alert this issue was filed from has gone quiet, so whatever "
            f"caused it is no longer happening. If it returns, the alert-triage "
            f"session files a fresh issue (a stale open one would suppress that).\n\n"
            f"_Closed by the alert-triage ops session._",
        )
        proc = c.gh("issue", "close", num, "--repo", REPO)
        if proc.returncode == 0:
            log.info("closed #%s — %s quiet for %dd", num, name, days)
            closed.add(name)
    return closed


async def _noisy_alerts() -> list[dict]:
    # NOISE_THRESHOLD / WINDOW are module constants, not user input — safe to inline.
    #
    # Gate on n_paged (dispatch_result NOT a suppression), not raw row count.
    # alert_dispatcher's #420 dedup already collapses repeats into
    # `dispatch_result='suppressed: ...'` rows that never reach the operator —
    # counting those as "firing" flags perfectly-deduped alerts as broken (a
    # 203-row/4-paged alert tripped the old `COUNT(*) > 5` every day). #2395
    # documented this exact false-positive class for an earlier issue batch;
    # this closes the root cause instead of re-triaging each batch by hand.
    rows = await c.fetch_all(
        f"""
        SELECT alertname,
               COUNT(*) AS n_total,
               COUNT(*) FILTER (WHERE dispatch_result NOT LIKE 'suppressed%') AS n_paged,
               MAX(dispatch_result) AS dispatch_result
        FROM alert_events
        WHERE received_at > NOW() - INTERVAL '{WINDOW}'
        GROUP BY alertname
        HAVING COUNT(*) FILTER (WHERE dispatch_result NOT LIKE 'suppressed%') > {NOISE_THRESHOLD}
        ORDER BY n_paged DESC LIMIT 20
        """  # noqa: S608 — interpolated values are module constants  # nosec B608 - WINDOW/NOISE_THRESHOLD are hardcoded module constants (line 18-19), never external input; ruff's noqa:S608 isn't recognized by standalone bandit
    )
    return [dict(r) for r in rows]


def main() -> int:
    log = c.get_logger("alert-triage")
    root = next(p for p in Path(__file__).resolve().parents if (p / "CLAUDE.md").exists())
    # Fail in seconds with the pull remedy, not minutes into the run (stack#3163).
    try:
        c.preflight_model_pins(c.MODEL_TRIAGE)
    except c.OllamaUnavailable as exc:
        log.error("model-pin preflight failed: %s", exc)
        c.notify_fail("alert-triage: Ollama preflight failed", str(exc)[:400], "alert_triage")
        return 1
    try:
        alerts = c.asyncio_run(_noisy_alerts())
        quiet_days, firing = c.asyncio_run(quiet_context())
    except Exception as exc:  # noqa: BLE001
        c.notify_fail("alert-triage DB error", str(exc)[:400], "alert_triage")
        return 1

    # One read serves both halves: which alerts are already tracked (so we do
    # not file a duplicate) and which tracked alerts have gone quiet (so we
    # close them). None means GitHub could not be read at all — file nothing
    # and close nothing rather than acting on a guess.
    open_issues = open_probe_issues()
    if open_issues is None:
        log.error("could not list open issues — skipping filing and closing this run")
        c.notify_fail(
            "alert-triage: GitHub unreadable",
            "gh issue list failed; skipped filing and closing to avoid duplicates.",
            "alert_triage",
        )
        return 1

    # Close BEFORE filing, and in the same run: a quiet alert's stale issue
    # would otherwise keep suppressing its own re-file for another whole day.
    resolved = close_resolved(open_issues, firing, quiet_days, log)
    tracked = {alertname_from_title(i["title"]) for i in open_issues} - resolved

    filed = 0
    skipped = 0
    unparseable = 0
    for a in alerts:
        alertname = a["alertname"]
        n_paged = a["n_paged"]
        n_total = a["n_total"]
        if alertname in tracked:
            skipped += 1
            continue
        probe = _probe_source(root, alertname)
        prompt = build_classification_prompt(
            alertname, n_paged, n_total, a.get("dispatch_result") or "", probe
        )
        try:
            raw = c.ollama_chat(prompt, model=c.MODEL_TRIAGE, system=_SYSTEM, as_json=True)
        except c.OllamaUnavailable as exc:
            c.notify_fail("alert-triage: Ollama down", str(exc)[:300], "alert_triage")
            return 1
        try:
            verdict = parse_classification(raw)
        except ValueError as exc:
            # One garbled reply must not abort the sweep. parse_classification
            # raises on non-JSON, and an uncaught ValueError here would exit
            # main() with a traceback and NO notify_fail — the exact
            # "exception nobody catches pages nobody" shape _common's
            # OllamaUnavailable docstring exists to prevent. Skip the alert;
            # the cron retries daily, and `unparseable=N` in the summary line
            # is what tells the operator the model is emitting junk.
            log.warning("unparseable classification for %s: %s", alertname, exc)
            unparseable += 1
            continue
        if verdict["classification"] == "probe_bug":
            suppressed = n_total - n_paged
            c.gh("issue", "create", "--repo", REPO, "--label", "bug",
                 "--title", f"probe bug: {alertname} paged {n_paged}x/24h "
                            f"({n_total}x total, {suppressed} dedup-suppressed)",
                 "--body", f"{verdict['reason']}\n\nSuspect: `{verdict['suspect_file']}`\n\n"
                           f"_Filed by the alert-triage ops session (local-model triage)._")
            filed += 1
    log.info(
        "alerts=%d filed=%d closed_quiet=%d skipped_already_tracked=%d unparseable=%d",
        len(alerts), filed, len(resolved), skipped, unparseable,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
