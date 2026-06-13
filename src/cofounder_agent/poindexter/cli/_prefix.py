"""Shared UUID-prefix resolution for the ``poindexter`` CLI.

Operator surfaces — Grafana panels, ``poindexter tasks list``,
``poindexter media pending``, the awaiting-approval queues — render ids
as 8-char prefixes (``LEFT(<id>::text, 8)``). Operators naturally paste
that prefix back into a CLI command whose service layer exact-matches a
UUID column, so asyncpg rejects the bare prefix client-side
(``invalid UUID '<prefix>': length must be between 32..36 characters``)
or the query simply returns nothing.

Three commands hit this one crash at a time and each grew its own fix
(#480 ``approve``/``reject``, #1490 ``pipeline resume``/``status``, the
``media`` / ``publish`` / ``schedule`` gaps). This module is the single
implementation they all delegate to: **exact-match-then-unique-LIKE-prefix**,
comparing on ``<column>::text`` so the bound parameter stays text (a bare
prefix is too short to cast to ``uuid`` client-side).

Design notes
------------

* ``table`` / ``column`` / ``order_by`` are interpolated into the SQL.
  They are **caller-supplied identifiers** (literal strings in CLI code),
  never operator input — the operator only supplies the bound ``prefix``.
  Do not pass user-controlled values as identifiers here.
* The resolver returns the full id (or ``None``) rather than the row, so
  each caller keeps its own richer fetch + its own not-found UX. The one
  shared decision is "does this prefix name exactly one row?".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import click


def looks_like_full_uuid(value: str) -> bool:
    """True when ``value`` is already a full 36-char dashed UUID.

    Callers use this to skip the DB round trip entirely when the operator
    pasted a complete id. Deliberately shape-based (length + dash count)
    rather than a strict parse: a malformed-but-full-length string falls
    through to the caller's own exact-match, which fails loud the same way
    it always did.
    """
    return bool(value) and len(value) >= 36 and value.count("-") == 4


class AmbiguousPrefixError(click.UsageError):
    """A short id prefix matched more than one row.

    Subclasses :class:`click.UsageError` so a caller that doesn't catch it
    still gets Click's standard ``Error: ...`` stderr render and exit
    code 2 — never a raw traceback. Callers wanting a different exception
    type (e.g. the #480 ``approve``/``reject`` contract) catch this and
    re-raise their own.
    """

    def __init__(self, prefix: str, candidates: Sequence[str], *, noun: str = "row") -> None:
        self.prefix = prefix
        self.candidates = list(candidates)
        shown = ", ".join(self.candidates[:10])
        plural = noun if noun.endswith("s") else f"{noun}s"
        super().__init__(
            f"Ambiguous id prefix {prefix!r} matches {len(self.candidates)} "
            f"{plural}: {shown}. Use the full id or a longer prefix."
        )


async def fetch_prefix_candidates(
    pool: Any,
    *,
    table: str,
    column: str,
    prefix: str,
    select_extra: Sequence[str] = (),
    extra_where: str | None = None,
    params: Sequence[Any] = (),
    order_by: str | None = None,
    limit: int = 5,
) -> list[Any]:
    """Return up to ``limit`` rows whose ``column`` starts with ``prefix``.

    The match is ``<column>::text LIKE <prefix> || '%'`` — casting the
    column to text keeps the comparison off the ``uuid`` type, so a bare
    8-char prefix never trips asyncpg's client-side UUID validation.

    ``params`` bind any placeholders in ``extra_where`` (``$1``..``$N``);
    the prefix is appended as the final bound parameter (``$N+1``). Each
    candidate row exposes ``column`` (cast to text) plus any
    ``select_extra`` columns the caller wants for a disambiguation
    message.
    """
    select_cols = ", ".join([f"{column}::text AS {column}", *select_extra])
    prefix_param = f"${len(params) + 1}"
    where = f"{column}::text LIKE {prefix_param} || '%'"
    if extra_where:
        where = f"({where}) AND ({extra_where})"
    sql = f"SELECT {select_cols} FROM {table} WHERE {where}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    sql += f" LIMIT {int(limit)}"

    async with pool.acquire() as conn:
        return await conn.fetch(sql, *params, prefix)


async def resolve_uuid_prefix(
    pool: Any,
    *,
    table: str,
    column: str,
    prefix: str,
    extra_where: str | None = None,
    params: Sequence[Any] = (),
    noun: str = "row",
) -> str | None:
    """Expand a short id prefix to the single full UUID it names.

    * full UUID in → returned unchanged, **no DB round trip**
    * exactly one match → the full id (``str``)
    * zero matches → ``None`` (the caller raises its own not-found error)
    * many distinct matches → :class:`AmbiguousPrefixError`

    Duplicate ids collapse before the ambiguity check, so resolving
    against a non-unique column (e.g. ``media_approvals.post_id``, which
    repeats once per medium) treats "same id, many rows" as one match.
    """
    if looks_like_full_uuid(prefix):
        return prefix

    rows = await fetch_prefix_candidates(
        pool,
        table=table,
        column=column,
        prefix=prefix,
        extra_where=extra_where,
        params=params,
        limit=6,
    )

    ids: list[str] = []
    for row in rows:
        rid = row[column]
        if rid not in ids:
            ids.append(rid)

    if not ids:
        return None
    if len(ids) > 1:
        raise AmbiguousPrefixError(prefix, ids, noun=noun)
    return ids[0]


__all__ = [
    "AmbiguousPrefixError",
    "fetch_prefix_candidates",
    "looks_like_full_uuid",
    "resolve_uuid_prefix",
]
