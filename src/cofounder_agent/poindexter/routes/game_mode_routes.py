"""Game-mode status for operator surfaces.

``GET /api/game-mode/status`` — thin adapter over ``services.game_mode`` (no
SQL and no logic here; transport-adapter contract, ADR 2026-06-10, #1340).

Game mode parks the GPU sidecars so the operator can use the machine
(``services/game_mode.py``). Nothing outside the CLI and the brain could see
that state, so the operator console rendered five deliberately-stopped
containers as five faults — a red "N SERVICE DOWN" banner for a mode the
operator turned on themselves. This route is the seam that lets a surface tell
"parked on purpose" apart from "down".

Read-only and cache-backed: ``status_from_config`` reads the SiteConfig cache
the reload job refreshes every minute, so this is cheap enough for the
console's service poll.
"""

from fastapi import APIRouter, Depends

from middleware.api_token_auth import verify_api_token
from poindexter.services.game_mode import status_from_config
from poindexter.services.site_config import SiteConfig
from poindexter.utils.route_utils import get_site_config_dependency

router = APIRouter(
    prefix="/api/game-mode",
    tags=["game-mode"],
    dependencies=[Depends(verify_api_token)],
)


@router.get("/status")
async def get_game_mode_status(
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict:
    """Whether game mode is on, until when, and what it parks.

    ``parked_services`` (compose names) and ``parked_containers`` (the same
    with the configured prefix) are both EMPTY while inactive, so a consumer
    that uses them to soften a service's status cannot suppress anything once
    game mode expires.
    """
    return status_from_config(site_config).as_dict()
