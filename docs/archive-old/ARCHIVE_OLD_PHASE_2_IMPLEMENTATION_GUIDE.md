# Phase 2 Implementation Guide - Quick Cleanup Steps

**Duration**: 30 minutes total  
**Risk Level**: LOW (no breaking changes)  
**Rollback**: Simple git revert if needed

---

## Step 1: Delete Dead Code (5 minutes)

### Action: Remove FeaturedImageService class

**File**: `src/cofounder_agent/services/content_router_service.py`

**Locate the class** (Line 309):

```python
class FeaturedImageService:
    """Service for featured image generation and search"""

    def __init__(self):
        """Initialize Pexels client"""
        self.pexels = PexelsClient()

    async def search_featured_image(
        self, topic: str, keywords: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Search for featured image via Pexels (free, no cost)

        Args:
            topic: Blog post topic
            keywords: Optional search keywords

        Returns:
            Image dict with url and metadata, or None if not found
        """
        try:
            # Use async method from Pexels client
            image = await self.pexels.get_featured_image(
                topic=topic,
                keywords=keywords
            )

            if image:
                logger.info(f"✅ Found featured image from Pexels: {image.get('photographer')}")
                return image
            else:
                logger.warning(f"⚠️  No Pexels image found for: {topic}")
                return None

        except Exception as e:
            logger.error(f"❌ Error searching for featured image: {e}")
            return None
```

**Delete**: Lines 309-342 (34 lines total)

### Verification Command

```bash
# After deletion, run this to confirm it's gone:
grep -n "class FeaturedImageService" src/cofounder_agent/services/content_router_service.py

# Expected output: (no results)
```

### Testing

```bash
# Test imports still work:
python -m py_compile src/cofounder_agent/services/content_router_service.py

# Should return: (no error)
```

---

## Step 2: Check for Orphaned Imports (5 minutes)

### Action: Verify content_router_service.py still has everything it needs

After deleting FeaturedImageService, check if `PexelsClient` is still imported:

**In content_router_service.py, find the imports section**:

```python
from .pexels_client import PexelsClient
```

**Question**: Is PexelsClient used anywhere else in the file?

**Search**:

```bash
grep -n "PexelsClient" src/cofounder_agent/services/content_router_service.py
```

**Expected Result**:

- If line 309 was the only match: SAFE TO REMOVE the `from .pexels_client import PexelsClient` import
- If other matches exist: KEEP the import

### If Safe to Remove Import

Find the import line (usually top of file) and remove:

```python
from .pexels_client import PexelsClient
```

### Test

```bash
python -m py_compile src/cofounder_agent/services/content_router_service.py
```

---

## Step 3: Verify Publishing Code (10 minutes)

### Action: Check if legacy publishing method is actually used

**Test Command**:

```bash
grep -r "_run_publish\|run_publish\|PostgreSQLPublishingAgent" src/cofounder_agent/routes/
```

**Results**:

- **If no matches**: Legacy publishing is unused, safe to deprecate
- **If matches found**: Document where it's used, plan migration

### If Unused (Recommended Action)

Mark as deprecated in `content_orchestrator.py`:

```python
async def _run_publish(self, topic: str, content: str) -> str:
    """
    🚫 DEPRECATED - Use IntelligentOrchestrator with modern publishers instead

    This method is deprecated in favor of:
    - LinkedInPublisher.publish()
    - TwitterPublisher.publish()
    - EmailPublisher.publish()

    Planned Removal: Q1 2025
    """
    # ... existing code
```

### If Still Used

Keep the method but add comment documenting where it's called.

---

## Step 4: Configure Serper API Key (5 minutes)

### Action: Set up your Serper API key for active use

**File**: `.env.local` (in project root)

**Add or update**:

```bash
# Web Search
SERPER_API_KEY=your_actual_serper_api_key_here
```

**Verification**:

```python
# In Python REPL:
import os
from dotenv import load_dotenv

load_dotenv('.env.local')
api_key = os.getenv('SERPER_API_KEY')
print(f"Serper API Key configured: {bool(api_key)}")
print(f"Key starts with: {api_key[:10]}..." if api_key else "Not found")
```

### Test the Research Endpoint

```bash
# Start your FastAPI server:
cd src/cofounder_agent
python main.py

# In another terminal, test research:
curl -X POST http://localhost:8000/api/content/subtasks/research \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI in Healthcare",
    "keywords": ["machine learning", "medical"],
    "parent_task_id": "test_task_001"
  }'

# Expected response:
# {
#   "status": "success",
#   "research_data": "Search results from Serper...",
#   "usage": { "monthly_searches": 1 }
# }
```

---

## Step 5: Run Full Test Suite (5 minutes)

### Action: Verify nothing broke

```bash
# Navigate to project root:
cd c:\Users\mattm\glad-labs-website

# Run Python tests:
pytest tests/ -v

# Run linting:
pylint src/cofounder_agent/services/content_router_service.py
```

### Expected Results

- ✅ All tests pass
- ✅ No import errors
- ✅ No linting warnings about FeaturedImageService

---

## Step 6: Git Commit (5 minutes)

### Action: Document changes in git

```bash
git add -A

git commit -m "Phase 2: Remove dead code and optimize

- Delete FeaturedImageService class (never instantiated)
- Verify legacy publishing code status
- Clean up unused imports
- Configure Serper API key
- All tests passing"
```

### If You Need to Rollback

```bash
git revert HEAD
```

---

## Checklist: Complete

After completing all 6 steps, verify:

- [ ] FeaturedImageService deleted (34 lines removed)
- [ ] No import errors in content_router_service.py
- [ ] Legacy publishing status documented
- [ ] Serper API key in .env.local
- [ ] Research endpoint tested and working
- [ ] All tests passing
- [ ] Changes committed to git

---

## Time Breakdown

| Step      | Task                        | Time       | Status              |
| --------- | --------------------------- | ---------- | ------------------- |
| 1         | Delete FeaturedImageService | 5 min      | ⏱️ Do this first    |
| 2         | Check imports               | 5 min      | ⏱️ Then this        |
| 3         | Verify publishing           | 10 min     | ⏱️ Check status     |
| 4         | Configure Serper            | 5 min      | ⏱️ Set API key      |
| 5         | Run tests                   | 5 min      | ⏱️ Verify all works |
| 6         | Git commit                  | 5 min      | ⏱️ Document changes |
| **Total** |                             | **35 min** | ✅ Done             |

---

## Troubleshooting

### Issue: Import Error After Deletion

**Error**: `ImportError: cannot import name 'FeaturedImageService'`

**Solution**:

```bash
# Check if anything imports FeaturedImageService:
grep -r "from.*content_router_service import FeaturedImageService" src/

# Also check:
grep -r "FeaturedImageService" src/

# If found, remove those imports too
```

### Issue: PexelsClient Import Orphaned

**Error**: `ImportError: cannot import name 'PexelsClient'`

**Solution**:

```python
# In content_router_service.py, check imports
# PexelsClient might still be imported but no longer used
# Either:
# A) Remove the import if not used elsewhere
# B) Keep it if used by other classes in the file
```

### Issue: Tests Fail After Deletion

**Error**: `AttributeError: ... FeaturedImageService ...`

**Solution**:

```bash
# Find which test references FeaturedImageService:
grep -r "FeaturedImageService" tests/

# Update the test to use ImageService instead:
# Replace: FeaturedImageService().search_featured_image()
# With: ImageService().search_featured_image()
```

---

## Next Phase (Optional Enhancements)

After Phase 2 cleanup is complete, consider Phase 3:

### Phase 3A: Expand Serper Research (2 hours)

Add deep research endpoint:

```python
@router.post("/api/content/subtasks/research/deep")
async def run_deep_research(request: DeepResearchRequest):
    """Multi-step research with validation"""
```

### Phase 3B: Agent Factory Migration (1 hour)

Centralize agent instantiation:

```python
# Instead of importing directly:
creative_agent = agent_factory.create_creative_agent(llm_client)
```

### Phase 3C: Archive Cleanup (15 min)

Create README explaining consolidation:

```markdown
# src/agents/archive/

All agents here have been consolidated into unified services.
See CONSOLIDATION_MAP for details.
```

---

## When to Stop

**You're done with Phase 2 when**:

1. ✅ FeaturedImageService is deleted
2. ✅ No broken imports
3. ✅ All tests pass
4. ✅ Serper API key configured and tested
5. ✅ Changes committed to git
6. ✅ This guide is marked complete

**Estimated time**: 30-35 minutes  
**Difficulty**: Low (no complex refactoring)  
**Risk**: Minimal (dead code removal only)

---

## Questions?

Refer back to `PHASE_2_FINAL_ANALYSIS.md` for context on each deletion.

**Status**: Ready to execute! 🚀
