"""``content.inject_images`` must render a chart result, not strip it.

The atom dispatches on ``result["source"]`` and its ``else`` branch REMOVES the
placeholder. ``[CHART:]`` shipped its producer (stack#3544: marker -> plan ->
catalog -> render -> R2 upload) without a matching consumer branch, so on the
first real chart-bearing draft the chart was rendered by chromium, uploaded to
R2 as ``images/charts/6713d7df.webp`` — and then dropped on the floor, leaving
three diffusion illustrations and no chart.
"""

from __future__ import annotations

import pytest

from modules.content.atoms import content_inject_images as atom

pytestmark = pytest.mark.unit

_URL = "https://cdn.test/images/charts/abc123.webp"


def _state(source: str, **over):
    st = {
        "content": "Intro.\n\n## Section\n\n[IMAGE-1: chart:llm-decode-vs-delivered]\n\nOutro.",
        "image_results": [{
            "num": "1", "url": _URL, "alt_text": "Bar chart: decode vs delivered.",
            "source": source, "width": 1200, "height": 640,
        }],
        "task_id": None,
    }
    st.update(over)
    return st


class TestChartInjection:
    async def test_a_chart_result_becomes_an_img_tag(self):
        out = await atom.run(_state("chart"))
        body = out["content"]
        assert _URL in body, (
            "the chart was rendered and uploaded — it must reach the post"
        )
        assert "[IMAGE-1:" not in body

    async def test_it_uses_the_charts_real_dimensions(self):
        """A chart is wide; the image_gen branch's 1024x1024 square would
        letterbox the axis labels and shift the page layout."""
        body = (await atom.run(_state("chart")))["content"]
        assert 'width="1200"' in body
        assert 'height="640"' in body

    async def test_the_alt_text_carrying_the_data_survives(self):
        """chart_alt_text serialises the whole series matrix — a PNG has no
        table view, so the alt attribute is where the numbers live."""
        body = (await atom.run(_state("chart")))["content"]
        assert "Bar chart: decode vs delivered." in body

    async def test_an_unknown_source_still_strips_its_placeholder(self):
        """The else branch is correct for genuinely unresolved slots — this
        pins that the chart fix did not weaken it."""
        body = (await atom.run(_state("mystery")))["content"]
        assert _URL not in body
        assert "[IMAGE-1:" not in body

    async def test_a_chart_result_with_no_url_is_stripped(self):
        st = _state("chart")
        st["image_results"][0]["url"] = None
        body = (await atom.run(st))["content"]
        assert "[IMAGE-1:" not in body
