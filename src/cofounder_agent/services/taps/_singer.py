"""SingerTap — wrap any Singer binary as a Poindexter Tap.

Singer (https://www.singer.io/) is an ETL spec: a Tap is a process
that outputs JSON-lines on stdout. Each line is one of:

- ``SCHEMA`` — describes the shape of a stream's records
- ``RECORD`` — one data record for a stream
- ``STATE`` — an incremental-sync bookmark

This wrapper invokes a Singer binary as a subprocess, parses the
JSON-lines output, converts each RECORD into a Poindexter
:class:`Document`, and persists STATE messages for incremental sync
on the next run.

## Usage

The base class :class:`SingerTap` is intentionally not registered
itself — it's a template. Concrete subclasses set ``binary`` and
``name`` and optionally override ``record_to_document()``:

.. code:: python

    class GiteaSingerTap(SingerTap):
        name = "gitea_singer"
        binary = "tap-gitea"
        interval_seconds = 3600

        def record_to_document(self, stream, record):
            return Document(
                source_id=f"{stream}/{record['id']}",
                source_table="issues",
                text=record.get("title", "") + "\\n" + record.get("body", ""),
                metadata={"stream": stream, **record},
                writer=f"singer:{self.binary}",
            )

And register via entry_points:

.. code:: toml

    [project.entry-points."poindexter.taps"]
    gitea_singer = "my_pkg.gitea_singer:GiteaSingerTap"

## STATE persistence

State bookmarks are stored under ``plugin.tap.<name>.state`` in
``app_settings`` as a JSON blob. The wrapper passes the most recent
state back to the subprocess on startup via ``--state <file>``, which
is the Singer spec convention.

## Config flow

Singer taps expect their config as a JSON file passed via
``--config <file>``. ``SingerTap`` writes the PluginConfig's ``config``
dict to a tempfile and passes the path. Secrets go through
``plugins.secrets`` and are written in plaintext to the tempfile then
deleted after the subprocess exits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any

from plugins.tap import Document

logger = logging.getLogger(__name__)


class SingerTap:
    """Base class for Taps that wrap Singer binaries.

    Subclasses MUST set ``binary`` and ``name``. May override
    ``interval_seconds``, ``record_to_document()``, and
    ``source_table``.
    """

    name: str = "singer_base"  # subclass overrides
    binary: str = ""           # subclass overrides (e.g. "tap-gitea")
    interval_seconds: int = 3600
    source_table: str = "singer"

    def record_to_document(self, stream: str, record: dict[str, Any]) -> Document | None:
        """Convert a Singer RECORD to a Poindexter Document.

        Default implementation: stringify the record as JSON and use
        ``<stream>/<first-field-value>`` as source_id. Subclasses should
        override with a source-specific transformation (title + body
        concat, etc.).

        Return ``None`` to skip a record.
        """
        if not record:
            return None
        source_id_parts = [stream]
        for key in ("id", "number", "uuid"):
            if key in record:
                source_id_parts.append(str(record[key]))
                break
        if len(source_id_parts) == 1:
            # Fallback: hash the record.
            import hashlib
            source_id_parts.append(
                hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:16]
            )
        source_id = "/".join(source_id_parts)

        text = json.dumps(record, indent=2)
        return Document(
            source_id=source_id,
            source_table=self.source_table,
            text=text,
            metadata={"stream": stream, **{k: v for k, v in record.items() if isinstance(v, (str, int, float, bool, type(None)))}},
            writer=f"singer:{self.binary}",
        )

    async def _load_state(self, pool: Any) -> dict[str, Any]:
        """Read this Tap's last STATE message from ``app_settings``."""
        try:
            async with pool.acquire() as conn:
                raw = await conn.fetchval(
                    "SELECT value FROM app_settings WHERE key = $1",
                    f"plugin.tap.{self.name}.state",
                )
        except Exception as e:
            logger.warning("SingerTap %s: state load failed: %s", self.name, e)
            return {}
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    async def _save_state(self, pool: Any, state: dict[str, Any]) -> None:
        """Persist a STATE message under ``plugin.tap.<name>.state``."""
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO app_settings (key, value, category, description)
                    VALUES ($1, $2, 'plugins', $3)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    f"plugin.tap.{self.name}.state",
                    json.dumps(state),
                    f"Singer tap state for {self.name}",
                )
        except Exception as e:
            logger.warning("SingerTap %s: state save failed: %s", self.name, e)

    async def extract(
        self,
        pool: Any,
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        if not self.binary:
            raise RuntimeError(
                f"SingerTap {self.name!r}: ``binary`` attribute not set. "
                "Subclasses must specify the Singer CLI binary name."
            )

        # Write config + state to temp files; Singer CLIs want file paths.
        state = await self._load_state(pool)

        config_fd, config_path = tempfile.mkstemp(prefix=f"singer-{self.name}-cfg-", suffix=".json")
        state_fd, state_path = tempfile.mkstemp(prefix=f"singer-{self.name}-state-", suffix=".json")

        try:
            with os.fdopen(config_fd, "w", encoding="utf-8") as f:
                json.dump(config or {}, f)
            with os.fdopen(state_fd, "w", encoding="utf-8") as f:
                json.dump(state, f)

            cmd = [self.binary, "--config", config_path, "--state", state_path]
            logger.info("SingerTap %s: invoking %s", self.name, " ".join(cmd))

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            new_state: dict[str, Any] | None = None
            try:
                assert proc.stdout is not None
                async for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("SingerTap %s: non-JSON line: %s", self.name, line[:200])
                        continue

                    msg_type = msg.get("type")
                    if msg_type == "RECORD":
                        stream = msg.get("stream", "")
                        record = msg.get("record", {})
                        doc = self.record_to_document(stream, record)
                        if doc is not None:
                            yield doc
                    elif msg_type == "STATE":
                        new_state = msg.get("value", {}) or {}
                    elif msg_type == "SCHEMA":
                        # We don't enforce schemas; log + move on.
                        logger.debug(
                            "SingerTap %s: SCHEMA for stream %s",
                            self.name, msg.get("stream"),
                        )
                    # Other message types (ACTIVATE_VERSION, etc.) — ignore.
            finally:
                await proc.wait()

            if proc.returncode != 0:
                err = (await proc.stderr.read()).decode("utf-8", errors="replace")
                logger.error(
                    "SingerTap %s: binary exited %d: %s",
                    self.name, proc.returncode, err[:2000],
                )
            if new_state is not None:
                await self._save_state(pool, new_state)

        finally:
            for path in (config_path, state_path):
                with suppress(OSError):
                    Path(path).unlink(missing_ok=True)
