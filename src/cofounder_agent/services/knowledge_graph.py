"""knowledge_graph — extract and traverse relationships between indexed documents.

The premise (poindexter#1035): the memory corpus is **already a knowledge
graph**. Memory files link to each other with ``[[wiki-link]]`` slugs — 938
resolved edges across 375 files at time of writing — written by hand as part of
the memory discipline. Nothing has ever parsed them, so retrieval sees 375
disconnected documents and the operator's own assertion that "this incident
relates to that project" is invisible to it.

Why this and not LLM extraction
-------------------------------
This is the one idea worth taking from cognee (topoteretes/cognee): traverse a
graph, then rank. Cognee builds its graph by running a structured-extraction LLM
call per chunk — thousands of local calls over this corpus, on a box with a GPU
admission queue. Here the edges are already authored, so the graph costs one
regex pass and no model at all. Rent the idea, not the ingestion pipeline.

Resolution
----------
A link is a bare slug (``[[some_memory]]``). Memory rows are keyed
``claude-code/<project>/<file-stem>``, so a slug resolves by stem. An install
can carry rows from SEVERAL project keys at once — a machine migration or an OS
change re-keys the Claude projects directory while the old rows persist — so one
stem can match more than one row.

Preference is derived from the corpus rather than from a hardcoded list of
project names: the project contributing the most indexed documents is taken as
the live one. That is portable to any install (the operator's own project keys
are not knowledge this module should hold) and self-correcting after a
migration, since the new project overtakes the old as it is indexed.

A slug matching no indexed document is **dangling**, and dangling is a feature:
the memory convention treats a link to a not-yet-written memory as a marker of
work to do. Those are counted and reported, never silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)

_WIKILINK = re.compile(r"\[\[([a-z0-9_\-]+)\]\]", re.IGNORECASE)

DEFAULT_RELATION = "links_to"
ORIGIN_MEMORY = "memory_wikilink"


@dataclass(frozen=True)
class Edge:
    src_table: str
    src_id: str
    dst_table: str
    dst_id: str
    relation: str = DEFAULT_RELATION
    weight: float = 1.0
    origin: str = ORIGIN_MEMORY


@dataclass
class ExtractStats:
    scanned: int = 0
    edges: int = 0
    dangling: int = 0
    self_links: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "edges": self.edges,
            "dangling": self.dangling,
            "self_links": self.self_links,
        }


def extract_wikilinks(text: str) -> list[str]:
    """Return the distinct ``[[slug]]`` targets in ``text``, order-preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _WIKILINK.finditer(text or ""):
        slug = m.group(1).lower()
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def stem_of(source_id: str) -> str:
    """The link-comparable stem of a memory ``source_id``.

    ``claude-code/<project>/always-commit-work.md`` -> ``always-commit-work``.

    The ``.md`` strip is load-bearing, not tidiness: source_ids keep the file
    extension and ``[[wiki-links]]`` never do, so without it every one of the
    922 links in the corpus resolves to nothing and the extractor reports a
    graph of zero edges — which looks exactly like "the corpus has no links".
    """
    stem = (source_id or "").rsplit("/", 1)[-1].lower()
    return stem[:-3] if stem.endswith(".md") else stem


def project_of(source_id: str) -> str:
    """The project segment of ``<writer>/<project>/<stem>`` ("" if absent)."""
    parts = (source_id or "").split("/")
    return parts[1] if len(parts) >= 3 else ""


def _prefer(candidates: list[str], project_weight: dict[str, int]) -> str:
    """Pick one source_id when a slug matches several.

    Ranked by how many indexed documents the candidate's project contributes —
    the biggest project is the live one — then shortest id, then lexically, so
    the choice is stable across runs instead of depending on row order.

    Derived from the corpus on purpose. An earlier version hardcoded this
    install's retired project keys, which both leaked operator paths into the
    public mirror and silently mis-resolved on anyone else's machine.
    """
    return sorted(
        candidates,
        key=lambda c: (-project_weight.get(project_of(c), 0), len(c), c),
    )[0]


def build_edges(
    docs: dict[str, str],
    *,
    source_table: str = "memory",
) -> tuple[list[Edge], ExtractStats]:
    """Turn ``{source_id: text}`` into edges plus a stats summary.

    Pure — no I/O — so the resolution rules are unit-testable without a corpus.
    """
    stats = ExtractStats(scanned=len(docs))

    by_stem: dict[str, list[str]] = {}
    project_weight: dict[str, int] = {}
    for sid in docs:
        by_stem.setdefault(stem_of(sid), []).append(sid)
        proj = project_of(sid)
        project_weight[proj] = project_weight.get(proj, 0) + 1

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for sid, text in docs.items():
        for slug in extract_wikilinks(text):
            targets = by_stem.get(slug)
            if not targets:
                stats.dangling += 1
                continue
            dst = _prefer(targets, project_weight)
            if dst == sid:
                # A file citing its own slug is a formatting artefact, not a
                # relationship — and a self-edge would make graph expansion
                # return the seed document as its own neighbour.
                stats.self_links += 1
                continue
            key = (sid, dst)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                Edge(
                    src_table=source_table, src_id=sid,
                    dst_table=source_table, dst_id=dst,
                )
            )
    stats.edges = len(edges)
    return edges, stats


async def load_memory_docs(pool: Any) -> dict[str, str]:
    """Reassemble each memory document from its indexed chunks.

    Links can sit in any chunk, so a per-chunk scan would miss every link past
    the first 6000 characters. Chunks are concatenated in order and
    ``chunk_text`` is preferred over the display preview — a link past
    character 500 would otherwise be invisible, which is the poindexter#1033
    failure mode reappearing one layer up.
    """
    rows = await pool.fetch(
        """
        SELECT source_id, chunk_index, COALESCE(chunk_text, text_preview) AS body
          FROM embeddings
         WHERE source_table = 'memory'
         ORDER BY source_id, chunk_index
        """
    )
    docs: dict[str, list[str]] = {}
    for r in rows:
        docs.setdefault(r["source_id"], []).append(r["body"] or "")
    return {sid: "\n".join(parts) for sid, parts in docs.items()}


async def replace_edges(pool: Any, edges: list[Edge], *, origin: str = ORIGIN_MEMORY) -> int:
    """Replace all edges of one ``origin`` in a single transaction.

    Delete-then-insert scoped to ``origin`` so a re-extract drops links the
    operator has since removed, without touching edges another extractor owns.
    Refuses to delete when the new set is empty: an extractor that produced
    nothing has failed, and wiping a working graph on that basis would be the
    worst possible response.
    """
    if not edges:
        logger.warning(
            "[knowledge_graph] refusing to replace '%s' edges with an empty set "
            "— extraction produced nothing, treating as failure not truth",
            origin,
        )
        return 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM knowledge_edges WHERE origin = $1", origin)
            await conn.executemany(
                """
                INSERT INTO knowledge_edges
                    (src_table, src_id, dst_table, dst_id, relation, weight, origin)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (src_table, src_id, dst_table, dst_id, relation)
                DO UPDATE SET weight = EXCLUDED.weight, updated_at = now()
                """,
                [
                    (e.src_table, e.src_id, e.dst_table, e.dst_id,
                     e.relation, e.weight, e.origin)
                    for e in edges
                ],
            )
    return len(edges)


async def neighbours(
    pool: Any,
    seeds: list[tuple[str, str]],
    *,
    limit: int = 20,
) -> list[tuple[str, str, float]]:
    """Return ``(table, id, weight)`` one hop from ``seeds``, in both directions.

    Undirected on purpose — see the migration's index comment. A neighbour
    reachable from several seeds accumulates weight, so a document the retrieved
    set collectively points at ranks above one cited only once. Seeds themselves
    are excluded; they are already in the result set the caller is expanding.
    """
    if not seeds:
        return []

    tables = [s[0] for s in seeds]
    ids = [s[1] for s in seeds]
    rows = await pool.fetch(
        """
        WITH seed(t, i) AS (
            SELECT * FROM unnest($1::text[], $2::text[])
        )
        SELECT dst_table AS t, dst_id AS i, sum(weight) AS w
          FROM knowledge_edges e JOIN seed s
            ON e.src_table = s.t AND e.src_id = s.i
         GROUP BY 1, 2
        UNION ALL
        SELECT src_table AS t, src_id AS i, sum(weight) AS w
          FROM knowledge_edges e JOIN seed s
            ON e.dst_table = s.t AND e.dst_id = s.i
         GROUP BY 1, 2
        """,
        tables,
        ids,
    )
    seed_set = set(seeds)
    agg: dict[tuple[str, str], float] = {}
    for r in rows:
        key = (r["t"], r["i"])
        if key in seed_set:
            continue
        agg[key] = agg.get(key, 0.0) + float(r["w"])
    ranked = sorted(agg.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(t, i, w) for (t, i), w in ranked[:limit]]


__all__ = [
    "Edge",
    "ExtractStats",
    "build_edges",
    "extract_wikilinks",
    "project_of",
    "load_memory_docs",
    "neighbours",
    "replace_edges",
    "stem_of",
]
