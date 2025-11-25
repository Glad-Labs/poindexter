# 🚀 PHASE 2 CLEANUP ACTION PLAN - Session Start

**Session Date:** November 2025  
**Phase:** 2 - Cleanup & Consolidation  
**Status:** ⏱️ IN PROGRESS | 15% Complete  
**Estimated Time to Complete:** 14-16 hours

---

## 🎯 PHASE 2 OBJECTIVES

Remove dead code, unused imports, and stub implementations to achieve:

- Single source of truth for all routes and models
- <2% dead code in codebase
- Zero unused imports
- Clear, maintainable architecture
- OAuth-only auth model clarity

---

## ✅ COMPLETED THIS SESSION

### Part 1: Duplicate Import Fix (COMPLETE)

- **File:** `src/cofounder_agent/main.py`
- **Change:** Removed duplicate `from routes.auth_routes import router as auth_router`
- **Status:** ✅ Done | No test impact

### Part 2: Auth Architecture Consolidation (COMPLETE)

- **File:** `src/cofounder_agent/routes/auth_routes.py`
- **Changes:**
  - ✅ Updated docstring to reflect OAuth-only architecture
  - ✅ Removed 116 lines of stub implementations
  - ✅ Removed endpoints: /login, /register, /refresh, /change-password, /setup-2fa, /verify-2fa, /disable-2fa
  - ✅ Kept Pydantic models for reference (can be removed next phase)
- **Impact:** Clear architectural intent, -116 LOC

---

## 📋 PRIORITY NEXT STEPS

### PRIORITY 1: Identify Duplicate Models & Content Routes (2 hours)

**Execute these commands to find all references:**

```bash
# Find all imports of OLD content files
grep -r "from routes.content import\|from routes.content_generation import\|from routes.enhanced_content import" src/

# Find all imports of old auth files
grep -r "from routes.auth_routes import\|import auth_routes" src/

# List old files still in codebase
find src/ -name "content.py" -o -name "content_generation.py" -o -name "enhanced_content.py"

# Find all Pydantic models to consolidate
grep -r "class.*Request(BaseModel)\|class.*Response(BaseModel)" src/routes/ | wc -l
```

**Expected Output:**

- Should find 0 imports from old files (all should use content_routes)
- Should find old files still existing (mark for deletion)
- Should count 50+ Pydantic models to consolidate

---

### PRIORITY 2: Consolidate Database Models (2.5 hours)

**Check for duplicates:**

```bash
# Find all table/model definitions
grep -r "class.*\(Base\|Model\):" src/ --include="*.py" | grep -v "BaseAgent\|BaseModel"

# Look for duplicate table definitions
grep -r "class Post\|class Task\|class Memory" src/ --include="*.py"
```

**Actions:**

1. Review `src/cofounder_agent/database.py` for complete model list
2. Check `content_publisher.py` for duplicate BlogPost class
3. Check `services/` for duplicate models
4. Consolidate to single source (database.py)

---

### PRIORITY 3: Remove Unused Imports (1.5 hours)

```bash
# Scan for unused imports in all Python files
find src/ -name "*.py" -type f | while read f; do
  echo "=== $f ==="
  grep -n "^import\|^from" "$f" | head -20
done

# Check for circular imports
python -m py_compile src/cofounder_agent/*.py src/cofounder_agent/routes/*.py

# Use pylint to find unused imports
python -m pylint --disable=all --enable=W0611 src/cofounder_agent/
```

**Expected Issues:**

- Imports from deleted files (content.py, content_generation.py, etc.)
- Unused Pydantic model imports in auth_routes.py
- Unused service imports

---

### PRIORITY 4: Delete Old Route Files (30 minutes)

After confirming NO imports from these files, delete:

```bash
# DO NOT DELETE until confirming zero imports (see PRIORITY 1)
rm -f src/cofounder_agent/routes/content.py
rm -f src/cofounder_agent/routes/content_generation.py
rm -f src/cofounder_agent/routes/enhanced_content.py
```

**Before deleting - run this to confirm:**

```bash
grep -r "routes\.content\|routes\.content_generation\|routes\.enhanced_content" src/
# Should return: (no results)
```

---

### PRIORITY 5: Run Test Suite (1 hour)

```bash
# Run pytest to ensure nothing broke
cd src/cofounder_agent
python -m pytest tests/ -v --tb=short

# Expected: All tests still pass
# If any fail: Revert the import change that caused it
```

---

## 📊 ROADMAP FOR REMAINING PHASES

### Phase 2 Continuation (14 hours total)

- [x] Part 1: Duplicate import fix (0.5 hrs)
- [x] Part 2: Auth architecture cleanup (1 hr)
- [ ] Part 3: Priority 1-5 above (7 hours)
- [ ] Part 4: Final validation & documentation (5.5 hours)

### Phase 3 (Planned)

- Remove unused Pydantic models
- Consolidate test fixtures
- Clean up configuration files
- Remove stub implementations throughout codebase

### Phase 4 (Planned)

- Performance optimization
- Code documentation
- Architecture review
- Production readiness checks

---

## 🔍 FILES TO MONITOR

### High Priority (Process these first)

```
src/cofounder_agent/
├── main.py                          # ✅ Already cleaned
├── routes/
│   ├── auth_routes.py               # ✅ Already cleaned
│   ├── auth_unified.py              # ⏱️ Next: verify no duplicates
│   ├── content_routes.py            # ⏱️ Next: verify is primary
│   ├── content.py                   # ❌ DELETE AFTER PRIORITY 1
│   ├── content_generation.py        # ❌ DELETE AFTER PRIORITY 1
│   └── enhanced_content.py          # ❌ DELETE AFTER PRIORITY 1
├── database.py                      # ⏱️ Check for duplicates
├── services/
│   ├── content_publisher.py         # ⏱️ Remove BlogPost class duplicate
│   └── database_service.py          # ⏱️ Check for duplicates
```

---

## ✅ VALIDATION CHECKLIST

Before considering Phase 2 COMPLETE:

- [ ] **Imports:** Zero imports from deleted files
- [ ] **Models:** Single source of truth for all data models
- [ ] **Routes:** All requests go through unified routes (content_routes, auth_unified)
- [ ] **Tests:** All pytest tests pass
- [ ] **Linting:** `pylint --disable=all --enable=W0611` finds zero unused imports
- [ ] **Performance:** No startup latency increase
- [ ] **Documentation:** All changes documented in commits

---

## 🎯 SUCCESS CRITERIA

When Phase 2 is DONE:

| Metric           | Target | Status |
| ---------------- | ------ | ------ |
| Dead Code %      | <2%    | 🟡 TBD |
| Unused Imports   | 0      | 🟡 TBD |
| Duplicate Models | 0      | 🟡 TBD |
| Test Pass Rate   | 100%   | 🟡 TBD |
| Code LOC         | <4,200 | 🟡 TBD |
| Duplicate Files  | 0      | 🟡 TBD |

---

## 📝 HOW TO CONTINUE

### Next Session (When Ready)

1. **Clone or open** the workspace
2. **Run Priority 1 commands** above to identify duplicates
3. **Systematically work through** Priorities 1-5 in order
4. **After each priority:** Run `pytest` to verify nothing broke
5. **Document all changes** with clear commit messages
6. **Update this document** as you progress

### Tips for Success

- ✅ Use grep extensively before deleting anything
- ✅ Test after each major change (every 30 minutes)
- ✅ Keep old files in archive/ folder before deleting
- ✅ Update imports incrementally, not all at once
- ✅ Ask questions if a file/import purpose is unclear

---

## 📞 DECISION POINTS

**During cleanup, you may encounter:**

1. **Pydantic model in multiple files** → Keep in shared schema/models.py
2. **Old import in test file** → Update to new import
3. **Duplicate service method** → Keep most recent version
4. **Unclear if code is used** → Grep for usage across entire codebase

---

## 🚀 READY TO START?

When you're ready to begin Phase 2 cleanup:

1. Open this file: `PHASE_2_CLEANUP_ACTION_PLAN.md`
2. Follow the PRIORITY sections in order
3. Execute the grep commands to find duplicates
4. Update this file with your progress
5. Mark items as DONE when complete

**Estimated Time:** 14-16 focused hours  
**Current Progress:** 15% (1.5 hrs done)  
**Remaining:** 12-14.5 hours

---

**Last Updated:** November 2025  
**Next Review:** After completing Priorities 1-3
