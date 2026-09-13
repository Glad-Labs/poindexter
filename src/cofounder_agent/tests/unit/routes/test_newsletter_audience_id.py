"""The Resend audience id is spliced into a URL path; it must be a plain token (CodeQL #462)."""
from __future__ import annotations

import pytest

from poindexter.routes import newsletter_routes as nr


@pytest.mark.unit
def test_audience_id_pattern_accepts_ids_and_rejects_path_shapes():
    ok = ["78261eea-8f8b-4381-83c6-79fa7120f1cf", "aud_123", "ABC-def_9"]
    bad = ["../contacts", "abc/def", "x?y=1", "", "a b", "é"]
    assert all(nr._AUDIENCE_ID_RE.fullmatch(v) for v in ok)
    assert not any(nr._AUDIENCE_ID_RE.fullmatch(v) for v in bad)
