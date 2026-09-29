"""Local-filesystem object store — the ``storage_provider=local`` backend.

:class:`~poindexter.services.r2_upload_service.R2UploadService` is the one seam
every uploader, reaper and feed builder goes through. With
``storage_provider=local`` it hands each call to this class instead of boto3, so
a fresh install publishes its static JSON export, images, podcasts and feeds to
a directory on disk. That needs no bucket, no credentials and no account. The
worker serves the directory at ``/site/`` (``poindexter.utils.local_site_mount``)
next to a small viewer, so the first approved post is readable in a browser.

The contract mirrors the S3 methods one for one. The things that differ, and
why:

* **Keys are validated, not trusted.** An S3 key is an opaque string, but here
  a key becomes a path, so one containing ``..``, a leading ``/``, a backslash
  or an empty segment could write outside the root. :meth:`path_for` refuses
  those with :class:`UnsafeObjectKey`. Every key the codebase builds today
  (``static/posts/<slug>.json``, ``images/inline/<hex>.png``,
  ``podcast/<version>/<id>.mp3``, …) passes.
* **Writes are atomic.** Each upload lands in a temp file beside its target and
  is moved into place with :func:`os.replace`. A reader never sees half a file:
  ``static/posts/index.json`` is rewritten on every publish while the viewer may
  be fetching it. Temp files carry :data:`_TMP_PREFIX`, a prefix no key may use,
  so listings skip them.
* **Listing uses S3 prefix semantics.** ``prefix`` is a string prefix of the
  key, not a directory, so ``list_objects("static/posts/")`` and
  ``list_objects("static/posts/ind")`` answer the way S3 would. Results come back
  sorted by key, as ``ListObjectsV2`` returns them.
* **Missing is not the same as unreadable.** :meth:`size` returns ``None`` only
  for an absent key and lets any other ``OSError`` (a permission error, say)
  propagate, so the caller keeps its "couldn't ask" / "not there" distinction.

This module reads no settings. :mod:`poindexter.services.local_site` builds an
instance from ``app_settings``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from urllib.parse import unquote

# Temp files are created beside their target with this prefix. No key segment
# may start with it, so a listing can skip in-flight writes without guessing.
_TMP_PREFIX = ".upload-"

# Written files are world-readable (0644), not mkstemp's 0600: the directory is
# a website, and host tooling (a static server, `docker cp`, a backup) reads it
# as a different user than the worker that wrote it.
_FILE_MODE = 0o644


class UnsafeObjectKey(ValueError):
    """An object key that could not be mapped safely to a path under the root."""


class LocalObjectStore:
    """Object storage in a directory: one file per key, keys as relative paths.

    Args:
        root: Directory holding the objects. ``~`` is expanded. It need not
            exist yet; the first write creates it.
        public_base: URL prefix the objects are served under (for example
            ``http://localhost:8002/site``). ``url_for(key)`` is
            ``f"{public_base}/{key}"``. Empty means no public URL can be built.
    """

    def __init__(self, root: Path | str, public_base: str) -> None:
        self._root = Path(root).expanduser()
        self._public_base = (public_base or "").rstrip("/")

    @property
    def root(self) -> Path:
        return self._root

    @property
    def public_base(self) -> str:
        return self._public_base

    # ------------------------------------------------------------------
    # Key <-> path <-> URL
    # ------------------------------------------------------------------

    def path_for(self, key: str) -> Path:
        """The file an object key is stored in.

        Raises:
            UnsafeObjectKey: the key is empty or absolute, contains a NUL or a
                backslash, has an empty / ``.`` / ``..`` segment, or uses the
                reserved temp-file prefix.
        """
        if not isinstance(key, str) or not key:
            raise UnsafeObjectKey(f"object key must be a non-empty string, got {key!r}")
        if "\x00" in key or "\\" in key:
            raise UnsafeObjectKey(f"object key contains a NUL or backslash: {key!r}")
        if key.startswith("/"):
            raise UnsafeObjectKey(f"object key must be relative, got {key!r}")
        parts = key.split("/")
        for part in parts:
            if part in ("", ".", ".."):
                raise UnsafeObjectKey(
                    f"object key has an empty, '.' or '..' segment: {key!r}",
                )
            if part.startswith(_TMP_PREFIX):
                raise UnsafeObjectKey(
                    f"object key uses the reserved temp prefix {_TMP_PREFIX!r}: {key!r}",
                )
        return self._root.joinpath(*parts)

    def url_for(self, key: str) -> str:
        """Public URL of ``key``, or ``""`` when no public base is configured."""
        return f"{self._public_base}/{key}" if self._public_base else ""

    def key_for_url(self, url: str) -> str | None:
        """The object key a URL names, or ``None`` when the URL isn't one of ours.

        The inverse of :meth:`url_for`. Readers that already hold a stored URL
        (vision QA, the image captioner) use it to read the file straight from
        disk: the URL the browser uses (``http://localhost:8002/site/…``) does
        not reach the worker from inside another container.
        """
        if not self._public_base or not isinstance(url, str):
            return None
        head = self._public_base + "/"
        if not url.startswith(head):
            return None
        key = url[len(head) :].split("#", 1)[0].split("?", 1)[0]
        key = unquote(key)
        try:
            self.path_for(key)
        except UnsafeObjectKey:
            return None
        return key

    # ------------------------------------------------------------------
    # Operations (all blocking I/O runs in a worker thread)
    # ------------------------------------------------------------------

    async def put_file(self, src: str | Path | BinaryIO, key: str) -> int:
        """Store ``src`` (a path, or a readable binary stream) at ``key``.

        Atomic: the object either keeps its old bytes or has all the new ones.
        Returns the stored size in bytes.
        """
        dest = self.path_for(key)
        return await asyncio.to_thread(self._put_sync, src, dest)

    @staticmethod
    def _put_sync(src: str | Path | BinaryIO, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=_TMP_PREFIX, dir=dest.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                if isinstance(src, (str, os.PathLike)):
                    with open(src, "rb") as fh:
                        shutil.copyfileobj(fh, out)
                else:
                    shutil.copyfileobj(src, out)
            os.chmod(tmp_name, _FILE_MODE)
            os.replace(tmp_name, dest)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_name)
            raise
        return dest.stat().st_size

    async def delete(self, key: str) -> bool:
        """Delete ``key``. Idempotent, like S3: deleting an absent key succeeds."""
        path = self.path_for(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)
        return True

    async def get_bytes(self, key: str) -> bytes | None:
        """The object's bytes, or ``None`` when there is no such key."""
        path = self.path_for(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None

    async def size(self, key: str) -> int | None:
        """Size of ``key`` in bytes, or ``None`` when there is no such key.

        Any other ``OSError`` propagates: an unreadable directory is "couldn't
        ask", not "not there".
        """
        path = self.path_for(key)
        try:
            st = await asyncio.to_thread(path.stat)
        except (FileNotFoundError, NotADirectoryError):
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        return int(st.st_size)

    async def list_objects(self, prefix: str) -> list[dict]:
        """Objects whose key starts with ``prefix``, sorted by key.

        Returns ``[{"key": str, "size": int, "last_modified": datetime}]`` —
        the shape :meth:`R2UploadService.list_objects` returns for S3.
        """
        return await asyncio.to_thread(self._list_sync, prefix or "")

    async def list_keys(self, prefix: str) -> list[str]:
        """Keys starting with ``prefix``, sorted."""
        return [obj["key"] for obj in await self.list_objects(prefix)]

    def _list_sync(self, prefix: str) -> list[dict]:
        # Walk only the deepest directory the prefix fully names: the prefix
        # "static/posts/ind" can only match files under static/posts/.
        dir_part = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        start = self._root.joinpath(*[p for p in dir_part.split("/") if p])
        if not start.is_dir():
            return []
        out: list[dict] = []
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = [d for d in dirnames if not d.startswith(_TMP_PREFIX)]
            for name in filenames:
                if name.startswith(_TMP_PREFIX):
                    continue
                full = Path(dirpath) / name
                key = full.relative_to(self._root).as_posix()
                if not key.startswith(prefix):
                    continue
                try:
                    st = full.stat()
                except FileNotFoundError:
                    continue  # deleted between the walk and the stat
                out.append(
                    {
                        "key": key,
                        "size": int(st.st_size),
                        "last_modified": datetime.fromtimestamp(
                            st.st_mtime,
                            tz=timezone.utc,
                        ),
                    },
                )
        out.sort(key=lambda obj: obj["key"])
        return out
