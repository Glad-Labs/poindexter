"""Unit tests for ``modules.finance.mercury_client``.

F1 (2026-05-13). Tests use httpx's MockTransport — no real network,
no external dependencies. Pins the parser shape against a captured
sample response shape (so a Mercury-side rename breaks a test
rather than silently producing zero balances).
"""

from __future__ import annotations

import json

import httpx
import pytest

from modules.finance.mercury_client import (
    MercuryAccount,
    MercuryAPIError,
    MercuryAuthError,
    MercuryClient,
    MercuryTransaction,
)


def _mock_transport(handler):
    """Wrap a request-handler callable into an httpx MockTransport.
    The handler receives a ``Request`` and returns a ``Response``."""
    return httpx.MockTransport(handler)


def _install_mock(client: MercuryClient, transport: httpx.MockTransport):
    """Replace the live httpx client on a MercuryClient with one that
    uses our MockTransport. Called inside the ``async with`` block."""
    assert client._client is not None, "use inside async with"
    # Swap the transport on the existing client so headers etc. carry over
    client._client._transport = transport


@pytest.mark.unit
def test_blank_token_raises_auth_error():
    """A missing token must fail loudly at construction time —
    matches feedback_no_silent_defaults."""
    with pytest.raises(MercuryAuthError, match="non-empty API token"):
        MercuryClient(token="")


@pytest.mark.unit
async def test_list_accounts_parses_response():
    sample = {
        "accounts": [
            {
                "id": "acc-1",
                "name": "Glad Labs Checking",
                "type": "checking",
                "currentBalance": 12345.67,
                "availableBalance": 12300.00,
                "kind": "businessChecking",
            },
            {
                "id": "acc-2",
                "name": "Glad Labs Savings",
                "type": "savings",
                "currentBalance": 50000.0,
                "availableBalance": 50000.0,
                "kind": "businessSavings",
            },
        ]
    }

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/accounts"
        assert req.headers["authorization"] == "Bearer test-token-1234"
        return httpx.Response(200, content=json.dumps(sample))

    async with MercuryClient(token="test-token-1234") as m:
        _install_mock(m, _mock_transport(handler))
        accounts = await m.list_accounts()

    assert len(accounts) == 2
    assert isinstance(accounts[0], MercuryAccount)
    assert accounts[0].id == "acc-1"
    assert accounts[0].name == "Glad Labs Checking"
    assert accounts[0].current_balance == 12345.67
    assert accounts[0].available_balance == 12300.00
    assert accounts[0].kind == "businessChecking"
    assert accounts[1].current_balance == 50000.0


@pytest.mark.unit
async def test_get_account_parses_response():
    sample = {
        "id": "acc-1",
        "name": "Glad Labs Checking",
        "type": "checking",
        "currentBalance": 9999.99,
        "availableBalance": 9950.00,
        "kind": "businessChecking",
    }

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/account/acc-1"
        return httpx.Response(200, content=json.dumps(sample))

    async with MercuryClient(token="t") as m:
        _install_mock(m, _mock_transport(handler))
        a = await m.get_account("acc-1")

    assert a.id == "acc-1"
    assert a.current_balance == 9999.99


@pytest.mark.unit
async def test_list_transactions_parses_response():
    sample = {
        "transactions": [
            {
                "id": "txn-1",
                "amount": -50.25,
                "postedAt": "2026-05-12T14:30:00Z",
                "counterpartyName": "Stripe Inc",
                "status": "posted",
            },
            {
                "id": "txn-2",
                "amount": 1000.00,
                "postedAt": "2026-05-13T09:15:00Z",
                "counterpartyName": "Gumroad Inc",
                "status": "posted",
            },
        ]
    }

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/account/acc-1/transactions"
        # limit/offset get passed through
        assert "limit=10" in str(req.url)
        return httpx.Response(200, content=json.dumps(sample))

    async with MercuryClient(token="t") as m:
        _install_mock(m, _mock_transport(handler))
        txns = await m.list_transactions("acc-1", limit=10)

    assert len(txns) == 2
    assert isinstance(txns[0], MercuryTransaction)
    assert txns[0].id == "txn-1"
    assert txns[0].amount == -50.25
    assert txns[0].counterparty == "Stripe Inc"
    assert txns[1].amount == 1000.00


@pytest.mark.unit
async def test_401_raises_auth_error():
    """A 401 from Mercury must surface as MercuryAuthError so the
    operator sees a real "token revoked" alert instead of a silent
    drop to zero balance."""
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b'{"error":"unauthorized"}')

    async with MercuryClient(token="bad") as m:
        _install_mock(m, _mock_transport(handler))
        with pytest.raises(MercuryAuthError, match="401"):
            await m.list_accounts()


@pytest.mark.unit
async def test_500_raises_api_error():
    """5xx is distinct from auth — caller may retry vs. paging."""
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b'{"error":"internal"}')

    async with MercuryClient(token="t") as m:
        _install_mock(m, _mock_transport(handler))
        with pytest.raises(MercuryAPIError, match="500"):
            await m.list_accounts()


@pytest.mark.unit
async def test_must_be_used_as_context_manager():
    """Calling client methods outside ``async with`` is a programmer
    error — fail loudly."""
    m = MercuryClient(token="t")
    with pytest.raises(RuntimeError, match="async context manager"):
        await m.list_accounts()
