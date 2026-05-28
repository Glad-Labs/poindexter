"""Shot-list-driven video composition (Glad-Labs/glad-labs-stack#649).

Director output (a ``VideoShotList`` produced by the
``generate_video_shot_list`` pipeline stage) drives per-shot rendering
via the existing image / video providers and concat-assembly via
``FFmpegLocalCompositor``. This package is the renderer half of the
seam — the schema lives in ``schemas/video_shot_list``, the director
in ``services/stages/generate_video_shot_list``.

The legacy Ken Burns slideshow path
(``services/video_service.generate_video_for_post`` without a shot
list) remains the fallback when ``posts.video_shot_list`` is NULL.
"""
