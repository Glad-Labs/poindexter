# Migration Rollbacks

Each file in this directory reverses its corresponding forward migration.

## Usage

To rollback a specific migration:
```bash
psql $DATABASE_URL -f rollbacks/rollback_NNN_migration_name.sql
```

To rollback all migrations in reverse order (nuclear option — destroys all data):
```bash
for f in $(ls rollbacks/rollback_*.sql | sort -r); do
  echo "Running $f ..."
  psql $DATABASE_URL -f "$f"
done
```

## Naming Convention

`rollback_NNN_migration_name.sql` reverses `NNN_migration_name.sql`.

## Important Notes

- All rollback scripts are wrapped in `BEGIN`/`COMMIT` — they run as a single transaction
- Rollbacks that drop tables will **permanently destroy data** — back up first
- Rollback #007 (FK constraint) requires data integrity verification before applying
- Rollback #001 (`initial_schema`) must be applied **last** as other tables depend on `tasks`/`content_tasks`

## Recommended Rollback Order

Apply rollbacks in **reverse numeric order** (highest number first):
1. `rollback_014_add_post_tags_table.sql`
2. `rollback_013_agent_status_tracking_table.sql`
3. ... (continue in descending order)
14. `rollback_001_initial_schema.sql`
