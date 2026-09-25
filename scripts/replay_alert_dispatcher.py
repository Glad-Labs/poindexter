#!/usr/bin/env python3
"""Replay alert_events history through the brain's alert dispatcher.

What a dispatcher or firefighter change does to paging only shows up against
real traffic, so measure it before it ships. Every row goes through the real
``poll_and_dispatch`` → dedup → firefighter engine → verify scan, in id order,
over the ``FirefighterWorld`` test double on a simulated clock. Nothing runs
(the action registry is faked) and nothing is sent (triage is off, notify
records, the LLM selector is scripted).

Built for glad-labs-stack#4025 (remediation episodes) and #4022 (the LLM
long-tail's look at a persistent alert), where it answered the questions a
unit test cannot: how many pages move, how many alerts reach the selector and
which, and what a verify window does to real producer cadences. Replaying
origin/main matched prod's recorded ``dispatch_result`` on 95-96% of rows. The
misses are brain restarts and settings that changed mid-history, which is
close enough to diff two versions of the code.

Export the history and the rules (read-only):

    docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -At -c \\
      "SELECT json_agg(t ORDER BY t.id) FROM (SELECT id, alertname, status, severity,
       category, labels, annotations, fingerprint, starts_at, received_at,
       dispatch_result FROM alert_events) t" > events.json
    docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -At -c \\
      "SELECT json_agg(t ORDER BY t.id) FROM (SELECT id, alertname, match_regex,
       action_name, params, enabled, max_attempts_per_window, window_minutes,
       verify_after_seconds, created_at FROM remediation_rules) t" > rules.json

Replay the working tree and a baseline, then compare:

    python scripts/replay_alert_dispatcher.py --events events.json --rules rules.json --out new.json
    git archive origin/main src/cofounder_agent/poindexter | tar -x -C /tmp/base
    python scripts/replay_alert_dispatcher.py --src /tmp/base/src/cofounder_agent \\
        --events events.json --rules rules.json --out old.json
    python scripts/replay_alert_dispatcher.py --compare old.json new.json

``--select`` scripts the selector: ``abstain`` counts what reaches it, ``act``
picks a restart every time (the worst case), and ``--settings`` overrides
app_settings (e.g. ``'{"ops_firefighter_llm_dry_run": "false"}'``).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _REPO_ROOT / "src" / "cofounder_agent"
_FAKES = _BACKEND / "tests" / "unit" / "brain" / "_remediation_fakes.py"

# The settings prod runs, so a replay is of prod unless --settings says
# otherwise. Triage stays off: a replay must not POST to the worker.
PROD_SETTINGS: dict[str, str] = {
    "alert_repeat_suppress_window_minutes": "120",
    "alert_repeat_summarize_threshold_minutes": "30",
    "ops_triage_enabled": "false",
}

_WORST_CASE_PICK = {
    "action_name": "restart_container", "params": {"container": "poindexter-replay-target"},
    "confidence": 0.9, "reason": "replay worst case", "model": "replay",
}


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _load_fakes() -> Any:
    """The test double, loaded by path so a --src baseline tree needs no tests/."""
    spec = importlib.util.spec_from_file_location("_replay_remediation_fakes", _FAKES)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _category(dispatch_result: str | None) -> str:
    text = str(dispatch_result or "")
    for prefix in ("sent: summary", "sent", "suppressed", "remediating", "error"):
        if text.startswith(prefix):
            return prefix
    return text[:20] or "none"


async def replay(
    events: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    *,
    settings: dict[str, str] | None = None,
    select: str = "abstain",
) -> dict[str, Any]:
    """Replay ``events`` and return a report of every decision the code made.

    Patches the dispatcher's and engine's clocks, the action registry and the
    selector transport for the duration of the call, and restores them after.
    """
    import poindexter.brain.alert_dispatcher as ad
    import poindexter.brain.remediation.engine as engine
    from poindexter.brain.remediation.registry import ActionResult

    fakes = _load_fakes()
    clock = [_parse_ts(events[0]["received_at"]) if events else datetime.now(UTC)]
    current_row: list[Any] = [None]
    report: dict[str, list[Any]] = {"offers": [], "selects": [], "actions": [], "pages": []}

    class _Meta(type):
        def __instancecheck__(cls, obj: Any) -> bool:
            return isinstance(obj, datetime)

    class _SimDatetime(metaclass=_Meta):
        fromisoformat = staticmethod(datetime.fromisoformat)

        @staticmethod
        def now(_tz: Any = None) -> datetime:
            return clock[0]

    class _World(fakes.FirefighterWorld):  # type: ignore[name-defined, misc]
        """FirefighterWorld on the simulated clock, indexed for 10k+ rows."""

        def __init__(self, **kw: Any) -> None:
            timed_rules = kw.pop("timed_rules")
            super().__init__(**kw)
            self.timed_rules = timed_rules
            self.by_id: dict[Any, dict[str, Any]] = {}
            self.by_key: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
            self.by_fingerprint: dict[Any, list[dict[str, Any]]] = defaultdict(list)
            self.undispatched: set[Any] = set()

        def now(self) -> datetime:
            return clock[0]

        def add_row(self, event: dict[str, Any]) -> dict[str, Any]:
            row = {
                "id": event["id"], "alertname": event["alertname"], "status": event["status"],
                "severity": event.get("severity"), "category": event.get("category"),
                "labels": json.dumps(event.get("labels") or {}),
                "annotations": json.dumps(event.get("annotations") or {}),
                "fingerprint": event.get("fingerprint"),
                "starts_at": _parse_ts(event["starts_at"]) if event.get("starts_at") else None,
                "received_at": _parse_ts(event["received_at"]),
                "dispatched_at": None, "dispatch_result": None,
            }
            self.by_id[row["id"]] = row
            self.by_key[(row["alertname"], row["fingerprint"])].append(row)
            self.by_fingerprint[row["fingerprint"]].append(row)
            self.undispatched.add(row["id"])
            return row

        async def fetch(self, sql: str, *args: Any) -> list:
            if "FROM alert_events" in sql and "dispatched_at IS NULL" in sql:
                limit = int(args[0]) if args else 50
                columns = ("id", "alertname", "status", "severity", "category",
                           "labels", "annotations", "fingerprint", "starts_at", "received_at")
                return [{k: self.by_id[i][k] for k in columns}
                        for i in sorted(self.undispatched)[:limit]]
            if "FROM remediation_rules" in sql:
                return [dict(r) for r in self.timed_rules
                        if r.get("enabled", True) and r["created_at"] <= clock[0]]
            return await super().fetch(sql, *args)

        async def fetchval(self, sql: str, *args: Any) -> Any:
            if "SELECT EXISTS" in sql and "dispatched_at >= $4" in sql:
                # alert_dispatcher._RUN_HAS_FIRED_SQL, from the key index
                alertname, stored_fp, severity, run_started_at = args
                return any(
                    r["status"].lower() == "firing" and (r["severity"] or "") == severity
                    and r["dispatched_at"] is not None and r["dispatched_at"] >= run_started_at
                    for r in self.by_key.get((alertname, stored_fp), [])
                )
            if "SELECT EXISTS" in sql and "alert_events r" in sql:
                stored_fp, alertname, since, row_id, severity = args
                mine = self.by_key.get((alertname, stored_fp), [])
                for resolved in mine:
                    if (resolved["status"].lower() != "resolved"
                            or resolved["received_at"] <= since or resolved["id"] >= row_id):
                        continue
                    if not any(
                        f["status"].lower() == "firing" and (f["severity"] or "") == severity
                        and resolved["id"] < f["id"] < row_id
                        for f in mine
                    ):
                        return True
                return False
            # The verify's evidence (engine._REFIRED_SINCE_SQL and
            # _LATEST_NOTIFICATION_SQL), answered from the fingerprint index.
            if "SELECT EXISTS" in sql and "received_at > $4" in sql:
                event_id, stored_fp, severity, since = args
                return any(
                    r["id"] > event_id and r["status"].lower() == "firing"
                    and (r["severity"] or "") == severity and r["received_at"] > since
                    for r in self.by_fingerprint.get(stored_fp, [])
                )
            if "FROM alert_events" in sql and "ORDER BY id DESC" in sql:
                event_id, stored_fp, severity = args
                said = [
                    r for r in self.by_fingerprint.get(stored_fp, [])
                    if r["id"] > event_id and (
                        r["status"].lower() == "resolved"
                        or (r["status"].lower() == "firing" and (r["severity"] or "") == severity))
                ]
                return max(said, key=lambda r: r["id"])["status"].lower() if said else None
            return await super().fetchval(sql, *args)

        async def execute(self, sql: str, *args: Any) -> str:
            if "UPDATE alert_events" in sql:
                row = self.by_id[args[0]]
                row["dispatched_at"] = clock[0]
                row["dispatch_result"] = "sent" if len(args) == 1 else args[1]
                self.undispatched.discard(args[0])
                return "UPDATE 1"
            return await super().execute(sql, *args)

    async def _execute(action_name: str, params: dict[str, Any], ctx: Any) -> Any:
        labels = (ctx.alert or {}).get("labels") or {}
        report["actions"].append({"row": current_row[0], "at": clock[0].isoformat(),
                                  "action": action_name, "params": params,
                                  "alertname": labels.get("alertname")})
        return ActionResult(status="ok", detail="replayed", latency_ms=1)

    async def _select(*, alert: dict[str, Any], catalog: list[dict[str, Any]]) -> Any:
        labels = alert.get("labels") or {}
        report["selects"].append({"row": current_row[0], "at": clock[0].isoformat(),
                                  "alertname": labels.get("alertname"),
                                  "summary": (alert.get("annotations") or {}).get("summary")})
        return dict(_WORST_CASE_PICK) if select == "act" else None

    async def _notify(message: str, *, critical: bool = False) -> dict[str, Any]:
        report["pages"].append({"row": current_row[0], "at": clock[0].isoformat(),
                                "critical": critical, "head": message.split("\n", 1)[0][:200]})
        return {"ok": True, "telegram_message_id": None, "discord_message_id": "replay"}

    def _recording(name: str) -> Any:
        real = getattr(ad, name)

        async def _hook(pool: Any, **kw: Any) -> Any:
            decision = await real(pool, **kw)
            labels = (kw.get("alert") or {}).get("labels") or {}
            report["offers"].append({
                "hook": name, "row": current_row[0], "alertname": labels.get("alertname"),
                "repeat_count": kw.get("repeat_count"), "age_minutes": kw.get("age_minutes"),
                "acted": bool(getattr(decision, "acted", False)),
                "action": getattr(decision, "action_name", None),
                "reason": getattr(decision, "reason", ""),
            })
            return decision

        return _hook

    timed_rules = [{**r, "created_at": _parse_ts(r.get("created_at") or "1970-01-01T00:00:00+00:00")}
                   for r in rules]
    world = _World(app_settings={**PROD_SETTINGS, **(settings or {})}, timed_rules=timed_rules)

    def _next_verify_due() -> datetime | None:
        verified = {v["details"].get("remediation_run_id")
                    for v in world.audit_rows("remediation_verify")}
        due = None
        for action in world.audit_rows("remediation_action"):
            if action["details"].get("remediation_run_id") in verified:
                continue
            after = int(action["details"].get("verify_after_seconds") or 120)
            at = action["timestamp"] + timedelta(seconds=after, milliseconds=500)
            due = at if due is None or at < due else due
        return due

    async def _verify_until(limit: datetime | None) -> None:
        # prod polls every 30 s, so a verify is judged on time even when no
        # alert rows arrive; replay it at its due time
        while (due := _next_verify_due()) is not None and (limit is None or due <= limit):
            clock[0], current_row[0] = due, None
            await ad.poll_and_dispatch(world, notify_fn=_notify)

    hooks = [h for h in ("evaluate_for_dispatch_hook", "evaluate_persistent_for_dispatch_hook")
             if hasattr(ad, h)]
    rows: list[dict[str, Any]] = []
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(engine, "datetime", _SimDatetime))
        stack.enter_context(mock.patch.object(engine, "execute", _execute))
        stack.enter_context(mock.patch.object(ad, "_default_now", lambda: clock[0]))
        stack.enter_context(mock.patch.object(ad, "_make_select_fn", lambda _pool: _select))
        for hook in hooks:
            stack.enter_context(mock.patch.object(ad, hook, _recording(hook)))
        for event in events:
            at = _parse_ts(event["received_at"])
            await _verify_until(at)
            clock[0] = at
            row = world.add_row(event)
            current_row[0] = event["id"]
            await ad.poll_and_dispatch(world, notify_fn=_notify)
            rows.append({"id": event["id"], "alertname": event["alertname"],
                         "status": event["status"], "received_at": str(event["received_at"]),
                         "prod": event.get("dispatch_result"), "replay": row["dispatch_result"]})
        await _verify_until(None)

    def _audit(event_type: str) -> list[dict[str, Any]]:
        return [{"at": a["timestamp"].isoformat(), **a["details"]} for a in world.audit_rows(event_type)]

    report["rows"] = rows
    for event_type in ("remediation_action", "remediation_verify", "remediation_dry_run", "finding"):
        report[event_type] = _audit(event_type)
    return report


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    rows = report["rows"]
    sources = {a.get("remediation_run_id"): a.get("source") for a in report["remediation_action"]}
    verifies = Counter(f"{sources.get(v.get('remediation_run_id'))}:{v.get('result')}"
                       for v in report["remediation_verify"])
    matched = sum(1 for r in rows if r["prod"] is not None and _category(r["prod"]) == _category(r["replay"]))
    with_prod = sum(1 for r in rows if r["prod"] is not None)
    return {
        "rows": len(rows),
        "pages": len(report["pages"]),
        "firefighter_pages": sum(1 for p in report["pages"] if p["head"].startswith("[FIREFIGHTER]")),
        "selector_calls": len(report["selects"]),
        "selector_calls_by_alert": Counter(s["alertname"] for s in report["selects"]).most_common(15),
        "actions_by_source": Counter(a.get("source") for a in report["remediation_action"]),
        "verifies": dict(verifies),
        "dry_run_picks": len(report["remediation_dry_run"]),
        "findings": len(report["finding"]),
        "matches_prod": f"{matched}/{with_prod}" if with_prod else "n/a",
    }


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old = {r["id"]: r for r in before["rows"]}
    moved = Counter(
        (_category(old[r["id"]]["replay"]), _category(r["replay"]))
        for r in after["rows"]
        if r["id"] in old and _category(old[r["id"]]["replay"]) != _category(r["replay"])
    )
    return {
        "pages": f"{len(before['pages'])} -> {len(after['pages'])}",
        "selector_calls": f"{len(before['selects'])} -> {len(after['selects'])}",
        "decisions_moved": {f"{a} -> {b}": n for (a, b), n in moved.most_common()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--events", help="alert_events export (JSON list)")
    parser.add_argument("--rules", help="remediation_rules export (JSON list)")
    parser.add_argument("--out", help="write the full report here")
    parser.add_argument("--src", help="replay this tree's poindexter package instead")
    parser.add_argument("--select", choices=("abstain", "act"), default="abstain")
    parser.add_argument("--settings", default="{}", help="app_settings overrides (JSON)")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                        help="diff two reports instead of replaying")
    args = parser.parse_args(argv)

    if args.compare:
        before, after = (json.loads(Path(p).read_text()) for p in args.compare)
        print(json.dumps({"before": summarize(before), "after": summarize(after),
                          "change": compare(before, after)}, indent=2, default=str))
        return 0
    if not args.events:
        parser.error("--events is required unless --compare is given")

    sys.path.insert(0, str(Path(args.src).resolve() if args.src else _BACKEND))
    events = json.loads(Path(args.events).read_text()) or []
    rules = json.loads(Path(args.rules).read_text()) or [] if args.rules else []
    report = asyncio.run(replay(events, rules, settings=json.loads(args.settings), select=args.select))
    if args.out:
        Path(args.out).write_text(json.dumps(report, default=str))
    print(json.dumps(summarize(report), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
