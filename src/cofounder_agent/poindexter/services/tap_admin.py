"""Add or remove an ``external_taps`` row without SQL.

An operator can set a niche's subject, goals, sources and writer prompt from
the CLI (#1127). Adding a topic-source row, such as an RSS feed for a niche,
still took a raw ``INSERT INTO external_taps`` (poindexter#1128). That is easy
to get subtly wrong: a ``tap_type`` that names no registered topic source, or
a niche id copied from another install. The tap runner then fails on the
first fire, hours later.

This service is what ``poindexter taps create`` / ``taps delete`` delegate to.
It checks everything the runner will need before writing anything:

- the handler is a registered ``tap.<handler>`` (``integrations/registry``);
- for the topic-source handler, ``tap_type`` names a registered topic source
  plugin, which is what ``tap.builtin_topic_source`` dispatches on;
- the niche slug is a known niche (required for topic sources, which are
  per-niche);
- the name is new. ``create`` never overwrites: changing a tap is
  ``taps set-config`` / ``enable`` / ``disable``.

The row goes through ``declarative_config_service.upsert_row``, the same
whitelisted writer the other ``taps`` commands use.
"""

from __future__ import annotations

import re
from typing import Any

from poindexter.services import declarative_config_service as dcs

#: The handler that runs one topic source per row (``row.tap_type`` names it).
TOPIC_SOURCE_HANDLER = "builtin_topic_source"
#: Sources ``tap.builtin_topic_source`` branches to directly, with no plugin.
_DIRECT_TOPIC_SOURCES = frozenset({"internal_rag"})
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_SURFACE = "taps"


class TapAdminError(ValueError):
    """A tap that can't be created or deleted as asked; the message says why."""


def known_topic_sources() -> set[str]:
    """Every name ``tap.builtin_topic_source`` can dispatch to."""
    from poindexter.plugins.registry import get_topic_sources

    names = {getattr(p, "name", type(p).__name__) for p in get_topic_sources()}
    return names | set(_DIRECT_TOPIC_SOURCES)


def registered_tap_handlers() -> set[str]:
    """Handler names registered on the ``tap`` surface (without the prefix)."""
    from poindexter.services.integrations import registry
    from poindexter.services.integrations.handlers import load_all

    load_all()
    return {name.split(".", 1)[1] for name in registry.registered_names("tap")}


async def _niche_id(pool: Any, slug: str) -> Any:
    from poindexter.services.niche_service import NicheService, get_known_niche_slugs

    niche = await NicheService(pool).get_by_slug(slug)
    if niche is None:
        known = ", ".join(sorted(await get_known_niche_slugs(pool))) or "(none)"
        raise TapAdminError(f"no niche with slug {slug!r}; known niches: {known}")
    return niche.id


async def create_tap(
    pool: Any,
    *,
    name: str,
    tap_type: str,
    niche_slug: str | None = None,
    handler: str = TOPIC_SOURCE_HANDLER,
    config: dict[str, Any] | None = None,
    schedule: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Validate and insert one tap row; return it as stored."""
    name = (name or "").strip()
    tap_type = (tap_type or "").strip()
    handler = (handler or "").strip()
    slug = (niche_slug or "").strip()
    if not _NAME_RE.match(name):
        raise TapAdminError(
            f"tap name {name!r} must start with a letter or digit and use only "
            "letters, digits, '_', '.' and '-' (max 100)"
        )
    if not tap_type:
        raise TapAdminError("--type is required")
    if config is not None and not isinstance(config, dict):
        raise TapAdminError(f"--config must be a JSON object, got {type(config).__name__}")

    handlers = registered_tap_handlers()
    if handler not in handlers:
        raise TapAdminError(
            f"no tap handler {handler!r}; registered: {', '.join(sorted(handlers))}"
        )
    if handler == TOPIC_SOURCE_HANDLER:
        sources = known_topic_sources()
        if tap_type not in sources:
            raise TapAdminError(
                f"no topic source {tap_type!r}; registered: {', '.join(sorted(sources))}"
            )
        if not slug:
            raise TapAdminError("a topic-source tap belongs to a niche: pass --niche SLUG")

    if await dcs.get_row(pool, _SURFACE, name) is not None:
        raise TapAdminError(
            f"a tap named {name!r} already exists; change it with "
            "`poindexter taps set-config` / `enable` / `disable`"
        )

    payload: dict[str, Any] = {
        "name": name,
        "handler_name": handler,
        "tap_type": tap_type,
        "config": dict(config or {}),
        "enabled": bool(enabled),
    }
    if schedule and schedule.strip():
        payload["schedule"] = schedule.strip()
    if slug:
        payload["niche_id"] = await _niche_id(pool, slug)
    return await dcs.upsert_row(pool, _SURFACE, payload)


async def delete_tap(pool: Any, name: str) -> bool:
    """Delete the tap row ``name``. ``False`` when there is none."""
    return await dcs.delete_row(pool, _SURFACE, (name or "").strip())


__all__ = [
    "TOPIC_SOURCE_HANDLER",
    "TapAdminError",
    "create_tap",
    "delete_tap",
    "known_topic_sources",
    "registered_tap_handlers",
]
