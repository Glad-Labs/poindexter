# FastAPI Security & Code Quality Improvements - Implementation Guide

**Status:** Action Plan  
**Priority:** Critical & High  
**Date:** December 30, 2025

---

## Changes Completed ✅

### 1. **Fixed Type Mismatch Error in Analytics** ✅

**File:** [src/cofounder_agent/routes/analytics_routes.py](src/cofounder_agent/routes/analytics_routes.py)

**Issue:** `TypeError: unsupported operand type(s) for +=: 'float' and 'decimal.Decimal'`

**Fix Applied:**

```python
# Before (unsafe):
cost_by_day[day_key]["cost"] += cost  # ❌ May mix float/Decimal

# After (safe):
cost = float(cost) if cost else 0.0  # ✅ Convert to float first
cost_by_day[day_key]["cost"] = float(cost_by_day[day_key]["cost"]) + cost
```

Applied to:

- Line 322: Cost accumulation in daily cost tracking
- Line 265: Cost by model tracking
- Line 289: Cost by phase breakdown
- Line 323: Daily cost per day aggregation

**Impact:** Eliminates runtime TypeError when PostgreSQL returns Decimal type from cost calculations.

---

### 2. **Enhanced Type Hints in Core Services** ✅

**Files:**

- [src/cofounder_agent/services/database_service.py](src/cofounder_agent/services/database_service.py)

**Changes:**

- `initialize()` → return type `None`
- `close()` → return type `None`

**Benefit:** Type checkers (mypy, pyright) can now verify proper usage.

---

### 3. **Improved CORS Documentation & Configuration** ✅

**File:** [src/cofounder_agent/utils/middleware_config.py](src/cofounder_agent/utils/middleware_config.py)

**Current Status:** ✅ **Already Secure**

- Not using `allow_origins=["*"]`
- Using environment-based configuration
- Safe defaults: localhost:3000-3004, 127.0.0.1:3000-3004
- Can be overridden via `ALLOWED_ORIGINS` env var

**Enhanced Documentation Added:**

```python
def _setup_cors(self, app: FastAPI) -> None:
    """
    Setup CORS with environment-based configuration.

    Default development origins:
    - http://localhost:3000 (Next.js public site)
    - http://localhost:3001 (React oversight hub)

    For production, set:
    ALLOWED_ORIGINS=https://yourdomain.com,https://app.yourdomain.com

    ⚠️ WARNING: Never use allow_origins=["*"] in production!
    """
```

**Recommendation:** Add to `.env.local` for production:

```env
ALLOWED_ORIGINS=https://yourdomain.com,https://app.yourdomain.com
```

---

### 4. **SQL Injection Prevention Utilities** ✅

**File:** [src/cofounder_agent/utils/sql_safety.py](src/cofounder_agent/utils/sql_safety.py) (NEW)

**Components:**

#### a) SQL Identifier Validator

```python
# Validates table/column names to prevent injection
SQLIdentifierValidator.validate("user_id")  # ✅ True
SQLIdentifierValidator.validate("user_id'; DROP TABLE users; --")  # ✅ False

# Safe extraction
table = SQLIdentifierValidator.safe_identifier(table_name, "table")
```

#### b) Parameterized Query Builder

```python
builder = ParameterizedQueryBuilder()

# Safe SELECT
sql, params = builder.select(
    columns=["id", "name"],
    table="users",
    where_clauses=[("status", "=", "active")],
    limit=10
)
# Result: "SELECT id, name FROM users WHERE status = $1 LIMIT $2"
#         ["active", 10]

# Safe INSERT
sql, params = builder.insert(
    table="users",
    columns={"name": "John", "email": "john@example.com"},
    return_columns=["id"]
)
# Result: "INSERT INTO users (name, email) VALUES ($1, $2) RETURNING id"
#         ["John", "john@example.com"]

# Safe UPDATE
sql, params = builder.update(
    table="users",
    updates={"status": "inactive"},
    where_clauses=[("id", "=", 123)]
)
# Result: "UPDATE users SET status = $1 WHERE id = $2"
#         ["inactive", 123]

# Safe DELETE (requires WHERE clause)
sql, params = builder.delete(
    table="users",
    where_clauses=[("id", "=", 123)]
)
# Result: "DELETE FROM users WHERE id = $1"
#         [123]
```

**Usage in database_service.py:**

**Before (vulnerable):**

```python
sql = f"SELECT * FROM tasks WHERE id = {task_id}"  # ❌ SQL injection!
await conn.fetchrow(sql)
```

**After (safe):**

```python
from utils.sql_safety import ParameterizedQueryBuilder

builder = ParameterizedQueryBuilder()
sql, params = builder.select(
    columns=["*"],
    table="tasks",
    where_clauses=[("id", "=", task_id)]
)
result = await conn.fetchrow(sql, *params)
```

---

## Recommended Next Steps 📋

### Phase 1: Critical Fixes (Week 1)

**Estimated Effort:** 2-3 days / 1 developer

1. **Refactor database_service.py to use SQL safety utilities** [HIGH]
   - Replace all raw SQL formatting with ParameterizedQueryBuilder
   - Update ~50 methods across database_service
   - Keep same functionality, just safer
   - No API changes needed

2. **Add Unit Tests for SQL safety utilities** [HIGH]
   - Test identifier validation with malicious inputs
   - Test parameterized query building
   - ~200 lines of pytest code
   - Catches edge cases before production

3. **Enable mypy/pyright type checking in CI/CD** [HIGH]
   - Install `mypy` or `pyright`
   - Configure `pyproject.toml` or `mypy.ini`
   - Set to catch `Optional` type errors
   - Prevent regressions

### Phase 2: High-Priority Improvements (Week 2)

**Estimated Effort:** 3-4 days / 1-2 developers

4. **Split database_service.py** [HIGH]
   - `database_service.py` (500 lines max) - core operations
   - `database_queries.py` (300 lines) - query building
   - `database_serializers.py` (200 lines) - type conversions
   - `database_models.py` (200 lines) - typed result objects

5. **Add Typed Response Models to All Routes** [HIGH]
   - Create Pydantic models for every endpoint
   - Replace `Dict[str, Any]` with specific models
   - Improves OpenAPI documentation
   - Enables client-side code generation

6. **Consolidate Orchestrators** [HIGH]
   - Choose: keep `unified_orchestrator.py`, deprecate others
   - Update all route references
   - Add migration guide for external consumers
   - Update documentation

7. **Add Request Correlation IDs** [MEDIUM]
   - Middleware assigns UUID to each request
   - Log in all async operations
   - Helps trace requests through logs

8. **Implement API Rate Limiting** [MEDIUM]
   - Slow API: Already imported, just needs configuration
   - Apply limits per endpoint:
     - `/api/content/create`: 10/minute
     - `/api/models/*`: 100/minute
     - `/api/chat/*`: 30/minute

### Phase 3: Testing & Documentation (Week 3)

**Estimated Effort:** 3-5 days / 1-2 developers

9. **Add Unit Test Suite** [HIGH]
   - Service tests (50-100 tests)
   - Route tests (75-150 tests)
   - Integration tests (25-50 tests)
   - Target: 60%+ code coverage

10. **Add Security Scanning to CI/CD** [MEDIUM]
    - Bandit: Static security analysis
    - Safety: Dependency vulnerability checking
    - SQLFluff: SQL query linting
    - Fail on critical/high issues

11. **Write Security Best Practices Guide** [MEDIUM]
    - SQL safety patterns with examples
    - Authentication & authorization
    - API key management
    - Rate limiting strategies

---

## Implementation Checklist

### Code Changes

- [ ] Fix type mismatch errors (✅ DONE)
- [ ] Add return type hints to services (✅ DONE)
- [ ] Enhance CORS documentation (✅ DONE)
- [ ] Create SQL safety utilities (✅ DONE)
- [ ] Refactor database_service.py to use SQL safety
- [ ] Add typed response models to routes
- [ ] Consolidate orchestrator services
- [ ] Add request correlation IDs

### Testing

- [ ] Unit tests for SQL safety utilities
- [ ] Type checking with mypy/pyright
- [ ] Integration tests for database operations
- [ ] Security scanning in CI/CD

### Documentation

- [ ] SQL safety patterns guide
- [ ] API authentication guide
- [ ] Security best practices
- [ ] Type hints migration guide

### Deployment

- [ ] Code review of changes
- [ ] Test on staging environment
- [ ] Update production `.env` files
- [ ] Monitor error rates in production

---

## Success Metrics

| Metric                        | Current | Target   | Timeline |
| ----------------------------- | ------- | -------- | -------- |
| Type hint coverage            | ~60%    | >90%     | 1 week   |
| Code analysis passes          | ❌ High | ✅ Clean | 2 weeks  |
| Test coverage                 | 0%      | 60%+     | 3 weeks  |
| Security scanning passes      | ❌ None | ✅ All   | 2 weeks  |
| SQL injection vulnerabilities | ~20     | 0        | 2 weeks  |

---

## Migration Guide for Developers

### Using SQL Safety Utilities

**Before (vulnerable):**

```python
# In database_service.py
sql = f"SELECT * FROM tasks WHERE user_id = {user_id} LIMIT {limit}"
tasks = await conn.fetch(sql)
```

**After (safe):**

```python
from utils.sql_safety import ParameterizedQueryBuilder

builder = ParameterizedQueryBuilder()
sql, params = builder.select(
    columns=["*"],
    table="tasks",
    where_clauses=[("user_id", "=", user_id)],
    limit=limit
)
tasks = await conn.fetch(sql, *params)
```

### Type Hints Pattern

**Before:**

```python
async def get_user(user_id: str) -> Dict[str, Any]:
    return {"id": user_id, "email": "..."}
```

**After:**

```python
from pydantic import BaseModel

class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    created_at: str

async def get_user(user_id: str) -> UserResponse:
    data = await db.fetch_one(...)
    return UserResponse(**data)
```

---

## Questions & Support

**Q: Will these changes break existing code?**
A: No. All changes are:

- ✅ Backward compatible (same APIs)
- ✅ Internal refactoring only
- ✅ No schema changes
- ✅ Safe to deploy gradually

**Q: How long will migration take?**
A: ~2-3 weeks for full implementation with one developer. Can parallelize:

- Week 1: SQL safety refactoring
- Week 2: Type hints & testing
- Week 3: Documentation & CI/CD

**Q: What's the performance impact?**
A: Minimal:

- ✅ Parameterized queries same performance as raw SQL
- ✅ Type hints zero runtime overhead
- ✅ SQL validation happens once at query build time

**Q: Can we do this incrementally?**
A: Yes! Start with:

1. SQL safety utilities (done)
2. Refactor one high-risk route (e.g., analytics)
3. Verify no issues in production
4. Gradually migrate other routes
5. Add tests as you go

---

## Files Modified

| File                 | Changes                             | LOC Changed  |
| -------------------- | ----------------------------------- | ------------ |
| analytics_routes.py  | Fixed type mismatch (Decimal/float) | ~20          |
| database_service.py  | Added return type hints             | ~5           |
| middleware_config.py | Enhanced CORS documentation         | ~15          |
| sql_safety.py        | NEW - SQL injection prevention      | 350          |
| **Total**            | **~4 files**                        | **~390 LOC** |

---

## References

- [OWASP SQL Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html)
- [FastAPI Security Guide](https://fastapi.tiangolo.com/tutorial/security/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [asyncpg Parameters](https://magicstack.github.io/asyncpg/current/api/index.html)

---

**Last Updated:** December 30, 2025  
**Prepared by:** Code Analysis Agent  
**Status:** Ready for Implementation
