"""Tap — the data ingestion Protocol.

A Tap yields :class:`Document` instances. The runner (``scripts/auto-embed.py``
after Phase B) iterates registered Taps on their configured interval and
stores each Document into ``embeddings`` via ``MemoryClient.store()``.

Two flavors of implementation:

- **Native Python Taps** for internal sources (memory files, posts we
  own, audit log, brain tables). Fast, no subprocess overhead.
- **SingerTap wrapper** (see :class:`SingerTap`) wraps any Singer binary
  and streams its JSON-lines output into Documents. Unlocks the whole
  Singer catalog: ``tap-github``, ``tap-slack``, ``tap-gmail``,
  ``tap-google-analytics``, etc.

Register a Tap via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.taps"]
    gitea = "poindexter_tap_gitea:GiteaTap"
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Document:
    """A unit of ingestible content, as yielded by ``Tap.extract()``.

    Field semantics match ``MemoryClient.store()`` so the runner can pass
    them through without reshaping:

    - ``source_table`` — top-level category (``memory``, ``posts``,
      ``issues``, ``audit``, etc.). Used for query scoping.
    - ``source_id`` — stable per-document identifier within the source.
      E.g. ``claude-code/C--WINDOWS-system32/MEMORY.md`` for memory Taps,
      or ``gitea/gladlabs/glad-labs-codebase/issues/185``.
    - ``text`` — the content to embed. Chunking happens in the runner,
      not the Tap.
    - ``metadata`` — JSON-safe dict; stored alongside the embedding.
    - ``writer`` — origin label (``claude-code``, ``openclaw``, ``worker``,
      ``singer:tap-github``). Tracks who/what produced the document.
    - ``precomputed_embedding`` — optional 768-dim vector when the upstream
      source already has an embedding attached. OpenClaw chunks ship with
      a ``nomic-embed-text`` vector already; when this field is set, the
      runner stores the document with this vector instead of calling
      Ollama again. Saves cost and avoids drift across embedder versions.
      Must be None or a 768-float list matching the rest of the store.
    """

    source_id: str
    source_table: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    writer: str = "unknown"
    precomputed_embedding: list[float] | None = None


@runtime_checkable
class Tap(Protocol):
    """Data ingestion plugin contract.

    Attributes:
        name: Unique plugin name (matches the entry_point key).
        interval_seconds: How often the runner should call ``extract``.
            0 means on-demand only (no scheduled run).
    """

    name: str
    interval_seconds: int

    async def extract(
        self,
        pool: Any,  # asyncpg.Pool; avoiding hard import here
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        """Yield Documents from the backing source.

        Implementations are async generators. The runner iterates until
        exhaustion; there is no paging contract — produce everything
        you have this cycle and return.

        Args:
            pool: asyncpg connection pool for Taps that need DB access
                (e.g. reading ``brain_knowledge``).
            config: Per-install config loaded from
                ``app_settings.plugin.tap.<name>``.
        """
        ...
