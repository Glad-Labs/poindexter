"""Unit tests for UnifiedPromptManager.get_prompt_resolution.

The resolution dataclass is the seam atoms use to stamp the
``prompt_template_key`` + ``prompt_template_version`` columns on the
``capability_outcomes`` table (Phase 0 lab observability, 2026-05-28).
The contract every downstream lab consumer depends on:

- ``key`` matches what was passed in (so log/correlate is trivial)
- ``version`` is the int version when one can be determined,
  otherwise ``None``
- ``source`` reflects which layer answered (yaml / langfuse / fallback)
- ``text`` is the same formatted string the legacy ``get_prompt``
  call produces

These tests don't talk to Langfuse — they exercise the YAML defaults
and any synthetic registry overrides.
"""

from __future__ import annotations

import pytest

from services.prompt_manager import (
    PromptCategory,
    PromptResolution,
    PromptVersion,
    UnifiedPromptManager,
)


@pytest.fixture
def pm() -> UnifiedPromptManager:
    return UnifiedPromptManager()


@pytest.mark.unit
class TestPromptResolution:
    def test_resolution_dataclass_is_frozen(self):
        """Frozen dataclass guarantees callers can't accidentally
        mutate the resolution between when they read it and when
        they stamp it onto an outcome row."""
        r = PromptResolution(text="hi", key="x.y", version=3, source="yaml")
        with pytest.raises(Exception):
            r.text = "mutated"  # type: ignore[misc]

    def test_default_source_is_yaml(self):
        r = PromptResolution(text="hi", key="x.y")
        assert r.source == "yaml"
        assert r.version is None

    def test_resolve_known_yaml_prompt_returns_text_and_key(
        self, pm: UnifiedPromptManager,
    ):
        """A YAML-registered prompt resolves with the matching key
        and a version derived from the YAML metadata."""
        # seo.generate_title is one of the well-known seed prompts
        r = pm.get_prompt_resolution(
            "seo.generate_title", topic="AI healthcare",
        )
        assert isinstance(r, PromptResolution)
        assert r.key == "seo.generate_title"
        assert r.source == "yaml"
        # Renders the same text the legacy get_prompt would return
        assert "AI healthcare" in r.text
        # Version derived from "v1.1" → major int 1
        assert r.version == 1

    def test_get_prompt_still_returns_str(self, pm: UnifiedPromptManager):
        """Backwards compat — existing call sites that use the plain
        ``get_prompt`` API must keep getting a string back."""
        out = pm.get_prompt("seo.generate_title", topic="X")
        assert isinstance(out, str)
        assert "X" in out

    def test_get_prompt_uses_resolution_internally(
        self, pm: UnifiedPromptManager,
    ):
        """``get_prompt`` should produce the same string as
        ``get_prompt_resolution(...).text`` — they share the same
        resolution path."""
        legacy = pm.get_prompt("seo.generate_title", topic="X")
        new = pm.get_prompt_resolution("seo.generate_title", topic="X").text
        assert legacy == new

    def test_missing_key_raises_keyerror(self, pm: UnifiedPromptManager):
        with pytest.raises(KeyError, match="not found"):
            pm.get_prompt_resolution("does.not.exist")

    def test_missing_format_var_raises_keyerror_with_hint(
        self, pm: UnifiedPromptManager,
    ):
        with pytest.raises(KeyError, match="missing required variable"):
            # seo.generate_title needs ``topic`` — leaving it out raises
            pm.get_prompt_resolution("seo.generate_title")

    def test_synthetic_yaml_version_extracted(
        self, pm: UnifiedPromptManager,
    ):
        """Register a synthetic prompt with a known YAML-style version,
        then confirm the resolver extracts the major int."""
        pm.prompts["test.synthetic"] = {
            "template": "hello {who}",
            "version": "v3.7",
        }
        # Stub metadata so other accesses don't blow up on lookups
        from services.prompt_manager import PromptMetadata
        pm.metadata["test.synthetic"] = PromptMetadata(
            category=PromptCategory.UTILITY,
            version=PromptVersion.V1_1,
            created_date="2026-05-28",
            last_modified="2026-05-28",
        )
        r = pm.get_prompt_resolution("test.synthetic", who="world")
        assert r.text == "hello world"
        assert r.version == 3
        assert r.source == "yaml"

    def test_non_numeric_yaml_version_becomes_none(
        self, pm: UnifiedPromptManager,
    ):
        """A version string that doesn't start with v<digit> resolves
        to ``None`` — the lab tolerates NULL on the column."""
        pm.prompts["test.nonnumeric"] = {
            "template": "x",
            "version": "draft",
        }
        from services.prompt_manager import PromptMetadata
        pm.metadata["test.nonnumeric"] = PromptMetadata(
            category=PromptCategory.UTILITY,
            version=PromptVersion.V1_1,
            created_date="2026-05-28",
            last_modified="2026-05-28",
        )
        r = pm.get_prompt_resolution("test.nonnumeric")
        assert r.version is None
