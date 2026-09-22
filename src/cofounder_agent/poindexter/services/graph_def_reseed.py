"""Graph_def reseeds — the migration-side half of the contract-drift gate.

Every active ``pipeline_templates`` row is stamped per node with the atom's
``contract_fingerprint()``. When an atom's ``AtomMeta`` contract changes (a
new ``FieldSpec`` input, a changed ``requires``/``produces``), the stored stamp
goes stale and ``pipeline_architect.assert_graph_def_current`` refuses to run
the graph — the whole lane halts at load. Twice now that reached prod behind
a green CI: poindexter#1876 (``qa.audio``, 2026-07) and glad-labs-stack#3928
(the four Stage-2 render atoms, 2026-09-22). Both times the PR refreshed the
per-atom fingerprint snapshot the CI gate compares against, which turned the
gate green, and shipped no reseed migration, which is the only thing that
fixes the stored row: the boot self-heal (``ensure_active_graph_defs_stamped``)
deliberately restamps only rows that carry NO fingerprint at all.

This module closes that gap by making the migration itself the thing CI
checks. A reseed migration declares, per graph, the **graph signature** it
brings the row to::

    _RESEEDS = (
        # (slug, new_version, spec module, spec attr, graph signature)
        ("media_pipeline", 6, "poindexter.services.media_pipeline_spec",
         "MEDIA_PIPELINE_GRAPH_DEF", "0d1f3d11b475"),
    )

    async def up(pool) -> None:
        await apply_reseeds(pool, _RESEEDS, log_prefix="reseed_media_pipeline_v6")

and ``tests/unit/services/test_graph_def_reseed_gate.py`` requires, for every
active in-tree spec, that the NEWEST migration declaring a reseed for that
slug declares the signature the live registry produces today
(:func:`expected_graph_signature`). A contract or topology change therefore
cannot merge until a new migration exists that carries it to prod — there is
no snapshot to refresh instead. Merged migrations are never edited (see
``docs/operations/migrations.md``), so the declared signature is a fact about
what prod's row will look like after that migration ran.

The signature is :func:`pipeline_architect.graph_signature` over the stamped
spec — node ``(id, _contract_fp)`` pairs plus edges — so it moves on any atom
contract change or any node/edge change, and stays put across a pure version
bump (restamping with identical fingerprints leaves LangGraph checkpoints
valid; ``template_runner`` discards a checkpoint only when the signature
differs).

Import discipline: this module is imported by migration files, which the
``migrations-smoke`` CI step runs without the atom registry's runtime
dependencies. Nothing here imports ``pipeline_architect`` at module import
time — the registry-touching helpers import it lazily and
:func:`apply_reseeds` treats an ``ImportError`` there as "defer stamping to
the worker's boot self-heal", exactly as the hand-written reseed migrations
did before this helper existed (``20260806_033653`` onward).
"""

from __future__ import annotations

import ast
import importlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Module-level name a reseed migration binds its declarations to. The gate
#: reads it with ``ast.literal_eval`` — a literal tuple of tuples, no calls.
RESEEDS_ATTR = "_RESEEDS"

#: The in-tree specs seeded ``active=true`` into ``pipeline_templates`` —
#: ``(slug, spec module, spec attr)``. Add a NEW active graph_def spec here so
#: the reseed gate covers it (mirrors ``_active_specs()`` in
#: ``test_graph_def_contract_freshness.py``; a test pins the two lists equal).
ACTIVE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("canonical_blog", "poindexter.services.canonical_blog_spec", "CANONICAL_BLOG_GRAPH_DEF"),
    ("dev_diary", "poindexter.services.dev_diary_spec", "DEV_DIARY_GRAPH_DEF"),
    ("image_rebuild", "poindexter.services.image_rebuild_spec", "IMAGE_REBUILD_GRAPH_DEF"),
    ("media_pipeline", "poindexter.services.media_pipeline_spec", "MEDIA_PIPELINE_GRAPH_DEF"),
    ("podcast_pipeline", "poindexter.services.podcast_pipeline_spec", "PODCAST_PIPELINE_GRAPH_DEF"),
    ("seo_refresh", "poindexter.services.seo_refresh_spec", "SEO_REFRESH_GRAPH_DEF"),
)

_SIGNATURE_RE = re.compile(r"^[0-9a-f]{12}$")

_UPDATE_SQL = (
    "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
    "version = $2, updated_at = now() "
    "WHERE slug = $3 AND active = true"
)


@dataclass(frozen=True)
class Reseed:
    """One ``_RESEEDS`` entry. ``graph_signature`` is ``None`` for the legacy
    4-field shape (migrations before 2026-09-22 declared no signature); the
    gate treats those as "predates the convention" and never as current."""

    slug: str
    version: int
    spec_module: str
    spec_attr: str
    graph_signature: str | None = None

    @classmethod
    def from_tuple(cls, raw: Any) -> Reseed:
        if not isinstance(raw, (tuple, list)) or len(raw) not in (4, 5):
            raise ValueError(
                f"reseed entry must be (slug, version, module, attr[, signature]); got {raw!r}"
            )
        slug, version, module, attr = raw[:4]
        signature = raw[4] if len(raw) == 5 else None
        if not (isinstance(slug, str) and slug):
            raise ValueError(f"reseed slug must be a non-empty str; got {slug!r}")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError(f"reseed version for {slug!r} must be a positive int; got {version!r}")
        if not (isinstance(module, str) and isinstance(attr, str) and module and attr):
            raise ValueError(f"reseed spec location for {slug!r} must be (module, attr) strs")
        if signature is not None and not (
            isinstance(signature, str) and _SIGNATURE_RE.match(signature)
        ):
            raise ValueError(
                f"reseed signature for {slug!r} must be 12 lowercase hex chars; got {signature!r}"
            )
        return cls(slug, version, module, attr, signature)


@dataclass(frozen=True)
class DeclaredReseed:
    """A :class:`Reseed` together with the migration file that declares it."""

    migration: str
    reseed: Reseed


def load_spec(reseed: Reseed) -> dict[str, Any]:
    """Import the RAW in-tree spec a reseed writes (pure data, no registry)."""
    spec = getattr(importlib.import_module(reseed.spec_module), reseed.spec_attr)
    if not isinstance(spec, dict) or not spec.get("nodes"):
        raise ValueError(
            f"{reseed.spec_module}.{reseed.spec_attr} is not a graph_def spec with nodes"
        )
    return spec


def expected_graph_signature(spec: dict[str, Any]) -> str:
    """The signature a reseed of ``spec`` brings the row to: the graph
    signature of the spec stamped against the LIVE atom registry. Needs the
    registry env (imports ``pipeline_architect`` lazily)."""
    from poindexter.services.pipeline_architect import (  # noqa: PLC0415 — registry env only
        graph_signature,
        stamp_graph_def,
    )

    return graph_signature(stamp_graph_def(spec))


async def apply_reseeds(
    pool: Any, reseeds: Iterable[Reseed | tuple[Any, ...]], *, log_prefix: str
) -> int:
    """Write each reseed's RAW spec at its version, then restamp through the
    boot self-heal when the registry env is available. Returns the number of
    rows written.

    RAW (no per-node ``_contract_fp``) is deliberate: it keeps the migration
    importable in the ``migrations-smoke`` env, and a fully-unstamped row is
    exactly the shape ``ensure_active_graph_defs_stamped`` restamps — on this
    call when the registry imports, else on the worker's next boot.
    """
    entries = [r if isinstance(r, Reseed) else Reseed.from_tuple(r) for r in reseeds]
    written = 0
    async with pool.acquire() as conn:
        for entry in entries:
            spec = load_spec(entry)
            tag = await conn.execute(_UPDATE_SQL, json.dumps(spec), entry.version, entry.slug)
            logger.info(
                "%s up: %s v%d %s (declared graph signature %s)",
                log_prefix,
                entry.slug,
                entry.version,
                tag,
                entry.graph_signature or "-",
            )
            written += 1

    try:
        from poindexter.services.pipeline_architect import (  # noqa: PLC0415 — registry env only
            ensure_active_graph_defs_stamped,
        )
    except ImportError as exc:
        logger.info(
            "%s: registry env unavailable, stamps deferred to boot self-heal (%s)",
            log_prefix,
            exc,
        )
        return written
    stamped = await ensure_active_graph_defs_stamped(pool)
    logger.info("%s: restamped %d row(s)", log_prefix, stamped)
    return written


def _reseeds_literal(source: str) -> Any | None:
    """The literal bound to :data:`RESEEDS_ATTR` at module level, or ``None``.

    AST + ``literal_eval`` — the gate must never import or execute a migration
    (they are not importable in every env, and executing one is applying it).
    """
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == RESEEDS_ATTR for t in node.targets
        ):
            return ast.literal_eval(node.value)
    return None


def declared_reseeds(migrations_dir: Path) -> dict[str, list[DeclaredReseed]]:
    """Every ``_RESEEDS`` declaration in ``migrations_dir``, keyed by slug and
    ordered by migration name (the timestamp prefix — i.e. apply order).

    A migration whose ``_RESEEDS`` is not a literal, or whose entries are
    malformed, raises — a declaration the gate cannot read is a declaration
    that was never checked.
    """
    out: dict[str, list[DeclaredReseed]] = {}
    for path in sorted(migrations_dir.glob("*.py")):
        literal = _reseeds_literal(path.read_text(encoding="utf-8"))
        if literal is None:
            continue
        if not isinstance(literal, (tuple, list)):
            raise ValueError(f"{path.name}: {RESEEDS_ATTR} must be a tuple of entries")
        for raw in literal:
            try:
                entry = Reseed.from_tuple(raw)
            except ValueError as exc:
                raise ValueError(f"{path.name}: {exc}") from exc
            out.setdefault(entry.slug, []).append(DeclaredReseed(path.name, entry))
    return out


def writes_graph_def(source: str) -> bool:
    """Does this migration source write ``pipeline_templates.graph_def``?
    The textual net the convention test casts over migrations newer than the
    bootstrap — any such write must come with a ``_RESEEDS`` declaration."""
    return bool(re.search(r"SET\s+graph_def\s*=", source, re.IGNORECASE))


__all__ = [
    "ACTIVE_SPECS",
    "RESEEDS_ATTR",
    "DeclaredReseed",
    "Reseed",
    "apply_reseeds",
    "declared_reseeds",
    "expected_graph_signature",
    "load_spec",
    "writes_graph_def",
]
