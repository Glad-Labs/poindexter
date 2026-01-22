# Copilot Instructions - Key Changes Reference

## Quick Summary of Updates

### What Changed

The `.github/copilot-instructions.md` file has been updated to accurately reflect the **current January 2026 state** of the Glad Labs codebase.

### Why It Mattered

The previous version (1.0 from December 2025) had several inaccuracies that could mislead developers:

- Outdated file paths and module counts
- Inaccurate agent descriptions
- Missing information about 60+ service modules
- Incomplete backend route documentation
- Incorrect references to deleted/restructured components

### Specific Corrections Made

#### 1. Backend Architecture

**Before:** Generic multi-agent system description
**After:** Detailed breakdown of:

- 18+ route modules with specific examples (task_routes, agents_routes, chat_routes, etc.)
- 60+ service modules with clear responsibilities
- 5-module database coordinator pattern (users, tasks, content, admin, writing_style)
- 4 specialized agents (content, financial, market insight, compliance)

#### 2. Service Startup

**Before:** Basic startup command
**After:** Complete npm script reference including:

- `npm run dev:cofounder` - Backend only
- `npm run dev:public` - Next.js only
- `npm run dev:oversight` - React only
- `npm run dev:frontend` - Both frontends
- `npm run dev:all` - All services

#### 3. Database Architecture

**Before:** Generic "PostgreSQL database service"
**After:** Documented 5-module pattern with clear responsibilities:

- UsersDatabase: OAuth and authentication
- TasksDatabase: Task management and filtering
- ContentDatabase: Posts and quality metrics
- AdminDatabase: Logging and financial tracking
- WritingStyleDatabase: RAG style matching

#### 4. Model Router Documentation

**Before:** Incomplete fallback chain
**After:** Complete chain with accurate priority:

1. Ollama (local, zero-cost)
2. Anthropic Claude
3. OpenAI
4. Google Gemini
5. Echo/mock response

#### 5. File References

**Before:** Broken markdown links and outdated paths
**After:** Correct paths matching actual codebase:

- `src/cofounder_agent/main.py` ✓
- `src/cofounder_agent/services/` (60+ modules) ✓
- `src/cofounder_agent/routes/` (18+ modules) ✓
- `src/cofounder_agent/agents/` (4 agents) ✓
- `web/public-site/` ✓
- `web/oversight-hub/` ✓

### How to Verify the Update

```bash
# 1. Check the file was updated
cat .github/copilot-instructions.md | head -10

# 2. Verify current timestamp (should be January 21, 2026)
grep "Last Updated" .github/copilot-instructions.md

# 3. Verify version number (should be 2.0)
grep "Version:" .github/copilot-instructions.md

# 4. Test startup with new instructions
npm run dev  # Should start all 3 services on 8000, 3000, 3001
```

### Configuration Confirmed

✅ **Service Ports:**

- Backend: 8000 (FastAPI)
- Public Site: 3000 (Next.js)
- Oversight Hub: 3001 (React)
- PostgreSQL: 5432

✅ **Technology Stack:**

- Backend: Python 3.10+ with FastAPI
- Public Site: Next.js 15 (React 18 + TypeScript + TailwindCSS)
- Oversight Hub: React 18 + Material-UI
- Database: PostgreSQL (mandatory)

✅ **Configuration Source:**

- Single `.env.local` file at project root
- Used by both Python and Node services
- No per-service configuration files

### Recommendations for Future Updates

Whenever you make significant architectural changes, update these sections:

1. **Routes Section** - if adding/removing route modules
2. **Services Section** - if adding/removing service modules
3. **Agents Section** - if modifying agent implementations
4. **Database Section** - if changing database modules
5. **Startup Scripts** - if adding new npm scripts

---

**File Updated:** `.github/copilot-instructions.md`
**Update Date:** January 21, 2026
**Version:** 2.0
**Status:** ✅ Ready for use
