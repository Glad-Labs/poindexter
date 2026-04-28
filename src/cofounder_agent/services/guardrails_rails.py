"""guardrails-ai integration as a parallel content rail (#198).

Doesn't replace the domain-specific fabrication-detection patterns in
``services/content_validator.py`` — those are the brand-protection
moat (fake stats, fabricated quotes, hallucinated library refs,
brand contradictions, etc). guardrails-ai is added as a *parallel*
rail so:

1. Operator gets hands-on with the framework conventions
   (BaseValidator, OnFailAction, RAIL spec, Guard.parse) which show
   up across modern LLM-app stacks.
2. The framework's hub of pre-built validators (PII, toxicity, JSON
   schema, regex, length, profanity) becomes available for surfaces
   where commodity rails make sense — operator chat input
   sanitization, user-facing form inputs, etc.

Activation
----------

Set ``app_settings.guardrails_enabled = true`` to run the
guardrails pass alongside the existing content_validator. Default
``false`` keeps the rail dormant. Per-validator toggles via
``app_settings.guardrails_validator_<name>_enabled = true|false``.

Custom validator pattern
------------------------

The example below registers a single ``brand_fabrication`` validator
that wraps the existing ``content_validator._check_patterns`` helper.
This is the "learning artifact" — once you've written one
``BaseValidator``, the others (toxicity, PII, JSON-shape) follow the
same pattern.
"""

from __future__ import annotations

from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom validator wrapping our existing fabrication patterns
# ---------------------------------------------------------------------------


def _build_brand_fabrication_validator():
    """Lazy-build the validator class.

    Imported lazily so callers without guardrails-ai installed don't
    crash on module import. Returns the registered validator class.
    """
    from guardrails.validators import (
        FailResult,
        PassResult,
        Validator,
        register_validator,
    )

    from services import content_validator as cv

    @register_validator(name="poindexter/brand_fabrication", data_type="string")
    class BrandFabricationValidator(Validator):
        """Reject content that trips Glad Labs' fabrication patterns.

        Wraps ``content_validator._check_patterns`` against the curated
        ``FAKE_*`` / ``HALLUCINATED_*`` / ``GLAD_LABS_IMPOSSIBLE`` /
        ``BRAND_CONTRADICTION`` pattern sets. Returns a FailResult
        listing every triggered pattern; PassResult when the text is
        clean.

        This is the canonical "wrap an existing domain check as a
        guardrails Validator" example for the Glad Labs codebase —
        future custom validators (citation grounding, internal-link
        validity, image-relevance) follow the same shape.
        """

        def validate(self, value: str, metadata: dict | None = None) -> Any:
            if not isinstance(value, str) or not value.strip():
                return PassResult()

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
                    value, pattern_set, "critical", label, label + ": '{matched}'",
                )
                for issue in hits:
                    issues.append(f"{issue.category}: {issue.description[:120]}")

            if issues:
                return FailResult(
                    error_message=(
                        f"Brand-fabrication validator flagged "
                        f"{len(issues)} issue(s):\n  - "
                        + "\n  - ".join(issues[:5])
                        + ("" if len(issues) <= 5 else f"\n  ... +{len(issues)-5} more")
                    ),
                    fix_value=None,
                )
            return PassResult()

    return BrandFabricationValidator


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


_GUARD_CACHE: dict[str, Any] = {}


def _get_brand_guard() -> Any:
    """Lazy-build a ``Guard`` wired to the brand-fabrication validator.

    Cache the Guard so repeated calls don't re-register the validator
    on every invocation.
    """
    if "brand" in _GUARD_CACHE:
        return _GUARD_CACHE["brand"]

    from guardrails import Guard

    validator_cls = _build_brand_fabrication_validator()
    guard = Guard().use(validator_cls(on_fail="exception"))
    _GUARD_CACHE["brand"] = guard
    return guard


def run_brand_guard(content: str) -> tuple[bool, str | None]:
    """Run the brand-fabrication guardrail.

    Returns ``(ok, reason)`` — ok=True when content passes;
    ok=False with a human-readable reason when it fails.

    Never raises — guardrails-ai's ``on_fail="exception"`` mode is
    caught here and surfaced as the (False, reason) tuple so callers
    don't need a try/except. The legacy ``content_validator`` is
    still authoritative; this rail is a parallel signal that the
    operator can correlate with the legacy check via the audit log.
    """
    if not content or not isinstance(content, str):
        return True, None

    try:
        guard = _get_brand_guard()
        result = guard.validate(content)
        if not result.validation_passed:
            return False, str(result.validation_summaries or "guardrails: failed")
        return True, None
    except ImportError as e:
        logger.warning(
            "[guardrails] guardrails-ai not installed (%s) — skipping rail",
            e,
        )
        return True, None
    except Exception as e:
        # Validator's on_fail=exception path raises ValidationError on
        # failure. We catch + surface as a clean (False, reason).
        # Specific class lookup defends against guardrails-ai version
        # variations in the exception hierarchy.
        from guardrails.errors import ValidationError as _GuardrailsVE
        if isinstance(e, _GuardrailsVE):
            return False, str(e)
        logger.warning("[guardrails] Unexpected error in brand rail: %s", e, exc_info=True)
        return True, None


def is_enabled(site_config: Any) -> bool:
    """Operator gate. ``app_settings.guardrails_enabled = true`` to run."""
    if site_config is None:
        return False
    try:
        return bool(site_config.get_bool("guardrails_enabled", False))
    except Exception:
        try:
            v = site_config.get("guardrails_enabled", "")
            return str(v).strip().lower() in ("true", "1", "yes", "on")
        except Exception:
            return False


__all__ = [
    "is_enabled",
    "run_brand_guard",
]
