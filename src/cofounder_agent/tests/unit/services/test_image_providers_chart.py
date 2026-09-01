"""Unit tests for ``services/image_providers/chart.py``.

The provider draws a chart from a data spec and uploads it. Its security
posture is the inverse of a prompt-driven provider: there is no query surface,
so the tests pin that a malformed payload degrades to "no image for this slot"
rather than raising into the pipeline — and that the alt text carries the data.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.image_providers.chart import ChartProvider, ChartSpecError, parse_spec

_MODULE = "services.image_providers.chart"

_PAYLOAD = {
    "form": "bar",
    "title": "Decode vs delivered",
    "subtitle": "same models, same GPU",
    "categories": ["qwen2.5:7b", "phi4:14b"],
    "series": [
        {"label": "Raw decode", "values": [235.1, 124.7]},
        {"label": "Delivered", "values": [55.6, 25.3]},
    ],
    "value_label": "output tokens / second",
    "source": "cost_logs — 9,110 calls",
}


class TestParseSpec:
    def test_parses_a_json_string(self):
        spec = parse_spec(json.dumps(_PAYLOAD))
        assert spec.form == "bar"
        assert spec.categories == ["qwen2.5:7b", "phi4:14b"]
        assert [s.label for s in spec.series] == ["Raw decode", "Delivered"]
        assert spec.series[0].values == [235.1, 124.7]

    def test_parses_a_mapping_directly(self):
        assert parse_spec(dict(_PAYLOAD)).title == "Decode vs delivered"

    def test_width_falls_back_to_the_configured_default(self):
        payload = {k: v for k, v in _PAYLOAD.items() if k != "width"}
        assert parse_spec(payload, default_width=900).width == 900

    def test_spec_width_wins_over_the_default(self):
        assert parse_spec({**_PAYLOAD, "width": 1600}, default_width=900).width == 1600

    @pytest.mark.parametrize(
        "payload,match",
        [
            ("not json at all", "not valid JSON"),
            ("[1, 2, 3]", "must be a JSON object"),
            ('{"categories": ["a"]}', "no 'series' list"),
            ('{"categories": ["a"], "series": []}', "no 'series' list"),
            ('{"series": [{"label": "s", "values": [1]}]}', "no 'categories' list"),
        ],
    )
    def test_rejects_malformed_payloads(self, payload, match):
        with pytest.raises(ChartSpecError, match=match):
            parse_spec(payload)

    def test_rejects_non_numeric_values(self):
        payload = {**_PAYLOAD, "series": [{"label": "s", "values": ["fast", "slow"]}]}
        with pytest.raises(ChartSpecError, match="non-numeric value"):
            parse_spec(payload)

    def test_rejects_a_ragged_spec(self):
        """A chart drawn from half a payload states something false."""
        payload = {**_PAYLOAD, "series": [{"label": "s", "values": [1.0]}]}
        with pytest.raises(ChartSpecError, match="values but there are 2"):
            parse_spec(payload)

    def test_rejects_an_unknown_form(self):
        with pytest.raises(ChartSpecError, match="unsupported chart form"):
            parse_spec({**_PAYLOAD, "form": "pie"})


class TestChartProviderFetch:
    def test_declares_its_kind(self):
        assert ChartProvider().name == "chart"
        assert ChartProvider().kind == "chart"

    async def test_empty_payload_returns_no_image(self):
        assert await ChartProvider().fetch("", {}) == []

    async def test_malformed_payload_degrades_to_no_image(self):
        """The slot falls through to the caller's normal handling, never raises."""
        assert await ChartProvider().fetch("{bad json", {}) == []

    async def test_render_failure_degrades_to_no_image(self):
        with patch(f"{_MODULE}.render_chart", AsyncMock(return_value=None)):
            assert await ChartProvider().fetch(json.dumps(_PAYLOAD), {}) == []

    async def test_returns_an_image_result_with_the_data_in_alt_text(self):
        with (
            patch(f"{_MODULE}.render_chart", AsyncMock(return_value=b"\x89PNG-bytes")),
            patch(f"{_MODULE}._upload", AsyncMock(return_value="https://cdn/x.png")),
        ):
            results = await ChartProvider().fetch(json.dumps(_PAYLOAD), {})
        assert len(results) == 1
        r = results[0]
        assert r.url == "https://cdn/x.png"
        assert r.source == "chart"
        assert r.width == 1200
        # Alt text is the table view a PNG cannot carry.
        for token in ("qwen2.5:7b", "235", "55.6"):
            assert token in r.alt_text
        assert r.metadata["chart_form"] == "bar"
        assert r.metadata["series"] == ["Raw decode", "Delivered"]

    async def test_upload_none_serves_a_local_file_url(self):
        with patch(f"{_MODULE}.render_chart", AsyncMock(return_value=b"png")):
            results = await ChartProvider().fetch(
                json.dumps(_PAYLOAD), {"upload_to": "none"},
            )
        assert results[0].url.startswith("file://")

    async def test_upload_failure_falls_back_to_the_local_file(self):
        """Losing the CDN must not lose the chart."""
        with (
            patch(f"{_MODULE}.render_chart", AsyncMock(return_value=b"png")),
            patch(f"{_MODULE}._upload", AsyncMock(side_effect=RuntimeError("no creds"))),
        ):
            results = await ChartProvider().fetch(json.dumps(_PAYLOAD), {})
        assert results[0].url.startswith("file://")

    async def test_scale_and_timeout_come_from_config(self):
        render = AsyncMock(return_value=b"png")
        with (
            patch(f"{_MODULE}.render_chart", render),
            patch(f"{_MODULE}._upload", AsyncMock(return_value="https://cdn/x.png")),
        ):
            await ChartProvider().fetch(
                json.dumps(_PAYLOAD), {"scale": 3, "timeout_ms": 9000},
            )
        assert render.await_args.kwargs["scale"] == 3
        assert render.await_args.kwargs["timeout_ms"] == 9000

    async def test_provider_contains_no_sql(self):
        """Structural: an image plugin must never fetch its own data."""
        import inspect

        import services.image_providers.chart as mod

        src = inspect.getsource(mod).lower()
        for token in ("select ", "insert ", "conn.fetch", "pool.acquire"):
            assert token not in src, f"{token!r} must not appear in an image provider"
