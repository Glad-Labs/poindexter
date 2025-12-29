# Technical Debt Quick Reference

**Quick Links to All Issues**

---

## 🔴 CRITICAL - Fix First

| Issue                   | File                                                                       | Lines      | Impact                  | Effort |
| ----------------------- | -------------------------------------------------------------------------- | ---------- | ----------------------- | ------ |
| KPI Analytics Mock Data | [analytics_routes.py](src/cofounder_agent/routes/analytics_routes.py#L130) | 130-138    | Dashboard broken        | 3-4h   |
| Database Query Missing  | [database_service.py](src/cofounder_agent/services/database_service.py)    | ?          | Multiple endpoints fail | 2-3h   |
| Task Status Untracked   | [database_service.py](src/cofounder_agent/services/database_service.py)    | ?          | Cannot manage tasks     | 3-4h   |
| Cost Hardcoded          | [main.py](src/cofounder_agent/main.py#L1074)                               | 1074, 1086 | Inaccurate tracking     | 2-3h   |

---

## 🟠 HIGH PRIORITY

| Issue                  | File                                                                               | Lines   | Impact                  | Effort |
| ---------------------- | ---------------------------------------------------------------------------------- | ------- | ----------------------- | ------ |
| Orchestrator Endpoints | [orchestrator_routes.py](src/cofounder_agent/routes/orchestrator_routes.py#L235)   | 235-325 | 4 endpoints broken      | 13-17h |
| LLM Quality Evaluation | [quality_service.py](src/cofounder_agent/services/quality_service.py#L360)         | 360-390 | Hybrid eval not working | 4-5h   |
| Fine-Tuning Stub       | [fine_tuning_service.py](src/cofounder_agent/services/fine_tuning_service.py#L179) | 179     | Jobs never complete     | 6-8h   |

---

## 🟡 MEDIUM PRIORITY

| Issue                | File                                                                                                | Lines   | Impact                      | Effort |
| -------------------- | --------------------------------------------------------------------------------------------------- | ------- | --------------------------- | ------ |
| Settings Mock Data   | [settings_routes.py](src/cofounder_agent/routes/settings_routes.py)                                 | 127+    | 7 endpoints, no persistence | 6-8h   |
| Constraint Expansion | [constraint_utils.py](src/cofounder_agent/utils/constraint_utils.py#L410)                           | 410-437 | Ignored constraints         | 3-4h   |
| Email Publishing     | [email_publisher.py](src/cofounder_agent/services/email_publisher.py#L170)                          | 170     | Emails not sent             | 5-6h   |
| Image Optimization   | [image_service.py](src/cofounder_agent/services/image_service.py#L751)                              | 751     | Images not optimized        | 4-5h   |
| MUI Grid v1→v2       | [ConstraintComplianceDisplay.jsx](web/oversight-hub/src/components/ConstraintComplianceDisplay.jsx) | ?       | Deprecation warnings        | 2-3h   |

---

## 🟢 LOW PRIORITY / ACCEPTABLE

| Issue                | File                                                                                        | Lines | Status        |
| -------------------- | ------------------------------------------------------------------------------------------- | ----- | ------------- |
| Mock Auth (Dev Only) | [auth_unified.py](src/cofounder_agent/routes/auth_unified.py#L51)                           | 51-85 | OK for Tier 1 |
| Mock Dashboard Data  | [ExecutiveDashboard.jsx](web/oversight-hub/src/components/pages/ExecutiveDashboard.jsx#L54) | 54-64 | OK for dev    |
| Placeholder Images   | [image_fallback_handler.py](src/cofounder_agent/services/image_fallback_handler.py#L316)    | 316   | Fallback OK   |

---

## By Phase

### Phase 1 - CRITICAL PATH (1 week)

```
✓ Fix Analytics KPI
✓ Implement DB Queries
✓ Task Status Tracking
✓ Fix Cost Calculations
```

### Phase 2 - CORE FEATURES (2 weeks)

```
✓ Orchestrator Endpoints (4 items)
✓ LLM Quality Evaluation
✓ Settings Persistence
✓ Constraint Expansion
```

### Phase 3 - POLISH (1-2 weeks)

```
✓ Email Publishing
✓ Fine-Tuning Completion
✓ MUI Grid Migration
✓ Image Optimization
```

### Phase 4 - SAFETY (1 week)

```
✓ Production Guards
✓ Mock Data Cleanup
```

---

## File Index - Most Issues

| File                   | Count | Status       |
| ---------------------- | ----- | ------------ |
| orchestrator_routes.py | 4     | Not started  |
| analytics_routes.py    | 1     | **CRITICAL** |
| settings_routes.py     | 7     | Not started  |
| quality_service.py     | 2     | Not started  |
| constraint_utils.py    | 3     | Not started  |
| fine_tuning_service.py | 1     | Not started  |

---

## Start Here

1. Read [TECHNICAL_DEBT_EXECUTIVE_SUMMARY.md](TECHNICAL_DEBT_EXECUTIVE_SUMMARY.md) (5 min)
2. Read [CODEBASE_TECHNICAL_DEBT_AUDIT.md](CODEBASE_TECHNICAL_DEBT_AUDIT.md) (30 min)
3. Read [TECHNICAL_DEBT_IMPLEMENTATION_ROADMAP.md](TECHNICAL_DEBT_IMPLEMENTATION_ROADMAP.md) (20 min)
4. Pick Phase 1 item and implement

---

## Search for TODOs

```bash
# Find all TODOs in source code
grep -r "TODO" src/

# Find all placeholders
grep -r "placeholder\|mock\|stub" src/ | grep -i "^\s*#"

# Find mock data returns
grep -r "mock_" src/cofounder_agent/routes/
```

---

## Critical Path Timeline

**To deploy: Must complete Phase 1 (1 week)**

```
Mon: Analytics KPI + Database Queries
Wed: Task Status Tracking
Thu: Cost Calculations
Fri: Testing

✅ Result: Dashboard works
```

---

## Effort by Category

```
Mock Data Returns:        15-20h
Unimplemented Features:   15-20h
Database Issues:           5-7h
Placeholder Calculations:  5-8h
Service Stubs:            15-20h
Frontend Cleanup:          5-7h
Dev-Only Code:             1-2h
────────────────────────
TOTAL:                   61-84h
```

---

## Success Criteria

- [ ] Analytics endpoint returns real KPI data
- [ ] All database queries work
- [ ] Task status properly tracked
- [ ] Settings persist across restarts
- [ ] All 4 orchestrator endpoints functional
- [ ] LLM quality evaluation working
- [ ] No more mock data in production
- [ ] No deprecation warnings
- [ ] All TODO comments resolved

---

## Who Should Fix What

**Backend Engineer (Senior):**

- Phase 1: Critical path (10-14h) ← START HERE
- Phase 2: Orchestrator + Services (26-35h)
- Phase 4: Safety guards (2-4h)

**Frontend Engineer:**

- Phase 3: MUI Grid migration (2-3h)
- Phase 4: Mock data cleanup (1-2h)

**DevOps/DBA:**

- Database schema migrations
- Performance monitoring
- Production deployment verification

---

## Reference Links

| Document                                            | Purpose                       |
| --------------------------------------------------- | ----------------------------- |
| [Audit](CODEBASE_TECHNICAL_DEBT_AUDIT.md)           | Detailed findings (37 issues) |
| [Roadmap](TECHNICAL_DEBT_IMPLEMENTATION_ROADMAP.md) | Implementation steps          |
| [Summary](TECHNICAL_DEBT_EXECUTIVE_SUMMARY.md)      | High-level overview           |
| **This Document**                                   | Quick reference               |

---

_Updated: 2025-12-27_  
_Total Issues: 37_  
_Critical Issues: 6_  
_Estimated Effort: 61-84 hours_
