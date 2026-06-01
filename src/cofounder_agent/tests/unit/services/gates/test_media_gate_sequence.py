"""Unit tests for ``media_gate_sequence`` (Glad-Labs/poindexter#24, #338).

Pure function — maps a post's ``media_to_generate`` array to the ordered
gate-name sequence the per-medium gate engine creates on approval. No DB.
This is the smallest, most-testable unit of the media-gated-publish wiring;
everything else (gate creation on approval, the driver) depends on it.
"""

import pytest

from services.gates.post_approval_gates import media_gate_sequence


@pytest.mark.parametrize("media,expected", [
    (["podcast", "video"], ["podcast", "video", "final"]),
    (["video", "podcast", "short"], ["podcast", "video", "short", "final"]),  # canonical order
    (["podcast"], ["podcast", "final"]),
    ([], ["final"]),                       # text-only still gets a final gate (D2 fast-path in driver)
    (["video", "bogus"], ["video", "final"]),  # unknown media dropped
])
def test_media_gate_sequence(media, expected):
    assert media_gate_sequence(media) == expected
