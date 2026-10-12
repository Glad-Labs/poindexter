"""The storytelling pack loads (poindexter#1136).

A pack whose ``metadata.category`` the prompt manager does not know is skipped
with only a log warning, so every story.* key would vanish at runtime. That
happened on the first draft of this pack; this pins it.
"""

from __future__ import annotations

from poindexter.services.prompt_manager import PromptCategory, UnifiedPromptManager


def test_story_prompts_load_under_the_story_category():
    pm = UnifiedPromptManager()
    for key in ("story.outline", "story.scene", "story.screenplay_fix"):
        assert key in pm.prompts, key
        assert pm.metadata[key].category is PromptCategory.STORY
