"""Integration tests for the DB-backed Taps.

Covers PostsTap, AuditTap, BrainKnowledgeTap, BrainDecisionsTap against
the real-services harness. Each test seeds its own rows into the
isolated ``poindexter_test`` DB and asserts the Tap yields Documents
with the expected shape.

GiteaIssuesTap has its own harness needs (HTTP + secrets) and is
tested separately.
"""

from __future__ import annotations

import asyncpg
import pytest

from plugins import Tap
from services.taps.audit import AuditTap
from services.taps.brain_decisions import BrainDecisionsTap
from services.taps.brain_knowledge import BrainKnowledgeTap
from services.taps.published_posts import PostsTap
from tests.integration.conftest import requires_real_services

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, requires_real_services]


# ---------------------------------------------------------------------------
# Schema fixtures — the Phase A0 harness's migrations_applied only
# guarantees app_settings exists. Taps that read from other tables need
# those tables created per-test.
# ---------------------------------------------------------------------------


async def _ensure_posts_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title TEXT,
            slug TEXT,
            content TEXT,
            excerpt TEXT,
            status TEXT,
            published_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


async def _ensure_audit_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id SERIAL PRIMARY KEY,
            event_type TEXT,
            source TEXT,
            task_id TEXT,
            details TEXT,
            severity TEXT,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


async def _ensure_brain_knowledge_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brain_knowledge (
            id SERIAL PRIMARY KEY,
            entity TEXT,
            attribute TEXT,
            value TEXT,
            source TEXT,
            confidence REAL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


async def _ensure_brain_decisions_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brain_decisions (
            id SERIAL PRIMARY KEY,
            decision TEXT,
            reasoning TEXT,
            context JSONB,
            confidence REAL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


# ---------------------------------------------------------------------------
# Protocol conformance (fast — no DB hits)
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_posts_tap_conforms(self):
        assert isinstance(PostsTap(), Tap)

    def test_audit_tap_conforms(self):
        assert isinstance(AuditTap(), Tap)

    def test_brain_knowledge_tap_conforms(self):
        assert isinstance(BrainKnowledgeTap(), Tap)

    def test_brain_decisions_tap_conforms(self):
        assert isinstance(BrainDecisionsTap(), Tap)


# ---------------------------------------------------------------------------
# PostsTap
# ---------------------------------------------------------------------------


class TestPostsTap:
    async def test_yields_published_posts_only(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await _ensure_posts_table(conn)
            await conn.execute(
                """
                INSERT INTO posts (title, slug, content, excerpt, status, published_at)
                VALUES
                  ('Published', 'pub', 'body1', 'exc1', 'published', NOW()),
                  ('Draft', 'drft', 'body2', 'exc2', 'draft', NULL)
                """
            )

        tap = PostsTap()
        docs = [d async for d in tap.extract(pool=clean_test_tables, config={})]

        assert len(docs) == 1
        assert docs[0].source_table == "posts"
        assert docs[0].metadata["slug"] == "pub"
        assert "Published" in docs[0].text
        assert "body1" in docs[0].text

    async def test_skips_empty_text(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await _ensure_posts_table(conn)
            await conn.execute(
                """
                INSERT INTO posts (title, slug, content, excerpt, status, published_at)
                VALUES (NULL, 'empty', NULL, NULL, 'published', NOW())
                """
            )

        tap = PostsTap()
        docs = [d async for d in tap.extract(pool=clean_test_tables, config={})]
        assert docs == []


# ---------------------------------------------------------------------------
# AuditTap
# ---------------------------------------------------------------------------


class TestAuditTap:
    async def test_filters_by_severity(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await _ensure_audit_table(conn)
            await conn.execute(
                """
                INSERT INTO audit_log (event_type, source, task_id, details, severity)
                VALUES
                  ('failure', 'worker', 'abc', '{}', 'error'),
                  ('warn_evt', 'brain', NULL, '{}', 'warning'),
                  ('routine', 'worker', NULL, '{}', 'info')
                """
            )

        tap = AuditTap()
        docs = [d async for d in tap.extract(pool=clean_test_tables, config={})]

        severities = {d.metadata["severity"] for d in docs}
        assert severities == {"error", "warning"}
        assert all(d.source_table == "audit" for d in docs)

    async def test_limit_honored(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await _ensure_audit_table(conn)
            for i in range(5):
                await conn.execute(
                    "INSERT INTO audit_log (event_type, source, details, severity) "
                    "VALUES ($1, 'worker', '{}', 'error')",
                    f"evt_{i}",
                )

        tap = AuditTap()
        docs = [d async for d in tap.extract(pool=clean_test_tables, config={"limit": 2})]
        assert len(docs) == 2


# ---------------------------------------------------------------------------
# BrainKnowledgeTap
# ---------------------------------------------------------------------------


class TestBrainKnowledgeTap:
    async def test_yields_documents(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await _ensure_brain_knowledge_table(conn)
            await conn.execute(
                """
                INSERT INTO brain_knowledge (entity, attribute, value, source, confidence)
                VALUES
                  ('gladlabs', 'site_url', 'https://gladlabs.io', 'app_settings', 1.0),
                  ('matt', 'role', 'founder', 'user_profile', 0.9)
                """
            )

        tap = BrainKnowledgeTap()
        docs = [d async for d in tap.extract(pool=clean_test_tables, config={})]

        assert len(docs) == 2
        assert all(d.source_table == "brain" for d in docs)
        assert all(d.writer == "brain-daemon" for d in docs)
        assert all(d.source_id.startswith("brain_knowledge/") for d in docs)

    async def test_skips_tiny_facts(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await _ensure_brain_knowledge_table(conn)
            await conn.execute(
                """
                INSERT INTO brain_knowledge (entity, attribute, value)
                VALUES ('a', 'b', 'c')
                """
            )
            # Facts under 10 chars are skipped per the pre-refactor behavior.

        tap = BrainKnowledgeTap()
        docs = [d async for d in tap.extract(pool=clean_test_tables, config={})]
        # "a: b = c" is 8 chars, should be skipped.
        assert docs == []


# ---------------------------------------------------------------------------
# BrainDecisionsTap
# ---------------------------------------------------------------------------


class TestBrainDecisionsTap:
    async def test_yields_documents(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        async with clean_test_tables.acquire() as conn:
            await _ensure_brain_decisions_table(conn)
            await conn.execute(
                """
                INSERT INTO brain_decisions (decision, reasoning, context, confidence)
                VALUES ('Retry task 123', 'transient ollama error', '{"task": "123"}'::jsonb, 0.8)
                """
            )

        tap = BrainDecisionsTap()
        docs = [d async for d in tap.extract(pool=clean_test_tables, config={})]

        assert len(docs) == 1
        assert docs[0].source_table == "brain"
        assert docs[0].writer == "brain-daemon"
        assert "Retry task 123" in docs[0].text
        assert "transient ollama error" in docs[0].text
