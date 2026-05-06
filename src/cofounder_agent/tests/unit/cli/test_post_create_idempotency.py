"""Click CLI tests for ``poindexter post create`` idempotency (#338).

The original ``post create`` command would happily insert a fresh
``posts`` row on every invocation — a fat-fingered double-tap during
content staging produced two near-duplicate posts + two parallel gate
trees. Glad-Labs/poindexter#338 ships a small idempotency layer:

- A stable 16-hex-char key derived from
  ``topic + media + gates + operator``
- An ``app_settings`` window
  (``cli_post_create_idempotency_window_minutes``, default 30) over
  which the same key returns the existing post id
- A master switch ``cli_post_create_idempotency_enabled``
- A ``--force`` flag the operator can use to bypass the check

These tests exercise the four switching points (hit, expiry, force,
disabled) plus the key formula itself. They patch ``asyncpg``,
``services.gates.post_approval_gates`` (the gate creator + notifier
+ getter) and ``services.site_config.SiteConfig`` so the suite never
touches a real DB.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.posts import _compute_idempotency_key, post_group


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")


def _make_conn(*, fetchrow_results: list[dict | None]) -> MagicMock:
    """Build an asyncpg-shaped connection with scripted ``fetchrow`` results.

    Pass the results in the order the CLI will consume them. For the
    happy-miss path that's ``[lookup_result, insert_result]``; for the
    force/disabled path that's ``[insert_result]`` (no lookup); for
    the hit path it's ``[lookup_result]`` (no insert because the CLI
    short-circuits).
    """
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=list(fetchrow_results))
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def fake_asyncpg(fake_dsn):
    """Patch ``asyncpg.create_pool`` so the CLI never reaches a real DB.

    Mirrors the pattern used by ``test_topics_cli.fake_asyncpg``.
    """
    conn = _make_conn(fetchrow_results=[
        None,  # lookup miss
        {
            "id": "11111111-2222-3333-4444-555555555555",
            "slug": "x-aabbcc",
            "title": "X",
            "status": "draft",
        },  # INSERT result
    ])

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock(return_value=None)

    async def _create_pool(_dsn, **_kwargs):
        return pool

    asyncpg = MagicMock()
    asyncpg.create_pool = _create_pool

    with patch.dict("sys.modules", {"asyncpg": asyncpg}):
        yield {"conn": conn, "pool": pool, "asyncpg": asyncpg}


def _patch_site_config(initial: dict[str, str] | None = None):
    """Replace ``SiteConfig`` with a stub fed from ``initial``.

    The CLI imports ``SiteConfig`` from ``services.site_config``, so we
    swap the class. Tests that need the master-switch / window-minutes
    knobs override them via ``initial``.
    """
    from services.site_config import SiteConfig

    class _StubSiteConfig(SiteConfig):
        def __init__(self, *_args, pool=None, **_kwargs):
            super().__init__(initial_config=dict(initial or {}), pool=pool)

        async def load(self, _pool):  # no-op (don't hit DB)
            return 0

    return patch("services.site_config.SiteConfig", _StubSiteConfig)


def _patch_gate_helpers(*, inserted_gates: list[dict] | None = None,
                         existing_gates: list[dict] | None = None):
    """Patch the three gate-helpers the CLI imports inside _impl().

    - ``create_gates_for_post`` returns ``inserted_gates``
    - ``get_gates_for_post`` returns ``existing_gates`` (used on the
      idempotent-hit path)
    - ``notify_gate_pending`` is a no-op (best-effort in production)
    """
    create_mock = AsyncMock(return_value=inserted_gates or [])
    get_mock = AsyncMock(return_value=existing_gates or [])
    notify_mock = AsyncMock(return_value=None)

    return patch.multiple(
        "services.gates.post_approval_gates",
        create_gates_for_post=create_mock,
        get_gates_for_post=get_mock,
        notify_gate_pending=notify_mock,
    ), create_mock, get_mock, notify_mock


# ---------------------------------------------------------------------------
# _compute_idempotency_key — pure-function smoke tests
# ---------------------------------------------------------------------------


class TestComputeIdempotencyKey:
    def test_same_inputs_produce_same_key(self):
        key1 = _compute_idempotency_key(
            topic="My Post", media=["podcast"], gates=["draft", "final"],
            operator="alice",
        )
        key2 = _compute_idempotency_key(
            topic="My Post", media=["podcast"], gates=["draft", "final"],
            operator="alice",
        )
        assert key1 == key2
        assert len(key1) == 16

    def test_media_order_does_not_matter(self):
        # Sorted internally so a,b == b,a.
        key_ab = _compute_idempotency_key(
            topic="t", media=["a", "b"], gates=[], operator="op",
        )
        key_ba = _compute_idempotency_key(
            topic="t", media=["b", "a"], gates=[], operator="op",
        )
        assert key_ab == key_ba

    def test_gates_order_does_not_matter(self):
        key_df = _compute_idempotency_key(
            topic="t", media=[], gates=["draft", "final"], operator="op",
        )
        key_fd = _compute_idempotency_key(
            topic="t", media=[], gates=["final", "draft"], operator="op",
        )
        assert key_df == key_fd

    def test_different_operators_produce_different_keys(self):
        key_a = _compute_idempotency_key(
            topic="t", media=[], gates=[], operator="alice",
        )
        key_b = _compute_idempotency_key(
            topic="t", media=[], gates=[], operator="bob",
        )
        assert key_a != key_b

    def test_different_topics_produce_different_keys(self):
        key_x = _compute_idempotency_key(
            topic="X", media=[], gates=[], operator="op",
        )
        key_y = _compute_idempotency_key(
            topic="Y", media=[], gates=[], operator="op",
        )
        assert key_x != key_y


# ---------------------------------------------------------------------------
# CLI: idempotent hit — second invocation returns existing row
# ---------------------------------------------------------------------------


class TestIdempotentHit:
    def test_returns_existing_post_when_lookup_hits(
        self, runner, fake_asyncpg
    ):
        # Override the default fixture conn so the SELECT returns an
        # existing row instead of None.
        existing_id = "abcdef01-2222-3333-4444-555555555555"
        existing_row = {
            "id": existing_id,
            "slug": "my-topic-aabbcc",
            "title": "my topic",
            "status": "draft",
            "media_to_generate": [],
        }
        new_conn = _make_conn(fetchrow_results=[existing_row])
        fake_asyncpg["pool"].acquire.return_value.__aenter__ = AsyncMock(
            return_value=new_conn
        )

        existing_gates = [
            {"gate_name": "draft", "ordinal": 1, "state": "pending",
             "approver": None, "notes": None, "metadata": {}},
        ]

        site_patch = _patch_site_config(
            initial={
                "cli_post_create_idempotency_enabled": "true",
                "cli_post_create_idempotency_window_minutes": "30",
                "default_workflow_gates": "draft",
            }
        )
        gate_patch, _create_mock, _get_mock, _notify_mock = (
            _patch_gate_helpers(existing_gates=existing_gates)
        )

        with site_patch, gate_patch:
            result = runner.invoke(
                post_group,
                ["create", "--topic", "my topic", "--json"],
            )

        assert result.exit_code == 0, result.output
        # JSON output goes to stdout; the idempotent-hit log line goes
        # to stderr. CliRunner merges them by default unless
        # ``mix_stderr=False`` — we just check the JSON payload.
        payload = json.loads(_extract_json(result.output))
        assert payload["idempotent_hit"] is True
        assert payload["post_id"] == existing_id
        assert payload["gates"][0]["gate_name"] == "draft"

        # The INSERT path must NOT have been called — only the lookup.
        assert new_conn.fetchrow.await_count == 1


# ---------------------------------------------------------------------------
# CLI: window expiry — lookup misses, fresh insert happens
# ---------------------------------------------------------------------------


class TestWindowExpiry:
    def test_no_existing_row_inserts_fresh_post(
        self, runner, fake_asyncpg
    ):
        # SELECT returns None (existing row outside the window or
        # absent), INSERT returns a fresh row.
        new_id = "fff00000-1111-2222-3333-444444444444"
        new_conn = _make_conn(fetchrow_results=[
            None,  # lookup miss (window-expired or never seen)
            {
                "id": new_id,
                "slug": "my-topic-aabbcc",
                "title": "my topic",
                "status": "draft",
            },  # INSERT result
        ])
        fake_asyncpg["pool"].acquire.return_value.__aenter__ = AsyncMock(
            return_value=new_conn
        )

        site_patch = _patch_site_config(
            initial={
                "cli_post_create_idempotency_enabled": "true",
                # Window of 1 minute — irrelevant since the lookup
                # already returns None for this test, but we set it to
                # mirror what an "after-expiry" production state looks
                # like to the SQL layer.
                "cli_post_create_idempotency_window_minutes": "1",
                "default_workflow_gates": "draft",
            }
        )
        gate_patch, _create_mock, _get_mock, _notify_mock = (
            _patch_gate_helpers(
                inserted_gates=[
                    {"gate_name": "draft", "ordinal": 1, "state": "pending"},
                ],
            )
        )

        with site_patch, gate_patch:
            result = runner.invoke(
                post_group,
                ["create", "--topic", "my topic", "--json"],
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(_extract_json(result.output))
        assert payload["idempotent_hit"] is False
        assert payload["post_id"] == new_id
        # Both the SELECT (lookup) and the INSERT must have been called.
        assert new_conn.fetchrow.await_count == 2

        # The INSERT must have stored the idempotency key on the row
        # (4th positional arg to fetchrow's INSERT call).
        insert_call = new_conn.fetchrow.await_args_list[1]
        # Args: query, title, slug, media, key
        stored_key = insert_call.args[4]
        assert stored_key is not None
        assert isinstance(stored_key, str) and len(stored_key) == 16


# ---------------------------------------------------------------------------
# CLI: --force — bypass the lookup entirely
# ---------------------------------------------------------------------------


class TestForceBypass:
    def test_force_skips_lookup_and_does_not_store_key(
        self, runner, fake_asyncpg
    ):
        # --force skips the SELECT entirely. Only the INSERT runs, so
        # only one fetchrow result needs scripting.
        new_id = "99999999-1111-2222-3333-444444444444"
        new_conn = _make_conn(fetchrow_results=[
            {
                "id": new_id,
                "slug": "my-topic-aabbcc",
                "title": "my topic",
                "status": "draft",
            },  # INSERT result
        ])
        fake_asyncpg["pool"].acquire.return_value.__aenter__ = AsyncMock(
            return_value=new_conn
        )

        site_patch = _patch_site_config(
            initial={
                "cli_post_create_idempotency_enabled": "true",
                "cli_post_create_idempotency_window_minutes": "30",
                "default_workflow_gates": "draft",
            }
        )
        gate_patch, _create_mock, _get_mock, _notify_mock = (
            _patch_gate_helpers(
                inserted_gates=[
                    {"gate_name": "draft", "ordinal": 1, "state": "pending"},
                ],
            )
        )

        with site_patch, gate_patch:
            result = runner.invoke(
                post_group,
                ["create", "--topic", "my topic", "--force", "--json"],
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(_extract_json(result.output))
        assert payload["idempotent_hit"] is False
        assert payload["post_id"] == new_id  # NOT existing_id

        # --force skips the SELECT, so only one fetchrow call — the INSERT.
        assert new_conn.fetchrow.await_count == 1

        # --force also stores NULL in cli_idempotency_key so a
        # subsequent forced-twin doesn't create a phantom dedup target.
        insert_call = new_conn.fetchrow.await_args_list[0]
        stored_key = insert_call.args[4]
        assert stored_key is None


# ---------------------------------------------------------------------------
# CLI: master switch off — behavior matches pre-#338 (no lookup, no key)
# ---------------------------------------------------------------------------


class TestMasterSwitchOff:
    def test_disabled_skips_lookup_and_stores_no_key(
        self, runner, fake_asyncpg
    ):
        new_id = "55555555-1111-2222-3333-444444444444"
        new_conn = _make_conn(fetchrow_results=[
            {
                "id": new_id,
                "slug": "my-topic-aabbcc",
                "title": "my topic",
                "status": "draft",
            },  # INSERT only — disabled = no SELECT lookup
        ])
        fake_asyncpg["pool"].acquire.return_value.__aenter__ = AsyncMock(
            return_value=new_conn
        )

        site_patch = _patch_site_config(
            initial={
                "cli_post_create_idempotency_enabled": "false",
                "cli_post_create_idempotency_window_minutes": "30",
                "default_workflow_gates": "draft",
            }
        )
        gate_patch, _create_mock, _get_mock, _notify_mock = (
            _patch_gate_helpers(
                inserted_gates=[
                    {"gate_name": "draft", "ordinal": 1, "state": "pending"},
                ],
            )
        )

        with site_patch, gate_patch:
            result = runner.invoke(
                post_group,
                ["create", "--topic", "my topic", "--json"],
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(_extract_json(result.output))
        assert payload["idempotent_hit"] is False
        assert payload["post_id"] == new_id

        # Master switch off: no lookup, no stored key — one fetchrow
        # (the INSERT) and a NULL idempotency_key column.
        assert new_conn.fetchrow.await_count == 1
        insert_call = new_conn.fetchrow.await_args_list[0]
        stored_key = insert_call.args[4]
        assert stored_key is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_json(out: str) -> str:
    """Pull the first ``{...}`` JSON object out of a CLI output blob.

    The CLI prints the idempotent-hit message + the JSON payload on
    different streams; CliRunner's default merges both into ``output``
    so we have to find the start of the JSON. It begins on a line that
    starts with ``{``.
    """
    lines = out.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("{"):
            start = i
            break
    assert start is not None, f"no JSON object in output: {out!r}"
    return "\n".join(lines[start:])
