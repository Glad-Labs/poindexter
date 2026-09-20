"""`parked_containers` — and the structural reason it empties when inactive.

The operator console softens a service row from `down` to a neutral
"parked · game mode" when its container appears on this list. That is only safe
because the list is EMPTY whenever game mode is off: the suppression cannot
outlive the mode, and a caller cannot forget to check `active` first.

If a future change populates these lists unconditionally, a stopped sidecar
would read as deliberately parked forever — turning a real outage into a quiet
grey row. These tests exist to make that change fail loudly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from poindexter.services.game_mode import (
    CONTAINER_PREFIX_KEY,
    PARKED_SERVICES_KEY,
    UNTIL_KEY,
    status_from_config,
)
from poindexter.services.site_config import SiteConfig


def _cfg(until: str, *, parked: str = "speaches,chatterbox", prefix: str = "poindexter-"):
    return SiteConfig(
        initial_config={
            UNTIL_KEY: until,
            PARKED_SERVICES_KEY: parked,
            CONTAINER_PREFIX_KEY: prefix,
        }
    )


def _future(hours: int = 2) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def _past(hours: int = 2) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def test_active_mode_exposes_prefixed_container_names() -> None:
    st = status_from_config(_cfg(_future()))
    assert st.active is True
    assert st.parked_services == ("speaches", "chatterbox")
    assert st.parked_containers == ("poindexter-speaches", "poindexter-chatterbox")


def test_container_prefix_is_read_not_hardcoded() -> None:
    """The console matches cAdvisor rows on container name; a baked-in prefix
    here would silently stop matching on an install that changed it."""
    st = status_from_config(_cfg(_future(), prefix="glad-"))
    assert st.parked_containers == ("glad-speaches", "glad-chatterbox")


def test_expired_mode_exposes_no_containers() -> None:
    st = status_from_config(_cfg(_past()))
    assert st.active is False
    assert st.parked_containers == ()
    assert st.parked_services == ()


def test_unset_mode_exposes_no_containers() -> None:
    st = status_from_config(_cfg(""))
    assert st.active is False
    assert st.parked_containers == ()


def test_unparseable_until_exposes_no_containers() -> None:
    """A malformed timestamp reads as OFF, so it must not license suppression."""
    st = status_from_config(_cfg("not-a-timestamp"))
    assert st.active is False
    assert st.parked_containers == ()


def test_as_dict_carries_both_lists_for_the_http_surface() -> None:
    d = status_from_config(_cfg(_future())).as_dict()
    assert d["parked_containers"] == ["poindexter-speaches", "poindexter-chatterbox"]
    assert d["parked_services"] == ["speaches", "chatterbox"]
    assert d["active"] is True


def test_inactive_as_dict_cannot_license_suppression() -> None:
    d = status_from_config(_cfg(_past())).as_dict()
    assert d["active"] is False
    assert d["parked_containers"] == []
