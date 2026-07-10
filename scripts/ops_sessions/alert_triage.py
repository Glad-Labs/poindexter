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


def build_classification_prompt(alertname: str, dispatch_result: str, probe_src: str) -> str:
    return (
        f"Alert: {alertname}\n"
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


async def _noisy_alerts() -> list[dict]:
    # NOISE_THRESHOLD / WINDOW are module constants, not user input — safe to inline.
    rows = await c.fetch_all(
        f"""
        SELECT alertname, MAX(dispatch_result) AS dispatch_result, COUNT(*) AS n
        FROM alert_events
        WHERE received_at > NOW() - INTERVAL '{WINDOW}'
        GROUP BY alertname HAVING COUNT(*) > {NOISE_THRESHOLD}
        ORDER BY n DESC LIMIT 20
        """  # noqa: S608 — interpolated values are module constants
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
    for a in alerts:
        probe = _probe_source(root, a["alertname"])
        prompt = build_classification_prompt(a["alertname"], a.get("dispatch_result") or "", probe)
        try:
            raw = c.ollama_chat(prompt, model=c.MODEL_TRIAGE, system=_SYSTEM, as_json=True)
        except c.OllamaUnavailable as exc:
            c.notify_fail("alert-triage: Ollama down", str(exc)[:300], "alert_triage")
            return 1
        verdict = parse_classification(raw)
        if verdict["classification"] == "probe_bug":
            c.gh("issue", "create", "--repo", REPO, "--label", "bug",
                 "--title", f"probe bug: {a['alertname']} firing {a['n']}x/24h",
                 "--body", f"{verdict['reason']}\n\nSuspect: `{verdict['suspect_file']}`\n\n"
                           f"_Filed by the alert-triage ops session (local-model triage)._")
            filed += 1
    log.info("alerts=%d filed=%d", len(alerts), filed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
