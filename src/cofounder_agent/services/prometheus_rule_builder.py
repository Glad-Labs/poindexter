"""Render Prometheus alert rules from ``app_settings`` (DB-first).

Phase D follow-up (GitHub #68). Alert thresholds and rule bodies live in
``app_settings`` so operators can tune without editing YAML or rebuilding
the worker image. The repo still ships with sensible defaults — the
brain seeds them into ``app_settings`` on first boot (see
``brain/seed_app_settings.json``), but every value can be overridden
with a single ``UPDATE app_settings ...``.

## Key scheme

``prometheus.threshold.<name>``
    Scalar value (stored as a string in app_settings.value, parsed at
    render time). Referenced inside rule expressions as
    ``{threshold.<name>}``. Examples:

    - ``prometheus.threshold.daily_spend_warning = "4.0"``
    - ``prometheus.threshold.embeddings_stale_seconds = "21600"``

``prometheus.rule.<alert_name>``
    Full JSON override for a single alert. Partial keys are merged on
    top of the built-in default, so an operator who just wants to
    disable one alert writes ``{"enabled": false}`` and nothing else.

    Keys:
    - ``enabled`` (bool, default true)
    - ``expr`` (string — may reference ``{threshold.X}``)
    - ``for`` (duration string, e.g. ``"5m"``)
    - ``severity`` (``"info"``, ``"warning"``, ``"critical"``)
    - ``category`` (``"infrastructure"``, ``"content"``, ``"business"``)
    - ``group`` (which Prometheus group the rule lives in)
    - ``interval`` (group evaluation interval, e.g. ``"30s"``)
    - ``summary``, ``description`` (annotation strings)

## Rendering

:func:`build_current` is the entry point — reads both key prefixes,
merges with :data:`DEFAULT_RULES` / :data:`DEFAULT_THRESHOLDS`, returns a
YAML string suitable for Prometheus's ``rule_files`` directive.

YAML is hand-rendered (no PyYAML dep) because the shape is fixed and
Prometheus's parser is strict about quoting in expressions — manual
control beats fighting ``safe_dump`` quirks.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults — also used as the seed in brain/seed_app_settings.json.
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS: dict[str, str] = {
    # Content pipeline
    "embeddings_stale_seconds": "21600",  # 6h
    # Infrastructure (Gitea #238 recovery)
    "postgres_p99_latency_seconds": "0.1",  # alert when SELECT 1 > 100ms p99
    "embeddings_missing_posts": "3",  # alert when >3 published posts lack embeddings
    # Business / cost guards
    "daily_spend_warning_usd": "4.0",
    "daily_spend_critical_usd": "5.0",
    "monthly_spend_warning_usd": "15.0",
}


# Each rule entry is the canonical shape. The built-in list ships the
# current Phase-D alerts as-is.
DEFAULT_RULES: dict[str, dict[str, Any]] = {
    # Infrastructure alerts (PoindexterWorkerDown, PoindexterPostgresDown,
    # PoindexterOllamaDown, PoindexterWorkerUnhealthy) live in the static
    # infrastructure/prometheus/alerts/infrastructure.yml file — they have
    # no thresholds to configure and they need to fire even when the DB
    # is down (which is exactly when dynamic-rules rendering would fail).
    # Only content + business rules live here, since those are the ones
    # with tunable numbers.

    # --- Content ---
    "EmbeddingsStale": {
        "enabled": True,
        "group": "poindexter-content",
        "interval": "1m",
        # Alert when the embeddings_total gauge hasn't grown at all in
        # the configured window — auto-embed is either stuck or the tap
        # runner is failing silently. The window is configured via the
        # threshold (converted from seconds to a Prometheus duration at
        # render time — see :func:`_render_rule`).
        # Previously this expression read ``time() - max(embeddings_total)``,
        # which subtracted the COUNT from the unix timestamp — always ≫ 0
        # so the alert fired permanently.
        "expr": "sum(increase(poindexter_embeddings_total[{threshold.embeddings_stale_seconds}s])) == 0",
        "for": "10m",
        "severity": "warning",
        "category": "content",
        "summary": "auto-embed hasn't updated embeddings_total gauge recently",
        "description": (
            "poindexter_embeddings_total hasn't changed in the configured window. "
            "Either the auto-embed container is stuck or the Tap runner is failing "
            "silently. Check `docker logs poindexter-auto-embed` and "
            "`tail ~/.gladlabs/auto-embed.log`."
        ),
    },
    "NoPublishedPostsRecently": {
        "enabled": True,
        "group": "poindexter-content",
        "interval": "1m",
        "expr": (
            '(poindexter_posts_total{status="published"} offset 24h) == '
            'poindexter_posts_total{status="published"}'
        ),
        "for": "48h",
        "severity": "info",
        "category": "content",
        "summary": "No new posts published in 48h",
        "description": (
            "Published post count hasn't grown in 48h. Either content "
            "generation is stalled, QA is rejecting everything, or the "
            "approval queue is backed up awaiting human review."
        ),
    },
    # Gitea #238 — recovers nuances the retired brain probes measured.
    "PostsNotEmbedded": {
        "enabled": True,
        "group": "poindexter-content",
        "interval": "1m",
        # Catches the "3 new posts published, 0 got embedded" case that
        # the embeddings_total rate alone misses when overall traffic
        # keeps the rate positive.
        "expr": (
            "poindexter_embeddings_missing_posts > "
            "{threshold.embeddings_missing_posts}"
        ),
        "for": "10m",
        "severity": "warning",
        "category": "content",
        "summary": "Published posts piling up without embeddings",
        "description": (
            "Published posts are not getting embeddings. Auto-embed is "
            "either wedged or the embeddings-writer pipeline is erroring "
            "silently for new posts. Run `poindexter memory backfill-posts "
            "--since 1d --dry-run` to inspect."
        ),
    },
    # --- Infrastructure (Gitea #238) ---
    "PostgresSlowQuery": {
        "enabled": True,
        "group": "poindexter-infra",
        "interval": "1m",
        # p99 latency of the SELECT 1 liveness probe. Recovers the
        # "DB up but slow" nuance the retired db_ping probe measured;
        # the 0/1 connected gauge doesn't discriminate slow from failed.
        "expr": (
            "histogram_quantile(0.99, "
            "sum(rate(poindexter_postgres_query_latency_seconds_bucket[5m])) "
            "by (le)) > {threshold.postgres_p99_latency_seconds}"
        ),
        "for": "5m",
        "severity": "warning",
        "category": "infra",
        "summary": "Postgres SELECT 1 p99 latency exceeding threshold",
        "description": (
            "SELECT 1 round-trip p99 > threshold for 5m. DB is reachable "
            "but degraded — check for long-running queries (pg_stat_activity), "
            "connection-pool saturation, or disk I/O pressure."
        ),
    },
    # GH-227 — alert on schema drift (worker on stale schema).
    "UnappliedMigrationsDrift": {
        "enabled": True,
        "group": "poindexter-infra",
        "interval": "1m",
        # Catches the failure mode where a container is rebuilt with
        # new migration files but the running worker process wasn't
        # restarted, so run_migrations() never re-ran. The /api/health
        # JSON probe surfaces this but Alertmanager couldn't see it
        # until the gauge landed in metrics_exporter.
        "expr": "poindexter_unapplied_migrations_count > 0",
        "for": "30m",
        "severity": "warning",
        "category": "infra",
        "summary": "Worker is on a stale schema — restart to apply pending migrations",
        "description": (
            "poindexter_unapplied_migrations_count > 0 for 30m. The container "
            "has migration files on disk that aren't in schema_migrations. "
            "Pull a fresh main + restart poindexter-worker — the migration "
            "runner applies pending migrations on boot. To inspect first: "
            "`poindexter migrate status` (or `--json` for machine-readable)."
        ),
    },
    "OllamaNoModelsLoaded": {
        "enabled": True,
        "group": "poindexter-infra",
        "interval": "1m",
        # "Up but empty" — /api/tags returns 200 with []. The existing
        # OllamaReachable gauge passes this case, which is why it needs
        # a dedicated alert.
        "expr": "poindexter_ollama_model_count == 0",
        "for": "2m",
        "severity": "critical",
        "category": "infra",
        "summary": "Ollama is up but has no models loaded",
        "description": (
            "Ollama /api/tags returns 200 but the models list is empty. "
            "Pipeline can't generate anything. Run `ollama pull gemma3:27b` "
            "(and the configured embed model) on the GPU host."
        ),
    },
    # --- Business / cost ---
    # Cost alerts include an ``unless approval_queue_length > 0`` cross-check
    # so they don't fire while the pipeline is throttling on pending human
    # approvals (Gitea #238 — matches the retired cost_freshness probe's
    # ``expected_idle`` logic).
    "DailySpendApproachingLimit": {
        "enabled": True,
        "group": "poindexter-business",
        "interval": "1m",
        "expr": (
            "poindexter_daily_spend_usd > {threshold.daily_spend_warning_usd} "
            "unless poindexter_approval_queue_length > 0"
        ),
        "for": "5m",
        "severity": "warning",
        "category": "business",
        "summary": "Daily AI spend approaching the soft cap",
        "description": (
            "cost_logs shows elevated daily spend. Check "
            "plugin.llm_provider.primary.* — maybe a workflow flipped "
            "to a paid provider unintentionally."
        ),
    },
    "DailySpendOverBudget": {
        "enabled": True,
        "group": "poindexter-business",
        "interval": "1m",
        "expr": (
            "poindexter_daily_spend_usd > {threshold.daily_spend_critical_usd} "
            "unless poindexter_approval_queue_length > 0"
        ),
        "for": "2m",
        "severity": "critical",
        "category": "business",
        "summary": "Daily AI spend exceeded hard cap",
        "description": (
            "Cost budget blown. Pipeline should have stopped itself but "
            "didn't. Check cost_guard logic + app_settings daily_spend_limit."
        ),
    },
    "MonthlySpendHigh": {
        "enabled": True,
        "group": "poindexter-business",
        "interval": "1m",
        "expr": "poindexter_monthly_spend_usd > {threshold.monthly_spend_warning_usd}",
        "for": "10m",
        "severity": "warning",
        "category": "business",
        "summary": "Monthly AI spend running high",
        "description": (
            "Running high for the expected Ollama-only defaults. Review "
            "cost_logs to see which provider is driving spend."
        ),
    },
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


THRESHOLD_PREFIX = "prometheus.threshold."
RULE_PREFIX = "prometheus.rule."


async def load_thresholds(pool_or_conn: Any) -> dict[str, str]:
    """Return ``threshold_name -> value`` with defaults filled in.

    Reads every ``prometheus.threshold.*`` row and overlays onto
    :data:`DEFAULT_THRESHOLDS`, so any key the operator hasn't touched
    still has a sane default at render time.
    """
    out = dict(DEFAULT_THRESHOLDS)
    rows = await _fetch_prefix(pool_or_conn, THRESHOLD_PREFIX)
    for key, raw in rows:
        name = key[len(THRESHOLD_PREFIX):]
        if raw is None:
            continue
        out[name] = str(raw).strip()
    return out


async def load_rules(pool_or_conn: Any) -> dict[str, dict[str, Any]]:
    """Return ``alert_name -> rule-dict`` with defaults + DB overrides merged.

    Unknown rule names (rows without a corresponding default) are
    accepted as-is — the DB can define entirely new alerts. Partial
    overrides merge shallowly on top of the default.
    """
    import json

    merged: dict[str, dict[str, Any]] = {}
    for name, default in DEFAULT_RULES.items():
        merged[name] = dict(default)

    rows = await _fetch_prefix(pool_or_conn, RULE_PREFIX)
    for key, raw in rows:
        name = key[len(RULE_PREFIX):]
        if not raw:
            continue
        try:
            override = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "prometheus.rule.%s has malformed JSON; ignoring override", name
            )
            continue
        if not isinstance(override, dict):
            logger.warning(
                "prometheus.rule.%s is not a JSON object; ignoring override", name
            )
            continue
        base = merged.get(name) or {}
        base.update(override)
        merged[name] = base

    return merged


async def _fetch_prefix(
    pool_or_conn: Any, prefix: str
) -> list[tuple[str, str | None]]:
    """SELECT key, value FROM app_settings WHERE key LIKE prefix||'%'."""
    # Escape SQL LIKE wildcards from the prefix just in case.
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = await pool_or_conn.fetch(
        "SELECT key, value FROM app_settings WHERE key LIKE $1 ESCAPE '\\'",
        f"{escaped}%",
    )
    return [(r["key"], r["value"]) for r in rows]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _substitute_thresholds(expr: str, thresholds: dict[str, str]) -> str:
    """Replace ``{threshold.X}`` tokens in an expr with the numeric value."""
    out = expr
    # Cheap literal substitution — there are never enough thresholds to
    # make a regex worth it, and {a} style placeholders would conflict
    # with PromQL's braces.
    for name, value in thresholds.items():
        out = out.replace("{threshold." + name + "}", value)
    return out


def render_yaml(
    thresholds: dict[str, str],
    rules: dict[str, dict[str, Any]],
) -> str:
    """Render rules to the YAML format Prometheus's ``rule_files`` expects.

    Groups rules by their ``group`` field, preserving the interval of
    the first rule seen per group. Disabled rules are omitted entirely
    (Prometheus has no "disabled" concept — the rule just doesn't exist).
    """
    groups: dict[str, dict[str, Any]] = {}
    for name, rule in rules.items():
        if not rule.get("enabled", True):
            continue
        group_name = rule.get("group") or "poindexter-default"
        group = groups.setdefault(
            group_name,
            {"name": group_name, "interval": rule.get("interval", "1m"), "rules": []},
        )
        expr = _substitute_thresholds(str(rule.get("expr", "vector(0)")), thresholds)
        group["rules"].append(
            {
                "alert": name,
                "expr": expr,
                "for": str(rule.get("for", "5m")),
                "severity": str(rule.get("severity", "info")),
                "category": str(rule.get("category", "content")),
                "summary": str(rule.get("summary", "")),
                "description": str(rule.get("description", "")),
            }
        )

    lines: list[str] = [
        "# Rendered by RenderPrometheusRulesJob — do not edit by hand.",
        "# Source of truth: app_settings.prometheus.rule.* + prometheus.threshold.*",
        "groups:",
    ]
    for group_name in sorted(groups):
        group = groups[group_name]
        lines.append(f"  - name: {_yaml_scalar(group['name'])}")
        lines.append(f"    interval: {_yaml_scalar(group['interval'])}")
        lines.append("    rules:")
        for rule in group["rules"]:
            lines.append(f"      - alert: {_yaml_scalar(rule['alert'])}")
            lines.append(f"        expr: {_yaml_quoted(rule['expr'])}")
            lines.append(f"        for: {_yaml_scalar(rule['for'])}")
            lines.append("        labels:")
            lines.append(f"          severity: {_yaml_scalar(rule['severity'])}")
            lines.append(f"          category: {_yaml_scalar(rule['category'])}")
            lines.append("        annotations:")
            lines.append(f"          summary: {_yaml_quoted(rule['summary'])}")
            lines.append(f"          description: {_yaml_quoted(rule['description'])}")
    lines.append("")  # trailing newline
    return "\n".join(lines)


def _yaml_scalar(value: str) -> str:
    """Emit a plain YAML scalar — quote only if needed for safety."""
    text = str(value)
    if not text:
        return '""'
    if text[0] in "!&*[]{},|>'\"#%@`" or text.strip() != text:
        return _yaml_quoted(text)
    return text


def _yaml_quoted(value: str) -> str:
    """Emit a double-quoted YAML string with escaping."""
    text = str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{text}"'


async def build_current(pool_or_conn: Any) -> str:
    """Convenience: load thresholds + rules and render the YAML."""
    thresholds = await load_thresholds(pool_or_conn)
    rules = await load_rules(pool_or_conn)
    return render_yaml(thresholds, rules)
