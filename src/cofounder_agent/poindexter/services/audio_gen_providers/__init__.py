"""Audio-generation provider plugins (music / SFX / ambient).

Each module here implements the :class:`AudioGenProvider <plugins.audio_gen_provider.AudioGenProvider>`
Protocol. Discovered via the ``poindexter.audio_gen_providers``
setuptools entry_points group.

Speech synthesis (TTS) lives elsewhere — see the (forthcoming)
``services/tts_providers/`` package and ``plugins/tts_provider.py``.
"""
