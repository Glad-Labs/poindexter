"""Core caption providers shipped with Poindexter.

Each module in this package exposes one ``CaptionProvider``-shaped
class registered via the ``poindexter.caption_providers`` entry_point
group. The video pipeline's ``stitch`` Stage picks one by name from
``app_settings.video_caption_engine`` (default: ``whisper_local``).

See :mod:`plugins.caption_provider` for the Protocol contract.
"""
