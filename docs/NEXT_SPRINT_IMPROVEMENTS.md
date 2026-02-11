# Next Sprint Improvements Roadmap

**Last Updated:** February 10, 2026

This document outlines planned improvements for three UI-integrated services that are currently functional but have opportunities for production hardening and enhanced capabilities.

---

## 1. ModelSelectorService - Phase-Aware Model Routing

**Status:** ✅ Active (Frontend UI integrated)

**Location:** [src/cofounder_agent/services/model_selector_service.py](../src/cofounder_agent/services/model_selector_service.py) (314 lines)

**Current Implementation:**

- Per-phase model selection (research, outline, draft, assess, refine, finalize)
- Auto-selection based on quality preference (fast, balanced, quality)
- Cost estimation before execution
- Model availability checking via `QualityPreference` enum

**Current Architecture:**

```text
Content Generation Request
  → UnifiedOrchestrator
    → ModelConsolidationService (intelligent fallback chain)
      → OllamaClient (local, zero-cost)
      → GeminiClient (fallback)
      → HuggingFaceClient (fallback)
      → ChatGPTClient (fallback)
      → ClaudeClient (fallback)
```

**Why Current Approach Works:**

- Single model per task (simplicity)
- Intelligent fallback across all providers
- Production-stable and effective

**Planned Improvements (Next Sprint):**

### 1.1 Phase-Aware Model Routing Integration

**Effort:** 3-4 hours
**Priority:** HIGH

Integrate ModelSelector into UnifiedOrchestrator to use phase-specific models:

```python
# Current (all phases use same model):
model = consolidation_service.get_model()
result = model.generate(prompt)

# Proposed (phase-specific):
model = model_selector.auto_select(
    phase="research",  # cheaper
    quality=QualityPreference.BALANCED
)
result = model.generate(prompt)
```

**Benefits:**

- `$X.XX` cost reduction per task by using cheaper models for research/outline
- Same final quality via GPT-4/Claude for assessment/refine/finalize
- Optional cost tracking per phase

**Implementation Steps:**

1. Update UnifiedOrchestrator to accept `phase` parameter
2. Update each Agent (ResearchAgent, CreativeAgent, QAAgent, PublishingAgent) to call `auto_select(phase)`
3. Add cost logging to metrics_routes.py
4. Test cost vs quality tradeoff
5. Update documentation/UI with quality tier information

### 1.2 User Quality Preferences

**Effort:** 2 hours
**Priority:** MEDIUM

Store user-selected quality preference (fast/balanced/quality) in settings:

```python
# Settings routes enhancement (see Section 2):
POST /api/settings/user_preferences
{
    "quality_preference": "balanced",
    "cost_limit_per_task": 0.10
}
```

**Benefits:**

- Users can choose cost vs quality tradeoff
- Automatic cost limiting to prevent bill shock
- Personalized experience

### 1.3 Cost Dashboard Integration

**Effort:** 2-3 hours
**Priority:** MEDIUM

Add cost breakdown to admin/oversight-hub:

- Cost per phase (research: $0.00, draft: $0.0015, assess: $0.001, etc.)
- Total monthly spend forecast
- Cost savings from ModelSelector vs single-model approach

**References:**

- [model_routes.py](../src/cofounder_agent/routes/model_routes.py) - Model endpoints
- [model_consolidation_service.py](../src/cofounder_agent/services/model_consolidation_service.py) - Active router
- [metrics_routes.py](../src/cofounder_agent/routes/metrics_routes.py) - Analytics integration

---

## 2. Settings Routes Enhancement - Full RBAC & Encryption

**Status:** ✅ Active (Frontend UI integrated)

**Location:** [src/cofounder_agent/routes/settings_routes.py](../src/cofounder_agent/routes/settings_routes.py) (718 lines)

**Current Implementation:**

- Mock implementation for testing
- Basic CRUD endpoints (GET, POST, PUT, DELETE)
- Settings categories (database, authentication, system, etc.)
- Mock authentication with Bearer tokens
- Audit logging structure (not fully implemented)

**Current Endpoints:**

```
GET    /api/settings              - List all settings (with filtering)
GET    /api/settings/{setting_id} - Get specific setting
POST   /api/settings              - Create new setting
PUT    /api/settings/{setting_id} - Update existing setting
PUT    /api/settings              - Batch update
DELETE /api/settings/{setting_id} - Delete setting
DELETE /api/settings              - Batch delete
GET    /api/settings/{setting_id}/history    - Get audit trail
POST   /api/settings/{setting_id}/rollback   - Rollback to previous value
POST   /api/settings/bulk/update  - Bulk update multiple
GET    /api/settings/export/all   - Export all settings
```

**Current Limitations:**

- Mock implementation (returns test data)
- No real database persistence
- No encryption of sensitive values
- Role-based access control (RBAC) is stubbed out
- Audit logging not fully implemented

**Planned Improvements (Next Sprint):**

### 2.1 Real Database Implementation

**Effort:** 4-5 hours
**Priority:** CRITICAL

Replace mock returns with real PostgreSQL queries:

```python
# Current (mock):
return SettingListResponse(
    total=10, page=page, items=mock_settings
)

# Proposed (real DB):
settings = db.query(SettingModel).filter(...).all()
return SettingListResponse(
    total=len(settings), page=page, items=settings
)
```

**Benefits:**

- Actual configuration management
- Persistent settings across deployments
- Single source of truth for app config

**Implementation Steps:**

1. Create `SettingModel` in database_service.py
2. Create migration SQL (002_settings_table.sql)
3. Replace all mock implementations with real queries
4. Add database transactions for atomic updates
5. Add rollback capability via audit log

### 2.2 Encryption for Sensitive Values

**Effort:** 3 hours
**Priority:** HIGH

Encrypt sensitive settings (API keys, passwords, secrets):

```python
# Fields to encrypt:
is_encrypted: bool  # New field in model
encrypted_value: str  # Encrypted storage

# Example encryption:
if is_encrypted:
    encrypted = cipher.encrypt(value)
    setting.encrypted_value = encrypted
    setting.value_preview = f"{value[:10]}..."  # Show only first 10 chars
```

**Benefits:**

- Secure storage of API keys, database passwords, secrets
- Compliance with security best practices
- User-controlled encryption (can choose sensitive fields)

**Implementation:**

1. Add `is_encrypted` and `encrypted_value` columns to SettingModel
2. Implement encryption/decryption using `cryptography` library
3. Update all endpoints to handle encrypted values
4. Add encryption toggle in UI

### 2.3 Full RBAC Implementation

**Effort:** 3-4 hours
**Priority:** HIGH

Implement role-based access control:

```python
# Roles:
VIEWER: Read-only, see non-sensitive settings only
EDITOR: Can modify application settings, never system-critical
ADMIN: Full access, can modify system settings and encryption

# Permission Matrix:
                VIEWER  EDITOR  ADMIN
GET settings      ✓       ✓       ✓
LIST settings     ✓       ✓       ✓
CREATE setting    ✗       ✓       ✓
UPDATE setting    ✗       ✓       ✓
DELETE setting    ✗       ✗       ✓
Encrypted values  ✗       ✗       ✓
Admin-only ops    ✗       ✗       ✓
```

**Benefits:**

- Prevent unauthorized configuration changes
- Protect sensitive settings from non-admin users
- Audit trail for permission checks

**Implementation:**

1. Integrate with existing JWT auth in main.py
2. Add role extraction from JWT token
3. Add permission decorators to each endpoint
4. Add role check in database queries (row-level security)

### 2.4 Complete Audit Logging

**Effort:** 2 hours
**Priority:** MEDIUM

Implement full audit trail for all setting changes:

```python
# SettingAuditLog schema:
id: UUID
setting_id: int
user_id: str
action: ENUM(CREATE, UPDATE, DELETE)
old_value: str (encrypted if applicable)
new_value: str (encrypted if applicable)
timestamp: datetime
ip_address: str
user_agent: str
change_description: str
```

**Benefits:**

- Compliance audit trail
- Ability to see who changed what and when
- Rollback to any previous value

**Implementation:**

1. Create `SettingAuditLog` model
2. Add audit log trigger on every change
3. Implement GET /api/settings/{id}/history
4. Implement POST /api/settings/{id}/rollback

**References:**

- [schemas/settings_schemas.py](../src/cofounder_agent/schemas/settings_schemas.py) - Pydantic models
- [database_service.py](../src/cofounder_agent/services/database_service.py) - DB integration
- [JWT authentication](../src/cofounder_agent/utils/jwt_utils.py) - Auth reference

---

## 3. Cloudinary CMS - Complete Image Hosting Solution

**Status:** ✅ Active (Image delivery solution for frontend)

**Location:** [src/cofounder_agent/services/cloudinary_cms_service.py](../src/cofounder_agent/services/cloudinary_cms_service.py) (420+ lines)

**Current Implementation:**

- Cloudinary SDK integration
- Image upload and asset management
- Responsive image variant generation (thumbnail, preview, full)
- SEO metadata support (alt text, title)
- Optional fallback when Cloudinary disabled

**Current Features:**

- `upload_image()` - Upload from URL to Cloudinary
- `optimize_featured_image()` - Optimize single featured image
- `delete_image()` - Remove from Cloudinary
- `get_usage_stats()` - Track account usage

**Current Configuration:**

```env
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret
```

**Current Limitations:**

- No transformation pipeline (only basic variants)
- No video hosting support (mentioned in docstring but not implemented)
- No batch operations
- Limited CDN optimization (only format/quality auto)
- No webhook integration for post-processing

**Planned Improvements (Next Sprint):**

### 3.1 Advanced Image Transformations

**Effort:** 3-4 hours
**Priority:** HIGH

Add image transformation pipeline:

```python
# Proposed variant strategy:
VARIANTS = {
    "thumbnail": {"width": 300, "height": 200, "quality": "auto", "format": "auto"},
    "preview": {"width": 600, "height": 400, "quality": "auto", "format": "auto"},
    "full": {"width": 1200, "height": 800, "quality": "auto", "format": "auto"},
    "hero": {"width": 1920, "height": 1080, "quality": "auto", "format": "auto"},
    "social": {"width": 1200, "height": 630, "quality": "high", "format": "jpg"},  # OG tags
}

# Usage:
asset = await cloudinary_service.upload_image(
    url, 
    variants=VARIANTS,  # Generate all variants on upload
    generate_srcset=True  # Create responsive <img srcset>
)
```

**Benefits:**

- Responsive images for all screen sizes
- Automatic WebP/AVIF conversion
- Quality optimization per use-case
- Faster page loads via smaller files

**Implementation:**

1. Add `variants` parameter to `upload_image()`
2. Generate all variants in parallel
3. Return variant URLs and srcset metadata
4. Integrate with front-end Image component

### 3.2 Video Hosting Support

**Effort:** 4-5 hours
**Priority:** MEDIUM

Add video upload and delivery:

```python
async def upload_video(
    self,
    video_url: str,
    folder: str = "oversight-hub/videos",
    quality: str = "h.265",  # auto-transcode
    generate_thumbnail: bool = True,
) -> Optional[VideoAsset]:
    """Upload and transcode video for web delivery"""
```

**Benefits:**

- Host video content on CDN
- Automatic compression and transcoding
- Thumbnail generation
- Streaming delivery across regions

**Implementation:**

1. Add `VideoAsset` dataclass
2. Implement `upload_video()` with quality tiers
3. Add automatic thumbnail generation
4. Implement `get_video_stats()`

### 3.3 Batch Operations

**Effort:** 2-3 hours
**Priority:** MEDIUM

Add batch upload and delete operations:

```python
async def batch_upload_images(
    self,
    image_urls: List[str],
    folder: str = "oversight-hub",
) -> List[ImageAsset]:
    """Upload multiple images in parallel"""

async def batch_delete_images(
    self,
    public_ids: List[str],
) -> Dict[str, bool]:
    """Delete multiple images in parallel"""
```

**Benefits:**

- Efficient bulk operations for content migration
- Parallel uploads (faster)
- Atomic delete operations

**Implementation:**

1. Use `asyncio.gather()` for parallel uploads
2. Add progress tracking
3. Add error handling per item

### 3.4 Webhook Integration

**Effort:** 2-3 hours
**Priority:** LOW

Add webhook endpoint for Cloudinary callbacks:

```python
POST /api/webhooks/cloudinary
- Image upload completion
- Video transcoding completion
- Auto-tagging results
- Moderation flags

# Database: Track events, enable retry logic, audit trail
```

**Benefits:**

- Real-time status updates
- Automatic image tagging (AI)
- Content moderation
- Error recovery

**Implementation:**

1. Create webhook route handler
2. Verify Cloudinary webhook signature
3. Store webhook events in database
4. Trigger processing on completion

**References:**

- [Cloudinary API Docs](https://cloudinary.com/documentation)
- [Image optimization best practices](https://res.cloudinary.com/demo/image/)
- [cms_routes.py](../src/cofounder_agent/routes/cms_routes.py) - CMS integration
- [content_agent/](../src/cofounder_agent/agents/content_agent/) - Content pipeline

---

## Implementation Priority & Timeline

### Sprint 1 (Current Sprint - Week 1-2)

- ✅ Phase 1: Delete dead code (done)
- ✅ Phase 2: Archive cleanup (done)
- ⏳ Phase 3: This document (done)

### Sprint 2 (Next Sprint - Week 3-4)

**Estimated: 30-40 hours**

**High Priority (Start Here):**

1. ModelSelector: Cost dashboard integration (2-3h)
2. Settings: Real database implementation (4-5h)
3. Settings: Encryption for sensitive values (3h)
4. Cloudinary: Advanced transformations (3-4h)

**Medium Priority (Following):**

1. Settings: Full RBAC implementation (3-4h)
2. Cloudinary: Video hosting support (4-5h)
3. Settings: Complete audit logging (2h)

**Low Priority (Polish):**

1. ModelSelector: User quality preferences (2h)
2. Cloudinary: Batch operations (2-3h)
3. Cloudinary: Webhook integration (2-3h)

---

## Success Criteria

**ModelSelector:**

- ✅ Phase-specific models reduce cost by >15% without quality loss
- ✅ Cost tracking visible in admin dashboard
- ✅ All tests pass

**Settings Routes:**

- ✅ Real database persistence working
- ✅ Sensitive values encrypted at rest
- ✅ RBAC permissions enforced
- ✅ Full audit trail of all changes
- ✅ Export/import functionality working

**Cloudinary CMS:**

- ✅ All image variants generated correctly
- ✅ Responsive images working in frontend
- ✅ Video hosting tested
- ✅ Webhook integration receiving callbacks

---

## Dependencies & Blockers

**Hard Blockers:**

- None identified

**Soft Dependencies:**

- Settings: Requires JWT auth completion in main.py
- Cloudinary: Requires Cloudinary API credentials
- ModelSelector: Benefits from cost_aggregation_service integration

**Known Issues:**

- settings_routes.py currently has mocked get_current_user function - needs real JWT integration
- No real database migrations exist for settings table yet

---

## Related Files

**ModelSelector References:**

- [model_selector_service.py](../src/cofounder_agent/services/model_selector_service.py) - Main service (314 lines)
- [model_routes.py](../src/cofounder_agent/routes/model_routes.py) - API endpoints
- [model_consolidation_service.py](../src/cofounder_agent/services/model_consolidation_service.py) - Active router

**Settings References:**

- [settings_routes.py](../src/cofounder_agent/routes/settings_routes.py) - API endpoints (718 lines)
- [settings_schemas.py](../src/cofounder_agent/schemas/settings_schemas.py) - Pydantic models
- [database_service.py](../src/cofounder_agent/services/database_service.py) - Database layer

**Cloudinary References:**

- [cloudinary_cms_service.py](../src/cofounder_agent/services/cloudinary_cms_service.py) - Main service (420+ lines)
- [cms_routes.py](../src/cofounder_agent/routes/cms_routes.py) - CMS API endpoints
- [content_agent/](../src/cofounder_agent/agents/content_agent/) - Content pipeline

---

**Last Review:** February 10, 2026  
**Next Review:** End of Sprint 2 (February 24, 2026)  
**Maintained By:** Codebase Maintenance Task
