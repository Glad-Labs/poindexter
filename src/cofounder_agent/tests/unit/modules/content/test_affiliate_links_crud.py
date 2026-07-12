"""Unit tests for the affiliate-link CRUD helpers (fake pool, no real DB).

Complements test_affiliate_links_service.py (pure matcher tests, no DB at
all) and the integration_db schema guard — these assert the SQL shape +
parameter order add_link/set_active/remove_link send, using a capturing
fake pool double.
"""

from __future__ import annotations

from modules.content.affiliate_links import add_link, remove_link, set_active


class _FakePool:
    def __init__(self, execute_result: str = "UPDATE 1"):
        self.calls: list[tuple] = []
        self._execute_result = execute_result

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return self._execute_result


async def test_add_link_persists_description_and_category():
    pool = _FakePool()
    await add_link(
        pool, code="mercury", keyword="Mercury", url="https://mercury.com/r/glad-labs",
        display_text="Mercury", program="Mercury Referral",
        description="Business banking we use daily.", category="service",
    )
    sql, args = pool.calls[0]
    assert "description" in sql
    assert "category" in sql
    assert args == (
        "mercury", "Mercury", "https://mercury.com/r/glad-labs", "Mercury",
        "Mercury Referral", "Business banking we use daily.", "service",
    )


async def test_add_link_defaults_category_to_product():
    pool = _FakePool()
    await add_link(
        pool, code="widget", keyword="Widget", url="https://x",
        description="A thing.",
    )
    _, args = pool.calls[0]
    assert args[-1] == "product"


async def test_set_active_returns_true_on_match():
    pool = _FakePool(execute_result="UPDATE 1")
    assert await set_active(pool, "mercury", False) is True


async def test_set_active_returns_false_when_no_match():
    pool = _FakePool(execute_result="UPDATE 0")
    assert await set_active(pool, "nope", False) is False


async def test_remove_link_returns_true_on_match():
    pool = _FakePool(execute_result="DELETE 1")
    assert await remove_link(pool, "mercury") is True


async def test_remove_link_returns_false_when_no_match():
    pool = _FakePool(execute_result="DELETE 0")
    assert await remove_link(pool, "nope") is False
