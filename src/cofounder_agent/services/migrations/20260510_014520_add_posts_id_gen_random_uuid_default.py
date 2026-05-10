"""Add ``DEFAULT gen_random_uuid()`` to ``posts.id``.

Every other UUID primary key in the baseline schema carries
``DEFAULT gen_random_uuid()`` — `media_assets.id`, `experiments.id`,
`integrations` rows, etc. `posts.id` is the odd one out: it's
`uuid NOT NULL` with NO default. Production code dodges this by
generating the UUID in Python and passing it as the first INSERT
parameter (`services.content_db.py` + `services.sync_service.py`),
but tests that don't replicate that pattern fail with:

    asyncpg.exceptions.NotNullViolationError: null value in column "id"
    of relation "posts" violates not-null constraint

23 gates tests in `tests/unit/services/gates/test_post_approval_gates.py`
hit this on every run because the test fixture builds posts via
`INSERT INTO posts (title, slug, content, status) VALUES (...)` and
expects the DB to fill in the id.

Adding the default is non-disruptive: existing rows already have
ids, the production INSERTs that explicitly pass the id keep working
(an explicit value still overrides the default), and tests that
omit the id now get a fresh UUID.

Idempotent — `ALTER TABLE ... SET DEFAULT` is safe to re-run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE posts ALTER COLUMN id SET DEFAULT gen_random_uuid()"
        )
    logger.info(
        "20260510_014520: posts.id now defaults to gen_random_uuid() "
        "(matches every other uuid PK in the schema)"
    )
