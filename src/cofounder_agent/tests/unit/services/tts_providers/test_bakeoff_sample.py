"""The built-in bake-off sample is present and exercises pronunciation + emotion."""

import pytest


@pytest.mark.unit
def test_sample_script_is_substantial_and_exercises_pronunciation():
    from services.tts_providers.bakeoff_sample import SAMPLE_SCRIPT

    assert isinstance(SAMPLE_SCRIPT, str)
    # ~150 words — enough audio to judge naturalness across a few sentences.
    assert len(SAMPLE_SCRIPT.split()) >= 100
    # Deliberately contains tokens the tts_pronunciations map rewrites.
    assert "VRAM" in SAMPLE_SCRIPT
    assert "GHz" in SAMPLE_SCRIPT
    # Multiple sentences so emotion/prosody has something to work with.
    assert SAMPLE_SCRIPT.count(".") >= 4


@pytest.mark.unit
def test_bakeoff_engine_defaults_seeded():
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS["plugin.tts_provider.cosyvoice2.base_url"] == "http://cosyvoice2:8000/v1"
    assert DEFAULTS["plugin.tts_provider.cosyvoice2.model"] == "cosyvoice2"
    assert DEFAULTS["plugin.tts_provider.chatterbox.base_url"] == "http://chatterbox:8000/v1"
    assert DEFAULTS["plugin.tts_provider.chatterbox.model"] == "chatterbox"
    # Emotion knobs have sane, comparable starting values.
    assert DEFAULTS["plugin.tts_provider.chatterbox.exaggeration"] == "0.5"
    assert DEFAULTS["plugin.tts_provider.chatterbox.cfg_weight"] == "0.5"
    # instruct is the unset sentinel (neutral) until the operator tunes it.
    assert DEFAULTS["plugin.tts_provider.cosyvoice2.instruct"] == ""
