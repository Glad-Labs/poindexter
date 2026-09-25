"""Shared asyncpg-pool test double for the firefighter unit tests.

Records executes for assertion, and lets each test register canned results for
fetch / fetchval / fetchrow keyed by a substring of the SQL so a single pool can
serve several distinct queries in call order.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


class FakePool:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._fetch: Callable[[str, tuple], list] | None = None
        self._fetchval: Callable[[str, tuple], Any] | None = None
        self._fetchrow: Callable[[str, tuple], Any] | None = None

    def set_fetch(self, fn: Callable[[str, tuple], list]) -> None:
        self._fetch = fn

    def set_fetchval(self, fn: Callable[[str, tuple], Any]) -> None:
        self._fetchval = fn

    def set_fetchrow(self, fn: Callable[[str, tuple], Any]) -> None:
        self._fetchrow = fn

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list:
        return list(self._fetch(sql, args)) if self._fetch else []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return self._fetchval(sql, args) if self._fetchval else None

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        return self._fetchrow(sql, args) if self._fetchrow else None


class FirefighterWorld:
    """In-memory stand-in for everything the dispatcher and the firefighter
    share: ``alert_events``, ``alert_dedup_state``, the remediation rows of
    ``audit_log``, ``remediation_rules`` and ``app_settings``.

    It answers the real SQL both modules send (routed by distinctive
    substrings), so a test drives the real ``poll_and_dispatch`` -> dedup ->
    ``evaluate_for_dispatch`` -> ``run_verify_scan`` chain across simulated time.

    Time: stored timestamps are real wall-clock values. ``advance()`` moves the
    world forward by shifting every stored timestamp BACKWARD, so the code under
    test keeps reading the real clock (the verify scan calls
    ``datetime.now(UTC)``; the fake evaluates SQL ``now()`` the same way).
    """

    def __init__(
        self,
        *,
        app_settings: dict[str, str] | None = None,
        rules: list[dict[str, Any]] | None = None,
    ) -> None:
        self.app_settings: dict[str, str] = dict(app_settings or {})
        self.rules: list[dict[str, Any]] = list(rules or [])
        self.alert_events: list[dict[str, Any]] = []
        self.dedup_state: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        self._next_alert_id = 1
        self._next_audit_id = 1

    # ------------------------------------------------------------------ time
    @staticmethod
    def now() -> Any:
        from datetime import UTC, datetime
        return datetime.now(UTC)

    def advance(self, *, minutes: float) -> None:
        from datetime import timedelta
        delta = timedelta(minutes=minutes)
        for row in self.alert_events:
            for key in ("received_at", "dispatched_at"):
                if row.get(key) is not None:
                    row[key] -= delta
        for state in self.dedup_state.values():
            for key in ("first_seen_at", "last_seen_at", "summary_dispatched_at"):
                if state.get(key) is not None:
                    state[key] -= delta
        for entry in self.audit:
            entry["timestamp"] -= delta

    # ------------------------------------------------------------- producers
    def fire(
        self,
        *,
        alertname: str,
        fingerprint: str,
        severity: str = "warning",
        status: str = "firing",
        labels: dict[str, Any] | None = None,
        summary: str = "",
    ) -> dict[str, Any]:
        import json
        row = {
            "id": self._next_alert_id,
            "alertname": alertname,
            "status": status,
            "severity": severity,
            "category": "infrastructure",
            "labels": json.dumps(labels or {}),
            "annotations": json.dumps({"summary": summary or f"{alertname} {status}"}),
            "fingerprint": fingerprint,
            "received_at": self.now(),
            "dispatched_at": None,
            "dispatch_result": None,
        }
        self._next_alert_id += 1
        self.alert_events.append(row)
        return row

    # ----------------------------------------------------------- inspection
    def dispatch_result(self, row: dict[str, Any]) -> str | None:
        return row.get("dispatch_result")

    def audit_rows(self, event_type: str) -> list[dict[str, Any]]:
        return [a for a in self.audit if a["event_type"] == event_type]

    # --------------------------------------------------------- pool surface
    async def fetch(self, sql: str, *args: Any) -> list:
        import json
        if "FROM alert_events" in sql and "dispatched_at IS NULL" in sql:
            limit = int(args[0]) if args else 50
            pending = [r for r in self.alert_events if r["dispatched_at"] is None]
            return [
                {k: r[k] for k in ("id", "alertname", "status", "severity", "category",
                                   "labels", "annotations", "fingerprint")}
                for r in sorted(pending, key=lambda r: r["id"])[:limit]
            ]
        if "FROM remediation_rules" in sql:
            return [dict(r) for r in self.rules if r.get("enabled", True)]
        if "event_type = 'remediation_action'" in sql and "NOT EXISTS" in sql:
            verified = {
                a["details"].get("remediation_run_id")
                for a in self.audit_rows("remediation_verify")
            }
            pending = [
                a for a in self.audit_rows("remediation_action")
                if a["details"].get("remediation_run_id") not in verified
            ]
            return [
                {"id": a["id"], "timestamp": a["timestamp"], "details": json.dumps(a["details"])}
                for a in sorted(pending, key=lambda a: a["id"])[:50]
            ]
        return []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if "FROM alert_dedup_state" in sql:
            state = self.dedup_state.get(args[0])
            return dict(state) if state is not None else None
        if "FROM app_settings" in sql:
            value = self.app_settings.get(args[0])
            return None if value is None else {"value": value, "is_secret": False}
        if "LEFT JOIN LATERAL" in sql:
            actions = [
                a for a in self.audit_rows("remediation_action")
                if a["details"].get("fingerprint") == args[0]
            ]
            if not actions:
                return None
            action = max(actions, key=lambda a: a["id"])
            run_id = action["details"].get("remediation_run_id")
            verifies = [
                v for v in self.audit_rows("remediation_verify")
                if v["details"].get("remediation_run_id") == run_id
            ]
            verify = max(verifies, key=lambda v: v["id"]) if verifies else None
            return {
                "acted_at": action["timestamp"],
                "run_id": run_id,
                "action_name": action["details"].get("action_name"),
                "verified_at": verify["timestamp"] if verify else None,
                "verify_result": verify["details"].get("result") if verify else None,
            }
        return None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        from datetime import timedelta
        if "FROM app_settings" in sql:
            return self.app_settings.get(args[0])
        if "SELECT EXISTS" in sql and "alert_events r" in sql:
            stored_fp, alertname, since, row_id, severity = args
            mine = [r for r in self.alert_events
                    if r["alertname"] == alertname and r["fingerprint"] == stored_fp]
            for resolved in mine:
                if (resolved["status"].lower() != "resolved"
                        or resolved["received_at"] <= since or resolved["id"] >= row_id):
                    continue
                refired = any(
                    f["status"].lower() == "firing" and (f["severity"] or "") == severity
                    and resolved["id"] < f["id"] < row_id
                    for f in mine
                )
                if not refired:
                    return True
            return False
        if "details->>'action_name' = $2" in sql:
            fingerprint, action_name, window_minutes = args
            cutoff = self.now() - timedelta(minutes=int(window_minutes))
            return sum(
                1 for a in self.audit_rows("remediation_action")
                if a["details"].get("fingerprint") == fingerprint
                and a["details"].get("action_name") == action_name
                and a["timestamp"] >= cutoff
            )
        if "interval '1 hour'" in sql:
            cutoff = self.now() - timedelta(hours=1)
            return sum(1 for a in self.audit_rows("remediation_action") if a["timestamp"] >= cutoff)
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        import json
        if "UPDATE alert_events" in sql:
            row = next(r for r in self.alert_events if r["id"] == args[0])
            row["dispatched_at"] = self.now()
            row["dispatch_result"] = "sent" if len(args) == 1 else args[1]
            return "UPDATE 1"
        if "INSERT INTO alert_dedup_state" in sql:
            fingerprint, now, severity, source, sample = args
            self.dedup_state.setdefault(fingerprint, {
                "fingerprint": fingerprint, "first_seen_at": now, "last_seen_at": now,
                "repeat_count": 1, "summary_dispatched_at": None,
                "severity": severity, "source": source, "sample_message": sample,
            })
            return "INSERT 0 1"
        if "UPDATE alert_dedup_state" in sql:
            state = self.dedup_state.get(args[0])
            if state is None:
                return "UPDATE 0"
            if len(args) == 5:  # reset: a fresh dedup run
                _, now, severity, source, sample = args
                state.update(first_seen_at=now, last_seen_at=now, repeat_count=1,
                             summary_dispatched_at=None, severity=severity,
                             source=source, sample_message=sample)
            elif "repeat_count + 1" in sql:  # bump on a suppressed repeat
                state["repeat_count"] += 1
                state["last_seen_at"] = args[1]
            elif "summary_dispatched_at = $2" in sql:  # summary latch
                state["summary_dispatched_at"] = args[1]
                state["last_seen_at"] = args[1]
            return "UPDATE 1"
        if "INSERT INTO audit_log" in sql:
            event_type, source, _task_id, details, severity = args
            self.audit.append({
                "id": self._next_audit_id, "event_type": event_type, "source": source,
                "details": json.loads(details) if isinstance(details, str) else details,
                "severity": severity, "timestamp": self.now(),
            })
            self._next_audit_id += 1
            return "INSERT 0 1"
        return "OK"
