"""ImageProvider — data-ingestion Protocol for featured + inline images.

An ImageProvider either SEARCHES an external catalog (Pexels, Unsplash,
Pixabay) or GENERATES images (SDXL, Flux, etc.) and returns
:class:`ImageResult` metadata that callers can drop into a post.

Replaces the god-file ``services/image_service.py`` (1132 lines, SDXL
pipeline + Pexels API + caching + model management all mushed together).
Phase G migration (GitHub #71) splits each concern into its own module
under ``services/image_providers/``.

Register an ImageProvider via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.image_providers"]
    pexels = "cofounder_agent.services.image_providers.pexels:PexelsProvider"
    sdxl = "cofounder_agent.services.image_providers.sdxl:SdxlProvider"

Per-install config lives in ``app_settings.plugin.image_provider.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ImageResult:
    """A single image returned by a provider.

    Field semantics match ``services.image_service.FeaturedImageMetadata``
    for wire compatibility during Phase G migration — same keys, same
    defaults, so downstream callers that expect the legacy type can
    consume this one via ``ImageResult.to_dict()``.
    """

    url: str
    thumbnail: str = ""
    photographer: str = "Unknown"
    photographer_url: str = ""
    width: int | None = None
    height: int | None = None
    alt_text: str = ""
    caption: str = ""
    source: str = "unknown"      # provider name: pexels, sdxl, flux, etc.
    search_query: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Empty thumbnail → fall back to full URL. Matches legacy behavior
        # (FeaturedImageMetadata.thumbnail = thumbnail or url).
        if not self.thumbnail:
            self.thumbnail = self.url

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "thumbnail": self.thumbnail,
            "photographer": self.photographer,
            "photographer_url": self.photographer_url,
            "width": self.width,
            "height": self.height,
            "alt_text": self.alt_text,
            "caption": self.caption,
            "source": self.source,
            "search_query": self.search_query,
            "metadata": self.metadata,
        }


@runtime_checkable
class ImageProvider(Protocol):
    """Image-source plugin contract.

    Two implementation styles:

    - **Search providers** (Pexels, Unsplash) — query an external
      catalog with a free-text term, return the top N hits.
    - **Generation providers** (SDXL, Flux) — synthesize an image
      from a prompt + style. May take many seconds; callers should
      await with a generous timeout.

    Both paths produce the same ``list[ImageResult]`` shape so
    orchestrators can mix + match without branching.

    Attributes:
        name: Unique plugin name (matches the entry_point key + the
            ``source`` label attached to each ImageResult).
        kind: Either ``"search"`` or ``"generate"``. Orchestrators use
            this to pick the right provider for the use case (featured
            image vs inline-prompt vs product photography) without
            hardcoding provider names.
    """

    name: str
    kind: str  # "search" or "generate"

    async def fetch(
        self,
        query_or_prompt: str,
        config: dict[str, Any],
    ) -> list[ImageResult]:
        """Return zero or more ImageResult instances.

        Args:
            query_or_prompt: For search providers, the free-text search
                term. For generation providers, the text-to-image prompt.
            config: Per-install config from
                ``app_settings.plugin.image_provider.<name>`` — API
                keys, model names, width/height, style presets, etc.

        Returns:
            list[ImageResult]. Empty list when the provider has no
            matching result (rate-limited search, model not ready,
            empty catalog hit). Providers should raise on genuine
            failures (auth error, invalid config) so callers can
            fall back to another provider.
        """
        ...
