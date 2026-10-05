"""Writer-authored data charts — a chart whose every number is in the research.

The chart catalog (``services.chart_catalog``) lets the writer name a chart
that code builds from our own telemetry. It holds one chart, so every post that
wanted a chart got the same one (10 posts in 30 days, three of them within a
week, 2026-10-05). Most posts in the hardware/AI niche carry figures worth
plotting — VRAM per card, benchmark scores, prices — that no catalog entry
could ever hold, because they come from the post's own research rather than
from our database.

This module lets the writer propose such a chart with a ``[DATA-CHART: …]``
marker and accepts it only if **every value is stated in the research, next
to its own label**. "The research" is the task's ``research_context``: the
two-pass writer sees it as its SOURCES section, and ``qa.numeric_fidelity``
checks the prose against the same text. A figure the writer knows only from
its own training — however true — has no source here and is never charted:

    [DATA-CHART: bar | VRAM by card | GB | RTX 5090 = 32; RTX 4090 = 24]

* **Value in the corpus** — the same extraction and precision rule as
  ``qa.numeric_fidelity`` (``services.numeric_fidelity``): a source number,
  rounded to the decimals the writer wrote, must equal the value.
* **The number NEAREST its label** — the label must appear in the research,
  and at one of its occurrences the closest number (within
  ``data_chart_label_window_chars``) must be the value. Merely nearby is not
  enough: in "16,000 requests from more than 4,000 users" both numbers are
  near both labels, and a window test passed a chart that swapped them.

One failed point drops the whole chart; nothing is drawn from a guess. A wrong
chart is worse than none (the chart-catalog rule), and a chart reads as
measured fact in a way prose does not.

There is no query surface here: the data comes from the marker and is checked
against text already in the pipeline state, so a writer-emitted marker cannot
reach the database (the ``ChartProvider`` / screenshot-allowlist seam rule).
Pure functions only; ``content.generate_images`` calls them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from poindexter.services.chart_render import ChartSpec, Series
from poindexter.services.numeric_fidelity import (
    DEFAULT_UNITS,
    _decimals,
    _matches,
    _to_float,
)

logger = logging.getLogger(__name__)

#: Plan-target prefix that routes a slot here instead of to the catalog.
DATA_TARGET_PREFIX = "data:"

_FORMS = ("bar", "line")
_VALUE_RE = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$|^\d+(?:\.\d+)?$")

ENABLED_SETTING = "writer_data_charts_enabled"
MAX_POINTS_SETTING = "data_chart_max_points"
WINDOW_SETTING = "data_chart_label_window_chars"
_DEFAULT_MAX_POINTS = 10
_DEFAULT_WINDOW_CHARS = 240


@dataclass(frozen=True)
class DataPoint:
    label: str
    token: str  # the value exactly as written, so precision is the writer's

    @property
    def value(self) -> float:
        return float(self.token.replace(",", ""))


@dataclass(frozen=True)
class DataChartDraft:
    form: str
    title: str
    unit: str
    points: tuple[DataPoint, ...]


@dataclass
class Verdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def _setting(site_config: Any, key: str, default: Any) -> Any:
    if site_config is None:
        return default
    try:
        value = site_config.get(key, default)
    except Exception:  # noqa: BLE001 — silent-ok: an unreadable tunable falls back to its documented default
        return default
    return default if value in (None, "") else value


def enabled(site_config: Any, niche_slug: str | None = None) -> bool:
    """Whether the writer is offered ``[DATA-CHART:]`` for this post.

    ``niche.<slug>.writer_data_charts_enabled`` overrides the global
    ``writer_data_charts_enabled`` when set, so a niche whose research is
    rarely numeric can switch it off without touching the others.
    """
    raw = None
    if niche_slug:
        raw = _setting(site_config, f"niche.{niche_slug}.{ENABLED_SETTING}", None)
    if raw is None:
        raw = _setting(site_config, ENABLED_SETTING, "true")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def prompt_block(site_config: Any, niche_slug: str | None = None) -> str:
    """The writer-prompt paragraph offering ``[DATA-CHART:]``, or ``""`` when off.

    Rendered by code (not baked into SKILL.md) so a niche that switches the
    feature off is never told about a marker that would do nothing.
    """
    if not enabled(site_config, niche_slug):
        return ""
    max_points = int(_setting(site_config, MAX_POINTS_SETTING, _DEFAULT_MAX_POINTS))
    return (
        "- DATA CHARTS. When a section compares several figures that the "
        "SOURCES state — specs, scores, prices, counts — place one data chart "
        "on its own line:\n"
        "  [DATA-CHART: bar | <title> | <unit> | <label> = <number>; <label> = <number>]\n"
        "  Use bar for categories and line for change over time, 2 to "
        f"{max_points} points, each label written exactly as the SOURCES name it "
        "and each number exactly as they state it. The chart is drawn only when "
        "every number is found in the SOURCES beside its label, so chart figures "
        "you can quote from them, and leave a post without a data chart when the "
        "SOURCES give no figures to plot."
    )


def parse(payload: str, *, max_points: int = _DEFAULT_MAX_POINTS) -> DataChartDraft | None:
    """``"bar | Title | GB | A = 1; B = 2"`` → :class:`DataChartDraft`, or None.

    Strict on purpose: anything malformed — unknown form, empty title, a value
    that is not a plain number, a duplicate label, fewer than two points or
    more than ``max_points`` — yields None, and the slot renders no image.
    """
    parts = [p.strip() for p in (payload or "").split("|")]
    if len(parts) != 4:
        return None
    form, title, unit, body = parts
    form = form.lower()
    if form not in _FORMS or not title:
        return None
    points: list[DataPoint] = []
    seen: set[str] = set()
    for raw in body.split(";"):
        raw = raw.strip()
        if not raw:
            continue
        if "=" not in raw:
            return None
        label, _, token = raw.rpartition("=")
        label, token = label.strip(), token.strip()
        if not label or not _VALUE_RE.match(token):
            return None
        key = label.lower()
        if key in seen:
            return None
        seen.add(key)
        points.append(DataPoint(label=label, token=token))
    if not 2 <= len(points) <= max_points:
        return None
    return DataChartDraft(form=form, title=title, unit=unit, points=tuple(points))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def _label_spans(corpus_norm: str, label: str) -> list[tuple[int, int]]:
    needle = _normalize(label).strip()
    if not needle:
        return []
    return [(m.start(), m.end()) for m in re.finditer(re.escape(needle), corpus_norm)]


_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _numbers_with_spans(
    text: str, unit_set: set[str],
) -> list[tuple[int, int, str]]:
    """Every value in ``text`` with its position, filtered the way
    ``numeric_fidelity.extract_corpus_numbers`` filters: digits fused to a
    preceding letter are an identifier (``qwen2.5``), and digits followed by
    letters are one too unless the letters are a known unit (``32GB`` is a
    quantity, ``14b`` a name)."""
    out: list[tuple[int, int, str]] = []
    for m in _NUMBER_RE.finditer(text):
        start, end = m.start(), m.end()
        if start > 0 and text[start - 1].isalpha():
            continue
        trailing = re.match(r"[A-Za-z/]+", text[end:])
        if trailing and trailing.group(0).lower().rstrip("/") not in unit_set:
            continue
        out.append((start, end, m.group(0)))
    return out


def _nearest_tokens(
    span: tuple[int, int], numbers: list[tuple[int, int, str]], window_chars: int,
) -> list[str]:
    """The number(s) closest to a label occurrence, within ``window_chars``.

    Distance runs from the label's edge to the number's edge, so "16,000
    requests" binds 16,000 to *requests* even when "4,000 users" sits fifteen
    characters further on. Digits inside the label itself (the 4090 of "RTX
    4090") never count. Ties keep every tied number.
    """
    l_start, l_end = span
    best: int | None = None
    tokens: list[str] = []
    for n_start, n_end, token in numbers:
        if n_end <= l_start:
            dist = l_start - n_end
        elif n_start >= l_end:
            dist = n_start - l_end
        else:
            continue  # inside the label
        if dist > window_chars:
            continue
        if best is None or dist < best:
            best, tokens = dist, [token]
        elif dist == best:
            tokens.append(token)
    return tokens


def verify(
    draft: DataChartDraft,
    research_context: str,
    *,
    window_chars: int = _DEFAULT_WINDOW_CHARS,
    units: tuple[str, ...] | list[str] = DEFAULT_UNITS,
) -> Verdict:
    """Every point's value must be the research's number for its label.

    For some occurrence of the label, the number NEAREST to it (within
    ``window_chars``) must equal the value at the precision the writer wrote.
    Nearest, not merely nearby: in "16,000 requests from more than 4,000
    users" both numbers are near both labels, and a window test passed a chart
    that swapped them (caught on real research, 2026-10-05).
    """
    corpus = research_context or ""
    if not corpus.strip():
        return Verdict(False, ["no research context to verify against"])
    corpus_norm = _normalize(corpus)
    unit_set = {u.strip().lower() for u in units if u and u.strip()}
    numbers = _numbers_with_spans(corpus_norm, unit_set)
    verdict = Verdict(True)
    for point in draft.points:
        spans = _label_spans(corpus_norm, point.label)
        if not spans:
            verdict.ok = False
            verdict.reasons.append(f"label {point.label!r} is not in the research")
            continue
        decimals = _decimals(point.token)
        value = _to_float(point.token)
        found = value is not None and any(
            _matches(value, float(tok.replace(",", "")), decimals)
            for span in spans
            for tok in _nearest_tokens(span, numbers, window_chars)
        )
        if not found:
            verdict.ok = False
            verdict.reasons.append(
                f"{point.label} = {point.token} is not the number the research "
                f"states for {point.label!r} (nearest within ±{window_chars} chars)"
            )
    return verdict


def to_spec(draft: DataChartDraft) -> ChartSpec:
    """A renderable spec. The source line says where the figures came from."""
    unit = draft.unit.strip()
    spec = ChartSpec(
        form=draft.form,  # type: ignore[arg-type]  # parse() admits only bar|line
        title=draft.title,
        categories=[p.label for p in draft.points],
        series=[Series(label=unit or draft.title, values=[p.value for p in draft.points])],
        value_label=unit,
        source="Figures as stated in the sources cited in this article",
        metadata={"chart_source": "writer_data_chart"},
    )
    spec.validate()
    return spec


def build_verified_spec(
    payload: str, research_context: str, *, site_config: Any = None,
) -> tuple[ChartSpec | None, list[str]]:
    """Parse → verify → spec. Returns ``(spec, [])`` or ``(None, reasons)``."""
    max_points = int(_setting(site_config, MAX_POINTS_SETTING, _DEFAULT_MAX_POINTS))
    window = int(_setting(site_config, WINDOW_SETTING, _DEFAULT_WINDOW_CHARS))
    draft = parse(payload, max_points=max_points)
    if draft is None:
        return None, [f"malformed data chart (expected 'form | title | unit | label = n; …', 2-{max_points} points)"]
    verdict = verify(draft, research_context, window_chars=window)
    if not verdict.ok:
        return None, verdict.reasons
    try:
        return to_spec(draft), []
    except ValueError as e:
        return None, [f"unrenderable data chart: {e}"]


__all__ = [
    "DATA_TARGET_PREFIX",
    "DataChartDraft",
    "DataPoint",
    "Verdict",
    "build_verified_spec",
    "enabled",
    "parse",
    "prompt_block",
    "to_spec",
    "verify",
]
