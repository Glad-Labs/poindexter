"""Add preview_token column to posts table for draft preview URLs."""


async def up(pool):
    async with pool.acquire() as conn:
        exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'posts' AND column_name = 'preview_token'
            )
        """)
        if not exists:
            await conn.execute("""
                ALTER TABLE posts ADD COLUMN preview_token TEXT
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_posts_preview_token
                ON posts (preview_token) WHERE preview_token IS NOT NULL
            """)
