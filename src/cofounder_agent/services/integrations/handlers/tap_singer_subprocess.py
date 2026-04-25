"""Handler: ``tap.singer_subprocess`` — run a Singer-spec tap binary
and route its records through the integrations record_handler.

The Singer protocol (https://github.com/singer-io/getting-started)
defines a stdout-based wire format that any of the 600+ existing
taps speak. By implementing this handler, Poindexter gains plug-in
ingestion for Stripe, Google Search Console, GA4, HubSpot, MySQL,
Postgres, etc. — no per-source code on our side.

## How it works

Each invocation:

1. Reads ``row.config`` for ``command`` (path to the tap binary or a
   shell-tokenisable invocation like ``python -m tap_csv``) and
   ``tap_config`` (the JSON the tap expects on its ``--config`` arg).
2. Writes ``tap_config`` to a temp ``config.json``.
3. Writes ``row.state`` to a temp ``state.json`` so taps that support
   incremental sync resume from the last bookmark.
4. Spawns ``<command> --config config.json --state state.json``.
5. Reads stdout line-by-line:
   - SCHEMA messages — validated, optionally filtered to
     ``row.config.streams`` whitelist.
   - RECORD messages — dispatched to the registered handler named in
     ``row.record_handler`` (any surface — webhook, retention, tap;
     the most common case is ``tap.external_metrics_writer``).
     Records for streams that have no SCHEMA yet are an error.
   - STATE messages — buffered; the most recent successful one is
     persisted back to ``external_taps.state`` after the subprocess
     exits 0.
6. Waits for the subprocess to terminate. On exit code 0 the tap is
   considered successful and STATE is committed. On non-zero the
   stderr tail is recorded as ``last_error`` and STATE is left
   unchanged so the next run resumes from the same bookmark.

## Limits + safety

- **Timeout:** ``row.config.timeout_seconds`` (default 600). On
  expiry the subprocess is sent SIGTERM, then SIGKILL after 5s.
- **Per-batch cap:** ``row.config.max_records`` (default 50000).
  Caps a runaway tap before it floods the target table.
- **Memory cap on stderr buffer:** stderr is read concurrently into
  a deque (last 200 lines) so a noisy tap can't OOM the worker.
- **No shell expansion:** ``shlex.split(command)`` so ``;`` /
  ``&&`` / backticks in operator-supplied strings don't escape into
  shell execution.

## Row config example

::

    {
      "command": "tap-csv",
      "tap_config": {"files": [{"entity": "page_views", "path": "/data/page_views.csv"}]},
      "streams": ["page_views"],
      "max_records": 100000,
      "timeout_seconds": 1800
    }

The ``record_handler`` column on the row names the handler that
turns Singer records into rows in ``target_table``. For
``external_metrics`` the canonical handler is
``tap.external_metrics_writer`` (this module's sibling file).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import tempfile
from collections import deque
from typing import Any

from services.integrations import registry
from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT_S = 600
_DEFAULT_MAX_RECORDS = 50_000
_STDERR_TAIL_LINES = 200


class SingerProtocolError(RuntimeError):
    """A tap emitted a malformed message or recorded a record before
    its schema."""


@register_handler("tap", "singer_subprocess")
async def singer_subprocess(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Run a Singer tap subprocess and route its output."""
    if pool is None:
        raise RuntimeError("tap.singer_subprocess: pool unavailable")

    config = row.get("config") or {}
    if not isinstance(config, dict):
        raise ValueError("tap.singer_subprocess: row.config must be an object")

    command = config.get("command")
    if not command or not isinstance(command, str):
        raise ValueError(
            "tap.singer_subprocess: row.config.command is required (e.g. 'tap-csv', 'python -m tap_stripe')"
        )

    tap_config = config.get("tap_config") or {}
    streams_filter = config.get("streams") or []
    if streams_filter and not isinstance(streams_filter, list):
        raise ValueError("tap.singer_subprocess: row.config.streams must be a list")

    max_records = int(config.get("max_records") or _DEFAULT_MAX_RECORDS)
    timeout_s = float(config.get("timeout_seconds") or _DEFAULT_TIMEOUT_S)

    record_handler_name = row.get("record_handler")
    if not record_handler_name:
        raise ValueError(
            "tap.singer_subprocess: row.record_handler is required "
            "(name of the handler that consumes RECORD messages)"
        )
    # Resolve to a callable up-front — fail fast if the handler isn't
    # registered. Singer record handlers register under the "tap"
    # surface by convention.
    record_handler_surface = "tap"
    record_handler = registry.lookup(record_handler_surface, record_handler_name)

    starting_state = row.get("state") or {}

    with tempfile.TemporaryDirectory(prefix="singer-tap-") as tmpdir:
        config_path = os.path.join(tmpdir, "config.json")
        state_path = os.path.join(tmpdir, "state.json")
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(tap_config, fh)
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(starting_state, fh)

        argv = shlex.split(command) + [
            "--config", config_path,
            "--state", state_path,
        ]

        logger.info(
            "[tap.singer_subprocess] %s: spawning %s",
            row.get("name"), " ".join(argv),
        )

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )

        # Stderr drained concurrently so the OS pipe doesn't fill and
        # block the tap; only the tail is kept.
        stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)

        async def _drain_stderr():
            assert proc.stderr is not None
            async for line in proc.stderr:
                try:
                    stderr_tail.append(line.decode("utf-8", errors="replace").rstrip("\n"))
                except Exception:
                    pass

        stderr_task = asyncio.create_task(_drain_stderr())

        # Collect schemas, route records, buffer states.
        schemas: dict[str, dict[str, Any]] = {}
        # Mutable container so _consume_stdout can update last_state.
        # Indices: 0=last_state, 1=records_processed, 2=records_filtered.
        capture: dict[str, Any] = {"last_state": None, "records": 0, "filtered": 0}

        try:
            assert proc.stdout is not None
            try:
                await asyncio.wait_for(
                    _consume_stdout(
                        proc.stdout, schemas, streams_filter, max_records,
                        record_handler, site_config, row, pool, capture,
                    ),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    proc.kill()
                raise RuntimeError(
                    f"tap.singer_subprocess: {row.get('name')} timed out after {timeout_s}s"
                )

            return_code = await proc.wait()
        finally:
            stderr_task.cancel()
            try:
                await stderr_task
            except (asyncio.CancelledError, Exception):
                pass

        if return_code != 0:
            tail = "\n".join(stderr_tail)[-2000:]
            raise RuntimeError(
                f"tap.singer_subprocess: {row.get('name')} exited {return_code}. "
                f"Stderr tail: {tail!r}"
            )

        last_state = capture["last_state"]
        records_processed = capture["records"]
        records_skipped_filtered = capture["filtered"]

        # Prefer stdout-emitted STATE; fall back to state.json (taps that
        # only persist on exit).
        if last_state is None:
            try:
                with open(state_path, encoding="utf-8") as fh:
                    file_state = json.load(fh)
                if file_state:
                    last_state = file_state
            except (OSError, json.JSONDecodeError):
                pass

    # Persist updated state on success.
    if last_state is not None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE external_taps SET state = $2::jsonb WHERE id = $1",
                row["id"], json.dumps(last_state),
            )

    logger.info(
        "[tap.singer_subprocess] %s: %d records processed, %d filtered out",
        row.get("name"), records_processed, records_skipped_filtered,
    )
    return {
        "records": records_processed,
        "filtered": records_skipped_filtered,
        "schemas": list(schemas.keys()),
    }


# ---------------------------------------------------------------------------
# stdout consumer — split out for testability
# ---------------------------------------------------------------------------


async def _consume_stdout(
    stdout: asyncio.StreamReader,
    schemas: dict[str, dict[str, Any]],
    streams_filter: list[str],
    max_records: int,
    record_handler: Any,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
    capture: dict[str, Any],
) -> None:
    """Drain Singer messages from ``stdout``.

    Mutates ``schemas`` and ``capture`` in place. ``capture`` carries
    ``last_state`` (most recent STATE.value), ``records`` (count of
    RECORDs dispatched), ``filtered`` (count of RECORDs skipped because
    of streams_filter). Raises :class:`SingerProtocolError` on
    malformed messages or RECORDs without a preceding SCHEMA. Caller
    is responsible for the timeout wrapper.
    """
    async for raw_line in stdout:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SingerProtocolError(
                f"tap emitted non-JSON line: {line[:200]!r}"
            ) from exc

        msg_type = msg.get("type")
        if msg_type == "SCHEMA":
            stream = msg.get("stream")
            if not stream:
                raise SingerProtocolError("SCHEMA message missing 'stream' field")
            schemas[stream] = msg.get("schema") or {}
            continue

        if msg_type == "RECORD":
            stream = msg.get("stream")
            if not stream:
                raise SingerProtocolError("RECORD message missing 'stream' field")
            if streams_filter and stream not in streams_filter:
                capture["filtered"] = capture.get("filtered", 0) + 1
                continue
            if stream not in schemas:
                raise SingerProtocolError(
                    f"RECORD for stream {stream!r} arrived before its SCHEMA"
                )
            record = msg.get("record")
            if record is None:
                raise SingerProtocolError(
                    f"RECORD message for {stream!r} missing 'record' field"
                )

            await record_handler(
                {"stream": stream, "record": record, "schema": schemas[stream],
                 "time_extracted": msg.get("time_extracted")},
                site_config=site_config,
                row=row,
                pool=pool,
            )
            capture["records"] = capture.get("records", 0) + 1
            if capture["records"] >= max_records:
                logger.warning(
                    "[tap.singer_subprocess] %s: hit max_records=%d cap; "
                    "remaining records dropped",
                    row.get("name"), max_records,
                )
                break
            continue

        if msg_type == "STATE":
            value = msg.get("value")
            if value is not None:
                capture["last_state"] = value
            continue

        # ACTIVATE_VERSION and other lesser-used Singer messages are
        # safe to ignore — most modern taps don't emit them.
        logger.debug(
            "[tap.singer_subprocess] %s: ignored message type %r",
            row.get("name"), msg_type,
        )
