"""qa.self_claim — do the draft's claims about OUR OWN system match reality?

The highest-stakes fabrication class, because it is the most checkable
(poindexter#1007): the code is public, so a reader who believes the post
can open the repo and find that the thing does not exist. Three fabricated
or stale self-claims reached ``awaiting_approval`` at Q94–95 on 2026-08-09
— an invented retrieval mechanism, invented quality scores ("a Q of 85 or
87" when real scores are 70 and 94–98), and a version number two releases
stale. Every truth-oriented rail missed them: fabrication/citation rails
check the draft against its research bundle, and the corpus these claims
need is the repo + the live database.

This is the issue's "cheapest useful subset" — the DETERMINISTIC layers,
no LLM call:

1. **Version strings** claimed for our own release vs the running
   package version (``pyproject.toml``). Version numbers in prose rot
   within days; instance 3 rotted within two.
2. **Quality-score claims** about our own queue vs the real
   ``pipeline_tasks`` score distribution (±1 tolerance).
3. **Backticked settings-shaped keys** vs the live ``app_settings``
   table.
4. **Package-relative file paths** (``services/x.py`` …) vs the tree on
   disk.

The fuzzier "named mechanism vs repo symbol" layer (instance 1) needs the
grounded-LLM treatment (propose → verify the symbol resolves, the
``content.llm_reconcile_citations`` pattern) and is deliberately NOT here.

**Self-reference gate is load-bearing for precision**: every check runs
only when the draft is about our own system (product-name match from
``qa_self_claim_product_names`` + the operator's ``site_name``, or
first-person-plural system prose), and the version extractor additionally
requires our-system context in its local window — a post reviewing
another product's v2.3.1 must never be judged against OUR version.

A draft with NO falsifiable self-claims appends no review at all (per the
issue's acceptance: dev-diary prose about the pipeline that asserts
nothing checkable does not fire). DB-dependent layers (2, 3) skip
silently without a pool — the file/version layers still run; a skipped
layer is reduced coverage, never a fake verdict.

Advisory-first: seeded ``qa_gates.self_claim.required_to_pass=false`` so
it scores + surfaces offenders in ``qa_feedback`` (QA Rails dashboard)
but does not veto until an operator graduates it. Master switch
``qa_self_claim_enabled`` (default true).

Chain position: after ``qa.title_coherence``, before ``qa.web_factcheck``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from modules.content.atoms._pool import resolve_pool
from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.self_claim",
    type="atom",
    version="1.0.0",
    description=(
        "Deterministic verification of the draft's claims about our own "
        "system: version strings vs pyproject, quality-score claims vs "
        "pipeline_tasks, backticked settings keys vs app_settings, and "
        "package file paths vs the tree. Advisory-first (DB-driven via "
        "qa_gates.self_claim)."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to review"),
        FieldSpec(name="topic", type="str", description="assignment topic", required=False),
    ),
    outputs=(
        FieldSpec(
            name="qa_rail_reviews",
            type="list[dict]",
            description="self-claim review result",
        ),
    ),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("two read-only DB lookups when a pool is available",),
    parallelizable=True,
)

# Penalty per confirmed-false claim. Advisory, so this shapes the all-rail
# score + the operator's read, not the gate. DB-tunable via
# qa_self_claim_offender_penalty.
_DEFAULT_PENALTY = 25.0

# A version claim only counts as OURS when its neighbourhood talks about our
# system — "release v0.116.0" in a post about someone else's product is not
# our claim to check.
_VERSION_RE = re.compile(
    r"(?:release|version|currently at|running|shipped)\s+v?(\d+\.\d+\.\d+)",
    re.IGNORECASE,
)
_CONTEXT_WINDOW = 140

# "a Q of 85", "Q: 94", "quality score of 87", "Qs of 85 or 87" — two-digit
# claims about our own queue's scores.
_QSCORE_RE = re.compile(
    r"\bQs?\s*(?:of|at|:)\s*(\d{2})\b|\bquality\s+scores?\s+(?:of|at|around)\s+(\d{2})\b",
    re.IGNORECASE,
)

# Backticked snake_case tokens shaped like app_settings keys. The suffix list
# keeps ordinary code identifiers (function names, columns) out of scope.
_SETTINGS_TOKEN_RE = re.compile(r"`([a-z][a-z0-9_]{4,})`")
_SETTINGS_SUFFIXES = (
    "_enabled", "_threshold", "_model", "_url", "_seconds", "_minutes",
    "_hours", "_days", "_max", "_min", "_count", "_limit", "_mode",
)

# Package-relative source paths the post asserts exist.
_PATH_RE = re.compile(
    r"\b((?:services|modules|routes|plugins|utils|poindexter)/[\w./-]+?\.py)\b"
)


def _is_enabled(site_config: Any) -> bool:
    try:
        raw = site_config.get("qa_self_claim_enabled", "true")
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        # silent-ok: optional master switch — default the advisory rail ON (it
        # only scores, never vetoes) when a config-read blip occurs.
        return True
    return str(raw).lower() in ("true", "1", "yes")


def _product_markers(site_config: Any) -> list[str]:
    """Lowercased markers that make a draft 'about our system'."""
    markers = []
    try:
        raw = site_config.get("qa_self_claim_product_names", "poindexter") or ""
    except Exception:  # noqa: BLE001 — stubbed site_config
        raw = "poindexter"
    markers.extend(m.strip().lower() for m in raw.split(",") if m.strip())
    try:
        site_name = (site_config.get("site_name", "") or "").strip().lower()
    except Exception:  # noqa: BLE001 — stubbed site_config
        site_name = ""
    if site_name:
        markers.append(site_name)
    return markers or ["poindexter"]


_SELF_PROSE_RE = re.compile(
    r"\b(?:our|we)\b.{0,50}\b(?:pipeline|rail|atom|graph|worker|scheduler|"
    r"codebase|repo|release|queue|dashboard)\b",
    re.IGNORECASE | re.DOTALL,
)


def is_self_referential(content: str, topic: str, markers: list[str]) -> bool:
    haystack = f"{topic}\n{content}".lower()
    if any(m in haystack for m in markers):
        return True
    return bool(_SELF_PROSE_RE.search(content))


def _package_root() -> Path:
    # …/modules/content/atoms/qa_self_claim.py → src/cofounder_agent
    return Path(__file__).resolve().parents[3]


def current_package_version(root: Path | None = None) -> str | None:
    """The running package version from pyproject.toml, or None."""
    root = root or _package_root()
    try:
        import tomllib

        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        # silent-ok: missing/unreadable pyproject just skips the version
        # layer (None = "no claim checked"), it never fakes a verdict — the
        # rail's other three layers still run.
        return None
    return (
        data.get("tool", {}).get("poetry", {}).get("version")
        or data.get("project", {}).get("version")
    )


def _window_is_ours(content: str, start: int, end: int, markers: list[str]) -> bool:
    lo = max(0, start - _CONTEXT_WINDOW)
    window = content[lo : end + _CONTEXT_WINDOW].lower()
    if any(m in window for m in markers):
        return True
    return bool(re.search(r"\b(?:we|our)\b", window, re.IGNORECASE))


def extract_our_version_claims(content: str, markers: list[str]) -> list[str]:
    """Version strings claimed in an our-system context, in order."""
    return [
        m.group(1)
        for m in _VERSION_RE.finditer(content)
        if _window_is_ours(content, m.start(), m.end(), markers)
    ]


def extract_qscore_claims(content: str) -> list[int]:
    claims = []
    for m in _QSCORE_RE.finditer(content):
        raw = m.group(1) or m.group(2)
        if raw:
            claims.append(int(raw))
    return claims


def check_qscores_against(claims: list[int], real_scores: set[int]) -> list[str]:
    """Claims not within ±1 of any real score are invented numbers."""
    if not real_scores:
        return []
    offenders = []
    for claimed in claims:
        if not any(abs(claimed - real) <= 1 for real in real_scores):
            offenders.append(
                f"quality-score claim Q{claimed} — no such score exists in "
                f"pipeline_tasks (feedback_no_dummy_data)"
            )
    return offenders


def extract_settings_tokens(content: str) -> list[str]:
    return [
        t for t in dict.fromkeys(_SETTINGS_TOKEN_RE.findall(content))
        if t.endswith(_SETTINGS_SUFFIXES)
    ]


def extract_paths(content: str) -> list[str]:
    return list(dict.fromkeys(_PATH_RE.findall(content)))


def check_paths(paths: list[str], root: Path | None = None) -> list[str]:
    root = root or _package_root()
    return [
        f"file path `{p}` does not exist in the repo"
        for p in paths
        if not (root / p).exists()
    ]


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None or not _is_enabled(site_config):
        return {}

    topic = str(state.get("topic") or "")
    markers = _product_markers(site_config)
    if not is_self_referential(content, topic, markers):
        return {}

    real_version = current_package_version()
    version_claims = extract_our_version_claims(content, markers)
    paths = extract_paths(content)
    qscore_claims = extract_qscore_claims(content)
    settings_tokens = extract_settings_tokens(content)

    offenders: list[str] = []
    versions_checked = bool(version_claims and real_version)
    if versions_checked:
        offenders += [
            f"version claim v{v} — the running release is v{real_version}"
            for v in version_claims if v != real_version
        ]
    offenders += check_paths(paths)

    pool = resolve_pool(state, atom="qa.self_claim")
    checked_db_layers = False
    if pool is not None and (qscore_claims or settings_tokens):
        try:
            async with pool.acquire() as conn:
                if qscore_claims:
                    rows = await conn.fetch(
                        "SELECT DISTINCT ROUND(quality_score)::int AS q "
                        "FROM pipeline_tasks WHERE quality_score > 0"
                    )
                    offenders += check_qscores_against(
                        qscore_claims, {r["q"] for r in rows},
                    )
                if settings_tokens:
                    rows = await conn.fetch(
                        "SELECT key FROM app_settings WHERE key = ANY($1::text[])",
                        settings_tokens,
                    )
                    present = {r["key"] for r in rows}
                    offenders += [
                        f"settings key `{t}` does not exist in app_settings"
                        for t in settings_tokens if t not in present
                    ]
            checked_db_layers = True
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[qa.self_claim] DB layers skipped (reduced coverage, "
                "never a fake verdict): %s", e,
            )

    # Nothing falsifiable EXTRACTED → no review at all. Prose ABOUT the
    # pipeline that asserts nothing checkable must not fire (issue
    # acceptance), and a vacuous 100 would skew the all-rail average.
    checked_anything = (
        versions_checked
        or bool(paths)
        or (checked_db_layers and bool(qscore_claims or settings_tokens))
    )
    if not offenders and not checked_anything:
        return {}

    from modules.content.multi_model_qa import MultiModelQA, ReviewerResult

    penalty = _DEFAULT_PENALTY
    try:
        penalty = float(
            site_config.get("qa_self_claim_offender_penalty", _DEFAULT_PENALTY)
            or _DEFAULT_PENALTY
        )
    except Exception:  # noqa: BLE001 — stubbed site_config
        penalty = _DEFAULT_PENALTY

    score = max(0.0, 100.0 - penalty * len(offenders))
    feedback = (
        "Self-claims verified against the running system."
        if not offenders
        else "False self-claims: " + "; ".join(offenders[:5])
    )
    review = ReviewerResult(
        reviewer="self_claim",
        approved=not offenders,
        score=score,
        feedback=feedback,
        provider="programmatic",
    )
    qa = MultiModelQA(
        pool=pool,
        settings_service=state.get("settings_service"),
        site_config=site_config,
        platform=state.get("platform"),
    )
    gate_states = await resolve_gate_states(qa)
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "self_claim")
    if offenders:
        logger.info(
            "[qa.self_claim] %d false self-claim(s): %s",
            len(offenders), "; ".join(offenders[:3]),
        )
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
