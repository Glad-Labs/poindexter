# Refactoring Continuation Guide

## Quick Reference - How to Continue

### Pattern to Apply for Each Method

```python
# BEFORE (vulnerable):
async def get_something(self, id: str) -> Optional[Dict[str, Any]]:
    sql = "SELECT * FROM table WHERE id = $1"
    params = [id]
    # ... execute query ...

# AFTER (safe):
async def get_something(self, id: str) -> Optional[Dict[str, Any]]:
    builder = ParameterizedQueryBuilder()
    sql, params = builder.select(
        columns=["*"],
        table="table",
        where_clauses=[("id", SQLOperator.EQ, id)]
    )
    # ... execute query ...
```

### SQLOperator Cheat Sheet

```python
SQLOperator.EQ          # = (equality)
SQLOperator.NE          # != (not equal)
SQLOperator.GT          # > (greater than)
SQLOperator.LT          # < (less than)
SQLOperator.GTE         # >= (greater than or equal)
SQLOperator.LTE         # <= (less than or equal)
SQLOperator.IN          # IN (list membership)
SQLOperator.NOT_IN      # NOT IN (not in list)
SQLOperator.LIKE        # LIKE (pattern matching)
SQLOperator.IS_NULL     # IS NULL (null check)
SQLOperator.IS_NOT_NULL # IS NOT NULL (not null check)
SQLOperator.BETWEEN     # BETWEEN (range check)
```

### Builder Methods

```python
# SELECT queries
sql, params = builder.select(
    columns=["col1", "col2"],           # List of columns (required)
    table="table_name",                 # Table name (required)
    where_clauses=[                     # Optional WHERE clauses
        ("column1", SQLOperator.EQ, value1),
        ("column2", SQLOperator.GT, value2)
    ],
    order_by=[("column1", "DESC")],     # Optional ORDER BY
    limit=10,                           # Optional LIMIT
    offset=0                            # Optional OFFSET
)

# INSERT queries
sql, params = builder.insert(
    table="table_name",                 # Table name (required)
    columns={                           # Column dict (required)
        "col1": value1,
        "col2": value2
    },
    return_columns=["id"]               # Optional RETURNING clause
)

# UPDATE queries
sql, params = builder.update(
    table="table_name",                 # Table name (required)
    columns={                           # Update columns dict (required)
        "col1": new_value1,
        "col2": new_value2
    },
    where_clauses=[                     # Optional WHERE clauses (IMPORTANT!)
        ("id", SQLOperator.EQ, id_value)
    ],
    return_columns=["*"]                # Optional RETURNING clause
)

# DELETE queries
sql, params = builder.delete(
    table="table_name",                 # Table name (required)
    where_clauses=[                     # WHERE clauses (ALWAYS include!)
        ("id", SQLOperator.EQ, id_value)
    ]
)
```

## Next 10 Methods to Refactor (Priority Order)

### High Priority (Read Operations - Easier)

1. **get_task_counts()** - COUNT(\*) with GROUP BY
2. **get_queued_tasks(limit)** - Simple SELECT with status filter
3. **get_drafts(limit, offset)** - SELECT with pagination
4. **get_user_by_id(user_id)** - Simple SELECT from users table
5. **get_user_by_email(email)** - SELECT with email filter

### Medium Priority (Write Operations - Medium difficulty)

6. **create_user(user_data)** - INSERT to users table
7. **update_user(user_id, updates)** - UPDATE with dynamic columns
8. **delete_user(user_id)** - DELETE with ID filter
9. **get_post_by_slug(slug)** - SELECT from posts table
10. **update_post(post_id, updates)** - UPDATE post with dynamic columns

## Testing Checklist for Each Method

After refactoring each method:

```bash
# 1. Run database service tests
npm run test:python -- tests/test_database_service.py -v

# 2. Run SQL safety tests
npm run test:python -- tests/test_sql_safety.py -v

# 3. Check for type errors (optional, has known config issues)
python -m mypy src/cofounder_agent/services/database_service.py
```

**Expected Results:**

- ✅ Same test pass rate as before (27/32 for database_service.py)
- ✅ 52/52 SQL safety tests passing
- ✅ No new errors introduced
- ✅ All refactored methods maintain backward compatibility

## Common Patterns in Remaining Methods

### Pattern 1: Simple SELECT by ID

```python
# Before
sql = "SELECT * FROM users WHERE user_id = $1"
params = [user_id]

# After
builder = ParameterizedQueryBuilder()
sql, params = builder.select(
    columns=["*"],
    table="users",
    where_clauses=[("user_id", SQLOperator.EQ, user_id)]
)
```

### Pattern 2: SELECT with Multiple Filters

```python
# Before
where_clauses = []
params = []
if status:
    where_clauses.append(f"status = ${len(params) + 1}")
    params.append(status)
if user_id:
    where_clauses.append(f"user_id = ${len(params) + 1}")
    params.append(user_id)
where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
sql = f"SELECT * FROM table WHERE {where_sql}"

# After
where_clauses = []
if status:
    where_clauses.append(("status", SQLOperator.EQ, status))
if user_id:
    where_clauses.append(("user_id", SQLOperator.EQ, user_id))

builder = ParameterizedQueryBuilder()
sql, params = builder.select(
    columns=["*"],
    table="table",
    where_clauses=where_clauses if where_clauses else None
)
```

### Pattern 3: INSERT with Multiple Columns

```python
# Before
columns = ["col1", "col2", "col3"]
values = [val1, val2, val3]
placeholders = ", ".join([f"${i+1}" for i in range(len(columns))])
sql = f"INSERT INTO table ({', '.join(columns)}) VALUES ({placeholders})"
params = values

# After
builder = ParameterizedQueryBuilder()
sql, params = builder.insert(
    table="table",
    columns={
        "col1": val1,
        "col2": val2,
        "col3": val3
    }
)
```

### Pattern 4: UPDATE with Dynamic Columns

```python
# Before
set_clauses = []
params = [id_value]
for key, value in updates.items():
    set_clauses.append(f"{key} = ${len(params) + 1}")
    params.append(value)
sql = f"UPDATE table SET {', '.join(set_clauses)} WHERE id = $1"

# After
builder = ParameterizedQueryBuilder()
sql, params = builder.update(
    table="table",
    columns=updates,
    where_clauses=[("id", SQLOperator.EQ, id_value)]
)
```

## Gotchas to Watch Out For

⚠️ **Always include WHERE clauses for UPDATE/DELETE!**

```python
# DON'T DO THIS (missing where_clauses)
builder.update(table="users", columns={"status": "inactive"})

# DO THIS (always specify WHERE)
builder.update(
    table="users",
    columns={"status": "inactive"},
    where_clauses=[("user_id", SQLOperator.EQ, user_id)]
)
```

⚠️ **For COUNT queries, use standard SQL if needed:**

```python
# If COUNT(*) with GROUP BY, may need manual SQL:
builder = ParameterizedQueryBuilder()
sql = """
    SELECT status, COUNT(*) as count
    FROM content_tasks
    WHERE created_at >= $1
    GROUP BY status
"""
params = [start_date]
# Execute: await conn.fetch(sql, *params)
```

⚠️ **Preserve serialization for special types:**

```python
# JSONB fields need json.dumps() first
updates = {
    "metadata": json.dumps(data),  # Serialize before passing
    "cost_breakdown": json.dumps(breakdown)
}

# Or use serialize_value_for_postgres helper:
from database_service import serialize_value_for_postgres
updates = {
    "metadata": serialize_value_for_postgres(data),
    "cost_breakdown": serialize_value_for_postgres(breakdown)
}
```

## Progress Tracking

As you complete each method, update this checklist:

- [x] get_task()
- [x] get_tasks_paginated()
- [x] get_tasks_by_date_range()
- [x] delete_task()
- [x] update_task_status()
- [x] update_task()
- [x] add_task()
- [ ] get_task_counts()
- [ ] get_queued_tasks()
- [ ] get_drafts()
- [ ] get_user_by_id()
- [ ] get_user_by_email()
- [ ] create_user()
- [ ] update_user()
- [ ] delete_user()
- [ ] get_post_by_slug()
- [ ] update_post()
- [ ] ... (30+ more methods)

## Files to Reference

- **Complete Pattern Examples:** [PHASE_1_REFACTORING_SESSION_SUMMARY.md](PHASE_1_REFACTORING_SESSION_SUMMARY.md)
- **ParameterizedQueryBuilder Tests:** [tests/test_sql_safety.py](src/cofounder_agent/tests/test_sql_safety.py)
- **SQL Safety Utility:** [utils/sql_safety.py](src/cofounder_agent/utils/sql_safety.py)
- **Current Target File:** [services/database_service.py](src/cofounder_agent/services/database_service.py)

## Support Commands

```bash
# Test after refactoring
npm run test:python -- tests/test_database_service.py -v

# Quick test of SQL safety
npm run test:python -- tests/test_sql_safety.py -v -k "test_inject"

# Find all methods in database_service.py
grep -n "async def " src/cofounder_agent/services/database_service.py

# Search for specific SQL patterns
grep -n "SELECT\|INSERT\|UPDATE\|DELETE" src/cofounder_agent/services/database_service.py
```

Good luck! You've got a solid foundation to continue. Just follow the patterns, test after each change, and the refactoring will go smoothly!
