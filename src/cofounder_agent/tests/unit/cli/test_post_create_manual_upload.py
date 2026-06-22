"""Click CLI tests for the repurposed ``poindexter posts create`` — a manual
markdown write/upload command (NOT the AI pipeline).

History: ``posts create`` used to insert an empty ``posts`` shell
(``content=''``, ``--topic`` required) that nothing ever completed — the AI
pipeline claims ``pipeline_tasks`` rows, never ``posts`` rows, so the shell was
a dead end. This redesign turns it into the real manual counterpart of the
``create_post`` MCP / ``POST /api/tasks`` AI path: the operator supplies a
finished markdown body (``--from-file`` or stdin) and the command inserts a
complete ``posts`` row.

Contract pinned here:

* **Body** comes from ``--from-file PATH`` or stdin; it is REQUIRED — an empty
  body fails loud (closes the empty-shell hole).
* **Title** is ``--title`` (or the hidden deprecated ``--topic`` alias), else the
  first markdown H1 in the body; neither present fails loud.
* **Slug** is ``--slug``, else slugified title + a short random suffix.
* **Status** defaults to ``draft``; ``awaiting_approval`` is allowed; anything
  else fails loud.
* **Niche** ``--niche`` lands in ``metadata.niche_slug``; omitting it warns but
  proceeds.
* **Dedup** runs on the resolved title (shared guard, glad-labs-stack#1823);
  ``--force`` bypasses.
* **Idempotency (#338)** is re-keyed on ``content + operator`` (body is the
  identity now, not the topic) — same body twice is idempotent, an edited body
  is a new post; ``--force`` bypasses.

These tests patch ``asyncpg`` + ``SiteConfig`` + the dedup guard so the suite
never touches a real DB or an embedding model. The INSERT's positional args are
``(query, title, slug, content, excerpt, status, media, metadata, key)``.
"""

from __future__ import annotations

import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.posts import post_group

# INSERT ... RETURNING positional arg indices (args[0] is the SQL string).
A_TITLE = 1
A_SLUG = 2
A_CONTENT = 3
A_EXCERPT = 4
A_STATUS = 5
A_MEDIA = 6
A_METADATA = 7
A_KEY = 8


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")


def _inserted_row() -> dict:
    return {
        "id": "11111111-2222-3333-4444-555555555555",
        "slug": "derived-slug-aabbcc",
        "title": "Derived Title",
        "status": "draft",
    }


def _make_conn(fetchrow_results: list[dict | None]) -> MagicMock:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=list(fetchrow_results))
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def fake_asyncpg(fake_dsn):
    """Patch ``asyncpg.create_pool`` so the CLI never reaches a real DB.

    Default conn answers a single INSERT (idempotency off path). Tests that
    need a different script reassign ``__aenter__`` (see ``_use_conn``).
    """
    conn = _make_conn([_inserted_row()])
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
        yield {"pool": pool, "conn": conn}


def _use_conn(fake_asyncpg, fetchrow_results: list[dict | None]) -> MagicMock:
    conn = _make_conn(fetchrow_results)
    fake_asyncpg["pool"].acquire.return_value.__aenter__ = AsyncMock(
        return_value=conn
    )
    return conn


def _parse_json(result) -> dict:
    """Parse the ``--json`` payload out of CliRunner output.

    CliRunner mixes stderr into ``output`` (operational chatter like the
    idempotent-hit notice), so slice from the first ``{`` — the JSON object
    is always printed last, to stdout.
    """
    out = result.output or ""
    return _json.loads(out[out.index("{"):])


def _patch_site_config(initial: dict[str, str] | None = None):
    """Stub SiteConfig; idempotency off by default so the INSERT is the only op."""
    from services.site_config import SiteConfig

    seed = {"cli_post_create_idempotency_enabled": "false"}
    seed.update(initial or {})

    class _StubSiteConfig(SiteConfig):
        def __init__(self, *_args, pool=None, **_kwargs):
            super().__init__(initial_config=dict(seed), pool=pool)

        async def load(self, _pool):
            return 0

    return patch("services.site_config.SiteConfig", _StubSiteConfig)


def _patch_guard_allow():
    """No-op the dedup guard (allow path) so no MemoryClient is built."""
    import services.topic_dedup_guard as guard

    return patch.object(guard, "assert_topic_not_duplicate", AsyncMock())


def _patch_guard_block():
    """Make the dedup guard raise DuplicateTopicError on the resolved title."""
    import types

    import services.topic_dedup_guard as guard

    captured: dict = {}

    async def _fake(title, *, site_config=None, force=False, **_kw):
        captured["title"] = title
        match = types.SimpleNamespace(
            similarity=0.82,
            metadata={"title": "The VRAM Currency Problem"},
            source_id="post/bb10de87",
        )
        raise guard.DuplicateTopicError(topic=title, match=match, threshold=0.75)

    return patch.object(guard, "assert_topic_not_duplicate", _fake), captured


# ---------------------------------------------------------------------------
# Body sourcing
# ---------------------------------------------------------------------------


class TestBodySourcing:
    def test_body_from_stdin_populates_content(self, runner, fake_asyncpg):
        body = "# My Manual Post\n\nThis is a real body the operator wrote."
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group, ["create", "--json"], input=body
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        assert insert.args[A_CONTENT] == body
        # No AI pipeline: the row is inserted directly with the body present.
        assert fake_asyncpg["conn"].fetchrow.await_count == 1

    def test_body_from_file(self, runner, fake_asyncpg, tmp_path):
        f = tmp_path / "post.md"
        body = "# From A File\n\nUploaded from disk, not stdin."
        f.write_text(body, encoding="utf-8")
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group, ["create", "--from-file", str(f), "--json"]
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        assert insert.args[A_CONTENT] == body

    def test_empty_body_fails_loud_and_skips_insert(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group, ["create", "--title", "X", "--json"], input=""
            )
        assert result.exit_code != 0
        combined = (result.output or "") + str(result.exception or "")
        assert "body" in combined.lower()
        assert fake_asyncpg["conn"].fetchrow.await_count == 0

    def test_missing_file_fails_loud(self, runner, fake_asyncpg, tmp_path):
        missing = tmp_path / "nope.md"
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group, ["create", "--from-file", str(missing), "--json"]
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Title resolution
# ---------------------------------------------------------------------------


class TestTitleResolution:
    def test_explicit_title_used(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--title", "Explicit Title", "--json"],
                input="# Heading In Body\n\nBody.",
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        assert insert.args[A_TITLE] == "Explicit Title"

    def test_title_derived_from_first_h1(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--json"],
                input="# Derived From H1\n\nSome body text.",
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        assert insert.args[A_TITLE] == "Derived From H1"

    def test_no_title_no_h1_fails_loud(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--json"],
                input="Just a paragraph with no heading at all.",
            )
        assert result.exit_code != 0
        combined = (result.output or "") + str(result.exception or "")
        assert "title" in combined.lower()
        assert fake_asyncpg["conn"].fetchrow.await_count == 0

    def test_topic_alias_sets_title(self, runner, fake_asyncpg):
        # The deprecated --topic alias still feeds the title (backcompat).
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--topic", "Legacy Topic Title", "--json"],
                input="# Different H1\n\nBody.",
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        assert insert.args[A_TITLE] == "Legacy Topic Title"


# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------


class TestSlug:
    def test_explicit_slug_respected(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--title", "Whatever", "--slug", "my-custom-slug",
                 "--json"],
                input="Body text.",
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        assert insert.args[A_SLUG] == "my-custom-slug"

    def test_slug_derived_from_title(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--title", "Hello World Title", "--json"],
                input="Body text.",
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        slug = insert.args[A_SLUG]
        assert slug.startswith("hello-world-title-")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_defaults_to_draft(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--title", "T", "--json"],
                input="Body.",
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        assert insert.args[A_STATUS] == "draft"

    def test_status_awaiting_approval_accepted(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--title", "T", "--status", "awaiting_approval",
                 "--json"],
                input="Body.",
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        assert insert.args[A_STATUS] == "awaiting_approval"

    def test_invalid_status_fails_loud(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--title", "T", "--status", "published", "--json"],
                input="Body.",
            )
        assert result.exit_code != 0
        assert "published" in (result.output or "")


# ---------------------------------------------------------------------------
# Niche → metadata.niche_slug
# ---------------------------------------------------------------------------


class TestNiche:
    def test_niche_stored_in_metadata(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--title", "T", "--niche", "ai_ml", "--json"],
                input="Body.",
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        meta = _json.loads(insert.args[A_METADATA])
        assert meta.get("niche_slug") == "ai_ml"

    def test_missing_niche_warns_but_proceeds(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--title", "T", "--json"],
                input="Body.",
            )
        assert result.exit_code == 0, result.output
        assert "niche" in (result.output or "").lower()
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        assert _json.loads(insert.args[A_METADATA]) == {}


# ---------------------------------------------------------------------------
# Excerpt
# ---------------------------------------------------------------------------


class TestExcerpt:
    def test_excerpt_stored(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--title", "T", "--excerpt", "A short summary.",
                 "--json"],
                input="Body.",
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        assert insert.args[A_EXCERPT] == "A short summary."

    def test_excerpt_defaults_none(self, runner, fake_asyncpg):
        with _patch_site_config(), _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--title", "T", "--json"],
                input="Body.",
            )
        assert result.exit_code == 0, result.output
        insert = fake_asyncpg["conn"].fetchrow.await_args_list[0]
        assert insert.args[A_EXCERPT] is None


# ---------------------------------------------------------------------------
# Dedup guard (runs on the resolved title)
# ---------------------------------------------------------------------------


class TestDedup:
    def test_duplicate_title_blocks_and_skips_insert(self, runner, fake_asyncpg):
        guard_patch, captured = _patch_guard_block()
        with _patch_site_config(), guard_patch:
            result = runner.invoke(
                post_group,
                ["create", "--title", "Quantization and VRAM", "--json"],
                input="Body.",
            )
        assert result.exit_code != 0
        combined = (result.output or "") + str(result.exception or "")
        assert "VRAM Currency Problem" in combined
        assert "--force" in combined
        # Dedup ran against the resolved TITLE, not the raw body.
        assert captured.get("title") == "Quantization and VRAM"
        assert fake_asyncpg["conn"].fetchrow.await_count == 0

    def test_force_bypasses_dedup(self, runner, fake_asyncpg):
        guard_patch, captured = _patch_guard_block()
        with _patch_site_config(), guard_patch:
            result = runner.invoke(
                post_group,
                ["create", "--title", "Quantization and VRAM", "--force",
                 "--json"],
                input="Body.",
            )
        assert result.exit_code == 0, result.output
        # --force short-circuits before the guard and still inserts.
        assert captured == {}
        assert fake_asyncpg["conn"].fetchrow.await_count == 1

    def test_distinct_title_passes_guard_and_inserts(self, runner, fake_asyncpg):
        import services.topic_dedup_guard as guard

        allow = AsyncMock()
        with _patch_site_config(), patch.object(
            guard, "assert_topic_not_duplicate", allow
        ):
            result = runner.invoke(
                post_group,
                ["create", "--title", "A Fresh Distinct Angle", "--json"],
                input="Body.",
            )
        assert result.exit_code == 0, result.output
        # Guard ran against the resolved title (positional) and the row inserted.
        assert allow.await_args is not None
        assert allow.await_args.args[0] == "A Fresh Distinct Angle"
        assert fake_asyncpg["conn"].fetchrow.await_count == 1


# ---------------------------------------------------------------------------
# Idempotency re-keyed on content (#338)
# ---------------------------------------------------------------------------


class TestIdempotencyKeyedOnContent:
    def _stored_key(self, runner, fake_asyncpg, *, body, media=None):
        """Run one create with idempotency ON (lookup miss) and return the
        idempotency key stored on the INSERT."""
        conn = _use_conn(fake_asyncpg, [None, _inserted_row()])  # miss, insert
        argv = ["create", "--title", "Same Title", "--json"]
        if media is not None:
            argv += ["--media", media]
        site = _patch_site_config({"cli_post_create_idempotency_enabled": "true"})
        with site, _patch_guard_allow():
            result = runner.invoke(post_group, argv, input=body)
        assert result.exit_code == 0, result.output
        insert = conn.fetchrow.await_args_list[-1]
        return insert.args[A_KEY]

    def test_same_body_different_media_same_key(self, runner, fake_asyncpg):
        # Media is no longer part of the identity — only content + operator.
        k1 = self._stored_key(runner, fake_asyncpg, body="Same body.",
                              media="podcast")
        k2 = self._stored_key(runner, fake_asyncpg, body="Same body.",
                              media="video")
        assert k1 == k2

    def test_different_body_different_key(self, runner, fake_asyncpg):
        k1 = self._stored_key(runner, fake_asyncpg, body="Body number one.")
        k2 = self._stored_key(runner, fake_asyncpg, body="A different body.")
        assert k1 != k2

    def test_idempotent_hit_returns_existing_without_insert(
        self, runner, fake_asyncpg
    ):
        existing = {
            "id": "99999999-8888-7777-6666-555555555555",
            "slug": "existing-slug",
            "title": "Same Title",
            "status": "draft",
            "media_to_generate": [],
        }
        conn = _use_conn(fake_asyncpg, [existing])  # lookup hit; no INSERT
        site = _patch_site_config({"cli_post_create_idempotency_enabled": "true"})
        with site, _patch_guard_allow():
            result = runner.invoke(
                post_group,
                ["create", "--title", "Same Title", "--json"],
                input="Some body.",
            )
        assert result.exit_code == 0, result.output
        payload = _parse_json(result)
        assert payload["post_id"] == existing["id"]
        assert payload["idempotent_hit"] is True
        # Only the lookup ran — no INSERT.
        assert conn.fetchrow.await_count == 1
