"""Sample Tap implementation — yields one static Document.

The smallest possible Tap. Serves as a reference for third-party Tap
authors: how ``extract()`` yields Documents, how ``config`` flows in,
how ``source_id`` / ``source_table`` / ``writer`` are set.

Real Tap migrations (memory files, Gitea issues, posts, audit, brain)
happen in Phase B.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from plugins.tap import Document


class HelloTap:
    """A Tap that yields a single Document each time ``extract()`` is called.

    Config (``plugin.tap.hello`` in ``app_settings``):
    - ``greeting`` (default ``"hello"``) — the text embedded in the Document
    """

    name = "hello"
    interval_seconds = 0  # on-demand only; don't auto-schedule

    async def extract(
        self,
        pool: Any,
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        greeting = config.get("greeting", "hello")
        yield Document(
            source_id="samples/hello/greeting",
            source_table="samples",
            text=f"{greeting} from the HelloTap sample plugin",
            metadata={"sample": True},
            writer="poindexter-samples",
        )
