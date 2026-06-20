"""Drift-guard + fail-loud contract for the resolve-then-fallback prompts.

Every production prompt that resolves via UnifiedPromptManager AND carries an
inline ``_*_FALLBACK`` constant is exercised here. That pattern (cycle-3 /
cycle-4 migrations, #612) keeps the pipeline running when the prompt registry
is unreachable — but the fallback must:

  (a) stay byte-identical to its SKILL.md default so it can't silently go
      stale (a fired-but-stale fallback would quietly serve old text), and
  (b) announce itself LOUDLY when it fires — per feedback_self_heal_not_suppress
      the fallback self-heals, but it must not suppress the signal that the
      registry is broken.

Each case calls the REAL resolver two ways — registry up (the SKILL.md
default) and registry down (the inline fallback) — and asserts they agree.
Driving the production resolver, rather than re-deriving each prompt's
brace-escaping by hand, is what lets one test cover every prompt uniformly.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from services.prompt_manager import UnifiedPromptManager

_PATCH_TARGET = "services.prompt_manager.get_prompt_manager"


def _critic():
    from modules.content.atoms import review_with_critic as m

    return m._resolve_system_prompt()


def _qa_rewrite():
    from modules.content.atoms import qa_rewrite as m

    return m._resolve_revise_prompt(content="A draft body.", feedback="- name the GPU")


def _quality():
    from modules.content import quality_service as m

    return m._resolve_quality_prompt(
        "qa.quality_evaluation_llm_rubric", topic="Topic", content_excerpt="Body.",
    )


def _narrate():
    from modules.content.atoms import narrate_bundle as m

    return m._resolve_system_prompt()


def _architect():
    from services import pipeline_architect as m

    return m._resolve_system_prompt()


def _social_twitter():
    from services import social_poster as m

    return m._resolve_social_prompt(
        "social.twitter_promote", fallback=m._TWITTER_PROMPT_FALLBACK,
        company_name="Glad Labs", char_limit=280, title="T", excerpt="E",
        post_url="https://gladlabs.io/p/x", hashtags="#a #b",
    )


def _social_linkedin():
    from services import social_poster as m

    return m._resolve_social_prompt(
        "social.linkedin_promote", fallback=m._LINKEDIN_PROMPT_FALLBACK,
        company_name="Glad Labs", char_limit=3000, title="T", excerpt="E",
        post_url="https://gladlabs.io/p/x", hashtags="#a #b",
    )


def _collapse():
    from services.jobs import collapse_old_embeddings as m

    return m._resolve_summary_prompt_template()


def _ops_triage():
    from services import firefighter_service as m

    return m._resolve_system_prompt()


# (name, skill_key, resolver_callable)
_CASES = [
    ("review_with_critic", "atoms.review_with_critic.system_prompt", _critic),
    ("qa_rewrite", "atoms.qa_rewrite.revise_prompt", _qa_rewrite),
    ("quality", "qa.quality_evaluation_llm_rubric", _quality),
    ("narrate_bundle", "atoms.narrate_bundle.system_prompt", _narrate),
    ("pipeline_architect", "atoms.pipeline_architect.system_prompt", _architect),
    ("social_twitter", "social.twitter_promote", _social_twitter),
    ("social_linkedin", "social.linkedin_promote", _social_linkedin),
    ("collapse", "memory.collapse_old_embeddings.summary", _collapse),
    ("ops_triage", "ops.triage.system_prompt", _ops_triage),
]

_IDS = [c[0] for c in _CASES]


@pytest.mark.unit
@pytest.mark.parametrize("name,key,call", _CASES, ids=_IDS)
def test_prompt_key_registered_from_skill(name, key, call):
    """Every fallback-bearing prompt must have a real SKILL.md source — else
    the inline fallback is the ONLY source and is not DB/Langfuse-tunable."""
    pm = UnifiedPromptManager()
    assert key in pm.prompts, f"{name}: {key} is not registered from any SKILL.md"


@pytest.mark.unit
@pytest.mark.parametrize("name,key,call", _CASES, ids=_IDS)
def test_skill_default_matches_inline_fallback(name, key, call):
    """The SKILL.md default and the inline fallback must render identically.

    Calls the production resolver with the registry up (SKILL.md path) and
    down (inline path) and asserts they agree — so the fallback can never
    silently drift from the shipped default."""
    skill_path = call()
    with patch(_PATCH_TARGET, side_effect=RuntimeError("registry down")):
        fallback_path = call()
    assert skill_path == fallback_path, (
        f"{name}: SKILL.md default and inline fallback have drifted"
    )


@pytest.mark.unit
@pytest.mark.parametrize("name,key,call", _CASES, ids=_IDS)
def test_fallback_logs_loud_when_registry_down(name, key, call, caplog):
    """When the fallback fires it must log at ERROR (self-heal, don't suppress)
    so the operator learns the prompt registry is unreachable."""
    with patch(_PATCH_TARGET, side_effect=RuntimeError("registry down")):
        with caplog.at_level(logging.ERROR):
            call()
    assert any(r.levelno >= logging.ERROR for r in caplog.records), (
        f"{name}: fallback fired without an ERROR-level log"
    )
