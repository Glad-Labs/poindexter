"""Core media compositors shipped with Poindexter.

Each module in this package exposes one ``MediaCompositor``-shaped
class registered via the ``poindexter.media_compositors`` entry_point
group. The video pipeline's ``stitch`` Stage picks one by name from
``app_settings.video_compositor`` (default: ``ffmpeg_local``).

See :mod:`plugins.media_compositor` for the Protocol contract.
"""
