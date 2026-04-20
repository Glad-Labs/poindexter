"""services.image_providers — ImageProvider plugin implementations.

Each module is one image source: Pexels search, SDXL generation, future
Unsplash / Flux / DALL-E providers. Providers conform to
``plugins.image_provider.ImageProvider`` and register via entry_points
under ``poindexter.image_providers``.

Phase G migration (GitHub #71) — split ``services/image_service.py``
into one file per provider. First landed: Pexels search.
"""
