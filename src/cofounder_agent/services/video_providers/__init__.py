"""Video provider plugins.

Each provider implements the :class:`VideoProvider <plugins.video_provider.VideoProvider>`
Protocol and generates a video from a text prompt or a still image
(Wan 2.1/2.2 is the reference provider).

Selection lives in ``app_settings.video_engine`` — flip it to swap
engines without touching code. Per-provider config lives under
``app_settings.plugin.video_provider.<name>.*``.
"""
