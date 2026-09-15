"""`nonempty()` — a loop whose body never ran has not tested anything.

An assertion that lives only inside a loop is silent when the loop body never
executes. The test goes green having checked nothing, and it stays green for
exactly as long as the bug that emptied the iterable survives.

This is not hypothetical here. `test_every_route_requires_auth` asserted that
every data-plane route declares `verify_api_token`; with the router emptied —
what a registration bug produces — it passed, certifying that every route was
guarded while none were. A security claim, vacuously true.

The repo already holds this doctrine on the other side of the fence:
`scripts/ci/lib_scan_floor.py::require_scanned` exists because ten CI lints
were found reporting clean on a missing scan root, and CLAUDE.md states the
rule outright — *a check that scanned nothing has not passed*. The same rule
had never been applied to the tests themselves.

Usage — one word at the loop head:

    for route in nonempty(router.routes, "router.routes"):
        assert verify_api_token in ...

    async for doc in anonempty(tap.extract(...), "tap.extract"):
        assert doc["type"] == ...

Why a wrapper rather than `assert x` before the loop: it consumes the iterable
exactly once, so it is correct for generators, async generators, one-shot
cursors and expensive calls — the cases where re-evaluating to check
emptiness would be wrong or costly. It also gives the ratchet in
``test_no_vacuous_loop_assertions.py`` a single shape to recognise.

`for ... else: pytest.fail(...)` is an equally valid floor and is already used
in this suite; the ratchet accepts both.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from typing import TypeVar

T = TypeVar("T")

_MSG = (
    "VACUOUS: {what} yielded no items, so the assertions inside the loop never "
    "ran and this test proved nothing. Either the thing under test is empty "
    "(a real bug) or the fixture no longer produces data."
)


def nonempty(iterable: Iterable[T], what: str) -> Iterator[T]:
    """Yield from ``iterable``, failing the test if it was empty."""
    count = 0
    for item in iterable:
        count += 1
        yield item
    assert count, _MSG.format(what=what)


async def anonempty(iterable: AsyncIterable[T], what: str) -> AsyncIterator[T]:
    """Async form of :func:`nonempty`."""
    count = 0
    async for item in iterable:
        count += 1
        yield item
    assert count, _MSG.format(what=what)
