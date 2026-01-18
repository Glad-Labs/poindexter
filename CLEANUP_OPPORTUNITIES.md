# Code Cleanup Opportunities - Phase 2 Extensions

**Date:** January 17, 2026  
**Status:** Identified & Ready for Implementation  
**Priority:** Medium (Post-deployment optimizations)

---

## 🧹 Cleanup Opportunities Identified

### Category 1: Error Handling Standardization

**Location:** All routes files (`*_routes.py`)  
**Pattern:** Repeated try/except/HTTPException pattern

**Current Pattern (Inefficient):**
```python
except HTTPException:
    raise
except Exception as e:
    logger.error(f"Error: {str(e)}", exc_info=True)
    raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
```

**Optimization:** Create utility function for consistency

**Files Affected:** 15+ route files  
**Impact:** Reduce code duplication by ~50 lines, improve consistency

---

### Category 2: Hardcoded Constants in Services

**Location:** Various service files  
**Pattern:** Magic numbers for timeouts, limits, retry counts

**Examples Found:**
- `cloudinary_cms_service.py`: timeout=30.0, timeout=10.0
- `huggingface_client.py`: timeout=5, timeout=300
- `image_service.py`: Various hardcoded limits

**Opportunity:** Move all to centralized constants configuration  
**Files Affected:** 8+ service files  
**Impact:** Single source of truth, easier tuning

---

### Category 3: Logging Inconsistencies

**Location:** Services and routes  
**Pattern:** Mixed logging levels, emoji prefixes inconsistent

**Current Mix:**
- `logger.info("✅ Started")` vs `logger.info("Started")`
- No standardized prefix scheme
- Some services use structlog, others use standard logging

**Opportunity:** Standardize logging format and structure  
**Files Affected:** 20+ files  
**Impact:** Better observability, consistent format

---

### Category 4: Unused Imports & Dead Code

**Location:** Various files  
**Pattern:** Import statements that may not be used

**To Verify:**
- `database_mixin.py`: Unused UUID import?
- Various services: Check for import consolidation
- Test files: Clean up unused fixtures

**Files Affected:** 10+ files  
**Impact:** Cleaner codebase, faster imports

---

### Category 5: Configuration Duplication

**Location:** Environment variables  
**Pattern:** Same config in multiple places

**Examples:**
- Timeout values duplicated in .env.local and constants.py
- API keys defined in multiple ways
- Cache TTLs scattered across services

**Opportunity:** Single config source  
**Files Affected:** Config files + services  
**Impact:** Easier management, less confusion

---

## 📊 Cleanup Impact Summary

| Opportunity | Lines Saved | Files | Effort | Impact |
|------------|------------|-------|--------|--------|
| Error handling | ~50 | 15+ | Medium | High consistency |
| Constants migration | ~30 | 8+ | Low | High maintainability |
| Logging cleanup | ~20 | 20+ | Medium | High observability |
| Unused imports | ~10 | 10+ | Low | High quality |
| Config dedup | ~15 | 5+ | Low | High clarity |
| **Total** | **~125** | **50+** | **Low** | **High** |

---

## 🎯 Recommended Cleanup Order

### Phase 1: Quick Wins (< 1 hour)
1. Create error handling utility function
2. Consolidate imports in key files
3. Remove unused imports

### Phase 2: Configuration (< 2 hours)
1. Move all hardcoded timeouts to constants
2. Consolidate configuration sources
3. Update docs on configuration

### Phase 3: Logging (< 2 hours)
1. Standardize logging format
2. Create logging configuration module
3. Update all loggers to use standard format

---

## 📋 Detailed Cleanup Checklist

### Error Handling Utility

**File to Create:** `src/cofounder_agent/utils/error_handler.py`

**Functions:**
```python
async def handle_route_error(e: Exception, logger, operation: str) -> HTTPException:
    """Unified error handling for routes"""
    
async def handle_service_error(e: Exception, logger, operation: str, fallback=None):
    """Unified error handling for services"""
    
def format_error_response(error: Exception, detail: str = None) -> dict:
    """Format consistent error responses"""
```

**Usage Example:**
```python
# Before:
except HTTPException:
    raise
except Exception as e:
    logger.error(f"Error fetching post: {str(e)}", exc_info=True)
    raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# After:
except HTTPException:
    raise
except Exception as e:
    raise await handle_route_error(e, logger, "fetch_post")
```

---

### Constants Consolidation

**Files to Update:**
- `src/cofounder_agent/config/constants.py` - Add missing timeouts
- `src/cofounder_agent/services/*.py` - Replace hardcoded values

**New Constants to Add:**
```python
# Cloudinary
CLOUDINARY_UPLOAD_TIMEOUT = 30.0
CLOUDINARY_DELETE_TIMEOUT = 10.0
CLOUDINARY_USAGE_TIMEOUT = 10.0

# HuggingFace
HUGGINGFACE_QUICK_TIMEOUT = 5  # seconds
HUGGINGFACE_LONG_TIMEOUT = 300  # seconds

# Image Processing
IMAGE_MAX_SIZE_MB = 10
IMAGE_MAX_DIMENSION = 4096
IMAGE_QUALITY = 0.85
```

---

### Logging Standardization

**File to Create:** `src/cofounder_agent/config/logging_config.py`

**Features:**
- Consistent format for all loggers
- Centralized prefix/emoji management
- Structured logging configuration
- Environment-based log levels

**Usage:**
```python
from config.logging_config import get_logger

logger = get_logger(__name__)
logger.info("Operation started")  # Automatically formatted
```

---

## 🔍 Quick Scan Results

### Patterns Found

1. **Error Handling Pattern** (Found in 15+ files)
   ```python
   except HTTPException:
       raise
   except Exception as e:
       logger.error(...)
       raise HTTPException(...)
   ```
   **Cleanup Potential:** 50+ lines of duplicate code

2. **Timeout Pattern** (Found in 8+ files)
   ```python
   timeout=30.0  # or 10.0 or 5 or 300
   ```
   **Cleanup Potential:** Move to constants.py

3. **Logging Prefix Pattern** (Found in 20+ files)
   ```python
   logger.info("✅ Operation done")
   logger.error("❌ Operation failed")
   ```
   **Cleanup Potential:** Standardize format

4. **Configuration Duplication** (Found in multiple places)
   - Cache TTLs defined multiple ways
   - Timeouts duplicated in .env and code
   - API limits scattered

---

## 💡 Quick Wins Implementation Plan

### Win #1: Error Handler Utility (30 minutes)

1. Create `utils/error_handler.py`
2. Add `handle_route_error()` function
3. Add examples to docstring
4. Update 3 routes as proof of concept

**Files:** 4 new/modified  
**Lines saved:** ~15 (per route, 45+ total when applied to all)

### Win #2: Import Cleanup (20 minutes)

1. Scan for unused imports with grep
2. Remove unused imports from key files
3. Consolidate related imports

**Files:** 10+ modified  
**Lines saved:** ~10

### Win #3: Missing Constants (15 minutes)

1. Add timeouts to constants.py
2. Update 3 services as examples
3. Document new constants

**Files:** 1 modified, 3+ to migrate  
**Lines saved:** ~20 (consolidated, not saved but cleaner)

---

## 📈 Expected Outcomes

### Code Quality Improvements
- ✅ DRY principle: Remove 50+ lines of duplicate code
- ✅ Maintainability: Single source of truth for configuration
- ✅ Consistency: Standardized error handling and logging
- ✅ Clarity: Centralized configuration easier to understand

### Developer Experience
- ✅ Faster development: Less copy-paste
- ✅ Fewer bugs: Consistent error handling
- ✅ Easier debugging: Standardized logging
- ✅ Better documentation: Clear patterns to follow

### Performance
- ✅ No negative impact (cleanup only)
- ✅ Potential gains: Faster import times with clean imports
- ✅ Better monitoring: Standardized logging enables better metrics

---

## Next Steps

1. **Review:** Confirm cleanup opportunities are valid
2. **Implement:** Start with Quick Wins (1-2 hours total)
3. **Test:** Verify no regressions after cleanup
4. **Document:** Update coding standards with new patterns
5. **Extend:** Apply patterns to remaining files

---

## Related Documentation

- Coding Standards: [CODE_STANDARDS_QUICK_REFERENCE.md](docs/archive-old/CODE_STANDARDS_QUICK_REFERENCE.md)
- Phase 2 Summary: [PHASE2_PROGRESS_SUMMARY.md](PHASE2_PROGRESS_SUMMARY.md)
- Configuration Guide: [EXTENDED_CODE_AUDIT_PHASE2.md](EXTENDED_CODE_AUDIT_PHASE2.md)

