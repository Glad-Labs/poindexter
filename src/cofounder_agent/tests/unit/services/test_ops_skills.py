"""Tests for the ops skill packs split out of ``prompts/system.yaml``
and ``prompts/tasks.yaml`` (#528).

The ops/infra prompts were migrated from the two mixed YAML files into
agentskills.io SKILL.md packs under the NEW ``skills/ops/`` pack:

* ``skills/ops/triage/SKILL.md`` — ``ops.triage.system_prompt``
  (category ``utility``)
* ``skills/ops/hygiene/SKILL.md`` — ``ops.retention.summarize_to_table`` +
  ``memory.collapse_old_embeddings.summary`` (category ``utility``)
* ``skills/ops/business/SKILL.md`` — ``task.business_financial_impact`` +
  ``task.business_market_analysis`` + ``task.business_performance_analysis``
  (category ``financial``)
* ``skills/ops/automation/SKILL.md`` — ``task.automation_email_campaign``
  (category ``utility``)

The ``skills/ops/`` pack is auto-discovered by the loader's ``*/*/SKILL.md``
glob — no loader change was needed. The pack DIRECTORY (``ops``) is
independent of each prompt's ``metadata.category``.

These tests pin that each key still resolves, that templates end with a
single trailing newline (the loader's ``|`` clip semantics), and that
each prompt keeps its exact YAML category.

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

import pytest

from services.prompt_manager import PromptCategory, UnifiedPromptManager

# (key, expected category) pairs — categories must match the original YAML.
_OPS_KEYS = (
    ("ops.triage.system_prompt", PromptCategory.UTILITY),
    ("ops.retention.summarize_to_table", PromptCategory.UTILITY),
    ("memory.collapse_old_embeddings.summary", PromptCategory.UTILITY),
    ("task.business_financial_impact", PromptCategory.FINANCIAL),
    ("task.business_market_analysis", PromptCategory.FINANCIAL),
    ("task.business_performance_analysis", PromptCategory.FINANCIAL),
    ("task.automation_email_campaign", PromptCategory.UTILITY),
)


@pytest.fixture
def pm() -> UnifiedPromptManager:
    """Fresh UnifiedPromptManager — YAML/SKILL.md only, no Langfuse, no DB."""
    return UnifiedPromptManager()


@pytest.mark.unit
@pytest.mark.parametrize(("key", "_category"), _OPS_KEYS)
def test_ops_key_resolves_from_skill(
    pm: UnifiedPromptManager, key: str, _category: PromptCategory,
) -> None:
    """Every ops key loads from a skills/ops/* SKILL.md pack."""
    assert key in pm.prompts, f"{key} did not load from an ops skill pack"
    assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


@pytest.mark.unit
@pytest.mark.parametrize(("key", "_category"), _OPS_KEYS)
def test_ops_template_clips_to_single_trailing_newline(
    pm: UnifiedPromptManager, key: str, _category: PromptCategory,
) -> None:
    """Loader normalizes to YAML ``|`` clip semantics: exactly one ``\\n``."""
    template = pm.prompts[key]["template"]
    assert template.endswith("\n"), f"{key} template lost its trailing newline"
    assert not template.endswith("\n\n"), f"{key} template has >1 trailing newline"


@pytest.mark.unit
@pytest.mark.parametrize(("key", "category"), _OPS_KEYS)
def test_ops_category_preserved(
    pm: UnifiedPromptManager, key: str, category: PromptCategory,
) -> None:
    """Each prompt keeps its exact YAML category despite the pack split."""
    assert pm.get_metadata(key).category is category


@pytest.mark.unit
def test_triage_template_matches_migrated_yaml(pm: UnifiedPromptManager) -> None:
    template = pm.prompts["ops.triage.system_prompt"]["template"]
    assert "You are the Poindexter operator" in template
    assert "ONE SHORT PARAGRAPH" in template


@pytest.mark.unit
def test_retention_template_keeps_placeholders(pm: UnifiedPromptManager) -> None:
    template = pm.prompts["ops.retention.summarize_to_table"]["template"]
    assert "compressing one calendar day" in template
    for placeholder in ("{source_table}", "{n}", "{bucket_start_iso}",
                         "{row_count}", "{joined}"):
        assert placeholder in template, f"{placeholder} missing from retention prompt"


@pytest.mark.unit
def test_collapse_template_keeps_placeholders(pm: UnifiedPromptManager) -> None:
    template = pm.prompts["memory.collapse_old_embeddings.summary"]["template"]
    assert "compressing a cluster of older memories" in template
    for placeholder in ("{n}", "{source_table}", "{joined}"):
        assert placeholder in template, f"{placeholder} missing from collapse prompt"


@pytest.mark.unit
def test_business_financial_impact_matches_migrated_yaml(
    pm: UnifiedPromptManager,
) -> None:
    template = pm.prompts["task.business_financial_impact"]["template"]
    assert "Analyze the financial impact of: {topic}" in template
    assert "roi_estimate" in template
