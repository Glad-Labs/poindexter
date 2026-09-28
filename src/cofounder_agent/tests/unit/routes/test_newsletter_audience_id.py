"""The Resend segment id guard lives in one place (CodeQL #462).

The id is spliced into URL paths, so it must be a plain token. The pattern
(``SEGMENT_ID_RE``) moved from this route into services/newsletter_audience.py
together with the segment mirror, and is tested there. This pins that the route
keeps no private copy of the guard or the Resend client, so the two cannot
drift apart.
"""
from __future__ import annotations

import pytest

from poindexter.routes import newsletter_routes as nr


@pytest.mark.unit
def test_route_has_no_private_copy_of_the_resend_client():
    for name in ("_AUDIENCE_ID_RE", "_RESEND_UA", "_sync_to_resend_audience"):
        assert not hasattr(nr, name), f"{name} belongs in services.newsletter_audience"
