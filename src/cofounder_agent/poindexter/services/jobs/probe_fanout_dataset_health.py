"""ProbeFanoutDatasetHealthJob — watch the featured fan-out's TRAINING DATA.

The fan-out (``services/image_fanout.py``) is Phase 1 of the multi-provider
image plan, and its stated purpose is to collect the dataset the Phase-2
class→provider router is seeded from. Images shipping correctly to readers
says nothing about whether that dataset measures what the router needs, and
the 2026-08-27 audit (poindexter#1032) found it did not:

* **27.0%** of candidate scores came back ``unparseable vision response`` —
  the judge's ``<think>`` trace sharing the token budget with its JSON answer.
  In 13 of 48 rows that left exactly ONE scored candidate, so the recorded win
  reflected *judge availability*, not image quality.
* Losing candidates were written to the worker's ``/tmp`` and never referenced
  again, so "did the judge's score match the image?" was unanswerable for every
  row collected.
* Net effect: only **42%** of recorded wins were comparative judge preferences.
  The rest were resolved by ``image_fanout_priority`` order or by the judge
  being down.

Both defects were fixed (candidates persist to R2 with a per-candidate ``url``
from 2026-08-31; the unscored rate is now 0.5%, and comparative preference is
66%). This probe exists because **nothing watched either of them** — the issue's
own closing ask was that the unscored rate "be tracked as a metric rather than
rediscovered by audit", since today a total judge failure and a healthy run
look identical in the row.

Four signals, all read from the ``image_fanout_judged`` rows themselves:

1. **unscored rate** — the judge-truncation regression, the one that already
   happened.
2. **candidate-url coverage** — the retention regression. A fan-out whose
   outputs are unauditable produces a router dataset nobody can check.
3. **comparative-preference rate** — the metric that actually answers "is this
   dataset worth training on?". Reported always, alerted never: a low rate can
   mean a miscalibrated judge (ties broken by priority order) rather than a
   broken one, and that is a prompt-calibration decision an operator makes, not
   a page.
4. **text-scan unverified rate** (2026-09-23) — every candidate is OCR-scanned
   by ``services/image_text_scan.py`` before judging, so no model is held to a
   no-text rule its rivals skip. That scan fails *open* by design (an
   unavailable scanner must not become "no hero image"), which means a dark
   scanner and a clean dataset look identical in the row counts. This is the
   watcher: candidates whose scan came back ``unavailable`` — or, in a row
   written after the scan existed, carry no scan at all — are counted as
   unverified. A check that scanned nothing has not passed.

Below ``image_fanout_probe_min_sample`` candidates the window has no verdict —
the fan-out runs a few times a day, so a quiet week must report rather than
page at 0/2.

Issue: Glad-Labs/poindexter#1032.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)

_ENABLED_KEY = "image_fanout_probe_enabled"
_WINDOW_HOURS_KEY = "image_fanout_probe_window_hours"
_MIN_SAMPLE_KEY = "image_fanout_probe_min_sample"
_MAX_UNSCORED_KEY = "image_fanout_probe_max_unscored_pct"
_MIN_URL_COVERAGE_KEY = "image_fanout_probe_min_url_coverage_pct"
_MAX_TEXT_SCAN_UNVERIFIED_KEY = "image_fanout_probe_max_text_scan_unavailable_pct"

# The fan-out produces ~3 rows/day, so a 24h window would sit under any useful
# sample floor. A week is the smallest window that carries a verdict.
_DEFAULT_WINDOW_HOURS = 168
_DEFAULT_MIN_SAMPLE = 20
_DEFAULT_MAX_UNSCORED_PCT = 10
_DEFAULT_MIN_URL_COVERAGE_PCT = 95
_DEFAULT_MAX_TEXT_SCAN_UNVERIFIED_PCT = 10

_FINDING_KIND = "image_fanout_dataset_degraded"

# Per-row shape, aggregated in Python so the provenance rules stay readable and
# testable. ``scores`` is the scored candidates only — a NULL score is the
# unscored case this probe is counting, not a zero.
_ROWS_QUERY = """
    SELECT
        COALESCE((details->>'judge_ran')::boolean, false) AS judge_ran,
        jsonb_array_length(COALESCE(details->'candidates', '[]'::jsonb)) AS n_candidates,
        COALESCE((
            SELECT array_agg((c->>'score')::numeric)
              FROM jsonb_array_elements(COALESCE(details->'candidates', '[]'::jsonb)) c
             WHERE c->>'score' IS NOT NULL
        ), ARRAY[]::numeric[]) AS scores,
        COALESCE((
            SELECT count(*)
              FROM jsonb_array_elements(COALESCE(details->'candidates', '[]'::jsonb)) c
             WHERE c->>'url' IS NOT NULL AND c->>'url' <> ''
        ), 0) AS candidates_with_url,
        -- Rows written since every candidate is text-scanned carry an
        -- `excluded` array (possibly empty). Only those rows can be held to
        -- "every candidate has a scan"; older rows predate the scan.
        (details ? 'excluded') AS text_scan_era,
        jsonb_array_length(COALESCE(details->'excluded', '[]'::jsonb)) AS n_excluded,
        COALESCE((
            SELECT count(*)
              FROM jsonb_array_elements(
                     COALESCE(details->'candidates', '[]'::jsonb)
                     || COALESCE(details->'excluded', '[]'::jsonb)) c
             -- `disabled` is an operator choice (image_ocr_gate_enabled off),
             -- not a scanner fault — out of the denominator.
             WHERE COALESCE(c->'text_scan'->>'status', '') <> 'disabled'
        ), 0) AS text_scan_expected,
        COALESCE((
            SELECT count(*)
              FROM jsonb_array_elements(
                     COALESCE(details->'candidates', '[]'::jsonb)
                     || COALESCE(details->'excluded', '[]'::jsonb)) c
             WHERE c->'text_scan' IS NULL
                OR jsonb_typeof(c->'text_scan') = 'null'
                OR c->'text_scan'->>'status' = 'unavailable'
        ), 0) AS text_scan_unverified
      FROM audit_log
     WHERE event_type = 'image_fanout_judged'
       AND timestamp >= NOW() - ($1 * INTERVAL '1 hour')
"""


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    return site_config.get_int(key, default) if site_config is not None else default


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def classify_row(judge_ran: bool, scores: list[float]) -> str:
    """How this row's winner was actually decided.

    Only ``judge_win`` is a comparative preference — the rest record a winner
    chosen by ``image_fanout_priority`` order or by there being nothing to
    compare against, and they enter the router dataset looking identical.
    """
    if not judge_ran:
        return "judge_down"
    if not scores:
        return "no_scores"
    if len(scores) == 1:
        return "only_scored_candidate"
    top = max(scores)
    if sum(1 for s in scores if s == top) > 1:
        return "tie_broken_by_priority"
    return "judge_win"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Dataset-health metrics for one window."""
    provenance: dict[str, int] = {}
    total_candidates = scored_candidates = with_url = 0
    scan_expected = scan_unverified = excluded = 0
    for r in rows:
        scores = [float(s) for s in (r.get("scores") or [])]
        n = int(r.get("n_candidates") or 0)
        total_candidates += n
        scored_candidates += len(scores)
        with_url += int(r.get("candidates_with_url") or 0)
        if r.get("text_scan_era"):
            scan_expected += int(r.get("text_scan_expected") or 0)
            scan_unverified += int(r.get("text_scan_unverified") or 0)
            excluded += int(r.get("n_excluded") or 0)
        if n == 0:
            # Every rendered candidate was excluded by the text scan: there
            # was no contest, so this row is neither a judge win nor a
            # judge-down row and must not dilute either.
            kind = "no_contest"
        else:
            kind = classify_row(bool(r.get("judge_ran")), scores)
        provenance[kind] = provenance.get(kind, 0) + 1
    unscored = total_candidates - scored_candidates
    n_rows = len(rows)
    return {
        "rows": n_rows,
        "candidates": total_candidates,
        "unscored_candidates": unscored,
        "unscored_pct": _pct(unscored, total_candidates),
        "url_coverage_pct": _pct(with_url, total_candidates),
        "judge_win_pct": _pct(provenance.get("judge_win", 0), n_rows),
        "text_scan_expected": scan_expected,
        "text_scan_unverified": scan_unverified,
        "text_scan_unverified_pct": _pct(scan_unverified, scan_expected),
        "text_scan_excluded": excluded,
        "provenance": provenance,
    }


def _build_body(
    stats: dict[str, Any], breaches: list[str], *, window_hours: int,
) -> str:
    prov = ", ".join(f"{k}={v}" for k, v in sorted(stats["provenance"].items()))
    return "\n".join([
        "The featured fan-out's router training data degraded over the last "
        f"{window_hours}h:",
        "",
        *[f"- {b}" for b in breaches],
        "",
        f"Window: {stats['rows']} judged row(s), {stats['candidates']} candidate(s).",
        f"Comparative judge preference: {stats['judge_win_pct']}% of rows ({prov}).",
        "",
        "Why this matters: the fan-out exists to collect the dataset the "
        "Phase-2 class→provider router is seeded from. Images still ship "
        "correctly to readers — this is a data-integrity signal, and every "
        "day of continued collection adds contaminated rows.",
        "",
        "Unverified text scans mean candidates competed without the no-text "
        "check: POST /scan on the image-gen server is unreachable, or the "
        "running image-gen image predates it (rebuild "
        "poindexter-image-gen-server).",
        "",
        "An unscored spike is usually the vision judge's `<think>` trace "
        "sharing its token budget with the JSON answer: check "
        "`image_fanout_judge_max_tokens` and `qa_vision_model`. Missing "
        "candidate URLs mean losing renders are no longer retrievable, which "
        "makes the judge's scores unauditable after the fact.",
    ])


class ProbeFanoutDatasetHealthJob:
    """Emit a finding when the fan-out's training data stops being trustworthy."""

    name = "probe_fanout_dataset_health"
    description = (
        "Alert when the featured fan-out's judged-row dataset degrades — "
        "unscored candidate rate, candidate-URL retention, and the share of "
        "wins that are real judge preferences (poindexter#1032)"
    )
    schedule = "every 12 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="probe disabled", changes_made=0)

        window_hours = _cfg_int(site_config, _WINDOW_HOURS_KEY, _DEFAULT_WINDOW_HOURS)
        min_sample = _cfg_int(site_config, _MIN_SAMPLE_KEY, _DEFAULT_MIN_SAMPLE)
        max_unscored = _cfg_int(site_config, _MAX_UNSCORED_KEY, _DEFAULT_MAX_UNSCORED_PCT)
        min_url_cov = _cfg_int(
            site_config, _MIN_URL_COVERAGE_KEY, _DEFAULT_MIN_URL_COVERAGE_PCT
        )
        max_scan_unverified = _cfg_int(
            site_config, _MAX_TEXT_SCAN_UNVERIFIED_KEY,
            _DEFAULT_MAX_TEXT_SCAN_UNVERIFIED_PCT,
        )

        try:
            async with pool.acquire() as conn:
                rows = [dict(r) for r in await conn.fetch(_ROWS_QUERY, window_hours)]
        except Exception as e:  # noqa: BLE001 — a probe must never crash a cycle
            detail = f"query failed: {describe_exception(e)}"
            logger.warning("[probe_fanout_dataset_health] %s", detail)
            return JobResult(ok=False, detail=detail, changes_made=0)

        stats = summarize(rows)
        metrics = {k: v for k, v in stats.items() if k != "provenance"}
        metrics.update({f"rows_{k}": v for k, v in stats["provenance"].items()})

        if stats["candidates"] < min_sample:
            return JobResult(
                ok=True,
                detail=(
                    f"only {stats['candidates']} candidate(s) in the last "
                    f"{window_hours}h (floor {min_sample}) — no verdict"
                ),
                changes_made=0,
                metrics={**metrics, "below_sample_floor": True},
            )

        breaches: list[str] = []
        if stats["unscored_pct"] > max_unscored:
            breaches.append(
                f"{stats['unscored_pct']}% of candidate scores unparseable "
                f"({stats['unscored_candidates']}/{stats['candidates']}), "
                f"threshold {max_unscored}%"
            )
        if stats["url_coverage_pct"] < min_url_cov:
            breaches.append(
                f"only {stats['url_coverage_pct']}% of candidates carry a "
                f"retrievable URL, threshold {min_url_cov}% — losing renders "
                "are not auditable"
            )
        # Own sample floor: during rollout the window mixes pre-scan rows,
        # and a handful of scanned candidates must not page at 1/2.
        if (
            stats["text_scan_expected"] >= min_sample
            and stats["text_scan_unverified_pct"] > max_scan_unverified
        ):
            breaches.append(
                f"{stats['text_scan_unverified_pct']}% of candidates competed "
                f"without a verified text scan "
                f"({stats['text_scan_unverified']}/{stats['text_scan_expected']}), "
                f"threshold {max_scan_unverified}% — the no-text rule is not "
                "being applied"
            )

        if not breaches:
            return JobResult(
                ok=True,
                detail=(
                    f"fan-out dataset healthy — {stats['unscored_pct']}% unscored, "
                    f"{stats['url_coverage_pct']}% url coverage, "
                    f"{stats['judge_win_pct']}% comparative wins, "
                    f"{stats['text_scan_unverified_pct']}% text scans unverified "
                    f"over {stats['rows']} row(s)"
                ),
                changes_made=0,
                metrics=metrics,
            )

        emit_finding(
            source="fanout_dataset_health_probe",
            kind=_FINDING_KIND,
            title=(
                f"Featured fan-out dataset degraded — "
                f"{stats['unscored_pct']}% unscored, "
                f"{stats['url_coverage_pct']}% url coverage"
            ),
            body=_build_body(stats, breaches, window_hours=window_hours),
            severity="warn",
            dedup_key=_FINDING_KIND,
            extra={**metrics, "provenance": stats["provenance"]},
        )
        logger.warning(
            "[probe_fanout_dataset_health] %d breach(es): %s",
            len(breaches), "; ".join(breaches),
        )
        return JobResult(
            ok=True,
            detail=f"emitted finding — {len(breaches)} threshold breach(es)",
            changes_made=0,
            metrics=metrics,
        )
