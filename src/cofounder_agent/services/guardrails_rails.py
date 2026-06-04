"""Native QA rails for brand-fabrication + competitor-mention screening
(#198 / #329 sub-issue 3; reimplemented dep-free for #996).

History
-------

These rails originally wrapped the ``guardrails-ai`` framework
(``BaseValidator`` / ``Guard.use`` / ``OnFailAction``). On 2026-05-12
``guardrails-ai`` was dropped from ``pyproject.toml`` because PyPI
**quarantined** the package after a supply-chain compromise
(CVE-2026-45758 — malware in the 0.10.1 release); it is no longer
installable. The framework was only ever a thin wrapper here: the
brand rail just ran ``content_validator``'s fabrication patterns, and
the competitor rail was a ``re`` word-boundary regex over an
operator-configured CSV. We own that logic, so the rails are now a
**native reimplementation with no third-party dependency** — the
capability is preserved, the supply-chain surface is gone.

What the rails do
-----------------

- ``run_brand_guard`` — runs ``content_validator._check_patterns``
  against the curated ``FAKE_*`` / ``HALLUCINATED_*`` /
  ``GLAD_LABS_IMPOSSIBLE`` / ``BRAND_CONTRADICTION`` pattern sets and
  reports any matches. A parallel signal to
  ``deepeval_brand_fabrication`` and the ``programmatic_validator``
  rail — the brand check now reports through three lenses, so
  correlation drift is itself a learnable signal.
- ``run_competitor_guard`` — flags the post when any name in the
  operator-configured ``guardrails_competitor_list`` (CSV of
  competitor brand names) appears in the body. Fills a gap DeepEval
  doesn't cover — accidental competitor promotion in branded content.

Activation
----------

Set ``app_settings.guardrails_enabled = true`` to run the rails
alongside the existing ``content_validator``. The competitor rail
additionally needs ``app_settings.guardrails_competitor_list`` seeded
(empty list → no enforcement).

Both wire in as reviewers in ``services/multi_model_qa.py`` via
``run_brand_guard`` / ``run_competitor_guard``, and run as the
``qa.guardrails`` atom on the ``canonical_blog`` graph_def path.
"""

from __future__ import annotations

import re
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# Cap the per-rail issue list so a pathological post can't blow up the
# audit-log feedback string. Matches the old guardrails-ai validator,
# which truncated at 5 with a "+N more" suffix.
_MAX_REPORTED_ISSUES = 5


# ---------------------------------------------------------------------------
# Brand-fabrication rail
# ---------------------------------------------------------------------------


def run_brand_guard(content: str) -> tuple[bool, str | None]:
    """Run the brand-fabrication rail.

    Returns ``(ok, reason)`` — ``ok=True`` when content passes;
    ``ok=False`` with a human-readable reason listing the triggered
    fabrication patterns when it fails.

    Runs ``content_validator._check_patterns`` against the same pattern
    sets the old ``BrandFabricationValidator`` used (FAKE_NAME /
    FAKE_STAT / GLAD_LABS_IMPOSSIBLE / FAKE_QUOTE /
    FABRICATED_EXPERIENCE / HALLUCINATED_LINK / BRAND_CONTRADICTION).
    The legacy ``content_validator`` is still authoritative; this rail
    is a parallel signal the operator can correlate with the legacy
    check via the audit log.

    Never raises — any unexpected error is logged and surfaced as a
    clean pass so a broken rail can't block the pipeline.
    """
    if not content or not isinstance(content, str) or not content.strip():
        return True, None

    try:
        from modules.content import content_validator as cv

        issues: list[str] = []
        # Hit each pattern set the legacy validator hits, accumulate
        # human-readable reasons for any matches.
        for pattern_set, label in [
            (cv.FAKE_NAME_PATTERNS, "fake_person"),
            (cv.FAKE_STAT_PATTERNS, "fake_stat"),
            (cv.GLAD_LABS_IMPOSSIBLE, "glad_labs_claim"),
            (cv.FAKE_QUOTE_PATTERNS, "fake_quote"),
            (cv.FABRICATED_EXPERIENCE_PATTERNS, "fabricated_experience"),
            (cv.HALLUCINATED_LINK_PATTERNS, "hallucinated_link"),
            (cv.BRAND_CONTRADICTION_PATTERNS, "brand_contradiction"),
        ]:
            hits = cv._check_patterns(
                content, pattern_set, "critical", label, label + ": '{matched}'",
            )
            for issue in hits:
                issues.append(f"{issue.category}: {issue.description[:120]}")

        if issues:
            reason = (
                f"Brand-fabrication rail flagged {len(issues)} issue(s):\n  - "
                + "\n  - ".join(issues[:_MAX_REPORTED_ISSUES])
                + (
                    ""
                    if len(issues) <= _MAX_REPORTED_ISSUES
                    else f"\n  ... +{len(issues) - _MAX_REPORTED_ISSUES} more"
                )
            )
            return False, reason
        return True, None
    except Exception as e:
        logger.warning(
            "[guardrails] Unexpected error in brand rail: %s", e, exc_info=True
        )
        return True, None


def is_enabled(site_config: Any) -> bool:
    """Operator gate. ``app_settings.guardrails_enabled = true`` to run.

    poindexter#455 — both fallback excepts used to be silent. Now they
    log a warning so a broken SiteConfig wrapper surfaces as a visible
    problem instead of masquerading as "guardrails turned off".
    """
    if site_config is None:
        return False
    try:
        return bool(site_config.get_bool("guardrails_enabled", False))
    except Exception as exc_primary:
        try:
            v = site_config.get("guardrails_enabled", "")
            return str(v).strip().lower() in ("true", "1", "yes", "on")
        except Exception as exc_fallback:
            logger.warning(
                "[guardrails] is_enabled: both get_bool and get raised "
                "while reading guardrails_enabled — treating as disabled. "
                "Primary error: %s: %s. Fallback error: %s: %s",
                type(exc_primary).__name__, exc_primary,
                type(exc_fallback).__name__, exc_fallback,
            )
            return False


# ---------------------------------------------------------------------------
# Competitor-mention rail
# ---------------------------------------------------------------------------


def _resolve_competitors(site_config: Any) -> list[str]:
    """Pull the operator-configured competitor list out of site_config.

    Reads ``app_settings.guardrails_competitor_list`` (CSV). Empty /
    missing → empty list (rail passes everything). Trimmed of
    whitespace, deduped while preserving order.
    """
    if site_config is None:
        return []
    try:
        raw = site_config.get("guardrails_competitor_list", "") or ""
    except Exception as e:
        # 2026-05-12 fail-loud sweep: previously this swallowed the
        # exception and returned []. That means the competitor
        # rail silently passes posts that mention competitors,
        # which defeats the whole rail. SiteConfig.get is sync + reads
        # from in-memory cache, so a raise here means SiteConfig isn't
        # wired correctly — a structural problem operators need to see.
        logger.warning(
            "guardrails_rails: failed to read guardrails_competitor_list; "
            "competitor rail will pass everything until fixed: %s",
            e, exc_info=True,
        )
        try:
            from utils.findings import emit_finding
            emit_finding(
                source="guardrails_rails._resolve_competitors",
                kind="guardrails_competitor_list_read_failed",
                severity="warning",
                title="Guardrails competitor-list read failed — rail open",
                body=(
                    f"site_config.get('guardrails_competitor_list') raised "
                    f"{type(e).__name__}: {e}. The competitor-mention "
                    "rail is effectively disabled until this is fixed. "
                    "Investigate SiteConfig wiring."
                ),
                dedup_key=f"guardrails_competitor_list_read_failed_{type(e).__name__}",
            )
        except Exception:
            logger.debug("emit_finding unavailable in guardrails_rails", exc_info=True)
        return []
    if not raw or not isinstance(raw, str):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for name in raw.split(","):
        n = name.strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def run_competitor_guard(
    content: str, competitors: list[str],
) -> tuple[bool, str | None]:
    """Run the competitor-mention rail.

    Returns ``(ok, reason)`` — ``ok=True`` when the post mentions none
    of the supplied competitors; ``ok=False`` with a human-readable
    reason listing the offenders when it doesn't.

    Match is case-insensitive and word-boundary — "Acme" matches
    "Acme Corp" and "acme", but not "AcmeForge" (longer compound brand
    names should be listed separately if the operator wants to flag
    them).

    Empty / missing ``competitors`` → ``(True, None)`` (no list, no
    enforcement). Empty content → ``(True, None)``. Never raises — any
    unexpected error is logged and surfaced as a clean pass.
    """
    if not content or not isinstance(content, str) or not content.strip():
        return True, None
    if not competitors:
        return True, None

    try:
        cleaned = [c.strip() for c in competitors if c and c.strip()]
        if not cleaned:
            return True, None

        hits: list[str] = []
        for name in cleaned:
            pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
            if pattern.search(content):
                hits.append(name)

        if hits:
            unique = sorted(set(hits))
            reason = (
                f"Competitor-mention rail flagged {len(unique)} brand(s): "
                + ", ".join(unique[:_MAX_REPORTED_ISSUES])
                + (
                    ""
                    if len(unique) <= _MAX_REPORTED_ISSUES
                    else f" (+{len(unique) - _MAX_REPORTED_ISSUES} more)"
                )
            )
            return False, reason
        return True, None
    except Exception as e:
        logger.warning(
            "[guardrails] Unexpected error in competitor rail: %s",
            e, exc_info=True,
        )
        return True, None


__all__ = [
    "is_enabled",
    "run_brand_guard",
    "run_competitor_guard",
    "_resolve_competitors",
]
