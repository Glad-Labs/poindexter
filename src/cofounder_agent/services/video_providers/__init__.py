"""Video provider plugins.

Each provider implements the :class:`VideoProvider <plugins.video_provider.VideoProvider>`
Protocol and either generates a video from a text prompt (true T2V —
Wan 2.1 1.3B is the first such provider) or composes a slideshow from
pre-rendered images + audio (the legacy ``ken_burns_slideshow`` pipeline).

Selection lives in ``app_settings.video_engine`` — flip it to swap
engines without touching code. Per-provider config lives under
``app_settings.plugin.video_provider.<name>.*``.
"""
