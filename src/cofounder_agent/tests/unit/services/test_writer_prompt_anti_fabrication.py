"""Pinning tests for the writer prompt's anti-fabrication clauses.

Background: a 2026-05-11 review of 10 awaiting_approval posts found
systemic fabrication patterns in output from the public
``blog_generation.initial_draft`` and ``blog_generation.blog_system_prompt``
templates:

- First-person plural authorial claims ("we tested", "our benchmarks
  showed") for work the publisher never did.
- Invented benchmark numbers ("120 tokens/sec", "$3-5/hour") without
  any sourcing.
- Fabricated organisation names ("The AI Advisory Practice", "At20.ai").
- Internal links pointing at rejected (un-published) posts.

The YAML prompts at ``prompts/blog_generation.yaml`` now ship with
explicit VOICE + SOURCING + SELF-CHECK blocks that ban each pattern by
name. These tests pin those clauses byte-of-meaning — they don't
match the prompt verbatim (so prose tweaks are still allowed) but they
DO assert each of the specific phrases the writer was caught
fabricating actually appears in the negative-example list. If a
future edit silently drops one of these clauses, this suite breaks.

Per ``feedback_positive_directives`` we keep both the negative phrasing
("do NOT…") AND the positive replacement ("prefer…") visible in the
prompt; the suite checks both.

See also:
- Glad-Labs/poindexter#469 (alt-text sanitizer leak)
- Glad-Labs/poindexter#470 (internal_link_coherence rejected-target bug)
- Glad-Labs/poindexter#471 (title-suffix sanitizer)
"""

from __future__ import annotations

import pytest

from services.prompt_manager import UnifiedPromptManager


@pytest.fixture
def pm() -> UnifiedPromptManager:
    return UnifiedPromptManager()


# ---------------------------------------------------------------------------
# blog_generation.initial_draft
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitialDraftBansFirstPersonAuthorialFakery:
    """The draft prompt must explicitly forbid "we tested" etc.

    Every banned phrase below was caught in production output during
    the 2026-05-11 review. The prompt enumerates them by name so the
    writer model has no excuse for emitting them; tests pin the list.
    """

    @pytest.fixture
    def template(self, pm: UnifiedPromptManager) -> str:
        return pm.prompts["blog_generation.initial_draft"]["template"]

    @pytest.mark.parametrize(
        "banned_phrase",
        [
            "we tested",
            "our benchmarks",
            "we measured",
            "we ran",
            "we found that",
            "our testing showed",
        ],
    )
    def test_banned_first_person_phrase_listed_in_prompt(
        self, template: str, banned_phrase: str,
    ):
        """The literal phrase must appear in the prompt as a negative example."""
        assert banned_phrase in template.lower(), (
            f"Anti-fabrication block must explicitly call out the phrase "
            f"{banned_phrase!r} so the writer model can't claim ignorance. "
            f"Spotted in production output 2026-05-11."
        )

    def test_positive_replacement_phrasings_offered(self, template: str):
        """Forbidding without alternatives just produces evasion.

        ``feedback_positive_directives``: pair every "don't do X" with
        a "do Y instead." The prompt must surface acceptable third-
        person sourcing phrasings.
        """
        lower = template.lower()
        # At least one of these positive-direction phrasings should be
        # named so the model has a concrete alternative pattern.
        positives = [
            "vendor benchmarks report",
            "independent reviewers",
            "industry observers",
            "the official documentation",
            "early adopters",
        ]
        present = [p for p in positives if p in lower]
        assert len(present) >= 2, (
            f"The prompt should name at least 2 acceptable third-person "
            f"sourcing patterns as positive examples; found: {present}"
        )

    def test_publisher_is_not_we_clause(self, template: str):
        """One sentence anywhere must spell out the rule explicitly."""
        lower = template.lower()
        assert "publisher is never \"we\"" in lower or (
            "first-person" in lower and "authorial" in lower
        ), (
            "Need an explicit 'publisher is never we' or 'first-person "
            "authorial' rule statement — otherwise the negative list reads "
            "like style advice instead of a hard ban."
        )


@pytest.mark.unit
class TestInitialDraftRequiresSourcingForSpecificClaims:
    """Numeric claims and named entities need a verifiable backing."""

    @pytest.fixture
    def template(self, pm: UnifiedPromptManager) -> str:
        return pm.prompts["blog_generation.initial_draft"]["template"]

    def test_specific_number_rule_present(self, template: str):
        """Numbers + URL-or-context requirement is the floor of the rule."""
        lower = template.lower()
        # The clause must mention both the trigger (specific number) and
        # the requirement (research_context OR working URL).
        assert "specific number" in lower
        assert "research_context" in lower
        assert "working url" in lower or "url" in lower

    def test_attribution_without_url_is_called_fabrication(self, template: str):
        """The "according to X" without URL → fabrication clause."""
        lower = template.lower()
        # Spot the exact failure mode from the 2026-05-11 review.
        assert "according to" in lower
        assert "fabrication" in lower

    def test_internal_link_invention_banned(self, template: str):
        """``[[Internal Link 1]]`` placeholder tokens were a real failure mode.

        Premium prompt's example_output includes ``[[Internal Link 1]]``
        as a *template placeholder* — the writer model copied it
        verbatim into draft output in past failures. The prompt must
        instruct against inventing slugs not present in research_context.
        """
        lower = template.lower()
        # Either name the placeholder pattern OR cite the slug requirement.
        assert ("internal link" in lower) or ("/posts/" in lower)
        assert "research_context" in lower


@pytest.mark.unit
class TestInitialDraftSelfCheck:
    """Pre-submission self-check section must enumerate the failure modes."""

    @pytest.fixture
    def template(self, pm: UnifiedPromptManager) -> str:
        return pm.prompts["blog_generation.initial_draft"]["template"]

    def test_self_check_section_present(self, template: str):
        lower = template.lower()
        assert "self-check" in lower or "before returning" in lower

    def test_self_check_covers_first_person(self, template: str):
        lower = template.lower()
        # Check the self-check section mentions both the trigger and
        # the corrective action.
        assert "\"we\"" in lower or "'we'" in lower or "we or our" in lower

    def test_self_check_covers_unsourced_numbers(self, template: str):
        lower = template.lower()
        assert "source url" in lower or "without a source" in lower


# ---------------------------------------------------------------------------
# blog_generation.blog_system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSystemPromptCarriesAntiFabricationFloor:
    """System prompts run on every request; they're the cheapest place to
    install a hard floor against the worst failure modes.

    The system prompt must independently carry the no-first-person-
    authorial-fakery rule — even when an operator-provided user prompt
    forgets to include it, the system prompt still reins it in.
    """

    @pytest.fixture
    def template(self, pm: UnifiedPromptManager) -> str:
        return pm.prompts["blog_generation.blog_system_prompt"]["template"]

    def test_third_person_observer_framing(self, template: str):
        lower = template.lower()
        assert (
            "observer" in lower
            or "third-person" in lower
            or "industry-analysis" in lower
        )

    def test_first_person_authorial_ban(self, template: str):
        lower = template.lower()
        # At minimum the system prompt should ban two of the four
        # worst phrases caught in production.
        bans = ["we tested", "our benchmarks", "we ran", "in our lab"]
        present = [b for b in bans if b in lower]
        assert len(present) >= 2, (
            f"System prompt must call out at least 2 first-person "
            f"authorial phrases by name; found: {present}"
        )

    def test_publisher_disclosure(self, template: str):
        """One sentence somewhere must spell out the underlying fact —
        the publisher has not run a test lab.

        Without this, the rule reads like stylistic preference; with it,
        the rule reads like a factual disclosure the model must respect.
        """
        lower = template.lower()
        assert (
            "has not run a test lab" in lower
            or "not run a lab" in lower
            or "publisher has not" in lower
        )
