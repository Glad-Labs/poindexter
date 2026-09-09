"""ProbeDecodeSplitCoverageJob — alert when the Ollama decode/prefill split goes dark.

``services/llm_providers/ollama_timings.py`` recovers Ollama's
``eval_duration`` / ``prompt_eval_duration`` split by **monkey-patching
``transform_response`` on two LiteLLM config classes**. That seam is the only
reason ``cost_logs.decode_duration_ms`` is populated at all, and it is
deliberately fail-open: if LiteLLM moves its internals, the wrapper logs once
and every subsequent call proceeds normally with the columns left NULL.

Fail-open is the right posture for the call path — an observability stash must
never break a completion — but it means the capture can stop **silently**. The
signature-pin test (``tests/unit/services/llm_providers/test_ollama_timings.py``)
catches the case where an upgrade lands in CI; it cannot catch a runtime drift
on a box whose litellm was bumped underneath it, and nothing else notices that
the throughput dataset simply stopped growing.

This probe is that missing watcher. It is the convergence-watchdog shape: rather
than carrying a hardcoded list of "local" model names (which rots every time a
pin changes), it **learns the denominator from the data** — any model that has
reported a split at least once inside the learning window is, by construction,
an Ollama-routed model, so its recent rows are entitled to one. Cloud models
never report a split, never enter the denominator, and never trigger a page.

Consequences of that design:

- A litellm upgrade that breaks the wrapper drops EVERY learned model to 0% →
  one finding naming all of them.
- A single model regressing (a pin moved to a route the wrapper doesn't cover)
  shows up as that model alone.
- A brand-new model that has never reported is invisible until it reports once.
  That is deliberate: the probe cannot distinguish "new local model whose
  capture is broken" from "new cloud model", and inventing a vendor list to try
  would reintroduce the rot this design exists to avoid.

Rows that legitimately carry NULL are excluded from the numerator's denominator
rather than counted as misses: failed calls (a GPU-lock timeout decodes
nothing) and zero-output calls have no decode phase to report.

Issue: Glad-Labs/poindexter#3340 follow-up (decode-split durability).
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# app_settings keys (seeded in settings_defaults.py).
_ENABLED_KEY = "llm_decode_split_probe_enabled"
_WINDOW_HOURS_KEY = "llm_decode_split_window_hours"
_LEARN_DAYS_KEY = "llm_decode_split_learn_days"
_MIN_COVERAGE_KEY = "llm_decode_split_min_coverage_pct"
_MIN_SAMPLE_KEY = "llm_decode_split_min_sample"

_DEFAULT_WINDOW_HOURS = 24
_DEFAULT_LEARN_DAYS = 30
_DEFAULT_MIN_COVERAGE = 90
_DEFAULT_MIN_SAMPLE = 50

_FINDING_KIND = "llm_decode_split_coverage_low"

# Model names are normalized with the house ``^ollama(_chat)?/`` strip before
# grouping (docs/architecture/cost-logs-model-identity.md): one engine logs
# under up to three spellings depending on the call site, and an un-normalized
# GROUP BY both splits a single model into several rows and — worse — makes a
# call site that changes its prefix look like a brand-new model with no history,
# which would silently drop it out of ``known_local``.
#
# ``known_local`` is the self-calibrating denominator described in the module
# docstring: a model earns a row here by having reported a split at least once
# in the learning window. The outer query then measures how well the CURRENT
# window is covered for exactly those models.
#
# success = true AND output_tokens > 0 excludes rows whose NULL is correct:
# a call that errored (GPU-lock timeout, connection refused) or produced no
# tokens never entered a decode phase, so counting it as a miss would page on
# a busy GPU rather than on a broken seam.
_COVERAGE_QUERY = """
    WITH normalized AS (
        SELECT
            regexp_replace(model, '^ollama(_chat)?/', '') AS model,
            decode_duration_ms,
            cost_type,
            success,
            output_tokens,
            created_at
        FROM cost_logs
    ),
    known_local AS (
        SELECT DISTINCT model
        FROM normalized
        WHERE decode_duration_ms IS NOT NULL
          AND created_at >= NOW() - ($1 * INTERVAL '1 day')
    )
    SELECT
        c.model                                        AS model,
        COUNT(*)                                       AS total,
        COUNT(c.decode_duration_ms)                    AS covered
    FROM normalized c
    JOIN known_local k ON k.model = c.model
    WHERE c.cost_type = 'inference'
      AND c.success = true
      AND c.output_tokens > 0
      AND c.created_at >= NOW() - ($2 * INTERVAL '1 hour')
    GROUP BY c.model
    ORDER BY COUNT(*) DESC
"""


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    return site_config.get_int(key, default) if site_config is not None else default


def _pct(covered: int, total: int) -> float:
    """Coverage percentage. ``total`` is never 0 — callers filter empty groups."""
    return round(100.0 * covered / total, 1)


def _build_body(
    offenders: list[dict[str, Any]],
    *,
    overall_covered: int,
    overall_total: int,
    window_hours: int,
    min_coverage: int,
) -> str:
    lines = [
        f"Ollama decode/prefill timing capture is below "
        f"{min_coverage}% on {len(offenders)} model(s) over the last "
        f"{window_hours}h — overall {_pct(overall_covered, overall_total)}% "
        f"({overall_covered}/{overall_total} eligible calls).",
        "",
        "`cost_logs.decode_duration_ms` is populated by a monkey-patch over "
        "LiteLLM's Ollama `transform_response` "
        "(`services/llm_providers/ollama_timings.py`). The patch is fail-open, "
        "so a LiteLLM upgrade that moves those internals stops the capture "
        "**silently** — true decode tok/s just stops being recorded while every "
        "LLM call keeps succeeding.",
        "",
        "Only models that have reported a split before are counted, and only "
        "successful calls that produced tokens — so this is not a busy-GPU or "
        "cloud-model artifact.",
        "",
        "**Per model (this window):**",
    ]
    for row in offenders:
        lines.append(
            f"- `{row['model']}` — {row['coverage_pct']}% "
            f"({row['covered']}/{row['total']})"
        )
    lines += [
        "",
        "First check: does `[ollama_timings] decode/prefill timing capture "
        "installed` still appear in the worker log, and did `litellm` change "
        "version recently?",
    ]
    return "\n".join(lines)


class ProbeDecodeSplitCoverageJob:
    """Emit a finding when Ollama decode-split capture regresses."""

    name = "probe_decode_split_coverage"
    description = (
        "Alert when cost_logs.decode_duration_ms coverage drops on models that "
        "previously reported an Ollama timing split (capture-seam watchdog)"
    )
    schedule = "every 6 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="probe disabled", changes_made=0)

        window_hours = _cfg_int(site_config, _WINDOW_HOURS_KEY, _DEFAULT_WINDOW_HOURS)
        learn_days = _cfg_int(site_config, _LEARN_DAYS_KEY, _DEFAULT_LEARN_DAYS)
        min_coverage = _cfg_int(site_config, _MIN_COVERAGE_KEY, _DEFAULT_MIN_COVERAGE)
        min_sample = _cfg_int(site_config, _MIN_SAMPLE_KEY, _DEFAULT_MIN_SAMPLE)

        try:
            async with pool.acquire() as conn:
                rows = [
                    dict(r)
                    for r in await conn.fetch(_COVERAGE_QUERY, learn_days, window_hours)
                ]
        except Exception as e:  # noqa: BLE001 — a probe must never crash a cycle
            detail = f"query failed: {describe_exception(e)}"
            logger.warning("[probe_decode_split_coverage] %s", detail)
            return JobResult(ok=False, detail=detail, changes_made=0)

        overall_total = sum(int(r["total"]) for r in rows)
        overall_covered = sum(int(r["covered"]) for r in rows)

        # Below the sample floor the percentage is noise — a quiet window on a
        # single model would page at 0/1. Report, don't alert.
        if overall_total < min_sample:
            return JobResult(
                ok=True,
                detail=(
                    f"only {overall_total} eligible call(s) in the last "
                    f"{window_hours}h (floor {min_sample}) — no verdict"
                ),
                changes_made=0,
                metrics={"eligible_calls": overall_total, "below_sample_floor": True},
            )

        offenders = []
        for r in rows:
            total = int(r["total"])
            covered = int(r["covered"])
            pct = _pct(covered, total)
            if pct < min_coverage:
                offenders.append(
                    {
                        "model": r["model"],
                        "total": total,
                        "covered": covered,
                        "coverage_pct": pct,
                    },
                )

        overall_pct = _pct(overall_covered, overall_total)
        metrics = {
            "eligible_calls": overall_total,
            "covered_calls": overall_covered,
            "coverage_pct": overall_pct,
            "models_tracked": len(rows),
            "models_below_threshold": len(offenders),
        }

        if not offenders:
            return JobResult(
                ok=True,
                detail=(
                    f"decode-split coverage {overall_pct}% across "
                    f"{len(rows)} model(s) — all above {min_coverage}%"
                ),
                changes_made=0,
                metrics=metrics,
            )

        emit_finding(
            source="decode_split_coverage_probe",
            kind=_FINDING_KIND,
            title=(
                f"Ollama decode-split capture degraded on "
                f"{len(offenders)} model(s) ({overall_pct}% overall)"
            ),
            body=_build_body(
                offenders,
                overall_covered=overall_covered,
                overall_total=overall_total,
                window_hours=window_hours,
                min_coverage=min_coverage,
            ),
            severity="warn",
            dedup_key=_FINDING_KIND,
            extra={
                "overall_coverage_pct": overall_pct,
                "window_hours": window_hours,
                "min_coverage_pct": min_coverage,
                "models": offenders,
            },
        )
        logger.warning(
            "[probe_decode_split_coverage] %d model(s) below %d%% coverage "
            "(overall %.1f%%)",
            len(offenders), min_coverage, overall_pct,
        )
        return JobResult(
            ok=True,
            detail=(
                f"emitted finding — {len(offenders)} model(s) below "
                f"{min_coverage}% decode-split coverage"
            ),
            changes_made=0,
            metrics=metrics,
        )
