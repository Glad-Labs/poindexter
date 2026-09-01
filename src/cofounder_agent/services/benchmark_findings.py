"""Benchmark findings — turn our own instrumentation into writable claims.

The pipeline does not lack writing ability; it lacks *inputs*. A post that
restates what already exists on the internet earns nothing no matter how well
it is written, and uniqueness is a property of information, not of who typed
it. This module is the input side of that fix: it reads the telemetry we
already collect and proposes a topic only when the data supports a claim
nobody else can make.

The corpus is ``cost_logs``. Since 2026-08-26 every Ollama-routed call records
both wall-clock duration and Ollama's true decode duration
(``decode_duration_ms``, see [decode-split-capture.md]), so we can measure the
gap between what a model *decodes* at and what the application actually
*receives*. Every benchmark on the internet publishes the first number. The
second one requires months of real multi-model workload on one contended box
with per-call instrumentation, which is why almost nobody has it.

**A finding is not "a number exists".** Three bars, all DB-tunable, because a
claim published on thin evidence is worse than no post:

1. **Sample floor** — a model needs ``min_calls`` measured calls. Below that a
   median is an anecdote.
2. **Breadth** (fleet finding only) — a comparison needs ``min_models``.
3. **Effect** (fleet finding only) — the spread between best and worst must
   clear ``min_spread_pct``. "All our models behave about the same" is true,
   dull, and not worth a post.

Two kinds, chosen because they have honest, self-limiting cadence:

- ``fleet_residency_tax`` — the comparative table. Re-proposable on a cooldown
  as the fleet changes.
- ``new_model_throughput`` — a model that has newly crossed the sample floor.
  Fires once per model, which is exactly when "nobody has these numbers yet"
  is most true.

**The rendered fact block is the deliverable, not the title.** It becomes the
task's caller-attached ``research_context`` (layer 1 of
``writer_core._collect_research_context``), which means it both grounds the
writer AND becomes the corpus that ``qa.numeric_fidelity`` checks the finished
draft against. A number the writer invents will not reconcile with it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

KIND_FLEET = "fleet_residency_tax"
KIND_NEW_MODEL = "new_model_throughput"

# Normalized per-model throughput over the window. `decode` is Ollama's own
# eval_duration; `wall` is what the caller received. The house
# ^ollama(_chat)?/ strip keeps one engine from splitting across spellings
# (docs/architecture/cost-logs-model-identity.md).
_MEASURE_SQL = """
    WITH normalized AS (
        SELECT
            regexp_replace(model, '^ollama(_chat)?/', '') AS model,
            output_tokens, duration_ms, decode_duration_ms, created_at
        FROM cost_logs
        WHERE cost_type = 'inference'
          AND success = true
          AND output_tokens > $1
          AND decode_duration_ms > 0
          AND duration_ms > 0
          AND created_at >= NOW() - ($2 * INTERVAL '1 day')
    )
    SELECT
        model,
        COUNT(*)                                                   AS calls,
        MIN(created_at)::date                                      AS first_seen,
        MAX(created_at)::date                                      AS last_seen,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
            ORDER BY output_tokens / (decode_duration_ms / 1000.0))::numeric, 1) AS decode_tps,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
            ORDER BY output_tokens / (duration_ms / 1000.0))::numeric, 1)        AS wall_tps,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
            ORDER BY (duration_ms - decode_duration_ms))::numeric, 0)            AS overhead_ms
    FROM normalized
    GROUP BY model
    HAVING COUNT(*) >= $3
    ORDER BY COUNT(*) DESC
"""


@dataclass
class ModelMeasurement:
    model: str
    calls: int
    decode_tps: float
    wall_tps: float
    overhead_ms: float
    first_seen: str = ""
    last_seen: str = ""

    @property
    def tax_pct(self) -> float:
        """Share of decode throughput the caller never sees."""
        if self.decode_tps <= 0:
            return 0.0
        return round((1.0 - self.wall_tps / self.decode_tps) * 100.0, 1)


@dataclass
class Finding:
    kind: str
    subject: str            # model name for new_model; "" for the fleet finding
    title: str
    fact_block: str
    keywords: list[str] = field(default_factory=list)
    measurements: list[ModelMeasurement] = field(default_factory=list)


def _fmt(value: float) -> str:
    return f"{value:g}"


async def measure_models(
    pool: Any,
    *,
    window_days: int,
    min_calls: int,
    min_output_tokens: int = 50,
) -> list[ModelMeasurement]:
    """Per-model decode-vs-delivered medians over the window. Read-only."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(_MEASURE_SQL, min_output_tokens, window_days, min_calls)
    out: list[ModelMeasurement] = []
    for r in rows:
        try:
            out.append(
                ModelMeasurement(
                    model=r["model"],
                    calls=int(r["calls"]),
                    decode_tps=float(r["decode_tps"]),
                    wall_tps=float(r["wall_tps"]),
                    overhead_ms=float(r["overhead_ms"] or 0),
                    first_seen=str(r["first_seen"] or ""),
                    last_seen=str(r["last_seen"] or ""),
                ),
            )
        except (TypeError, ValueError) as e:
            # A degenerate row (NULL percentile on a single odd call) is
            # dropped rather than published as a zero. WARNING, not debug:
            # dropping a row shrinks the sample behind a claim we are about to
            # publish, and Loki only ships INFO and above — a debug line here
            # would make the shrinkage invisible in production.
            logger.warning(
                "[benchmark_findings] dropped an unusable measurement row "
                "(%s) — the sample behind any finding is correspondingly "
                "smaller: %r", e, dict(r),
            )
    return out


def render_fleet_fact_block(
    measurements: list[ModelMeasurement], *, window_days: int,
) -> str:
    """The measurements, stated plainly enough to be checked line by line.

    This becomes ``research_context``, so it is written for two readers: the
    writer LLM that must ground its prose in it, and ``qa.numeric_fidelity``,
    which will extract every number here and require the draft's attributed
    figures to reconcile against them.
    """
    total = sum(m.calls for m in measurements)
    ordered = sorted(measurements, key=lambda m: m.tax_pct, reverse=True)
    lines = [
        "MEASURED DATA — Glad Labs local inference fleet.",
        "",
        f"Source: our own cost_logs table, {total} instrumented production "
        f"calls over the last {window_days} days. Every figure below is a "
        "median over real pipeline work, not a synthetic benchmark.",
        "",
        "Two different numbers are recorded per call. 'Decode speed' is the "
        "rate Ollama reports for token generation alone (its eval_duration). "
        "'Delivered speed' is what the calling application actually received, "
        "measured wall-clock, and therefore includes queue wait and the cost "
        "of loading the model back into VRAM when it had been evicted.",
        "",
    ]
    for m in ordered:
        lines.append(
            f"- {m.model}: decode {_fmt(m.decode_tps)} tokens/second, "
            f"delivered {_fmt(m.wall_tps)} tokens/second "
            f"({_fmt(m.tax_pct)}% of decode throughput never reaches the "
            f"caller). Median overhead {_fmt(m.overhead_ms)} ms per call. "
            f"Measured over {m.calls} calls.",
        )
    if ordered:
        worst, best = ordered[0], ordered[-1]
        lines += [
            "",
            f"The spread is the finding: {worst.model} loses "
            f"{_fmt(worst.tax_pct)}% of its decode throughput while "
            f"{best.model} loses only {_fmt(best.tax_pct)}%, on the same "
            "hardware.",
            "",
            "CAUSAL NOTE — do not write this up as one model being slower "
            "than another. The mechanism is residency: a model that stays "
            "resident in VRAM answers immediately, while an intermittently "
            "called model is evicted and must be reloaded on almost every "
            "call. The correct claim is about what a workload delivers given "
            "how often each model is resident.",
        ]
    return "\n".join(lines)


def render_new_model_fact_block(
    m: ModelMeasurement, *, window_days: int,
) -> str:
    return "\n".join([
        f"MEASURED DATA — first throughput numbers for {m.model} on the "
        "Glad Labs local inference fleet.",
        "",
        f"Source: our own cost_logs table, {m.calls} instrumented production "
        f"calls between {m.first_seen} and {m.last_seen}. Medians over real "
        "pipeline work, not a synthetic benchmark.",
        "",
        f"- Decode speed (Ollama eval_duration alone): {_fmt(m.decode_tps)} "
        "tokens/second.",
        f"- Delivered speed (wall-clock, what the caller received): "
        f"{_fmt(m.wall_tps)} tokens/second.",
        f"- {_fmt(m.tax_pct)}% of decode throughput never reaches the caller.",
        f"- Median per-call overhead: {_fmt(m.overhead_ms)} ms.",
        "",
        "CAUSAL NOTE — the gap is residency (eviction and reload from VRAM), "
        "not a property of the model's speed. Write it as what this workload "
        "delivers, not as a verdict on the model.",
    ])


def build_findings(
    measurements: list[ModelMeasurement],
    *,
    window_days: int,
    min_models: int,
    min_spread_pct: float,
    new_model_names: set[str] | None = None,
) -> list[Finding]:
    """Apply the significance bars and render whatever clears them. Pure."""
    findings: list[Finding] = []
    if not measurements:
        return findings

    taxes = [m.tax_pct for m in measurements]
    spread = max(taxes) - min(taxes)
    if len(measurements) >= min_models and spread >= min_spread_pct:
        findings.append(
            Finding(
                kind=KIND_FLEET,
                subject="",
                title=(
                    "What local models actually deliver versus their "
                    "benchmark decode speed"
                ),
                fact_block=render_fleet_fact_block(
                    measurements, window_days=window_days,
                ),
                keywords=[
                    "local llm throughput", "tokens per second",
                    "ollama performance", "model residency", "vram eviction",
                ],
                measurements=list(measurements),
            ),
        )
    else:
        logger.info(
            "[benchmark_findings] fleet finding below bar "
            "(models=%d/%d, spread=%.1f/%.1f)",
            len(measurements), min_models, spread, min_spread_pct,
        )

    # ``None`` means "the caller did not determine which models are new", and
    # the safe reading of that is NONE of them. Treating it as "all of them"
    # would make a bare build_findings() call propose a first-numbers post for
    # every model on the fleet, including ones running for months.
    new_names = new_model_names or set()
    for m in measurements:
        if m.model not in new_names:
            continue
        findings.append(
            Finding(
                kind=KIND_NEW_MODEL,
                subject=m.model,
                title=(
                    f"{m.model} on a local fleet: measured decode speed "
                    "versus what the caller receives"
                ),
                fact_block=render_new_model_fact_block(m, window_days=window_days),
                keywords=[
                    m.model, "tokens per second", "local llm benchmark",
                    "ollama throughput",
                ],
                measurements=[m],
            ),
        )
    return findings


__all__ = [
    "KIND_FLEET",
    "KIND_NEW_MODEL",
    "Finding",
    "ModelMeasurement",
    "build_findings",
    "measure_models",
    "render_fleet_fact_block",
    "render_new_model_fact_block",
]
