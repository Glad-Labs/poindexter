# Technical Debt Implementation Roadmap

**Created:** December 27, 2025  
**Purpose:** Prioritized action plan to resolve all technical debt identified in audit

---

## Phase 1: Critical Blockers (1-2 weeks) ⚠️ URGENT

These issues prevent core functionality from working properly.

### 1.1 Fix Analytics KPI Endpoint (Priority: 🔴 URGENT)

**Current State:**

- Dashboard tries to load KPI metrics but gets empty data
- Endpoint returns hardcoded zero values

**Work Items:**

- [ ] Define task query in DatabaseService using proper SQLAlchemy ORM
- [ ] Implement `get_tasks_by_date_range(start_date, end_date)` method
- [ ] Update analytics_routes.py to use real queries instead of mock data
- [ ] Test endpoint returns valid KPI metrics with sample data
- [ ] Update ExecutiveDashboard to handle metric updates

**Acceptance Criteria:**

- ✅ `GET /api/analytics/kpis?range=30d` returns HTTP 200 with real task metrics
- ✅ Dashboard displays actual task counts, success rates, costs
- ✅ Time-series data populates for charts

**Estimated Effort:** 3-4 hours

**Files:**

- [analytics_routes.py](src/cofounder_agent/routes/analytics_routes.py#L130-L138)
- [database_service.py](src/cofounder_agent/services/database_service.py)
- [ExecutiveDashboard.jsx](web/oversight-hub/src/components/pages/ExecutiveDashboard.jsx)

**Dependencies:** None - can be done immediately

---

### 1.2 Implement DatabaseService Query Methods

**Current State:**

- Code calls `db.query()` which doesn't exist
- Falls back to mock data in multiple endpoints

**Work Items:**

- [ ] Add `query(sql, params)` async method to DatabaseService
- [ ] Add `get_tasks_in_date_range()` helper
- [ ] Add `get_task_by_id()` helper
- [ ] Add `get_tasks_by_status()` helper
- [ ] Test all query methods with real data

**Acceptance Criteria:**

- ✅ All database queries use proper ORM/queries
- ✅ No more `.query()` calls on DatabaseService
- ✅ Queries return properly typed objects

**Estimated Effort:** 2-3 hours

**File:** [database_service.py](src/cofounder_agent/services/database_service.py)

**Dependencies:** None

---

### 1.3 Implement Task Status Persistence

**Current State:**

- Task status not tracked in database
- Cannot query tasks by status

**Work Items:**

- [ ] Define task status enum (created, queued, processing, completed, failed)
- [ ] Add `status` column to tasks table if not exists
- [ ] Implement `update_task_status(task_id, new_status)` method
- [ ] Add status lifecycle validation (prevent invalid transitions)
- [ ] Implement `get_tasks_by_status(status)` query

**Acceptance Criteria:**

- ✅ Tasks have proper status in database
- ✅ Status transitions are tracked with timestamps
- ✅ Can query tasks by status

**Estimated Effort:** 3-4 hours

**Files:**

- [database_service.py](src/cofounder_agent/services/database_service.py)
- [task_routes.py](src/cofounder_agent/routes/task_routes.py)
- Database migration

**Dependencies:** 1.2 (DatabaseService methods)

---

### 1.4 Fix Cost Calculation Placeholders

**Current State:**

- Blog post cost hardcoded to $0.03
- Image generation cost hardcoded to $0.02
- Inaccurate cost tracking

**Work Items:**

- [ ] Replace hardcoded values with model_router API calls
- [ ] Calculate based on actual token usage
- [ ] Store cost breakdown by phase in task metadata
- [ ] Update cost display in ExecutiveDashboard

**Acceptance Criteria:**

- ✅ Cost calculated based on actual model/tokens
- ✅ Cost breakdown stored in task metadata
- ✅ Dashboard shows accurate cost metrics

**Estimated Effort:** 2-3 hours

**File:** [main.py](src/cofounder_agent/main.py#L1074, #L1086)

**Dependencies:** model_router is already available

---

## Phase 2: Core Features (2-3 weeks)

These implement critical missing functionality.

### 2.1 Implement Orchestrator Endpoints

**Current State:**

- 4 orchestrator endpoints return empty/stub responses
- Cannot export training data, upload models, view patterns

**Work Items:**

#### 2.1.1 Training Data Export

- [ ] Query completed tasks from database
- [ ] Filter by quality_score threshold
- [ ] Format as JSONL (one task per line)
- [ ] Generate CSV option
- [ ] Create download endpoint
- [ ] Store exported files in /tmp or S3

**Endpoint:** `POST /api/orchestrator/training-data/export`

**Acceptance Criteria:**

- ✅ Returns export with actual task count
- ✅ Can filter by quality threshold
- ✅ Download URL is valid

**Estimated Effort:** 3-4 hours

#### 2.1.2 Model Upload Registration

- [ ] Accept uploaded model file
- [ ] Validate model format (safetensors, etc.)
- [ ] Store in model registry directory
- [ ] Register in database with metadata
- [ ] Make available to orchestrator

**Endpoint:** `POST /api/orchestrator/training-data/upload-model`

**Acceptance Criteria:**

- ✅ Model registered in database
- ✅ Model available for task creation
- ✅ Metadata stored correctly

**Estimated Effort:** 3-4 hours

#### 2.1.3 Learning Patterns Extraction

- [ ] Query execution history for patterns
- [ ] Calculate success rate by task type
- [ ] Identify common failure modes
- [ ] Extract optimal parameters for each task type

**Endpoint:** `GET /api/orchestrator/learning-patterns`

**Acceptance Criteria:**

- ✅ Returns actual success rates
- ✅ Patterns extracted from execution history
- ✅ Can use patterns for task optimization

**Estimated Effort:** 4-5 hours

#### 2.1.4 MCP Tool Discovery

- [ ] Scan MCP directory for available tools
- [ ] Extract tool metadata (name, description, parameters)
- [ ] Register tools in orchestrator
- [ ] Expose via API endpoint

**Endpoint:** `POST /api/orchestrator/mcp/discover`

**Acceptance Criteria:**

- ✅ Discovers all MCP tools
- ✅ Metadata properly extracted
- ✅ Tools available for task assignment

**Estimated Effort:** 3-4 hours

**Total for 2.1:** 13-17 hours

**File:** [orchestrator_routes.py](src/cofounder_agent/routes/orchestrator_routes.py)

**Dependencies:** 1.1, 1.2 (database queries)

---

### 2.2 Implement LLM-Based Quality Evaluation

**Current State:**

- Quality scoring uses only heuristics
- LLM-based and hybrid evaluation not implemented

**Work Items:**

- [ ] Create LLM prompt for quality evaluation
- [ ] Implement `_evaluate_llm_based()` method
- [ ] Parse LLM response to QualityAssessment
- [ ] Implement weighting (60% LLM, 40% pattern-based)
- [ ] Test hybrid evaluation on sample content

**Acceptance Criteria:**

- ✅ LLM evaluation returns structured scores
- ✅ Hybrid evaluation combines both methods
- ✅ Can switch between evaluation modes

**Estimated Effort:** 4-5 hours

**File:** [quality_service.py](src/cofounder_agent/services/quality_service.py#L360-L390)

**Dependencies:** model_router available

---

### 2.3 Implement Settings Persistence

**Current State:**

- All settings endpoints return mock data
- Settings not persisted across restarts

**Work Items:**

- [ ] Create settings database schema
- [ ] Implement CRUD operations in DatabaseService
- [ ] Wire up all settings endpoints to database
- [ ] Add settings cache layer for performance
- [ ] Test full lifecycle (create, read, update, delete)

**Settings to Persist:**

- API keys (OpenAI, Anthropic, Google, etc.)
- Model preferences
- Content preferences (tone, style, etc.)
- Constraint settings
- Cost limits

**Acceptance Criteria:**

- ✅ Settings stored and retrieved from database
- ✅ Settings persist across application restarts
- ✅ All endpoints use real database

**Estimated Effort:** 6-8 hours

**File:** [settings_routes.py](src/cofounder_agent/routes/settings_routes.py)

**Dependencies:** 1.2 (DatabaseService methods)

---

### 2.4 Implement Constraint Expansion with LLM

**Current State:**

- `expand_content_to_word_count()` returns unchanged content
- Word count constraints ignored

**Work Items:**

- [ ] Create expansion prompt template
- [ ] Call model_router with expansion prompt
- [ ] Parse expanded content
- [ ] Validate word count meets target
- [ ] Add retry logic if expansion insufficient

**Acceptance Criteria:**

- ✅ Content expanded to meet word count target
- ✅ Expansion maintains quality and relevance
- ✅ Handles edge cases (very short content, very long target)

**Estimated Effort:** 3-4 hours

**File:** [constraint_utils.py](src/cofounder_agent/utils/constraint_utils.py#L410-L437)

**Dependencies:** model_router available

---

## Phase 3: Polish & Completeness (2-3 weeks)

### 3.1 Implement Email/Newsletter Publishing

**Current State:**

- Email publisher returns placeholder responses
- Newsletters not actually sent

**Work Items:**

- [ ] Choose newsletter service (ConvertKit, SendGrid, etc.) or implement custom
- [ ] Integrate with chosen service
- [ ] Implement subscriber list management
- [ ] Add email template support
- [ ] Implement batch sending with rate limiting
- [ ] Add delivery tracking

**Acceptance Criteria:**

- ✅ Emails actually sent to subscribers
- ✅ Can track delivery status
- ✅ Supports multiple newsletter lists

**Estimated Effort:** 5-6 hours

**File:** [email_publisher.py](src/cofounder_agent/services/email_publisher.py#L160-L175)

**Dependencies:** None (independent feature)

---

### 3.2 Implement Fine-Tuning Job Completion Tracking

**Current State:**

- Fine-tuning jobs created but never complete
- Cannot monitor progress or get results

**Work Items:**

- [ ] Implement Google Gemini fine-tuning API calls
- [ ] Store operation object for monitoring
- [ ] Implement job status polling
- [ ] Handle completion and error callbacks
- [ ] Store completed model reference in database

**Acceptance Criteria:**

- ✅ Fine-tuning jobs actually complete
- ✅ Can monitor job progress
- ✅ Completed models available for use

**Estimated Effort:** 6-8 hours

**File:** [fine_tuning_service.py](src/cofounder_agent/services/fine_tuning_service.py#L170-L195)

**Dependencies:** Google Gemini API access

---

### 3.3 Complete MUI Grid v1→v2 Migration

**Current State:**

- ModelSelectionPanel.jsx fixed (12/27)
- Other components still use deprecated v1 props

**Work Items:**

- [ ] Migrate ConstraintComplianceDisplay.jsx Grid components
- [ ] Migrate BlogPostCreator.jsx Grid components
- [ ] Test layout looks correct after migration
- [ ] Remove v1 import if still present

**Acceptance Criteria:**

- ✅ No MUI Grid deprecation warnings in console
- ✅ All layouts render correctly
- ✅ No visual regressions

**Estimated Effort:** 2-3 hours

**Files:**

- [ConstraintComplianceDisplay.jsx](web/oversight-hub/src/components/ConstraintComplianceDisplay.jsx)
- [BlogPostCreator.jsx](web/oversight-hub/src/components/tasks/BlogPostCreator.jsx)

**Dependencies:** None

---

### 3.4 Implement Image Optimization

**Current State:**

- Images not optimized for web
- Using via.placeholder.com for fallbacks

**Work Items:**

- [ ] Set up image optimization library (Sharp or Pillow)
- [ ] Implement WebP conversion
- [ ] Add compression with quality presets
- [ ] Cache optimized images
- [ ] Update image service to use optimization

**Acceptance Criteria:**

- ✅ Images optimized for web
- ✅ File sizes reduced by 50%+
- ✅ WebP versions available

**Estimated Effort:** 4-5 hours

**File:** [image_service.py](src/cofounder_agent/services/image_service.py#L751-L757)

**Dependencies:** None

---

## Phase 4: Safety & Optimization (1-2 weeks)

### 4.1 Add Production Safety Guards

**Current State:**

- Mock authentication works in any environment
- Could accidentally deploy with mock auth enabled

**Work Items:**

- [ ] Add environment check for mock auth (dev only)
- [ ] Add warning if mock auth detected in production
- [ ] Add feature flag to disable mock auth
- [ ] Document development vs production auth

**Acceptance Criteria:**

- ✅ Mock auth blocked in production
- ✅ Clear warnings if accidentally enabled
- ✅ Easy to disable for production builds

**Estimated Effort:** 1-2 hours

**File:** [auth_unified.py](src/cofounder_agent/routes/auth_unified.py#L51-L85)

**Dependencies:** None

---

### 4.2 Clean Up Frontend Mock Data

**Current State:**

- Frontend still loads mock dashboard data on mount
- Should use real API in production

**Work Items:**

- [ ] Remove `getMockDashboardData()` function
- [ ] Rely entirely on API calls
- [ ] Add loading states while data fetches
- [ ] Add error handling with retry

**Acceptance Criteria:**

- ✅ Dashboard always loads real data from API
- ✅ No hardcoded mock data in production
- ✅ Loading states properly displayed

**Estimated Effort:** 1-2 hours

**File:** [ExecutiveDashboard.jsx](web/oversight-hub/src/components/pages/ExecutiveDashboard.jsx#L54-L64)

**Dependencies:** 1.1 (working API)

---

## Implementation Timeline

### Week 1: Phase 1 (Critical Blockers)

- Days 1-2: Fix analytics KPI endpoint & DatabaseService.query()
- Days 3-4: Implement task status persistence
- Days 5: Fix cost calculations & testing
- **Blocker Removed:** Dashboard now shows real metrics ✅

### Week 2: Phase 2 Part 1 (Orchestrator)

- Days 1-2: Training data export
- Days 2-3: Model upload registration
- Days 4: Learning patterns extraction
- Days 5: MCP tool discovery

### Week 3: Phase 2 Part 2 (Services)

- Days 1-2: LLM-based quality evaluation
- Days 2-3: Settings persistence
- Days 4-5: Constraint expansion with LLM

### Week 4: Phase 3 (Polish)

- Days 1-2: Email/newsletter integration
- Days 2-3: Fine-tuning completion tracking
- Days 4: MUI Grid migration
- Days 5: Image optimization

### Week 5: Phase 4 (Safety & Cleanup)

- Days 1-2: Production safety guards
- Days 3-4: Frontend mock data cleanup
- Days 5: Testing and verification

---

## Effort Summary

| Phase                      | Hours     | Duration      |
| -------------------------- | --------- | ------------- |
| Phase 1: Critical Blockers | 10-14     | 1 week        |
| Phase 2: Core Features     | 26-35     | 2 weeks       |
| Phase 3: Polish            | 17-22     | 1.5 weeks     |
| Phase 4: Safety            | 2-4       | 1 week        |
| **Total**                  | **55-75** | **5-6 weeks** |

---

## Risk Mitigation

### Database Migration Risks

- [ ] Always test migrations on staging first
- [ ] Keep rollback scripts ready
- [ ] Run migrations during low-traffic periods

### API Breaking Changes

- [ ] Use API versioning (v1, v2)
- [ ] Document breaking changes
- [ ] Provide migration guide for clients

### Performance Risks

- [ ] Monitor query performance in analytics_routes.py
- [ ] Add database indexes for frequently queried fields
- [ ] Implement query result caching

---

## Success Criteria

At end of Phase 1:

- ✅ Analytics KPI endpoint returns real data
- ✅ Dashboard displays metrics
- ✅ No more `db.query()` errors

At end of Phase 2:

- ✅ All orchestrator endpoints functional
- ✅ LLM-based evaluation working
- ✅ Settings persisted across restarts

At end of Phase 3:

- ✅ Emails actually being sent
- ✅ Fine-tuning jobs complete
- ✅ No MUI deprecation warnings

At end of Phase 4:

- ✅ Production-safe authentication
- ✅ No hardcoded mock data
- ✅ All TODOs resolved

---

## Notes

- **Parallel Work:** Phases 2.1-2.4 can be worked on in parallel
- **Testing:** Each phase should include unit tests + integration tests
- **Documentation:** Update API docs and implementation guides as features complete
- **Monitoring:** Set up alerts for failed analytics queries to catch regressions

---

_Roadmap Version: 1.0_  
_Last Updated: 2025-12-27_
