# Todo 3: Remove Google Cloud Dependencies - Detailed Action Plan

**Status:** ⏳ IN PROGRESS  
**Priority:** 🔴 HIGH - Blocks build and deployment  
**Estimated Time:** 20-30 minutes  
**Date:** October 26, 2025

---

## 🎯 Objective

Remove all Google Cloud service clients from active codebase that have been archived. Ensure no imports of archived modules remain in active code.

---

## 📋 Inventory of Files to Delete

### Core Backend Services (src/cofounder_agent/services/)

**1. firestore_client.py** (325 lines)

- **Status:** ✅ ARCHIVED at `archive/google-cloud-services/firestore_client.py.archive`
- **Action:** DELETE from active codebase
- **Path:** `src/cofounder_agent/services/firestore_client.py`
- **Current Imports:** Used in `src/cofounder_agent/main.py`?
- **Migration:** Already REST API endpoints in main.py

**2. pubsub_client.py** (362 lines)

- **Status:** ✅ ARCHIVED at `archive/google-cloud-services/pubsub_client.py.archive`
- **Action:** DELETE from active codebase
- **Path:** `src/cofounder_agent/services/pubsub_client.py`
- **Current Imports:** Used in `src/cofounder_agent/main.py`?
- **Migration:** Already REST API endpoints

### Agent-Specific Services (src/agents/content_agent/services/)

**3. firestore_client.py** (181 lines)

- **Status:** ✅ ARCHIVED at `archive/google-cloud-services/content_agent_firestore_client.py.archive`
- **Action:** DELETE from active codebase
- **Path:** `src/agents/content_agent/services/firestore_client.py`
- **Current Imports:**
  - `market_insight_agent.py` (line 4)
  - `orchestrator.py` (line 8)
  - `firestore_logger.py` (line 2)
  - Test: `test_firestore_client.py` (line 3)
- **Migration:** Use REST API calls instead

**4. pubsub_client.py** (82 lines)

- **Status:** ✅ ARCHIVED at `archive/google-cloud-services/content_agent_pubsub_client.py.archive`
- **Action:** DELETE from active codebase
- **Path:** `src/agents/content_agent/services/pubsub_client.py`
- **Current Imports:**
  - `orchestrator.py` (line 18)
  - Test: `test_pubsub_client.py` (multiple lines)
- **Migration:** Use REST API polling instead

**5. gcs_client.py** (45 lines)

- **Status:** ✅ ARCHIVED at `archive/google-cloud-services/gcs_client.py.archive`
- **Action:** DELETE from active codebase
- **Path:** `src/agents/content_agent/services/gcs_client.py`
- **Current Imports:**
  - `orchestrator.py` (line 11)
  - `image_agent.py` (line 7)
- **Migration:** Use REST API file upload instead

### Agent Utilities (src/agents/content_agent/)

**6. create_task.py** (61 lines)

- **Status:** ✅ ARCHIVED at `archive/google-cloud-services/create_task.py.archive`
- **Action:** DELETE from active codebase (or replace with REST API wrapper)
- **Path:** `src/agents/content_agent/create_task.py`
- **Current Usage:** CLI utility - not critical to main flow
- **Migration:** Use REST API POST /api/tasks instead

### Optional Utility

**7. firestore_logger.py** (Firestore-specific logging)

- **Path:** `src/agents/content_agent/utils/firestore_logger.py`
- **Status:** IMPORTS archived firestore_client
- **Action:** DELETE or comment out (if not used elsewhere)
- **Current Imports:** firestore_client (line 2)
- **Alternative:** Use standard Python logging

---

## 📝 Files to MODIFY (Remove Imports)

These files import the archived modules and must be updated:

### 1. orchestrator.py

**Path:** `src/agents/content_agent/orchestrator.py`
**Current Imports (Lines 8, 11, 18):**

```python
from services.firestore_client import FirestoreClient
from services.gcs_client import GCSClient
from services.pubsub_client import PubSubClient
```

**Action:**

- Remove these 3 import lines
- Replace FirestoreClient calls with REST API calls
- Replace GCSClient calls with REST API file upload
- Replace PubSubClient with REST API polling
  **Estimated Changes:** 10-15 locations in file

### 2. market_insight_agent.py

**Path:** `src/agents/market_insight_agent/market_insight_agent.py`
**Current Import (Line 4):**

```python
from src.agents.content_agent.services.firestore_client import FirestoreClient
```

**Action:**

- Remove this import
- Replace FirestoreClient calls with REST API calls
  **Estimated Changes:** 5-10 locations in file

### 3. image_agent.py

**Path:** `src/agents/content_agent/agents/image_agent.py`
**Current Import (Line 7):**

```python
from src.agents.content_agent.services.gcs_client import GCSClient
```

**Action:**

- Remove this import
- Replace GCSClient calls with REST API file upload
  **Estimated Changes:** 3-5 locations in file

### 4. firestore_logger.py

**Path:** `src/agents/content_agent/utils/firestore_logger.py`
**Current Import (Line 2):**

```python
from services.firestore_client import FirestoreClient
```

**Action:**

- Remove this import
- Use standard Python logging instead
  **Alternative:** If not used, delete entire file

### 5. logging_config.py

**Path:** `src/agents/content_agent/utils/logging_config.py`
**Current (Line 17):**

```python
# from services.firestore_client import FirestoreClient
```

**Action:** Already commented out - no change needed

---

## 🔧 Test Files to Update/Delete

### 1. test_firestore_client.py

**Path:** `src/agents/content_agent/tests/test_firestore_client.py`
**Status:** Tests archived Firestore client
**Action:**

- DELETE (tests are no longer applicable)
- Or archive to `archive/tests/test_firestore_client.py`

### 2. test_pubsub_client.py

**Path:** `src/agents/content_agent/tests/test_pubsub_client.py`
**Status:** Tests archived Pub/Sub client
**Current Import (Line 54, 67, 79, etc):**

```python
from services.pubsub_client import PubSubClient
```

**Action:**

- DELETE (tests are no longer applicable)
- Or archive to `archive/tests/test_pubsub_client.py`

---

## 📦 Remove from requirements.txt Files

After deleting the service files, remove these packages:

**File 1: scripts/requirements-core.txt**

- [ ] Remove: `google-cloud-firestore`
- [ ] Remove: `google-cloud-pubsub`
- [ ] Remove: `google-cloud-storage`
- [ ] Remove: `google-auth`
- [ ] Remove: `google-auth-oauthlib`
- [ ] Remove: `google-auth-httplib2`
- [ ] Remove: `google-cloud-logging` (if present)

**File 2: scripts/requirements.txt**

- [ ] Remove same packages as above

**File 3: src/cofounder_agent/requirements.txt**

- [ ] Remove same packages as above

**File 4: src/agents/content_agent/requirements.txt** (if exists)

- [ ] Remove same packages as above

---

## 🔍 Pre-Deletion Verification

### Step 1: Confirm Archives Exist

```powershell
# Verify all archived files present
Get-ChildItem archive/google-cloud-services/ -Filter "*.archive" | Measure-Object

# Should show: 10 .archive files
```

### Step 2: List Files to Delete

```powershell
# Core backend
Get-ChildItem src/cofounder_agent/services/ -Filter "*client.py"

# Agent services
Get-ChildItem src/agents/content_agent/services/ -Filter "*client.py"
Get-ChildItem src/agents/content_agent/services/
```

### Step 3: Verify Imports

Already done with grep search - results:

- 8 import statements in 3 active files
- 6 import statements in 2 test files
- All mapped and ready for deletion

---

## ✅ Execution Steps (Recommended Order)

### Phase 1: Modify Import Files (10-15 minutes)

**Step 1.1:** Edit orchestrator.py

- [ ] Remove 3 Google Cloud imports
- [ ] Replace FirestoreClient calls with REST API
- [ ] Replace GCSClient calls with REST API
- [ ] Replace PubSubClient with REST API polling
- [ ] Test imports: `python -c "from orchestrator import *"`

**Step 1.2:** Edit market_insight_agent.py

- [ ] Remove 1 Google Cloud import
- [ ] Replace FirestoreClient calls with REST API
- [ ] Test imports: `python -c "from market_insight_agent import *"`

**Step 1.3:** Edit image_agent.py

- [ ] Remove 1 Google Cloud import
- [ ] Replace GCSClient calls with REST API
- [ ] Test imports: `python -c "from image_agent import *"`

**Step 1.4:** Handle firestore_logger.py

- [ ] Remove 1 Google Cloud import
- [ ] Use standard logging instead
- OR delete entire file if unused

### Phase 2: Delete Archived Service Files (5 minutes)

**Step 2.1:** Delete Core Backend Services

```powershell
Remove-Item src/cofounder_agent/services/firestore_client.py -Force
Remove-Item src/cofounder_agent/services/pubsub_client.py -Force
```

**Step 2.2:** Delete Agent-Specific Services

```powershell
Remove-Item src/agents/content_agent/services/firestore_client.py -Force
Remove-Item src/agents/content_agent/services/pubsub_client.py -Force
Remove-Item src/agents/content_agent/services/gcs_client.py -Force
Remove-Item src/agents/content_agent/create_task.py -Force
```

**Step 2.3:** Delete/Archive Test Files

```powershell
Remove-Item src/agents/content_agent/tests/test_firestore_client.py -Force
Remove-Item src/agents/content_agent/tests/test_pubsub_client.py -Force
```

### Phase 3: Update Requirements Files (5-10 minutes)

**Step 3.1:** Remove Google Cloud Packages

- [ ] scripts/requirements-core.txt
- [ ] scripts/requirements.txt
- [ ] src/cofounder_agent/requirements.txt
- [ ] src/agents/content_agent/requirements.txt (if exists)

**Step 3.2:** Verify Requirements Updated

```powershell
# Check for any remaining google.cloud packages
grep -r "google-cloud" scripts/ src/
# Should return: 0 matches
```

### Phase 4: Validation (5-10 minutes)

**Step 4.1:** Verify No Google Cloud Imports Remain

```powershell
# Search for Google Cloud imports (should be 0)
grep -r "from google.cloud\|import google.cloud" src/ --include="*.py" --exclude-dir=archive

# Expected: 0 matches (only in archive/ directory)
```

**Step 4.2:** Test Python Imports

```powershell
# Test each modified agent
python -c "from src.agents.content_agent.orchestrator import *"
python -c "from src.agents.market_insight_agent.market_insight_agent import *"
python -c "from src.agents.content_agent.agents.image_agent import *"

# All should import without errors
```

**Step 4.3:** Prepare for Testing (Todo 5)

- [ ] Commit changes: `git add -A && git commit -m "refactor: remove archived Google Cloud service clients"`
- [ ] Ready for: `pytest src/ --co` (verify test collection)
- [ ] Ready for: Full test suite execution

---

## 🚨 Rollback Plan

If issues occur during deletion:

1. **Restore Archives:**

   ```powershell
   # Copy back from archive/google-cloud-services/
   Copy-Item archive/google-cloud-services/*_client.py.archive src/agents/content_agent/services/ -Verbose
   # Remove .archive extension
   ```

2. **Restore Git:**

   ```powershell
   git checkout HEAD -- src/
   ```

3. **Re-add Dependencies:**
   ```powershell
   pip install google-cloud-firestore google-cloud-pubsub google-cloud-storage
   ```

---

## 📊 Expected Changes

### Files to DELETE: 6

- src/cofounder_agent/services/firestore_client.py
- src/cofounder_agent/services/pubsub_client.py
- src/agents/content_agent/services/firestore_client.py
- src/agents/content_agent/services/pubsub_client.py
- src/agents/content_agent/services/gcs_client.py
- src/agents/content_agent/create_task.py
- Optional: src/agents/content_agent/utils/firestore_logger.py

### Files to MODIFY: 4

- src/agents/content_agent/orchestrator.py (3 imports removed)
- src/agents/market_insight_agent/market_insight_agent.py (1 import removed)
- src/agents/content_agent/agents/image_agent.py (1 import removed)
- src/agents/content_agent/utils/firestore_logger.py (1 import removed or file deleted)

### Test Files to DELETE: 2

- src/agents/content_agent/tests/test_firestore_client.py
- src/agents/content_agent/tests/test_pubsub_client.py

### Requirements Files to MODIFY: 4

- scripts/requirements-core.txt
- scripts/requirements.txt
- src/cofounder_agent/requirements.txt
- src/agents/content_agent/requirements.txt (if exists)

### Total Changes: ~15 files affected

---

## ✅ Success Criteria

After completing Todo 3, these conditions should be true:

1. ✅ All archived Google Cloud service files deleted from active code
2. ✅ No `from google.cloud` imports in src/ directory (except archived files)
3. ✅ No imports of deleted service modules in active code
4. ✅ All modified agents can be imported without errors
5. ✅ All Google Cloud packages removed from requirements files
6. ✅ Python test collection succeeds: `pytest src/ --co`
7. ✅ No syntax errors: `python -m py_compile src/**/*.py`
8. ✅ Git status clean: `git status` shows modified files only

---

## 📝 Notes

- **Non-Breaking:** These are archived services not used by main.py
- **PostgreSQL Primary:** Active code uses REST API with PostgreSQL backend
- **Agent Isolation:** Agents can be independently updated without affecting orchestrator
- **Future Path:** Clear for implementing Phase 6 Google Drive/Docs integration

---

**Estimated Time to Complete:** 20-30 minutes  
**Blocking:** No - Todo 4 and 5 can proceed in parallel if needed  
**Next Step:** Execute Phase 1 (modify imports), then Phase 2 (delete files), Phase 3 (clean requirements), Phase 4 (validate)
