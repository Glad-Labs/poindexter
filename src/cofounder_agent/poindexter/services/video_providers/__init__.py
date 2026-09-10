"""Video provider plugins.

Each provider implements the :class:`VideoProvider <plugins.video_provider.VideoProvider>`
Protocol and generates a video from a text prompt or a still image
(Wan 2.1/2.2 is the reference provider).

Selection is **per call site**, not a global setting. The shot-list
renderer (``services/video_renderers/shot_list_renderer.py``) imports
``Wan21Provider`` directly to render hero clips; the Ken Burns
slideshow path composes the rest of the timeline. Per-provider config
lives under ``app_settings.plugin.video_provider.<name>.*``.

.. note::

   Earlier revisions of this docstring promised that swapping engines
   was "a single ``app_settings.video_engine`` flip". That setting was
   never implemented and has no readers — see Glad-Labs/poindexter#669.
   ``plugins.registry.get_video_providers()`` does enumerate registered
   providers, but nothing in production dispatches through it yet.
"""
