"""Tests for ``services.media_quality_service`` (Layer 1).

The ffmpeg / ffprobe subprocess calls are stubbed so the tests don't
need real audio files. We focus on the decision logic:

- Threshold lookups fall back to defaults when app_settings is silent
- Failing thresholds flip the row to ``status='rejected'`` with
  ``decided_by='auto:layer1'`` and the offending signal in ``notes``
- Passing thresholds leave the row alone (still ``pending``) but write
  ``quality_score=1.0`` + ``quality_signals`` JSON
- ``evaluate_video`` rejects unknown media values (no silent default)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import media_quality_service


@pytest.fixture
def mock_db() -> MagicMock:
    db = MagicMock()
    # _get_threshold reads via fetchrow; default returns None so the
    # service falls back to _DEFAULT_THRESHOLDS.
    db.fetchrow = AsyncMock(return_value=None)
    db.execute = AsyncMock(return_value="UPDATE 1")
    return db


# ---------------------------------------------------------------------------
# evaluate_podcast — auto-reject on Layer 1 failures
# ---------------------------------------------------------------------------


async def test_podcast_auto_rejects_when_too_short(
    mock_db: MagicMock, tmp_path,
) -> None:
    """Duration under the threshold trips the auto-reject path."""
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x" * 100_000)  # > min_file_size_bytes default

    with patch.object(
        media_quality_service, "_probe_duration", return_value=5.0,
    ), patch.object(
        media_quality_service, "_probe_silence_ratio", return_value=0.1,
    ):
        result = await media_quality_service.evaluate_podcast(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
        )

    assert result["score"] == 0.0
    assert "duration_seconds" in result["layer1_failures"][0]
    # The UPDATE that fires must be the rejecting branch.
    update_sql = mock_db.execute.call_args.args[0]
    assert "status = 'rejected'" in update_sql
    assert "auto:layer1" in update_sql


async def test_podcast_auto_rejects_when_silence_too_high(
    mock_db: MagicMock, tmp_path,
) -> None:
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x" * 100_000)

    with patch.object(
        media_quality_service, "_probe_duration", return_value=120.0,
    ), patch.object(
        media_quality_service, "_probe_silence_ratio", return_value=0.95,
    ):
        result = await media_quality_service.evaluate_podcast(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
        )

    assert result["score"] == 0.0
    assert any("silence_ratio" in f for f in result["layer1_failures"])


async def test_podcast_passes_layer1_when_signals_clean(
    mock_db: MagicMock, tmp_path,
) -> None:
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x" * 1_000_000)

    with patch.object(
        media_quality_service, "_probe_duration", return_value=180.0,
    ), patch.object(
        media_quality_service, "_probe_silence_ratio", return_value=0.05,
    ):
        result = await media_quality_service.evaluate_podcast(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
        )

    assert result["score"] == 1.0
    assert result["layer1_failures"] == []
    # The UPDATE that fires must be the passing branch (no status change).
    update_sql = mock_db.execute.call_args.args[0]
    assert "status = 'rejected'" not in update_sql
    assert "quality_score = $3" in update_sql


async def test_podcast_persists_signals_as_json(
    mock_db: MagicMock, tmp_path,
) -> None:
    """Signals dict must round-trip through JSON encode."""
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x" * 1_000_000)

    with patch.object(
        media_quality_service, "_probe_duration", return_value=120.0,
    ), patch.object(
        media_quality_service, "_probe_silence_ratio", return_value=0.1,
    ):
        await media_quality_service.evaluate_podcast(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
        )

    # The signals JSON is the 4th positional arg of the passing UPDATE.
    json_arg = mock_db.execute.call_args.args[4]
    parsed = json.loads(json_arg)
    assert parsed["duration_seconds"] == 120.0
    assert parsed["silence_ratio"] == 0.1


# ---------------------------------------------------------------------------
# evaluate_video
# ---------------------------------------------------------------------------


async def test_video_rejects_unknown_medium(mock_db: MagicMock) -> None:
    """Typo'd medium MUST fail loud — no silent fallback."""
    with pytest.raises(ValueError, match="unsupported medium"):
        await media_quality_service.evaluate_video(
            mock_db, "post-id", "/tmp/f.mp4", medium="audio",
        )


async def test_video_auto_rejects_when_too_small(
    mock_db: MagicMock, tmp_path,
) -> None:
    f = tmp_path / "test.mp4"
    f.write_bytes(b"x" * 10)  # way below default min_file_size_bytes

    with patch.object(
        media_quality_service, "_probe_duration", return_value=30.0,
    ):
        result = await media_quality_service.evaluate_video(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
            medium="video",
        )

    assert result["score"] == 0.0
    assert any("file_size_bytes" in fl for fl in result["layer1_failures"])


async def test_video_passes_layer1_when_signals_clean(
    mock_db: MagicMock, tmp_path,
) -> None:
    f = tmp_path / "test.mp4"
    f.write_bytes(b"x" * 5_000_000)

    with patch.object(
        media_quality_service, "_probe_duration", return_value=45.0,
    ):
        result = await media_quality_service.evaluate_video(
            mock_db, "00000000-0000-0000-0000-000000000001", str(f),
            medium="video",
        )

    assert result["score"] == 1.0
    assert result["layer1_failures"] == []


# ---------------------------------------------------------------------------
# _get_threshold — app_settings override + numeric coercion
# ---------------------------------------------------------------------------


async def test_threshold_falls_back_to_default(mock_db: MagicMock) -> None:
    mock_db.fetchrow.return_value = None
    val = await media_quality_service._get_threshold(
        mock_db, "media.podcast.min_duration_seconds",
    )
    assert val == 30.0  # _DEFAULT_THRESHOLDS


async def test_threshold_uses_app_settings_override(mock_db: MagicMock) -> None:
    mock_db.fetchrow.return_value = {"value": "90"}
    val = await media_quality_service._get_threshold(
        mock_db, "media.podcast.min_duration_seconds",
    )
    assert val == 90.0


async def test_threshold_invalid_value_falls_back(mock_db: MagicMock) -> None:
    """A garbled value (operator typo) must NOT crash — fall back to default."""
    mock_db.fetchrow.return_value = {"value": "not-a-number"}
    val = await media_quality_service._get_threshold(
        mock_db, "media.podcast.min_duration_seconds",
    )
    assert val == 30.0
