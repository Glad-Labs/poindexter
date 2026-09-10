"""Core TTS providers shipped with Poindexter.

Each module in this package exposes one ``TTSProvider``-shaped class
registered via the ``poindexter.tts_providers`` entry_point group.
The pipeline picks one by name from
``app_settings.podcast_tts_engine`` (default: ``speaches`` — the
Speaches/Kokoro HTTP container, Apache 2.0). The ``kokoro`` provider
is the local-model alternative when the container is not available.

See :mod:`plugins.tts_provider` for the Protocol contract.
"""
