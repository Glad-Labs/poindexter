"""
Grafana alert rule sync — DB-driven, no redeploy needed. (GH-28)

Reads ``alert_rules`` rows from Postgres, converts each row to Grafana's
alert-rule provisioning JSON payload, and POSTs/PUTs to the Grafana HTTP
API. Hashed drift detection keeps the loop idempotent — Grafana is only
touched when a rule actually changed, so the sync cost is a single
``SELECT`` in the steady state.

Standalone like the rest of ``brain/`` — stdlib + asyncpg only. No
FastAPI, no prometheus_client, no site_config. Config comes from
``app_settings`` via direct SQL:

* ``grafana_api_base_url`` — Grafana reachable URL
  (default ``http://poindexter-grafana:3000``)
* ``grafana_api_token`` — service-account token. Empty = sync disabled
  with a warning (auth is an operator setup step, not a crash).
* ``grafana_alert_sync_enabled`` — master switch (``"true"``/``"false"``)

Called from ``brain_daemon.run_cycle`` every N cycles (default every 3
cycles = 15 min). Cycle cadence is tunable via
``grafana_alert_sync_interval_cycles``.

Drift detection: we hash ``(promql_query, threshold, duration, severity,
labels_json, annotations_json)`` per rule and cache the hash in
``brain_knowledge`` under entity ``alert_sync``. Only rules whose hash
changed (or that Grafana reports 404 for) get PUT to the API.

Error handling: Grafana unreachable is logged at WARNING and skipped —
the brain daemon keeps running. Each rule's sync is independent, so one
bad rule doesn't poison the rest.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.request
from typing import Any

import asyncpg

from brain.secret_reader import read_app_setting

logger = logging.getLogger("brain.alert_sync")

# Folder under which Grafana groups these rules in the UI. Operators can
# rename via the Grafana UI without breaking the sync — we key by rule
# UID, not folder/name pair.
GRAFANA_FOLDER_UID = "glad-labs"
GRAFANA_RULE_GROUP = "poindexter-db-alerts"

# Per-rule sync timeout. Grafana is local so responses are fast; 10s is
# generous. Kept small so a hung Grafana doesn't stall the brain cycle.
HTTP_TIMEOUT_SECONDS = 10

# Fail-loud cadence for the "empty token" skip path. Default cycle interval
# is 15 min, so 4 = ~1 h of silent skipping before we escalate. The
# counter doubles after each fire to avoid spamming the operator while
# the token is genuinely unset. Reset to zero on the first successful
# sync (token present, regardless of Grafana HTTP outcome) so a temporary
# misconfig doesn't permanently mute the alarm.
_empty_token_skips: int = 0
_empty_token_alarm_at: int = 4
_EMPTY_TOKEN_ALARM_MAX: int = 96  # ~24h at 15-min cadence — hard ceiling


def _rule_uid(name: str) -> str:
    """Deterministic Grafana rule UID from the rule name.

    Grafana UIDs are alphanumeric + ``-``/``_``, max 40 chars. We hash
    the name so rule renames generate a new UID (and the old UID is
    orphaned in Grafana — operator can clean up manually). Most of the
    time rule names are stable so UIDs are stable too.
    """
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()
    return f"pdx-{h[:20]}"


def _hash_rule(row: dict[str, Any]) -> str:
    """Stable hash of the rule's sync-relevant fields.

    Any change to query/threshold/duration/severity/labels/annotations
    invalidates the hash and triggers a sync. Row metadata like
    ``created_at``/``id`` is deliberately excluded — those change without
    changing rule behaviour.
    """
    # Labels/annotations may already be dicts (asyncpg decodes jsonb) or
    # JSON strings (tests pass plain dicts through a MagicMock). Normalise
    # before hashing so the hash is invariant under representation.
    labels = row.get("labels_json") or {}
    annotations = row.get("annotations_json") or {}
    if isinstance(labels, str):
        labels = json.loads(labels)
    if isinstance(annotations, str):
        annotations = json.loads(annotations)

    # sort_keys=True → deterministic across dict insertion orders.
    payload = json.dumps(
        {
            "name": row["name"],
            "promql_query": row["promql_query"],
            "threshold": float(row["threshold"]),
            "duration": row["duration"],
            "severity": row["severity"],
            "labels": labels,
            "annotations": annotations,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rule_to_grafana_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a DB row to Grafana's /api/v1/provisioning/alert-rules
    POST body.

    Schema reference:
    https://grafana.com/docs/grafana/latest/developers/http_api/alerting_provisioning/

    Keys Grafana cares about:
    * ``uid`` — stable UID per rule (we derive from name)
    * ``title`` — display name
    * ``condition`` — letter ref to the query stage that holds the
      boolean outcome. We use ``B`` (threshold check on query ``A``).
    * ``data`` — list of query stages. Two stages: ``A`` = the user's
      PromQL, ``B`` = threshold check.
    * ``noDataState`` / ``execErrState`` — what Grafana does when the
      query returns no data or errors. We default to ``OK`` so a
      broken query doesn't page the team.
    * ``for`` — duration the rule must stay firing before it alerts.
    * ``labels`` / ``annotations`` — per-rule metadata.
    """
    labels = row.get("labels_json") or {}
    annotations = row.get("annotations_json") or {}
    if isinstance(labels, str):
        labels = json.loads(labels)
    if isinstance(annotations, str):
        annotations = json.loads(annotations)

    # Ensure severity is reflected as a label for Alertmanager routing.
    labels = {**labels, "severity": row["severity"]}

    return {
        "uid": _rule_uid(row["name"]),
        "title": row["name"],
        "folderUID": GRAFANA_FOLDER_UID,
        "ruleGroup": GRAFANA_RULE_GROUP,
        "condition": "B",
        "noDataState": "OK",
        "execErrState": "OK",
        "for": row["duration"],
        "labels": labels,
        "annotations": annotations,
        "data": [
            {
                "refId": "A",
                "relativeTimeRange": {"from": 600, "to": 0},
                "datasourceUid": "prometheus",
                "model": {
                    "refId": "A",
                    "expr": row["promql_query"],
                    "instant": True,
                },
            },
            {
                "refId": "B",
                "relativeTimeRange": {"from": 0, "to": 0},
                "datasourceUid": "__expr__",
                "model": {
                    "refId": "B",
                    "type": "threshold",
                    "expression": "A",
                    "conditions": [
                        {
                            "evaluator": {
                                "type": "gt",
                                "params": [float(row["threshold"])],
                            },
                            "operator": {"type": "and"},
                            "query": {"params": ["A"]},
                            "reducer": {"type": "last", "params": []},
                            "type": "query",
                        }
                    ],
                },
            },
        ],
    }


async def _get_setting(pool, key: str, default: str = "") -> str:
    """Read a string value from app_settings, decrypting if ``is_secret=true``.

    Routes through ``brain.secret_reader.read_app_setting`` so the
    encrypted ``enc:v1:<base64>`` envelope used by
    ``services.plugins.secrets`` gets pgp_sym_decrypt'd transparently.
    The previous direct ``SELECT value`` returned the ciphertext for
    secret rows (notably ``grafana_api_token`` once it was promoted to
    is_secret=true), which then went into the Bearer header verbatim
    and failed every alert sync with "Invalid header value b'Bearer
    enc:v1:...'". One-liner fix; same bug class as the
    operator-overlay CLI token-decryption fix (2026-05-13).
    """
    try:
        return await read_app_setting(pool, key, default=default)
    except Exception as e:
        logger.warning(
            "alert_sync: app_settings read failed for %s (%s) — using default",
            key, e,
        )
        return default


async def _load_rule_hashes(pool) -> dict[str, str]:
    """Fetch the last-synced rule hashes from brain_knowledge.

    Each rule's hash is stored as its own row:
    entity='alert_sync', attribute='hash:<rule_name>', value=<sha256>.
    """
    hashes: dict[str, str] = {}
    try:
        rows = await pool.fetch(
            "SELECT attribute, value FROM brain_knowledge "
            "WHERE entity = 'alert_sync' AND attribute LIKE 'hash:%'"
        )
    except Exception as e:
        logger.debug("alert_sync: hash cache load failed (%s)", e)
        return hashes
    for r in rows:
        name = r["attribute"][len("hash:"):]
        hashes[name] = r["value"]
    return hashes


async def _record_rule_hash(pool, name: str, new_hash: str) -> None:
    """Persist a rule's hash so the next cycle can skip it if unchanged."""
    try:
        await pool.execute(
            """
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
            VALUES ('alert_sync', $1, $2, 1.0, 'brain_alert_sync')
            ON CONFLICT (entity, attribute) DO UPDATE SET
                value = EXCLUDED.value, updated_at = NOW()
            """,
            f"hash:{name}", new_hash,
        )
    except Exception as e:
        # Hash cache is best-effort; we can still sync on the next cycle.
        logger.debug("alert_sync: hash persist failed for %s (%s)", name, e)


def _http_request(
    method: str,
    url: str,
    token: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, str]:
    """Issue a single HTTP request against Grafana. Returns (status, body).

    Raises ``urllib.error.URLError`` on network failure so the caller can
    distinguish ``Grafana down`` (skip) from ``4xx/5xx`` (log + move on).
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS)
        return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        # 4xx/5xx from Grafana — still a reachable server, just rejected
        # our payload. Surface the status so the caller can decide.
        return e.code, (e.read() or b"").decode("utf-8", errors="replace")


async def _push_rule(
    base_url: str, token: str, payload: dict[str, Any]
) -> tuple[bool, str]:
    """PUT a single rule to Grafana, creating it if needed.

    Grafana's provisioning API treats PUT as idempotent upsert when the
    UID is supplied. On 404 (no such rule yet) we POST to create.

    Returns ``(ok, detail)`` where ok=True means 2xx.
    """
    uid = payload["uid"]
    put_url = f"{base_url}/api/v1/provisioning/alert-rules/{uid}"
    try:
        status, body = _http_request("PUT", put_url, token, payload)
    except urllib.error.URLError:
        # Grafana unreachable — bubble up so caller can stop the whole
        # cycle (no point trying the other rules if the server's down).
        raise
    if 200 <= status < 300:
        return True, f"updated (HTTP {status})"
    if status == 404:
        # Rule doesn't exist yet — POST to create.
        post_url = f"{base_url}/api/v1/provisioning/alert-rules"
        status, body = _http_request("POST", post_url, token, payload)
        if 200 <= status < 300:
            return True, f"created (HTTP {status})"
    return False, f"HTTP {status}: {body[:200]}"


async def sync_alert_rules(pool) -> dict[str, Any]:
    """Read DB rules, compare to cached hashes, push changes to Grafana.

    Top-level entrypoint called by ``brain_daemon.run_cycle``. Returns a
    summary dict the caller can log or emit to ``brain_decisions``.

    Never raises: a failing sync is logged + reflected in the summary
    but never takes down the brain daemon.
    """
    summary = {
        "enabled": False,
        "rules_total": 0,
        "rules_synced": 0,
        "rules_unchanged": 0,
        "rules_failed": 0,
        "error": None,
    }

    enabled = (await _get_setting(pool, "grafana_alert_sync_enabled", "true")).lower()
    if enabled != "true":
        summary["error"] = "disabled via grafana_alert_sync_enabled"
        logger.debug("alert_sync: disabled via app_settings — skipping")
        return summary

    global _empty_token_skips, _empty_token_alarm_at  # noqa: PLW0603

    token = await _get_setting(pool, "grafana_api_token", "")
    if not token:
        _empty_token_skips += 1
        summary["error"] = "grafana_api_token not set"
        summary["empty_token_skip_count"] = _empty_token_skips
        logger.warning(
            "alert_sync: grafana_api_token is empty — set it via "
            "app_settings to enable Grafana push. Skipping this cycle "
            "(consecutive skips: %d).",
            _empty_token_skips,
        )
        if _empty_token_skips >= _empty_token_alarm_at:
            await _fire_empty_token_alarm(pool, _empty_token_skips)
            # Exponential back-off so the operator gets one page per
            # hour, then ~2h, then ~4h, etc. — bounded at ~24h.
            _empty_token_alarm_at = min(
                _empty_token_alarm_at * 2, _EMPTY_TOKEN_ALARM_MAX,
            )
        return summary

    # Token present — reset the skip counter so a temporary misconfig
    # doesn't permanently mute the alarm. An empty token is a config
    # problem; an unreachable Grafana is a runtime problem with its
    # own alerting downstream.
    if _empty_token_skips:
        _empty_token_skips = 0
        _empty_token_alarm_at = 4

    base_url = (await _get_setting(
        pool, "grafana_api_base_url", "http://poindexter-grafana:3000"
    )).rstrip("/")

    summary["enabled"] = True

    try:
        rows = await pool.fetch(
            "SELECT name, promql_query, threshold, duration, severity, "
            "enabled, labels_json, annotations_json "
            "FROM alert_rules WHERE enabled = TRUE ORDER BY name"
        )
    except asyncpg.exceptions.UndefinedTableError:
        summary["error"] = "alert_rules table missing — run migrations"
        logger.warning(
            "alert_sync: alert_rules table not present — run migrations. Skipping."
        )
        return summary
    except Exception as e:
        summary["error"] = f"db read failed: {e}"
        logger.error("alert_sync: failed to read alert_rules: %s", e, exc_info=True)
        return summary

    summary["rules_total"] = len(rows)
    if not rows:
        logger.info("alert_sync: no enabled alert_rules — nothing to push")
        return summary

    cached_hashes = await _load_rule_hashes(pool)

    for row in rows:
        # asyncpg Record → dict so _hash_rule / rule_to_grafana_payload
        # can use mapping access without isinstance branches.
        r = dict(row)
        name = r["name"]
        new_hash = _hash_rule(r)
        if cached_hashes.get(name) == new_hash:
            summary["rules_unchanged"] += 1
            logger.debug("alert_sync: %s unchanged (hash %s)", name, new_hash[:8])
            continue

        payload = rule_to_grafana_payload(r)
        try:
            ok, detail = await _push_rule(base_url, token, payload)
        except urllib.error.URLError as e:
            # Grafana unreachable — stop pushing further rules this cycle.
            # The next cycle will retry. No crash.
            summary["error"] = f"grafana unreachable: {e.reason}"
            summary["rules_failed"] += 1
            logger.warning(
                "alert_sync: Grafana unreachable at %s (%s) — skipping remaining rules",
                base_url, e.reason,
            )
            return summary
        except Exception as e:  # pragma: no cover — defensive
            summary["rules_failed"] += 1
            logger.warning("alert_sync: rule %s failed unexpectedly: %s", name, e)
            continue

        if ok:
            summary["rules_synced"] += 1
            logger.info("alert_sync: pushed %s — %s", name, detail)
            await _record_rule_hash(pool, name, new_hash)
        else:
            summary["rules_failed"] += 1
            logger.warning("alert_sync: rule %s push failed: %s", name, detail)

    logger.info(
        "alert_sync: cycle complete — %d total, %d synced, %d unchanged, %d failed",
        summary["rules_total"], summary["rules_synced"],
        summary["rules_unchanged"], summary["rules_failed"],
    )
    return summary


async def _fire_empty_token_alarm(pool: Any, skip_count: int) -> None:
    """Page the operator that the Grafana sync has been silently no-oping.

    Lazy-imports ``brain_daemon.notify`` to avoid the circular import a
    top-level dep would create (brain_daemon imports alert_sync inside
    ``_maybe_sync_grafana_alerts``). Both name resolution forms — the
    package-qualified one used when brain runs as a module and the bare
    one used when ``python brain_daemon.py`` is the entrypoint — are
    tried, matching the pattern brain_daemon itself uses for its own
    optional imports. Failures are swallowed: a missing ``notify`` shouldn't
    crash the sync cycle.
    """
    minutes = skip_count * 15  # default cycle cadence = 15 min
    body = (
        "[brain.alert_sync] grafana_api_token has been empty for "
        f"{skip_count} cycle(s) (~{minutes} min). DB-driven alert rules "
        "are NOT being pushed to Grafana — any rule edits since the "
        "token went empty are unapplied. Fix: set app_settings."
        "grafana_api_token to a Grafana service-account token with "
        "alerting write scope."
    )
    notify_fn = None
    try:
        from brain_daemon import notify as _notify  # type: ignore[import-not-found]

        notify_fn = _notify
    except ImportError:
        try:
            from brain.brain_daemon import notify as _notify  # type: ignore[import-not-found]

            notify_fn = _notify
        except ImportError:
            logger.warning(
                "alert_sync: cannot import brain_daemon.notify — "
                "empty-token alarm logged but not paged (skip_count=%d)",
                skip_count,
            )
            return
    try:
        result = notify_fn(body, pool=pool)
        if hasattr(result, "__await__"):
            await result
    except Exception as e:  # noqa: BLE001 — never let alarm crash the sync
        logger.warning("alert_sync: notify_operator failed: %s", e)
