"""Pure helpers for ``grafana_panels_lint.py`` — macro substitution,
panel walking, target classification.

Kept separate so the lint script stays under ~250 LOC and the helpers
stay unit-testable without spinning up a datasource. See the script's
module docstring for the WHY.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Macro substitutions — Grafana resolves these per-dashboard at render time;
# we hand the planner sane defaults so EXPLAIN / PromQL parse can run.
#
# Add new macros here when a dashboard introduces them. Per
# feedback_no_silent_defaults: an unknown macro is a HARD FAIL — fix it
# by adding the substitution rather than silencing the message.
#
# Order matters: the longest patterns first so $__timeGroupAlias doesn't
# collide with $__timeGroup.
# ---------------------------------------------------------------------------
MACRO_DOCS: dict[str, str] = {
    "$__timeFilter(col)": "col BETWEEN NOW() - INTERVAL '1 hour' AND NOW()",
    "$__timeGroupAlias(col, '1m')": "date_trunc('minute', col) AS time",
    "$__timeGroup(col, '1m')": "date_trunc('minute', col)",
    "$__timeFrom()": "(EXTRACT(EPOCH FROM NOW() - INTERVAL '1 hour') * 1000)::bigint",
    "$__timeTo()": "(EXTRACT(EPOCH FROM NOW()) * 1000)::bigint",
    "$__interval": "'1 minute'",
}

# Hard-fail SQLSTATE codes — the bugs we exist to catch.
SQL_FAIL_CODES = {"42703", "42P01"}

# Datasource-type → category mapping. Anything else is skipped.
POSTGRES_TYPES = {"grafana-postgresql-datasource", "postgres"}
PROMETHEUS_TYPES = {"prometheus"}
LOKI_TYPES = {"loki"}

# Errors we *don't* hard-fail on — argument-substitution issues
# from $__timeFilter etc. are warnings, not failures.
SQL_WARN_CODES = {"22P02", "42883", "42804"}


def substitute(sql: str) -> str:
    """Apply the macro table to a query string.

    Raises ``ValueError`` for any unknown ``$__*`` macro — per
    feedback_no_silent_defaults the lint must fail loud rather than
    silently strip macros it doesn't understand.
    """
    out = sql
    while "$__timeFilter(" in out:
        start = out.index("$__timeFilter(")
        end = out.index(")", start)
        col = out[start + len("$__timeFilter(") : end]
        out = (
            out[:start]
            + f"{col} BETWEEN NOW() - INTERVAL '1 hour' AND NOW()"
            + out[end + 1 :]
        )
    while "$__timeGroupAlias(" in out:
        start = out.index("$__timeGroupAlias(")
        end = out.index(")", start)
        args = out[start + len("$__timeGroupAlias(") : end]
        col = args.split(",", 1)[0].strip()
        out = out[:start] + f"date_trunc('minute', {col}) AS time" + out[end + 1 :]
    while "$__timeGroup(" in out:
        start = out.index("$__timeGroup(")
        end = out.index(")", start)
        args = out[start + len("$__timeGroup(") : end]
        col = args.split(",", 1)[0].strip()
        out = out[:start] + f"date_trunc('minute', {col})" + out[end + 1 :]
    out = out.replace(
        "$__timeFrom()",
        "(EXTRACT(EPOCH FROM NOW() - INTERVAL '1 hour') * 1000)::bigint",
    )
    out = out.replace(
        "$__timeTo()",
        "(EXTRACT(EPOCH FROM NOW()) * 1000)::bigint",
    )
    out = out.replace("$__interval", "'1 minute'")
    if "$__" in out:
        raise ValueError(
            "unknown macro in query — add to MACRO_DOCS in "
            f"lib_grafana_panels.py: {out!r}"
        )
    # Dashboard variables ($service, $container, …) — Grafana resolves
    # these to a concrete value at render time. We never *run* the query,
    # so a literal placeholder is fine for both EXPLAIN and PromQL parse.
    if "$" in out:
        out = re.sub(r"\$([A-Za-z_]\w*)", "placeholder", out)
    return out


def walk_panels(panel: dict) -> Iterable[dict]:
    """Yield every renderable panel, descending through row containers.

    Grafana nests ``panels[].panels[]`` for collapsed/expanded rows.
    Both row containers and their children must be walked so we don't
    miss queries hidden under a row.
    """
    if panel.get("type") == "row":
        for child in panel.get("panels", []) or []:
            yield from walk_panels(child)
        return
    yield panel
    for child in panel.get("panels", []) or []:
        yield from walk_panels(child)


def iter_root_panels(dashboard: dict) -> Iterable[dict]:
    yield from dashboard.get("panels", []) or []


def classify(target: dict, panel: dict) -> str | None:
    """Return ``'postgres' | 'prometheus' | 'loki' | None``.

    Targets may set their own ``datasource``; otherwise inherit the
    panel's. ``None`` means we don't know how to validate this target.
    """
    ds = target.get("datasource") or panel.get("datasource") or {}
    dtype = (ds.get("type") or "").lower() if isinstance(ds, dict) else ""
    if dtype in POSTGRES_TYPES:
        return "postgres"
    if dtype in PROMETHEUS_TYPES:
        return "prometheus"
    if dtype in LOKI_TYPES:
        return "loki"
    return None


def extract_query(target: dict, kind: str) -> str | None:
    if kind == "postgres":
        return target.get("rawSql")
    # Prometheus + loki both put the query under ``expr`` (loki sometimes ``query``).
    return target.get("expr") or target.get("query")


def classify_pg_error(sqlstate: str) -> str:
    """Map a postgres SQLSTATE to one of: ``FAIL`` | ``WARN``.

    Hard-fail on the schema-drift codes (42703 column-missing,
    42P01 relation-missing) — those are the bugs this lint exists
    to catch. Everything else is a WARN; CI doesn't go red on a
    macro arg-substitution glitch.
    """
    if sqlstate in SQL_FAIL_CODES:
        return "FAIL"
    return "WARN"


__all__ = [
    "MACRO_DOCS",
    "SQL_FAIL_CODES",
    "POSTGRES_TYPES",
    "PROMETHEUS_TYPES",
    "LOKI_TYPES",
    "substitute",
    "walk_panels",
    "iter_root_panels",
    "classify",
    "extract_query",
    "classify_pg_error",
]
