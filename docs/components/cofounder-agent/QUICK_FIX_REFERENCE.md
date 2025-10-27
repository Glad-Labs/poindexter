# Quick Reference: Co-founder Agent Deployment Fix

## Issue

```
ERROR: type object 'DatabaseService' has no attribute 'connect'
WARNING: orm_mode deprecated in Pydantic V2
```

## Three Fixes Applied

### Fix 1: main.py (Line 91)

```python
# ❌ WRONG
database_service = await DatabaseService.connect()

# ✅ FIXED
database_service = DatabaseService()
await database_service.initialize()
```

### Fix 2: main.py (Line 102)

```python
# ❌ WRONG
await database_service.create_tables()

# ✅ FIXED
# Removed (already done in initialize())
```

### Fix 3: task_routes.py (Lines 74, 85)

```python
# ❌ WRONG
class Config:
    orm_mode = True

# ✅ FIXED
class Config:
    from_attributes = True
```

## Verification

```bash
git status src/cofounder_agent/
# Should show 2 files modified:
#   - src/cofounder_agent/main.py
#   - src/cofounder_agent/routes/task_routes.py
```

## Deploy

```bash
docker build -t glad-labs-agent:latest .
docker run -p 8000:8000 glad-labs-agent:latest
```

## Expected Output

```
✅ Application startup complete
✅ Uvicorn running on http://0.0.0.0:8080
```

✅ **ALL FIXED - READY FOR DEPLOYMENT**
