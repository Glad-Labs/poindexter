"""Classify noisy alerts (local Ollama) → file probe-bug issues."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/poindexter"
_SYSTEM = (
    "You are an SRE triaging a repeatedly-firing alert. Decide if the alert is a "
    "PROBE BUG (false positive / broken dedup repeating one fingerprint) or a REAL "
    "FAILURE (service down, resource exhausted). Respond with strict JSON: "
    '{"classification": "probe_bug"|"real_failure", "reason": "...", "suspect_file": "..."}'
)

NOISE_THRESHOLD = 5
WINDOW = "24 hours"


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
    cls = str(data.get("classification", "")).strip().lower()
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


def _has_open_probe_issue(alertname: str) -> bool:
    """True if an OPEN probe-bug issue already tracks this alert.

    Fail-safe: any error checking (gh failure, unparseable output) returns
    True so a transient hiccup skips filing rather than resuming the old
    every-run duplicate-issue spam — the cron retries daily regardless.
    """
    proc = c.gh(
        "issue", "list", "--repo", REPO, "--state", "open",
        "--search", f'"probe bug: {alertname}" in:title',
        "--json", "number", "--limit", "1",
    )
    if proc.returncode != 0:
        return True
    try:
        return bool(json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        return True


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
    try:
        alerts = c.asyncio_run(_noisy_alerts())
    except Exception as exc:  # noqa: BLE001
        c.notify_fail("alert-triage DB error", str(exc)[:400], "alert_triage")
        return 1
    filed = 0
    skipped = 0
    for a in alerts:
        alertname = a["alertname"]
        n_paged = a["n_paged"]
        n_total = a["n_total"]
        if _has_open_probe_issue(alertname):
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
        verdict = parse_classification(raw)
        if verdict["classification"] == "probe_bug":
            suppressed = n_total - n_paged
            c.gh("issue", "create", "--repo", REPO, "--label", "bug",
                 "--title", f"probe bug: {alertname} paged {n_paged}x/24h "
                            f"({n_total}x total, {suppressed} dedup-suppressed)",
                 "--body", f"{verdict['reason']}\n\nSuspect: `{verdict['suspect_file']}`\n\n"
                           f"_Filed by the alert-triage ops session (local-model triage)._")
            filed += 1
    log.info("alerts=%d filed=%d skipped_already_tracked=%d", len(alerts), filed, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
