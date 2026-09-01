"""ChartProvider — render a data chart as a post image.

``blog-generation/SKILL.md`` forbids the writer from asking for a chart because
a diffusion model renders axis labels as garbled glyphs. ``ScreenshotProvider``
closed half that gap for posts about Poindexter's own surfaces; this closes the
other half for posts about *measurements*, where the honest illustration is the
numbers themselves drawn to scale.

**The data is never authored by a model.** The payload is a chart *spec* —
categories and series values — supplied by whatever computed them (a job, an
atom, a benchmark sweep). This provider only draws and uploads. That boundary
is deliberate and matches the screenshot provider's target-allowlist lesson:
an image plugin that could fetch its own data would need a query surface, and a
query surface reachable from a writer-emitted marker is an injection seam. Here
there is nothing to inject — a malformed spec fails ``ChartSpec.validate()``
and the slot falls through to the caller's normal "no image" handling.

Consequently **there is no SQL in this file and there should never be one.**
A future named-chart surface (operator-authored queries in app_settings, keyed
like ``plugin.image_provider.screenshot.targets``) is the natural next step, but
it belongs in a service that owns the query, handing the result here as a spec.

Payload: a JSON object matching ``services.chart_render.ChartSpec`` —

    {"form": "bar", "title": "...", "categories": ["a", "b"],
     "series": [{"label": "...", "values": [1, 2]}],
     "subtitle": "...", "value_suffix": "", "value_label": "",
     "source": "..."}

Config (``plugin.image_provider.chart`` in app_settings):

- ``upload_to`` (default ``"r2"``) — ``"r2"`` or ``"none"`` (``file://``).
- ``scale`` (default 2) — device pixel ratio; 2 keeps text crisp at blog width.
- ``width`` (default 1200) — spec ``width`` wins when present.
- ``timeout_ms`` (default 30000).

Kind: ``"chart"`` — neither a catalog search, a diffusion render, nor a
screenshot of a live surface.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from contextlib import suppress
from typing import Any

from plugins.image_provider import ImageResult
from services.chart_render import ChartSpec, Series, chart_alt_text, render_chart

logger = logging.getLogger(__name__)

_DEFAULT_SCALE = 2
_DEFAULT_WIDTH = 1200
_DEFAULT_TIMEOUT_MS = 30000


class ChartSpecError(ValueError):
    """The payload is not a usable chart spec."""


def parse_spec(payload: Any, *, default_width: int = _DEFAULT_WIDTH) -> ChartSpec:
    """Build a validated :class:`ChartSpec` from a JSON string or mapping.

    Raises :class:`ChartSpecError` on anything unusable — never returns a
    partially-populated spec, because a chart drawn from half a payload is a
    chart that states something false.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            raise ChartSpecError(f"payload is not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise ChartSpecError(f"payload must be a JSON object, got {type(payload).__name__}")

    raw_series = payload.get("series")
    if not isinstance(raw_series, list) or not raw_series:
        raise ChartSpecError("payload has no 'series' list")
    series: list[Series] = []
    for entry in raw_series:
        if not isinstance(entry, dict):
            raise ChartSpecError("each series must be an object with label + values")
        values = entry.get("values")
        if not isinstance(values, list):
            raise ChartSpecError(f"series {entry.get('label')!r} has no 'values' list")
        try:
            numeric = [float(v) for v in values]
        except (TypeError, ValueError) as e:
            raise ChartSpecError(
                f"series {entry.get('label')!r} has a non-numeric value: {e}"
            ) from e
        series.append(Series(label=str(entry.get("label", "")), values=numeric))

    categories = payload.get("categories")
    if not isinstance(categories, list):
        raise ChartSpecError("payload has no 'categories' list")

    spec = ChartSpec(
        form=str(payload.get("form", "bar")),  # type: ignore[arg-type]
        title=str(payload.get("title", "")),
        categories=[str(c) for c in categories],
        series=series,
        subtitle=str(payload.get("subtitle", "")),
        value_label=str(payload.get("value_label", "")),
        value_suffix=str(payload.get("value_suffix", "")),
        source=str(payload.get("source", "")),
        width=int(payload.get("width", default_width) or default_width),
    )
    try:
        spec.validate()
    except ValueError as e:
        raise ChartSpecError(str(e)) from e
    return spec


class ChartProvider:
    """Draw a chart from a data spec and return it as an ``ImageResult``."""

    name = "chart"
    kind = "chart"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[ImageResult]:
        payload = (query_or_prompt or "").strip()
        if not payload:
            return []

        default_width = int(config.get("width", _DEFAULT_WIDTH) or _DEFAULT_WIDTH)
        try:
            spec = parse_spec(payload, default_width=default_width)
        except ChartSpecError as e:
            # Loud, but not fatal: an unusable spec means this slot gets no
            # image, exactly like a screenshot target that fails to resolve.
            logger.error("[ChartProvider] %s", e)
            return []

        png = await render_chart(
            spec,
            scale=int(config.get("scale", _DEFAULT_SCALE) or _DEFAULT_SCALE),
            timeout_ms=int(
                config.get("timeout_ms", _DEFAULT_TIMEOUT_MS) or _DEFAULT_TIMEOUT_MS,
            ),
        )
        if not png:
            # render_chart returns None (never raises) when playwright is
            # missing or chromium failed — it has already logged the cause.
            logger.warning(
                "[ChartProvider] render returned nothing for %r", spec.title,
            )
            return []

        with tempfile.NamedTemporaryFile(
            suffix=".png", prefix="chart-", delete=False,
        ) as tmp:
            tmp.write(png)
            local_path = tmp.name

        upload_to = str(config.get("upload_to", "r2") or "r2")
        image_url = f"file://{local_path}"
        if upload_to == "r2":
            try:
                image_url = await _upload(
                    local_path, site_config=config.get("_site_config"),
                )
            except Exception as e:  # noqa: BLE001 — serving the local path beats losing the chart
                logger.warning(
                    "[ChartProvider] upload failed, serving file:// URL: %s", e,
                )

        return [
            ImageResult(
                url=image_url,
                thumbnail=image_url,
                photographer="Glad Labs",
                photographer_url="",
                width=spec.width,
                height=None,
                # The alt text carries the full data matrix — a PNG has no
                # table view or hover layer, so this is where the numbers live
                # for a screen reader and for any later pass over the post.
                alt_text=chart_alt_text(spec),
                caption=spec.subtitle,
                source=self.name,
                search_query=spec.title,
                metadata={
                    "chart_form": spec.form,
                    "chart_title": spec.title,
                    "chart_source": spec.source,
                    "series": [s.label for s in spec.series],
                    "categories": spec.categories,
                    "local_path": local_path,
                },
            ),
        ]


async def _upload(path: str, *, site_config: Any) -> str:
    """Upload a rendered PNG through the shared object-store service.

    The service transcodes PNG -> WebP@80 and fits the result inside
    1920x1920. A 1200pt spec rendered at ``scale=2`` is 2400px, so it lands at
    1920px — still ~1.6x the CSS width the blog displays it at, which keeps
    axis text crisp after the downscale.
    """
    from services.r2_upload_service import R2UploadService

    if site_config is None:
        raise RuntimeError(
            "chart upload requires site_config; the image dispatcher seeds it "
            "as config['_site_config']"
        )
    svc = R2UploadService(site_config=site_config)
    key = f"images/charts/{uuid.uuid4().hex[:8]}.png"
    url = await svc.upload_to_r2(path, key, "image/png")
    if not url:
        raise RuntimeError("r2_upload_service returned empty URL")
    with suppress(OSError):  # silent-ok: temp cleanup, upload already landed
        os.remove(path)
    return url


__all__ = ["ChartProvider", "ChartSpecError", "parse_spec"]
