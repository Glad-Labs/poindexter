"""Make cost_logs.task_id nullable + add cost_type column for electricity/idle tracking."""


async def up(pool):
    await pool.execute("""
        -- Allow NULL task_id for system-level costs (electricity, idle)
        ALTER TABLE cost_logs ALTER COLUMN task_id DROP NOT NULL;
    """)
    await pool.execute("""
        DO $$ BEGIN
            ALTER TABLE cost_logs ADD COLUMN cost_type VARCHAR(30) DEFAULT 'inference';
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$;
    """)
    await pool.execute("""
        CREATE INDEX IF NOT EXISTS idx_cost_logs_cost_type ON cost_logs (cost_type);
    """)
