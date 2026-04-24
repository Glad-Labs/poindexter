"""Handler: ``tap.singer_subprocess`` — scaffolding only for v1.

Full implementation spawns a Singer-spec tap binary as a subprocess,
pipes a ``config.json`` + ``state.json`` on stdin, reads Singer
SCHEMA/RECORD/STATE messages from stdout, routes records through
``row.record_handler`` (a registered handler under any surface that
knows how to INSERT into ``row.target_table``), and persists the
updated ``state`` back to the row on successful completion.

Deferred because:

- Subprocess I/O with proper cancellation + timeout semantics
  requires careful handling (the test matrix for "tap hangs mid-
  stream" alone is substantial).
- The record_handler contract needs to handle SCHEMA/RECORD/STATE
  message types distinctly — different from the webhook handlers
  which receive a pre-parsed payload dict.
- No operator has wired a concrete Singer tap yet; speccing it now
  risks building against an imagined use case.

This stub handler is registered so that seed rows targeting
``singer_subprocess`` don't crash the runner at dispatch time — they
fail loudly with a clear message pointing at the follow-up issue.
"""

from __future__ import annotations

from typing import Any

from services.integrations.registry import register_handler


@register_handler("tap", "singer_subprocess")
async def singer_subprocess(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Placeholder — full Singer subprocess runner is a follow-up."""
    raise NotImplementedError(
        "tap.singer_subprocess handler is not yet implemented. "
        "See GH-103 and docs/integrations/tap_singer_subprocess.md "
        "for the intended contract. Disable this row "
        f"({row.get('name')!r}) or switch its handler_name to one "
        "that exists in the registry."
    )
