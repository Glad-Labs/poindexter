"""The [CHART: key] path from writer marker to rendered image.

Three hops, each mirroring the proven ``[SCREENSHOT:]`` plumbing:

    [CHART: key]  →  [IMAGE-N: chart:key]     (_writer_markers)
                  →  plan["chart_target"]      (content.plan_image_markers)
                  →  ChartProvider             (content.generate_images)

The property that matters most is where the DATA comes from: the writer picks
which chart, the catalog (a service) owns the query, and the image provider
receives a finished spec. A query surface reachable from a writer-emitted
marker would be an injection seam.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from modules.content.atoms import content_plan_image_markers as planner
from services.site_config import SiteConfig

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_vram_unload(monkeypatch):
    """Stub the VRAM guard, which calls Ollama on EVERY plan_image_markers run.

    It sits after the decision-agent branch and fires unconditionally, so it
    opens a socket even when the draft already carries markers and the agent
    is skipped. Locally that call succeeds and the assertions still pass —
    only CI's network-egress guard (poindexter#1011) surfaces it, and a unit
    test that reaches a live model is grading live state, not the code.
    """

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(
        "services.llm_providers.ollama_unload."
        "maybe_unload_writer_before_image_gen",
        _noop,
    )


class TestPlanBuilding:
    async def test_a_chart_marker_becomes_a_chart_target_plan(self):
        state = {
            "content": "Intro.\n\n## Section\n\n[IMAGE-1: chart:llm-decode-vs-delivered]\n",
            "site_config": SiteConfig(),
        }
        out = await planner.run(state)
        plans = out["image_plans"]
        assert len(plans) == 1
        assert plans[0]["chart_target"] == "llm-decode-vs-delivered"
        assert "screenshot_target" not in plans[0]

    async def test_a_screenshot_marker_is_unaffected(self):
        state = {
            "content": "Intro.\n\n## S\n\n[IMAGE-1: screenshot:qa-rails]\n",
            "site_config": SiteConfig(),
        }
        plans = (await planner.run(state))["image_plans"]
        assert plans[0]["screenshot_target"] == "qa-rails"
        assert "chart_target" not in plans[0]

    async def test_an_ordinary_marker_carries_neither_target(self):
        state = {
            "content": "Intro.\n\n## S\n\n[IMAGE-1: a desk by a window]\n",
            "site_config": SiteConfig(),
        }
        plans = (await planner.run(state))["image_plans"]
        assert "chart_target" not in plans[0]
        assert "screenshot_target" not in plans[0]
        assert plans[0]["desc"] == "a desk by a window"


class TestGenerationRouting:
    """``content.generate_images`` must route chart plans away from image-gen —
    diffusion renders axis labels as garbled glyphs, which is the whole reason
    this path exists."""

    def test_chart_plans_are_excluded_from_the_image_gen_batch(self):
        import inspect

        from modules.content.atoms import content_generate_images as atom

        src = inspect.getsource(atom.run)
        assert 'not p.get("chart_target")' in src

    def test_the_provider_receives_a_spec_never_a_query(self):
        """The key→data step happens in the atom via the catalog service; the
        provider is handed finished JSON and has no query surface."""
        import inspect

        from modules.content.atoms import content_generate_images as atom

        src = inspect.getsource(atom._render_chart)
        assert "from services.chart_catalog import resolve" in src
        assert "json.dumps" in src
        for banned in ("select ", "conn.fetch", "pool.acquire"):
            assert banned not in src.lower()

    async def test_an_unresolvable_key_leaves_an_empty_slot(self):
        from modules.content.atoms import content_generate_images as atom

        with patch(
            "services.chart_catalog.resolve", AsyncMock(return_value=None),
        ):
            out = await atom._render_chart(
                "made-up", site_config=None, task_id="t", post_id=None,
                num="1", pool=object(),
            )
        assert out == {"num": "1", "url": None, "alt_text": "", "source": "none"}

    async def test_a_rendered_chart_carries_the_data_matrix_as_alt_text(self):
        from modules.content.atoms import content_generate_images as atom
        from services.chart_render import ChartSpec, Series

        spec = ChartSpec(
            form="bar", title="T", categories=["a", "b"],
            series=[Series("s", [1.0, 2.0])], source="cost_logs",
        )
        result = SimpleNamespace(
            url="https://cdn/x.png", alt_text="Bar chart: T. a: s 1. b: s 2.",
            width=1200, height=400, metadata={"chart_source": "cost_logs"},
        )
        provider = SimpleNamespace(
            name="chart", fetch=AsyncMock(return_value=[result]),
        )
        with (
            patch("services.chart_catalog.resolve", AsyncMock(return_value=spec)),
            patch("plugins.registry.get_image_providers", lambda: [provider]),
            patch(
                "modules.content.atoms._image_helpers.record_inline_image_asset",
                AsyncMock(return_value=None),
            ),
        ):
            out = await atom._render_chart(
                "llm-decode-vs-delivered", site_config=None, task_id="t",
                post_id=None, num="1", pool=object(),
            )
        assert out["source"] == "chart"
        assert out["url"] == "https://cdn/x.png"
        assert "a: s 1" in out["alt_text"]

    async def test_a_provider_crash_leaves_an_empty_slot(self):
        """A chart must never break the post."""
        from modules.content.atoms import content_generate_images as atom
        from services.chart_render import ChartSpec, Series

        spec = ChartSpec(
            form="bar", title="T", categories=["a"], series=[Series("s", [1.0])],
        )
        provider = SimpleNamespace(
            name="chart", fetch=AsyncMock(side_effect=RuntimeError("boom")),
        )
        with (
            patch("services.chart_catalog.resolve", AsyncMock(return_value=spec)),
            patch("plugins.registry.get_image_providers", lambda: [provider]),
        ):
            out = await atom._render_chart(
                "llm-decode-vs-delivered", site_config=None, task_id="t",
                post_id=None, num="1", pool=object(),
            )
        assert out["url"] is None and out["source"] == "none"


class TestWriterPrompt:
    def test_the_skill_pack_enumerates_charts_and_forbids_inventing_keys(self):
        from pathlib import Path

        import services  # noqa: F401 — locate the package root

        skill = (
            Path(services.__file__).resolve().parent.parent
            / "skills" / "content" / "blog-generation" / "SKILL.md"
        )
        text = skill.read_text(encoding="utf-8")
        assert "[CHART: chart-key]" in text
        assert "{chart_targets}" in text
        # The writer must never author the data.
        assert "never put data, numbers, or a description in the brackets" in text
