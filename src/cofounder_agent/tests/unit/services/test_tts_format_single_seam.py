"""The TTS output format must resolve through exactly ONE seam.

2026-07-17 audit finding. `podcast_tts_format` had FOUR disagreeing values:

* `settings_defaults.py`                      -> 'mp3'
* `0000_baseline.seeds.sql`                   -> 'wav'   (fixed in this PR)
* `tts_service._DEFAULT_FORMAT`               -> 'mp3'
* `generate_media_scripts.py` inline fallback -> 'wav'

`wav` is not a preference — it is the **unrecoverable** Speaches failure mode
(#1696/#1706): only the first segment's RIFF header is valid, and `ffmpeg -c
copy` remux cannot repair it. So on a fresh install the stage named its temp
file `.wav` while `tts_service` synthesized `mp3` — a suffix that lied about
its contents, from two literals that were free to drift apart.

Fixing the two literals to both say 'mp3' would leave two literals. This pins
the actual invariant: there is one resolver, and both callers use it, so they
*cannot* disagree again.
"""

from __future__ import annotations

from services import tts_service


class _Cfg:
    """Minimal SiteConfig stand-in: only `podcast_tts_format` is interesting."""

    def __init__(self, fmt: str | None = None) -> None:
        self._fmt = fmt

    def get(self, key: str, default=None):
        if key == "podcast_tts_format":
            return self._fmt if self._fmt is not None else default
        return default


def test_unset_format_resolves_to_mp3_never_wav() -> None:
    """An unconfigured install must not land on wav — it is unrecoverable."""
    assert tts_service.resolve_tts_format(_Cfg()) == "mp3"


def test_unset_format_with_no_site_config_resolves_to_mp3() -> None:
    """The stage may hold no site_config at all (`sc` can be None)."""
    assert tts_service.resolve_tts_format(None) == "mp3"


def test_configured_format_is_honoured() -> None:
    assert tts_service.resolve_tts_format(_Cfg("opus")) == "opus"


def test_empty_string_falls_back_rather_than_yielding_a_dotfile() -> None:
    """'' is the app_settings unset sentinel. Passing it through would name the
    temp file `.` + '' — a dotfile with no extension."""
    assert tts_service.resolve_tts_format(_Cfg("")) == "mp3"


def test_format_is_normalised_to_lowercase() -> None:
    assert tts_service.resolve_tts_format(_Cfg("MP3")) == "mp3"


def test_media_scripts_stage_uses_the_shared_resolver() -> None:
    """The stage must not carry its own format literal. This is the drift
    guard: a re-introduced inline default would make the file suffix disagree
    with the synthesized bytes again."""
    from pathlib import Path

    stage = (
        Path(tts_service.__file__).resolve().parents[1]
        / "modules" / "content" / "stages" / "generate_media_scripts.py"
    )
    src = stage.read_text(encoding="utf-8")
    assert "resolve_tts_format" in src, (
        "generate_media_scripts must resolve the TTS format through "
        "tts_service.resolve_tts_format, not a local literal"
    )
    assert '"wav"' not in src and "'wav'" not in src, (
        "generate_media_scripts still carries a wav literal — wav is the "
        "unrecoverable Speaches failure mode (#1696/#1706)"
    )
