"""Video provider plugins.

Each provider implements the :class:`VideoProvider <plugins.video_provider.VideoProvider>`
Protocol and generates a video from a text prompt or a still image
(Wan 2.1/2.2 is the reference provider).

Selection: hero clips go to the animator ``app_settings.video_generative_provider``
names, ``"comfyui"`` (:class:`ComfyUIProvider`) or ``"wan21"``
(:class:`Wan21Provider`, the default). :func:`configured_animator` is the one
reading of that setting. The shot-list renderer builds its provider from it,
and the Stage-2 dispatch gate (``services/media_infra_health.py``) probes the
server it names, so the gate always watches the animator the render will
call. Presenter (speech-to-video) shots use ComfyUI whatever the setting
says. Per-provider config lives under
``app_settings.plugin.video_provider.<name>.*`` and each provider's own
``video_*`` keys.

.. note::

   Earlier revisions of this docstring promised that swapping engines
   was "a single ``app_settings.video_engine`` flip". That setting was
   never implemented and has no readers — see Glad-Labs/poindexter#669.
   ``plugins.registry.get_video_providers()`` does enumerate registered
   providers, but nothing in production dispatches through it yet.
"""

from __future__ import annotations

from typing import Any

#: The animator an install gets when ``video_generative_provider`` is unset,
#: unreadable, or names no provider this package implements.
DEFAULT_ANIMATOR = "wan21"


def configured_animator(site_config: Any) -> str:
    """The hero-clip animator this install selects: ``"comfyui"`` or ``"wan21"``.

    Anything other than ``comfyui`` resolves to ``wan21``, because that is
    the provider the renderer builds for it.
    """
    if site_config is None:
        return DEFAULT_ANIMATOR
    try:
        choice = str(
            site_config.get("video_generative_provider", DEFAULT_ANIMATOR)
            or DEFAULT_ANIMATOR
        )
    except Exception:  # noqa: BLE001  # silent-ok: a settings read must not
        # decide which animator renders; the documented default stands.
        return DEFAULT_ANIMATOR
    return "comfyui" if choice.strip().lower() == "comfyui" else DEFAULT_ANIMATOR


__all__ = ["DEFAULT_ANIMATOR", "configured_animator"]
