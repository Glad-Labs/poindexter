"""IGDBSource — pull recently-released indie games from IGDB.

IGDB (Internet Game Database) is the canonical metadata source for video
games — same authentication model as the Twitch API (since both are
owned by Amazon/Twitch). Free for non-commercial use up to 4 requests/sec.

Indie-development articles are core to the Glad Labs niche set
(see ``feedback_brand_niches.md``). This source surfaces:

* games tagged ``themes = 32`` (Indie) on IGDB
* released in the last ``config.lookback_days`` window
* sorted by release date descending

Returns a ``DiscoveredTopic`` per game, rewritten through the shared
``rewrite_as_blog_topic`` filter so titles match the news-style topic
shape the writer expects.

## Config (``plugin.topic_source.igdb`` in app_settings)

- ``enabled`` (default false) — opt-in; IGDB requires Twitch OAuth
  client credentials which Matt's box has but a fresh fork won't
- ``config.lookback_days`` (default 14) — rolling window for "recently
  released"
- ``config.limit`` (default 20) — max games per fetch
- ``config.theme_id`` (default 32) — IGDB theme id; 32 = Indie
- ``config.api_base`` (default ``https://api.igdb.com/v4``) — override
  for self-hosted Forem-style proxies
- ``config.token_endpoint`` (default ``https://id.twitch.tv/oauth2/token``)

## Required app_settings keys

- ``igdb_twitch_client_id`` (plain) — Twitch app client id
- ``igdb_twitch_client_secret`` (``is_secret=true``) — Twitch app client
  secret; encrypted at rest via plugins.secrets

Both come from the Twitch developer console — go to
https://dev.twitch.tv/console/apps, register an app, copy the credentials.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from plugins.topic_source import DiscoveredTopic
from services.topic_sources._filters import classify_category

logger = logging.getLogger(__name__)


_DEFAULT_API_BASE = "https://api.igdb.com/v4"
_DEFAULT_TOKEN_ENDPOINT = "https://id.twitch.tv/oauth2/token"
_DEFAULT_THEME_INDIE = 32

# Token cache — IGDB access tokens are valid for ~60 days. We refresh
# when within 1 day of expiry or on 401. Keyed by client_id so multiple
# operators on a shared module instance (test only — production is one
# operator) don't collide.
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_REFRESH_BUFFER_SECONDS = 24 * 60 * 60


async def _fetch_twitch_token(
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    token_endpoint: str,
) -> tuple[str, float]:
    """Exchange Twitch app credentials for an IGDB bearer token.

    Returns (token, expires_at_unix). Raises httpx.HTTPStatusError on
    auth failure — the runner catches per-source exceptions so a bad
    credential pair never tanks unrelated sources.
    """
    resp = await client.post(
        token_endpoint,
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload["access_token"]
    expires_in = int(payload.get("expires_in", 60 * 24 * 60 * 60))
    return token, time.time() + expires_in


async def _resolve_token(
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    token_endpoint: str,
) -> str:
    """Return a valid Twitch bearer token, fetching a fresh one if the
    cached entry is missing or within the refresh buffer."""
    cached = _TOKEN_CACHE.get(client_id)
    if cached is not None and cached[1] - time.time() > _REFRESH_BUFFER_SECONDS:
        return cached[0]
    token, expires_at = await _fetch_twitch_token(
        client, client_id, client_secret, token_endpoint,
    )
    _TOKEN_CACHE[client_id] = (token, expires_at)
    return token


def _format_release_phrase(unix_ts: int | None) -> str:
    """Human-readable release phrase for the rewritten topic title.

    Returns "" when the timestamp is missing or malformed — caller folds
    it into the title only when truthy.
    """
    if not unix_ts:
        return ""
    try:
        dt = datetime.fromtimestamp(int(unix_ts), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return ""
    return dt.strftime("%B %Y")


def _rewrite_indie_topic(game: dict[str, Any]) -> str | None:
    """Compose a blog-topic title from an IGDB game record.

    The shared ``rewrite_as_blog_topic`` filter is tuned for HN/Dev.to
    noise — it strips ``"<Name>: ..."`` prefixes, which is exactly the
    pattern that makes sense for indie-game posts. IGDB titles already
    come from a curated database (no Launch HN / Show HN noise), so we
    skip the rewrite filter for this source and use a shape the filter
    wouldn't mangle anyway.
    """
    name = (game.get("name") or "").strip()
    if not name:
        return None
    release = _format_release_phrase(game.get("first_release_date"))
    if release:
        return f"A look at {name}, a recent indie release from {release}"
    return f"A look at {name}, an indie game worth a closer look"


class IGDBSource:
    """Pull recently-released indie games from IGDB via Twitch OAuth."""

    name = "igdb"

    async def extract(
        self,
        pool: Any,
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        lookback_days = int(config.get("lookback_days", 14) or 14)
        limit = int(config.get("limit", 20) or 20)
        theme_id = int(config.get("theme_id", _DEFAULT_THEME_INDIE) or _DEFAULT_THEME_INDIE)
        api_base = str(
            config.get("api_base", _DEFAULT_API_BASE) or _DEFAULT_API_BASE
        ).rstrip("/")
        token_endpoint = str(
            config.get("token_endpoint", _DEFAULT_TOKEN_ENDPOINT)
            or _DEFAULT_TOKEN_ENDPOINT
        )

        # Read Twitch app credentials via the plugins.secrets seam so
        # client_secret is auto-decrypted from its enc:v1: ciphertext.
        if pool is None:
            logger.warning(
                "IGDBSource: pool unavailable — cannot read Twitch credentials, skipping",
            )
            return []

        from plugins.secrets import get_secret
        async with pool.acquire() as conn:
            client_id = await get_secret(conn, "igdb_twitch_client_id") or ""
            client_secret = await get_secret(conn, "igdb_twitch_client_secret") or ""

        if not client_id or not client_secret:
            logger.info(
                "IGDBSource: igdb_twitch_client_id / igdb_twitch_client_secret "
                "not configured — skipping. Set via `poindexter set-setting`."
            )
            return []

        since_ts = int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp())
        now_ts = int(datetime.now(timezone.utc).timestamp())

        # Apicalypse query — IGDB's POST-body query language. Newlines are
        # whitespace; semicolons terminate clauses.
        body = (
            "fields name, summary, slug, url, first_release_date, "
            "genres.name, themes.name, platforms.name; "
            f"where themes = ({theme_id}) "
            f"& first_release_date != null "
            f"& first_release_date >= {since_ts} "
            f"& first_release_date <= {now_ts}; "
            "sort first_release_date desc; "
            f"limit {limit};"
        )

        topics: list[DiscoveredTopic] = []
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
        ) as http:
            try:
                token = await _resolve_token(http, client_id, client_secret, token_endpoint)
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "IGDBSource: Twitch OAuth token exchange failed (%s) — skipping",
                    e.response.status_code,
                )
                return []
            except Exception as e:  # noqa: BLE001 — network blip / DNS / etc.
                logger.warning("IGDBSource: token fetch failed (%s) — skipping", e)
                return []

            try:
                resp = await http.post(
                    f"{api_base}/games",
                    headers={
                        "Client-ID": client_id,
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    content=body,
                    timeout=15,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("IGDBSource: IGDB request failed (%s) — skipping", e)
                return []

            if resp.status_code == 401:
                # Token revoked / Twitch invalidated — drop cache and let the
                # next cycle re-mint. One-shot retry would be possible but
                # the runner re-invokes the source on its interval anyway.
                _TOKEN_CACHE.pop(client_id, None)
                logger.warning(
                    "IGDBSource: IGDB returned 401 — token cache dropped, will refresh next cycle",
                )
                return []
            if resp.status_code != 200:
                logger.warning(
                    "IGDBSource: IGDB returned %s — body=%s",
                    resp.status_code, resp.text[:200],
                )
                return []

            try:
                games = resp.json()
            except Exception as e:  # noqa: BLE001
                logger.warning("IGDBSource: IGDB response was not JSON (%s)", e)
                return []

        for game in games or []:
            if not isinstance(game, dict):
                continue
            rewritten = _rewrite_indie_topic(game)
            if not rewritten:
                continue
            category = classify_category(rewritten)
            description = (game.get("summary") or "").strip()[:500]
            # IGDB doesn't ship an engagement score we can normalise.
            # Bias toward newer releases by using release-date freshness
            # as the relevance score: 5.0 for "released today" → 0.5
            # for "released a year ago". Clamped to [0.5, 5.0].
            relevance = 0.5
            rd = game.get("first_release_date")
            if isinstance(rd, int) and rd > 0:
                days_old = max(0, (time.time() - rd) / 86400)
                relevance = max(0.5, min(5.0, 5.0 - (days_old / 30.0)))
            topics.append(
                DiscoveredTopic(
                    title=rewritten,
                    category=category,
                    source=self.name,
                    source_url=(game.get("url") or "").strip(),
                    relevance_score=relevance,
                    description=description,
                )
            )

        logger.info(
            "IGDBSource: %d indie topics from IGDB (lookback=%dd, theme=%d)",
            len(topics), lookback_days, theme_id,
        )
        return topics
