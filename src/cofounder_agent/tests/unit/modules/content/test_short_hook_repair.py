"""The corrective hook call (modules/content/short_hook_repair.py).

Contract, in order of how much it matters:

* a hook that already passes the gate buys NO LLM call;
* a replacement is spliced in only when it is STRICTLY better;
* nothing here can fail a script — a raised dispatch, an unusable reply and a
  worse candidate all return the script untouched;
* the repair happens at SCRIPT time, so the fixed line is what the presenter
  SAYS and what the title shows.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.modules.content import short_hook_repair as shr
from poindexter.services.site_config import SiteConfig

GOOD = "Zero-click content is the new standard. Platforms answer in the feed now."
# A CONTENT defect the strip CANNOT rescue — that is what buys an LLM call.
# ("Discover how JPMorgan highlights six shifts" would not: the strip turns it
# into a clean claim, so it costs nothing.)
BAD = ("Ever wondered why nobody clicks on links anymore? "
       "Agentic systems reconcile trades automatically.")
# Long but a perfectly good claim — length is shortened deterministically by
# the title builder, so this must NOT buy a call.
LONG_BUT_GOOD = ("Our GPU lock bug was quietly wrecking our RAG sweep for weeks on end. "
                 "The advisory lock was global.")


def _sc(**over):
    base = {"media.short_hook.repair_enabled": "true", "video_scene_model": "ollama/phi4:14b"}
    base.update(over)
    return SiteConfig(initial_config=base)


def _platform(reply: str):
    p = MagicMock()
    p.dispatch.complete = AsyncMock(return_value=SimpleNamespace(text=reply))
    return p


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(shr, "emit_finding", lambda **kw: None)

    class _Lock:
        async def __aenter__(self): return None
        async def __aexit__(self, *a): return False

    import poindexter.services.gpu_scheduler as gs
    monkeypatch.setattr(gs, "gpu", SimpleNamespace(lock=lambda *a, **k: _Lock()))
    monkeypatch.setattr(gs, "media_wait_budget_s", lambda: 1.0)


@pytest.mark.asyncio
class TestGate:
    async def test_a_clean_hook_costs_no_call(self):
        platform = _platform("unused")
        out, outcome = await shr.repair_short_hook(
            GOOD, title="Nobody Clicks Anymore", article="body",
            site_config=_sc(), platform=platform, pool=MagicMock(),
        )
        assert out == GOOD
        assert outcome["repaired"] is False and outcome["defects"] == []
        platform.dispatch.complete.assert_not_awaited()

    async def test_a_defective_hook_is_repaired_and_spliced(self):
        platform = _platform("JPMorgan confirms small models already won.")
        out, outcome = await shr.repair_short_hook(
            BAD, title="Nobody Clicks Anymore", article="body",
            site_config=_sc(), platform=platform, pool=MagicMock(), task_id="t1",
        )
        assert outcome["repaired"] is True
        assert out.startswith("JPMorgan confirms small models already won.")
        # The rest of the narration survives the splice.
        assert "Agentic systems reconcile trades automatically." in out
        platform.dispatch.complete.assert_awaited_once()

    async def test_a_long_but_good_hook_costs_no_call(self):
        """Length is a shortening problem. phi4 wrote 59-92 char sentences
        that were good claims; calling the LLM again would not improve them."""
        platform = _platform("unused")
        out, outcome = await shr.repair_short_hook(
            LONG_BUT_GOOD, title="How a GPU Lock Bug Hit Our Sweep", article="body",
            site_config=_sc(), platform=platform, pool=MagicMock(),
        )
        assert out == LONG_BUT_GOOD and outcome["repaired"] is False
        platform.dispatch.complete.assert_not_awaited()

    async def test_disabled_makes_no_call(self):
        platform = _platform("x")
        out, outcome = await shr.repair_short_hook(
            BAD, title="T", article="b",
            site_config=_sc(**{"media.short_hook.repair_enabled": "false"}),
            platform=platform, pool=MagicMock(),
        )
        assert out == BAD and outcome["skipped"] == "disabled"
        platform.dispatch.complete.assert_not_awaited()


@pytest.mark.asyncio
class TestNeverWorse:
    async def test_a_candidate_that_is_not_better_is_rejected(self):
        """Trading one defect for another is not an improvement, and the
        original at least matches the narration built around it."""
        platform = _platform("Ever wondered how banks pick models?")   # question
        out, outcome = await shr.repair_short_hook(
            BAD, title="T", article="b", site_config=_sc(),
            platform=platform, pool=MagicMock(),
        )
        assert out == BAD and outcome["repaired"] is False

    async def test_a_raised_dispatch_returns_the_script_untouched(self):
        platform = MagicMock()
        platform.dispatch.complete = AsyncMock(side_effect=RuntimeError("ollama down"))
        out, outcome = await shr.repair_short_hook(
            BAD, title="T", article="b", site_config=_sc(),
            platform=platform, pool=MagicMock(),
        )
        assert out == BAD and outcome["repaired"] is False and "error" in outcome

    async def test_an_empty_reply_returns_the_script_untouched(self):
        out, outcome = await shr.repair_short_hook(
            BAD, title="T", article="b", site_config=_sc(),
            platform=_platform("   "), pool=MagicMock(),
        )
        assert out == BAD and outcome["repaired"] is False

    async def test_no_platform_degrades_without_raising(self):
        out, outcome = await shr.repair_short_hook(
            BAD, title="T", article="b", site_config=_sc(), platform=None, pool=None,
        )
        assert out == BAD and outcome["skipped"] == "no_platform"


class TestReplyCleaning:
    @pytest.mark.parametrize(
        ("reply", "expected"),
        [
            ('"Zero-click content is the new standard."', "Zero-click content is the new standard."),
            ("OPENING LINE: Clicks died on the open web.", "Clicks died on the open web."),
            ("**Clicks died on the open web.**", "Clicks died on the open web."),
            ("Here is the line:\nClicks died on the open web.", "Clicks died on the open web."),
            ("Clicks died on the open web. This works because…", "Clicks died on the open web."),
            # the run-up strip applies to the model's reply too
            ("In today's digital age, clicks died on the open web.", "Clicks died on the open web."),
            ("", ""),
        ],
    )
    def test_wrappers_are_removed(self, reply, expected):
        assert shr.clean_hook_reply(reply) == expected


class TestSplice:
    def test_replaces_only_the_first_sentence(self):
        assert shr.splice_hook("One. Two. Three.", "New") == "New. Two. Three."

    def test_adds_terminal_punctuation(self):
        assert shr.splice_hook("One. Two.", "New claim").startswith("New claim.")

    def test_empty_script_becomes_the_hook(self):
        assert shr.splice_hook("", "New claim") == "New claim"


class TestPrompt:
    def test_carries_no_quotable_example(self):
        """#3951's example sentence was copied verbatim onto four unrelated
        articles. This prompt names the shapes instead of demonstrating one."""
        p = shr.build_hook_prompt(
            title="T", article="A", max_words=9, max_chars=42, target_seconds=45,
        )
        assert "Zero-click content is the new standard" not in p
        assert "Discover how" in p and "In today's" in p
        assert "9 words" in p and "42 characters" in p

    def test_model_prefers_the_hook_pin_then_the_scene_model(self):
        """The SCENE model, not a bigger one: measured 2026-09-22, gemma-4-31B
        restated the brief on 10 of 10 hooks while phi4 wrote clean claims,
        and swapping 8 GB of weights for 17 GB made admission refuse 7 of 10
        script calls."""
        assert shr._model(_sc(**{"media.short_hook.model": "ollama/glm-4.7-5090:latest"})) == "ollama/glm-4.7-5090:latest"
        assert shr._model(_sc()) == "ollama/phi4:14b"
        assert shr._model(_sc(video_scene_model="auto")) == shr._DEFAULT_MODEL
        assert shr._model(None) == shr._DEFAULT_MODEL


@pytest.mark.asyncio
async def test_gpu_busy_is_a_skip_not_a_failure(monkeypatch):
    """This caller is allowlisted to be skipped under GPU contention, so a
    refusal must leave the script alone and report a routine skip — never a
    terminal one, because nothing the piece needed was lost."""
    from poindexter.services.gpu_admission import GpuBusyError

    class _Busy:
        async def __aenter__(self): raise GpuBusyError("eta_exceeds_budget", 120.0)
        async def __aexit__(self, *a): return False

    import poindexter.services.gpu_scheduler as gs
    monkeypatch.setattr(gs, "gpu", SimpleNamespace(lock=lambda *a, **k: _Busy()))
    monkeypatch.setattr(gs, "media_wait_budget_s", lambda: 1.0)
    seen = {}
    import poindexter.modules.content.stages._media_gpu_skip as skipmod
    monkeypatch.setattr(
        skipmod, "surface_media_gpu_busy_skip",
        lambda stage, busy, *, task_id, terminal=False: seen.update(stage=stage, terminal=terminal),
    )
    platform = _platform("unused")
    out, outcome = await shr.repair_short_hook(
        BAD, title="T", article="b", site_config=_sc(),
        platform=platform, pool=MagicMock(), task_id="t9",
    )
    assert out == BAD
    assert outcome["skipped"] == "gpu_busy" and outcome["repaired"] is False
    assert seen == {"stage": "short_hook", "terminal": False}
