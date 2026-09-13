"""Request-supplied voice references stay inside the voices directories (CodeQL #228)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "tts_sidecars"))
import _voice_paths as vp  # noqa: E402,I001


@pytest.fixture
def voices(tmp_path: Path):
    root = tmp_path / "voices"
    root.mkdir()
    (root / "podcast-voice.wav").write_bytes(b"RIFF")
    outside = tmp_path / "etc"
    outside.mkdir()
    (outside / "passwd").write_text("x")
    return root, outside


@pytest.mark.unit
def test_roots_come_from_env_and_the_default_prompt(tmp_path: Path):
    roots = vp.voice_roots(default_prompt=str(tmp_path / "pinned" / "v.wav"), env={"CHATTERBOX_VOICE_DIRS": f"{tmp_path}/a:{tmp_path}/b"})
    assert roots == [(tmp_path / "a").resolve(), (tmp_path / "b").resolve(), (tmp_path / "pinned").resolve()]
    assert vp.voice_roots(env={}) == [Path(vp.DEFAULT_VOICE_DIRS).resolve()]


@pytest.mark.unit
def test_inside_is_allowed_outside_and_traversal_are_not(voices):
    root, outside = voices
    roots = [root.resolve()]
    assert vp.contained_voice_path(str(root / "podcast-voice.wav"), roots) == (root / "podcast-voice.wav").resolve()
    assert vp.contained_voice_path(str(outside / "passwd"), roots) is None
    assert vp.contained_voice_path(str(root / ".." / "etc" / "passwd"), roots) is None
    assert vp.contained_voice_path(str(root), roots) is None            # a directory is not a voice
    assert vp.contained_voice_path(str(root / "missing.wav"), roots) is None
