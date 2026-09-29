"""Local publishing mode: publish to a folder the worker serves at ``/site/``.

A fresh install has no object store. Before this mode existed, every upload
logged "No object-store credentials … skipping upload" and returned ``None``: a
post could be approved and "published" and still exist only as a database row.
``storage_provider=local`` (the fresh-install default) sends the same uploads
to a directory instead (:class:`~poindexter.services.local_object_store.LocalObjectStore`),
and ``poindexter.utils.local_site_mount`` serves that directory, with a small
viewer, at ``{api_url}/site/``.

This module holds the settings-aware parts, so the store itself reads nothing:

* :func:`storage_provider` — the one resolver for the ``storage_provider`` key.
  A missing or blank row means ``s3``. That keeps every install that predates
  the key on its existing behaviour, including the moment between a code
  deploy and the worker's next boot, when a Prefect flow run may load new code
  before the migration that pins configured installs to ``s3`` has run.
* :func:`local_object_store` — the store, rooted at ``storage_local_dir``.
* :func:`local_site_url` / :func:`local_public_base` — where objects are served.
* :func:`read_local_object` — lets readers that hold a stored URL (vision QA,
  the image captioner) read the file from disk. The browser-facing URL
  (``http://localhost:8002/site/…``) does not reach the worker from inside the
  Prefect container.
* :func:`fill_local_site_identity` — gives a local-mode install with no site
  identity a working one at boot, so publishing stops raising on
  ``require("site_url")``.

Design notes and limits: ``docs/architecture/local-storage-provider.md``.
"""

from __future__ import annotations

from typing import Any

from poindexter.services.local_object_store import LocalObjectStore, UnsafeObjectKey
from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

STORAGE_PROVIDER_S3 = "s3"
STORAGE_PROVIDER_LOCAL = "local"
STORAGE_PROVIDERS = (STORAGE_PROVIDER_S3, STORAGE_PROVIDER_LOCAL)

# Where the worker serves the local folder. A constant, not a setting, for the
# same reason the console's /console/ is: the mount is registered at import
# time, before settings load, and nothing outside this process needs to move it.
LOCAL_SITE_MOUNT_PATH = "/site"

# Code-side default for ``storage_local_dir``; settings_defaults seeds the same
# value. ``~`` expands per process, so inside the worker containers it names
# /home/appuser/.poindexter/site, where docker-compose.consumer.yml mounts the
# shared ``poindexter-site`` volume.
DEFAULT_LOCAL_DIR = "~/.poindexter/site"

# The name a local-mode install publishes under until the operator sets one.
# The free-tier brain seed (poindexter/brain/seed_app_settings.json) ships the
# same placeholder; a unit test keeps the two equal.
PLACEHOLDER_SITE_NAME = "My Content Site"

# Values already reported, so a bad setting logs once per process rather than
# on every upload.
_reported_invalid: set[str] = set()


def storage_provider(site_config: Any) -> str:
    """Which object store uploads go to: ``"s3"`` or ``"local"``.

    Missing or blank means ``"s3"`` (see the module docstring). An unknown value
    is a misconfiguration: it is logged at ERROR, once per value per process,
    and treated as ``"s3"``, the behaviour every install had before the key
    existed.
    """
    raw = site_config.get("storage_provider", STORAGE_PROVIDER_S3)
    value = raw.strip().lower() if isinstance(raw, str) else ""
    if not value:
        return STORAGE_PROVIDER_S3
    if value in STORAGE_PROVIDERS:
        return value
    if value not in _reported_invalid:
        _reported_invalid.add(value)
        logger.error(
            "[STORAGE] storage_provider=%r is not one of %s. Using 's3'. Fix it "
            "with: poindexter settings set storage_provider local",
            raw,
            "/".join(STORAGE_PROVIDERS),
        )
    return STORAGE_PROVIDER_S3


def is_local(site_config: Any) -> bool:
    """True when uploads go to the local folder."""
    return storage_provider(site_config) == STORAGE_PROVIDER_LOCAL


def _setting(site_config: Any, key: str) -> str:
    value = site_config.get(key, "")
    return value.strip().rstrip("/") if isinstance(value, str) else ""


def local_site_url(site_config: Any) -> str:
    """The URL the worker serves the local folder at: ``{api_url}/site``.

    ``api_url`` is the worker as the operator's browser reaches it
    (``http://localhost:8002`` on the consumer stack). Empty when unset.
    """
    api = _setting(site_config, "api_url")
    return f"{api}{LOCAL_SITE_MOUNT_PATH}" if api else ""


def local_public_base(site_config: Any) -> str:
    """URL prefix local objects are published under.

    In order: ``storage_public_url`` (the operator serves the folder somewhere
    else), then the site URL (``public_site_url``, then ``site_url``: in local
    mode the folder is the site), then :func:`local_site_url`. Keeping objects
    under the site's own origin is also what stops the citation check from
    probing them: it skips URLs on ``site_url``'s origin as internal.
    """
    for key in ("storage_public_url", "public_site_url", "site_url"):
        value = _setting(site_config, key)
        if value:
            return value
    return local_site_url(site_config)


def local_object_store(site_config: Any) -> LocalObjectStore:
    """The local store configured by ``storage_local_dir``."""
    root = site_config.get("storage_local_dir", DEFAULT_LOCAL_DIR)
    if not isinstance(root, str) or not root.strip():
        root = DEFAULT_LOCAL_DIR
    return LocalObjectStore(root.strip(), local_public_base(site_config))


async def read_local_object(url: str, site_config: Any) -> bytes | None:
    """Bytes of the locally stored object ``url`` names.

    ``None`` when the install isn't in local mode, the URL isn't under the
    local public base, or the object is missing, so callers fall back to their
    usual HTTP fetch.
    """
    if not url or not is_local(site_config):
        return None
    store = local_object_store(site_config)
    key = store.key_for_url(url)
    if key is None:
        return None
    try:
        return await store.get_bytes(key)
    except (OSError, UnsafeObjectKey) as exc:
        logger.warning("[STORAGE] local read failed for %s: %s", key, exc)
        return None


async def fill_local_site_identity(pool: Any) -> dict[str, str]:
    """Give a local-mode install with no site identity a working one.

    Publishing calls ``site_config.require("site_url")`` and the static export
    also requires ``site_name``. The reference seed leaves both empty, and
    ``poindexter setup`` never asks. On the Docker stacks that is covered by the
    brain daemon, which boots before the worker and refills empty values from
    its free-tier seed (``poindexter/brain/seed_loader.py``). That seed now
    points at this preview. Where no brain runs, for example the backend
    started on the host with ``npm run dev``, identity stays empty and the first
    publish raises after the post row is written. This is the backstop for
    that case: in local mode the site's address is known (the worker serves it
    at ``{api_url}/site``), so when ``storage_provider`` is ``local`` this
    fills an empty ``site_url`` with that URL and an empty ``site_name`` with
    :data:`PLACEHOLDER_SITE_NAME`, the same values the brain seed writes.

    It only ever fills empty values. A value the operator set, or one the
    brain seed wrote, is left alone. In ``s3`` mode it does nothing, because
    the site lives wherever the operator deploys it and an unset ``site_url``
    should keep failing loudly.

    Reads from the database, not a ``SiteConfig``: it runs during boot right
    after the defaults are seeded, before settings are loaded into memory.

    Returns ``{key: value}`` for each key it filled.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
            ["storage_provider", "api_url"],
        )
        current = {r["key"]: (r["value"] or "") for r in rows}
        provider = current.get("storage_provider", "").strip().lower()
        if provider != STORAGE_PROVIDER_LOCAL:
            return {}
        api = current.get("api_url", "").strip().rstrip("/")
        if not api:
            logger.warning(
                "[STORAGE] storage_provider=local but api_url is empty, so the "
                "local site URL is unknown. Set it with: poindexter settings "
                "set api_url http://localhost:8002",
            )
            return {}
        wanted = {
            "site_url": f"{api}{LOCAL_SITE_MOUNT_PATH}",
            "site_name": PLACEHOLDER_SITE_NAME,
        }
        filled: dict[str, str] = {}
        for key, value in wanted.items():
            updated = await conn.fetchval(
                "UPDATE app_settings SET value = $2, updated_at = NOW() "
                "WHERE key = $1 AND COALESCE(value, '') = '' RETURNING key",
                key,
                value,
            )
            if updated:
                filled[key] = value
    if filled:
        logger.info(
            "[STORAGE] local mode: filled empty site identity (%s). Change it "
            "with: poindexter settings set site_url <url>",
            ", ".join(f"{k}={v}" for k, v in filled.items()),
        )
    return filled
