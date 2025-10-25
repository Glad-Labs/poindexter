# 🔐 Implementation Plan: Settings Management + Authentication System

**Last Updated:** October 24, 2025  
**Version:** 1.0  
**Status:** 📋 For Review  
**Document Type:** Technical Architecture & Implementation Roadmap  
**Audience:** Technical Team, Architecture Review

---

## 📊 Executive Summary

This plan outlines the implementation of an **industry-standard Settings Management and Authentication system** that will:

- ✅ **Centralize Configuration** - Move 67 of 82 "secrets" from static GitHub to dynamic runtime settings
- ✅ **Add User Authentication** - JWT-based login for Oversight Hub with role-based access control
- ✅ **Enable Real-time Updates** - Change API keys, feature flags, and settings without deployments
- ✅ **Audit Everything** - Complete audit trail of who changed what and when
- ✅ **Secure Sensitive Data** - AES-256 encryption at rest for API keys and credentials
- ✅ **Use Existing Infrastructure** - Leverage your current Strapi PostgreSQL volume for all data
- ✅ **Optimize Performance** - Redis caching for settings and sessions

**Decision Point:** Single shared Postgres instance (Strapi) vs separate instance

**Recommended:** Shared instance with proper schema isolation (simpler, lower cost)

---

## 🏗️ Part 1: Infrastructure & Database Strategy

### 1.1 Database Architecture Decision

#### Option A: Single PostgreSQL Instance (RECOMMENDED)

```
Railway Postgres Volume (glad_labs_production)
├── Strapi schemas
│   ├── strapi_core_store
│   ├── strapi_webhooks
│   └── ... other Strapi tables
│
└── GLAD Labs Application (NEW)
    ├── public.users
    ├── public.roles
    ├── public.permissions
    ├── public.settings
    ├── public.settings_audit_log
    ├── public.sessions
    ├── public.feature_flags
    └── public.integrations_config
```

**Advantages:**

- ✅ Single database to manage (same volume)
- ✅ Lower cost (no additional instance)
- ✅ Easier backup/restore (one database)
- ✅ Reduced complexity
- ✅ Transactions across Strapi and app data
- ✅ Schema isolation prevents conflicts

**Disadvantages:**

- ⚠️ Shared resource contention (mitigated with proper connection pooling)
- ⚠️ Single point of failure (mitigated with backups)

#### Option B: Separate PostgreSQL Instance

```
Database 1 (Strapi) - glad_labs_production
├── All Strapi tables
└── Strapi-specific config

Database 2 (App) - glad_labs_app (NEW)
├── users, roles, permissions
├── settings, audit_log
├── sessions
└── feature_flags
```

**Advantages:**

- ✅ Complete isolation
- ✅ Independent scaling
- ✅ Separate backups

**Disadvantages:**

- ❌ Higher cost (+$25-50/month)
- ❌ Additional complexity
- ❌ Harder to maintain consistency

**Recommendation:** **Option A (Single Instance)** - Use schema namespacing in PostgreSQL

### 1.2 Redis Caching Strategy

**Three-tier caching approach:**

```
Layer 1: In-Memory Cache (Python)
├── TTL: 5 minutes
├── Size: 100MB
└── Data: Settings, feature flags, permissions

Layer 2: Redis Cache (Shared)
├── TTL: 30 minutes
├── Size: 1GB
└── Data: Hot settings, session data, feature flags

Layer 3: PostgreSQL Database
├── TTL: Permanent
└── Data: Source of truth for everything
```

**Redis Usage:**

- **Sessions:** `session:<session_id>` → User session data (TTL: 24 hours)
- **Settings:** `setting:<key>` → Setting value + metadata (TTL: 30 min)
- **Permissions:** `perms:<user_id>` → User permissions (TTL: 1 hour)
- **Feature Flags:** `flags:<user_id>` → User-specific flags (TTL: 30 min)
- **Rate Limits:** `ratelimit:<user_id>:<endpoint>` → Counter (TTL: 1 min)

**Connection Pooling:**

```python
# Redis connection pooled
REDIS_POOL = redis.ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    max_connections=20,
    decode_responses=True
)

# Usage: redis.Redis(connection_pool=REDIS_POOL)
```

---

## 🔐 Part 2: Authentication & Authorization System

### 2.1 User Authentication Flow

```
User enters credentials (Oversight Hub login)
           ↓
POST /api/auth/login { username, password, 2fa_code }
           ↓
Backend validates credentials against users table
           ↓
Generate JWT token + refresh token
           ↓
Store session in Redis (24 hour TTL)
           ↓
Return JWT + refresh token + user profile
           ↓
Frontend stores JWT in localStorage (client-side only)
           ↓
All subsequent requests include Authorization header
           ↓
Middleware validates JWT signature + expiration + revocation status
           ↓
Request proceeds with user context or returns 401/403
```

### 2.2 Role-Based Access Control (RBAC)

**Four-tier permission model:**

```
Level 1: ROLES (what you are)
├── ADMIN - Full system access
├── MANAGER - Can manage settings, view logs
├── OPERATOR - Can view settings, test connections
└── VIEWER - Read-only access

Level 2: PERMISSIONS (what you can do)
├── settings.read
├── settings.write
├── settings.admin
├── audit.read
├── users.manage
└── ...

Level 3: RESOURCE SCOPES (what you can access)
├── settings:ai_models (only AI model settings)
├── settings:integrations (only integration keys)
├── settings:features (only feature flags)
└── *:* (all resources)

Level 4: DATA RESTRICTIONS (additional filters)
├── By organization (future multi-tenant)
├── By environment (staging vs prod)
└── By setting category
```

### 2.3 JWT Token Structure

**Access Token** (15 minutes TTL)

```json
{
  "sub": "user_uuid",
  "username": "admin",
  "email": "admin@gladlabs.io",
  "roles": ["ADMIN"],
  "permissions": ["settings:*", "users:manage", "audit:read"],
  "scopes": ["settings:*", "audit:*"],
  "exp": 1729791234,
  "iat": 1729790334,
  "iss": "GLAD Labs",
  "jti": "unique_token_id"
}
```

**Refresh Token** (7 days TTL, stored in Redis)

```
Type: Stored in Redis with server-side revocation capability
Key: refresh_token:<token_id>
Value: {user_id, issued_at, expires_at}
Revocation: Delete key to logout across all devices
```

### 2.4 Security Features

**Password Hashing:**

```python
# bcrypt with work factor 12
password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
# Verification time: ~100ms per attempt (prevents brute force)
```

**Multi-Factor Authentication (2FA):**

```
Method 1: TOTP (Time-based One-Time Password)
- TOTP codes valid for 30 seconds
- User scans QR code with authenticator app
- Code required on login

Method 2: Email verification (phase 2)
- 6-digit code emailed on login
- Valid for 10 minutes
- Rate limited: 5 attempts per hour
```

**Session Management:**

```python
# Session data in Redis
{
  "user_id": "user_uuid",
  "username": "admin",
  "roles": ["ADMIN"],
  "ip_address": "192.168.1.1",
  "user_agent": "Mozilla/5.0...",
  "created_at": 1729790334,
  "expires_at": 1729876734,
  "is_active": true
}
```

**Rate Limiting:**

```
Login attempts: 5 per minute per IP
API calls: 100 per minute per user (configurable)
Password reset: 1 per hour per email
API key rotation: Once per day per key
```

---

## 💾 Part 3: Database Schema

### 3.1 Users Table

```sql
CREATE TABLE public.users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username VARCHAR(255) UNIQUE NOT NULL,
  email VARCHAR(255) UNIQUE NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  first_name VARCHAR(255),
  last_name VARCHAR(255),

  -- Authentication & Security
  is_active BOOLEAN DEFAULT true,
  is_locked BOOLEAN DEFAULT false,
  failed_login_attempts INT DEFAULT 0,
  locked_until TIMESTAMP,

  -- Multi-Factor Authentication
  totp_secret VARCHAR(255),
  totp_enabled BOOLEAN DEFAULT false,
  backup_codes TEXT[],

  -- Tracking
  last_login TIMESTAMP,
  last_password_change TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  created_by UUID REFERENCES public.users(id),
  updated_by UUID REFERENCES public.users(id),

  -- Metadata
  metadata JSONB DEFAULT '{}',

  CONSTRAINT email_lowercase CHECK (email = LOWER(email)),
  CONSTRAINT username_format CHECK (username ~ '^[a-zA-Z0-9_-]+$')
);

CREATE INDEX idx_users_username ON public.users(username);
CREATE INDEX idx_users_email ON public.users(email);
CREATE INDEX idx_users_is_active ON public.users(is_active);
```

### 3.2 Roles Table

```sql
CREATE TABLE public.roles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) UNIQUE NOT NULL,
  description TEXT,
  is_system_role BOOLEAN DEFAULT false, -- Cannot be deleted
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),

  CONSTRAINT valid_role_name CHECK (name ~ '^[A-Z_]+$')
);

INSERT INTO public.roles (name, description, is_system_role) VALUES
('ADMIN', 'Full system access - use with caution', true),
('MANAGER', 'Can manage settings and view audit logs', false),
('OPERATOR', 'Can view and test settings', false),
('VIEWER', 'Read-only access', false);
```

### 3.3 Permissions Table

```sql
CREATE TABLE public.permissions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource VARCHAR(100) NOT NULL, -- 'settings', 'users', 'audit'
  action VARCHAR(100) NOT NULL, -- 'read', 'write', 'delete', 'admin'
  description TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),

  CONSTRAINT valid_action CHECK (action IN ('read', 'write', 'delete', 'admin')),
  UNIQUE(resource, action)
);

INSERT INTO public.permissions (resource, action, description) VALUES
('settings', 'read', 'View settings'),
('settings', 'write', 'Create/update settings'),
('settings', 'delete', 'Delete settings'),
('settings', 'admin', 'Manage all settings'),
('users', 'read', 'View users'),
('users', 'write', 'Create/update users'),
('users', 'delete', 'Delete users'),
('users', 'admin', 'Manage all users'),
('audit', 'read', 'View audit logs'),
('audit', 'write', 'Write audit logs');
```

### 3.4 Role-Permission Mapping

```sql
CREATE TABLE public.role_permissions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  role_id UUID NOT NULL REFERENCES public.roles(id) ON DELETE CASCADE,
  permission_id UUID NOT NULL REFERENCES public.permissions(id) ON DELETE CASCADE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),

  UNIQUE(role_id, permission_id)
);

-- Seed role permissions
INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM public.roles r, public.permissions p
WHERE r.name = 'ADMIN'; -- ADMIN gets all permissions

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM public.roles r, public.permissions p
WHERE r.name = 'MANAGER' AND p.resource IN ('settings', 'audit', 'users')
  AND p.action IN ('read', 'write');
```

### 3.5 User-Role Mapping

```sql
CREATE TABLE public.user_roles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  role_id UUID NOT NULL REFERENCES public.roles(id) ON DELETE CASCADE,
  assigned_at TIMESTAMP NOT NULL DEFAULT NOW(),
  assigned_by UUID REFERENCES public.users(id),

  UNIQUE(user_id, role_id)
);

CREATE INDEX idx_user_roles_user_id ON public.user_roles(user_id);
```

### 3.6 Settings Table

```sql
CREATE TABLE public.settings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Identification
  key VARCHAR(255) UNIQUE NOT NULL,
  category VARCHAR(100) NOT NULL, -- 'ai_models', 'integrations', 'features'
  display_name VARCHAR(255),
  description TEXT,

  -- Value & Type
  value TEXT, -- Encrypted if is_encrypted=true
  value_type VARCHAR(50) NOT NULL DEFAULT 'string', -- 'string', 'number', 'boolean', 'json'

  -- Security
  is_encrypted BOOLEAN DEFAULT false,
  is_sensitive BOOLEAN DEFAULT false, -- Masked in UI
  is_secret BOOLEAN DEFAULT false, -- Never logged or audited in plaintext

  -- Validation
  validation_rule JSONB DEFAULT '{}', -- {"type": "email", "min": 1, "max": 100, etc}
  allowed_values TEXT[], -- For enum-type settings

  -- Metadata
  is_active BOOLEAN DEFAULT true,
  requires_restart BOOLEAN DEFAULT false,
  requires_deployment BOOLEAN DEFAULT false,

  -- Versioning
  version INT DEFAULT 1,

  -- Tracking
  modified_by UUID REFERENCES public.users(id),
  modified_at TIMESTAMP NOT NULL DEFAULT NOW(),
  created_by UUID REFERENCES public.users(id),
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),

  -- Scoping (for future multi-environment)
  environment VARCHAR(50) DEFAULT 'production', -- 'development', 'staging', 'production'

  CONSTRAINT valid_category CHECK (category IN ('ai_models', 'integrations', 'features', 'system', 'security', 'performance')),
  CONSTRAINT valid_value_type CHECK (value_type IN ('string', 'number', 'boolean', 'json', 'secret')),
  CONSTRAINT valid_environment CHECK (environment IN ('development', 'staging', 'production'))
);

CREATE INDEX idx_settings_key ON public.settings(key);
CREATE INDEX idx_settings_category ON public.settings(category);
CREATE INDEX idx_settings_environment ON public.settings(environment);
CREATE INDEX idx_settings_modified_at ON public.settings(modified_at DESC);
```

### 3.7 Settings Audit Log Table

```sql
CREATE TABLE public.settings_audit_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Reference
  setting_id UUID NOT NULL REFERENCES public.settings(id) ON DELETE RESTRICT,

  -- Change Details
  old_value TEXT, -- Encrypted if was_encrypted=true
  new_value TEXT, -- Encrypted if is_encrypted=true
  change_description TEXT,

  -- Metadata
  was_encrypted BOOLEAN,
  is_encrypted BOOLEAN,

  -- Audit Trail
  changed_by UUID NOT NULL REFERENCES public.users(id),
  changed_at TIMESTAMP NOT NULL DEFAULT NOW(),

  -- Request Context
  ip_address INET,
  user_agent TEXT,
  request_id VARCHAR(255),

  -- Recovery
  change_reason TEXT,
  can_rollback BOOLEAN DEFAULT true,

  CONSTRAINT audit_immutable CHECK (changed_at <= NOW())
);

CREATE INDEX idx_audit_setting_id ON public.settings_audit_log(setting_id);
CREATE INDEX idx_audit_changed_by ON public.settings_audit_log(changed_by);
CREATE INDEX idx_audit_changed_at ON public.settings_audit_log(changed_at DESC);
CREATE INDEX idx_audit_setting_changed ON public.settings_audit_log(setting_id, changed_at DESC);

-- Create immutable audit log (append-only)
ALTER TABLE public.settings_audit_log SET (fillfactor=90);
```

### 3.8 Sessions Table

```sql
CREATE TABLE public.sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Reference
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,

  -- Session Data
  token_jti VARCHAR(255) UNIQUE NOT NULL, -- JWT Token ID for revocation
  refresh_token_jti VARCHAR(255) UNIQUE,

  -- Device & Location
  ip_address INET,
  user_agent TEXT,
  device_name VARCHAR(255),

  -- Status
  is_active BOOLEAN DEFAULT true,

  -- Timestamps
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMP NOT NULL,
  last_activity TIMESTAMP NOT NULL DEFAULT NOW(),
  revoked_at TIMESTAMP,

  CONSTRAINT session_validity CHECK (created_at <= expires_at),
  CONSTRAINT revocation_logic CHECK (
    (is_active AND revoked_at IS NULL) OR (NOT is_active AND revoked_at IS NOT NULL)
  )
);

CREATE INDEX idx_sessions_user_id ON public.sessions(user_id);
CREATE INDEX idx_sessions_token_jti ON public.sessions(token_jti);
CREATE INDEX idx_sessions_is_active ON public.sessions(is_active);
CREATE INDEX idx_sessions_expires_at ON public.sessions(expires_at);

-- Cleanup old sessions (run daily)
CREATE MATERIALIZED VIEW stale_sessions AS
SELECT id FROM public.sessions
WHERE (is_active = false AND revoked_at < NOW() - INTERVAL '30 days')
   OR (expires_at < NOW());
```

### 3.9 Feature Flags Table

```sql
CREATE TABLE public.feature_flags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Identification
  flag_name VARCHAR(255) UNIQUE NOT NULL,
  description TEXT,

  -- Status
  is_enabled BOOLEAN DEFAULT false,
  percentage INT DEFAULT 0, -- 0-100: percentage of users with this flag

  -- Targeting
  target_users TEXT[], -- UUID list
  target_roles VARCHAR(255)[], -- ADMIN, MANAGER, etc

  -- Metadata
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  created_by UUID REFERENCES public.users(id),
  updated_by UUID REFERENCES public.users(id),

  CONSTRAINT valid_percentage CHECK (percentage >= 0 AND percentage <= 100)
);

CREATE INDEX idx_flags_name ON public.feature_flags(flag_name);
CREATE INDEX idx_flags_enabled ON public.feature_flags(is_enabled);
```

### 3.10 API Keys Table

```sql
CREATE TABLE public.api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Identification
  name VARCHAR(255),
  key_hash VARCHAR(255) NOT NULL UNIQUE,
  key_prefix VARCHAR(10) NOT NULL, -- First 10 chars for display

  -- Ownership
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,

  -- Permissions
  permissions TEXT[], -- ['settings:read', 'settings:write', etc]
  allowed_ips INET[],

  -- Status
  is_active BOOLEAN DEFAULT true,

  -- Limits
  rate_limit_per_hour INT DEFAULT 1000,

  -- Tracking
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMP,
  expires_at TIMESTAMP,
  revoked_at TIMESTAMP,

  CONSTRAINT key_not_stored CHECK (key_hash IS NOT NULL AND LENGTH(key_hash) = 64)
);

CREATE INDEX idx_api_keys_key_hash ON public.api_keys(key_hash);
CREATE INDEX idx_api_keys_user_id ON public.api_keys(user_id);
CREATE INDEX idx_api_keys_is_active ON public.api_keys(is_active);
```

---

## 🔌 Part 4: Backend API Design

### 4.1 Authentication Endpoints

#### POST /api/auth/register (Admin Only)

```json
REQUEST:
{
  "username": "john.doe",
  "email": "john@gladlabs.io",
  "password": "SecurePassword123!",
  "first_name": "John",
  "last_name": "Doe",
  "roles": ["OPERATOR"]
}

RESPONSE 201:
{
  "user_id": "uuid",
  "username": "john.doe",
  "email": "john@gladlabs.io",
  "roles": ["OPERATOR"],
  "created_at": "2025-10-24T12:00:00Z"
}
```

#### POST /api/auth/login

```json
REQUEST:
{
  "username": "admin",
  "password": "AdminPassword123!",
  "totp_code": "123456"
}

RESPONSE 200:
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "ref_token_xxx",
  "token_type": "Bearer",
  "expires_in": 900,
  "user": {
    "id": "user_uuid",
    "username": "admin",
    "email": "admin@gladlabs.io",
    "roles": ["ADMIN"],
    "permissions": ["settings:*", "users:*"]
  }
}
```

#### POST /api/auth/refresh

```json
REQUEST:
{
  "refresh_token": "ref_token_xxx"
}

RESPONSE 200:
{
  "access_token": "new_jwt_token",
  "expires_in": 900
}
```

#### POST /api/auth/logout

```json
REQUEST: (Header: Authorization: Bearer token)

RESPONSE 200:
{
  "message": "Logged out successfully"
}
```

#### GET /api/auth/me

```json
REQUEST: (Header: Authorization: Bearer token)

RESPONSE 200:
{
  "id": "user_uuid",
  "username": "admin",
  "email": "admin@gladlabs.io",
  "roles": ["ADMIN"],
  "permissions": ["settings:*", "users:*"],
  "last_login": "2025-10-24T12:00:00Z",
  "totp_enabled": true,
  "active_sessions": 1
}
```

### 4.2 Settings Endpoints

#### GET /api/settings

Query Parameters: `?category=ai_models&is_encrypted=false&limit=50&offset=0`

```json
RESPONSE 200:
{
  "data": [
    {
      "id": "setting_uuid",
      "key": "OPENAI_API_KEY",
      "category": "ai_models",
      "display_name": "OpenAI API Key",
      "value_type": "secret",
      "is_encrypted": true,
      "is_sensitive": true,
      "is_active": true,
      "modified_at": "2025-10-24T12:00:00Z",
      "modified_by": "admin"
    }
  ],
  "pagination": {
    "total": 82,
    "limit": 50,
    "offset": 0,
    "pages": 2
  }
}
```

#### GET /api/settings/{key}

```json
RESPONSE 200:
{
  "id": "setting_uuid",
  "key": "OPENAI_API_KEY",
  "category": "ai_models",
  "display_name": "OpenAI API Key",
  "description": "API key for OpenAI GPT-4 access",
  "value": "***REDACTED***",
  "value_type": "secret",
  "is_encrypted": true,
  "is_sensitive": true,
  "validation_rule": {
    "type": "string",
    "min_length": 20,
    "pattern": "^sk-"
  },
  "is_active": true,
  "modified_at": "2025-10-24T12:00:00Z",
  "modified_by": "admin@gladlabs.io",
  "created_at": "2025-10-01T08:00:00Z",
  "created_by": "system"
}
```

#### PUT /api/settings/{key}

```json
REQUEST:
{
  "value": "sk-new-key-here",
  "change_reason": "Rotated key for security"
}

RESPONSE 200:
{
  "id": "setting_uuid",
  "key": "OPENAI_API_KEY",
  "value": "***REDACTED***",
  "version": 42,
  "modified_at": "2025-10-24T13:00:00Z",
  "modified_by": "admin@gladlabs.io",
  "requires_deployment": false,
  "audit_id": "audit_uuid"
}
```

#### POST /api/settings/{key}/test

```json
REQUEST:
{
  "test_type": "connection"
}

RESPONSE 200:
{
  "success": true,
  "message": "OpenAI API connection successful",
  "response_time_ms": 245,
  "details": {
    "model": "gpt-4",
    "available_models": ["gpt-4", "gpt-3.5-turbo"]
  }
}
```

#### POST /api/settings

```json
REQUEST:
{
  "key": "NEW_SETTING",
  "category": "features",
  "display_name": "New Feature Flag",
  "value": "true",
  "value_type": "boolean",
  "validation_rule": {},
  "change_reason": "Adding new feature flag"
}

RESPONSE 201:
{
  "id": "new_setting_uuid",
  "key": "NEW_SETTING",
  "created_at": "2025-10-24T13:00:00Z"
}
```

#### DELETE /api/settings/{key}

```json
REQUEST:
{
  "change_reason": "Deprecated setting"
}

RESPONSE 200:
{
  "message": "Setting marked as inactive"
}
```

#### GET /api/settings/{key}/history

```json
RESPONSE 200:
{
  "data": [
    {
      "audit_id": "audit_uuid",
      "version": 42,
      "old_value": "***REDACTED***",
      "new_value": "***REDACTED***",
      "changed_by": "admin@gladlabs.io",
      "changed_at": "2025-10-24T13:00:00Z",
      "change_reason": "Rotated key for security",
      "can_rollback": true
    }
  ],
  "pagination": {...}
}
```

### 4.3 Audit Log Endpoints

#### GET /api/audit-logs

Query Parameters: `?user_id=uuid&resource=settings&limit=100&offset=0&start_date=2025-10-01&end_date=2025-10-24`

```json
RESPONSE 200:
{
  "data": [
    {
      "id": "audit_uuid",
      "setting_id": "setting_uuid",
      "setting_key": "OPENAI_API_KEY",
      "old_value": "***REDACTED***",
      "new_value": "***REDACTED***",
      "changed_by": "admin@gladlabs.io",
      "changed_at": "2025-10-24T13:00:00Z",
      "ip_address": "192.168.1.100",
      "user_agent": "Mozilla/5.0...",
      "change_reason": "Rotated key for security"
    }
  ],
  "pagination": {...}
}
```

#### POST /api/audit-logs/export

```json
REQUEST:
{
  "format": "csv",
  "filters": {
    "start_date": "2025-10-01",
    "end_date": "2025-10-24",
    "user_id": null
  }
}

RESPONSE 200:
{
  "download_url": "https://api.example.com/exports/audit-2025-10-24.csv",
  "expires_at": "2025-10-25T12:00:00Z"
}
```

### 4.4 Users Endpoints (Admin Only)

#### GET /api/users

```json
RESPONSE 200:
{
  "data": [
    {
      "id": "user_uuid",
      "username": "admin",
      "email": "admin@gladlabs.io",
      "roles": ["ADMIN"],
      "is_active": true,
      "last_login": "2025-10-24T12:00:00Z",
      "totp_enabled": true,
      "created_at": "2025-10-01T08:00:00Z"
    }
  ],
  "pagination": {...}
}
```

#### POST /api/users/{user_id}/roles

```json
REQUEST:
{
  "role_id": "role_uuid"
}

RESPONSE 200:
{
  "message": "Role assigned",
  "user_roles": ["ADMIN", "MANAGER"]
}
```

---

## 💻 Part 5: Frontend Implementation

### 5.1 Oversight Hub Settings Page Structure

```
┌─────────────────────────────────────────────────────────────┐
│ Settings Management                                          │
├─────────────────────────────────────────────────────────────┤
│
│  [Tab] AI Models    [Tab] Integrations  [Tab] Features  ...
│
│  AI Models Tab:
│  ┌────────────────────────────────────────────────────────┐
│  │ OpenAI API Key                                         │
│  │ Status: ✅ Connected (5 mins ago)                     │
│  │ │••••••••••••••••••••••• sk-abc123 │                  │
│  │ [Test Connection] [Rotate Key] [View History]        │
│  │                                                        │
│  │ Anthropic API Key                                      │
│  │ Status: ✅ Connected                                  │
│  │ │••••••••••••••••••••••• sk-ant-xxx │                │
│  │ [Test Connection] [Rotate Key] [View History]        │
│  │                                                        │
│  │ Google Gemini API Key                                  │
│  │ Status: ⚠️ Not Configured                             │
│  │ [Configure] [Test Connection]                         │
│  └────────────────────────────────────────────────────────┘
│
│  [Tab] Audit Logs
│  ┌────────────────────────────────────────────────────────┐
│  │ Filter: [User ▼] [Resource ▼] [Date Range ▼] [Search] │
│  │                                                        │
│  │ admin@gladlabs.io changed OPENAI_API_KEY              │
│  │ Oct 24, 2025 1:00 PM | 192.168.1.100                 │
│  │ Reason: Rotated key for security                      │
│  │ [View Details] [Rollback]                             │
│  │                                                        │
│  │ (more audit logs...)                                  │
│  └────────────────────────────────────────────────────────┘
│
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Login Page

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│           GLAD Labs Oversight Hub                       │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │                                                  │  │
│  │  Username: [________________]                   │  │
│  │  Password: [________________] [Show/Hide]       │  │
│  │                                                  │  │
│  │  [ ] Remember me      [Forgot password?]       │  │
│  │                                                  │  │
│  │  [       Sign In       ]                        │  │
│  │                                                  │  │
│  │  Don't have an account? Contact Administrator  │  │
│  │                                                  │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  If 2FA Enabled:                                       │
│  ┌──────────────────────────────────────────────────┐  │
│  │  Two-Factor Authentication                       │  │
│  │  Enter code from your authenticator app:        │  │
│  │  [______] [______] [______] [______]            │  │
│  │  [       Verify      ]                          │  │
│  │  [Can't access your app?]                       │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 5.3 Settings Components (React)

```typescript
// src/pages/Settings.tsx
import React, { useState, useEffect } from 'react';
import { useAuth } from '../hooks/useAuth';
import SettingsList from '../components/settings/SettingsList';
import SettingsForm from '../components/settings/SettingsForm';
import AuditLog from '../components/settings/AuditLog';

export default function Settings() {
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState('ai_models');
  const [settings, setSettings] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchSettings();
  }, [activeTab]);

  const fetchSettings = async () => {
    setLoading(true);
    try {
      const response = await fetch(`/api/settings?category=${activeTab}`, {
        headers: {
          Authorization: `Bearer ${localStorage.getItem('access_token')}`
        }
      });
      const data = await response.json();
      setSettings(data.data);
    } catch (error) {
      console.error('Failed to fetch settings:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateSetting = async (key, newValue) => {
    try {
      const response = await fetch(`/api/settings/${key}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('access_token')}`
        },
        body: JSON.stringify({
          value: newValue,
          change_reason: 'Updated via UI'
        })
      });
      if (response.ok) {
        await fetchSettings();
      }
    } catch (error) {
      console.error('Failed to update setting:', error);
    }
  };

  return (
    <div className="settings-container">
      <h1>Settings Management</h1>

      <div className="tabs">
        {['ai_models', 'integrations', 'features', 'security', 'performance'].map(tab => (
          <button
            key={tab}
            className={activeTab === tab ? 'tab active' : 'tab'}
            onClick={() => setActiveTab(tab)}
          >
            {tab.replace('_', ' ').toUpperCase()}
          </button>
        ))}
      </div>

      <SettingsList
        settings={settings}
        loading={loading}
        onUpdate={handleUpdateSetting}
        canEdit={user?.permissions?.includes('settings:write')}
      />

      <AuditLog category={activeTab} />
    </div>
  );
}
```

```typescript
// src/pages/Login.tsx
import React, { useState } from 'react';
import { useAuth } from '../hooks/useAuth';

export default function Login() {
  const { login, error } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [totp, setTotp] = useState('');
  const [step, setStep] = useState('credentials'); // credentials, totp

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (step === 'credentials') {
      // First, try login without TOTP
      const result = await login(username, password);
      if (result.requires_totp) {
        setStep('totp');
      }
    } else {
      // Login with TOTP
      await login(username, password, totp);
    }
  };

  return (
    <div className="login-container">
      <div className="login-box">
        <h1>GLAD Labs Oversight Hub</h1>

        {error && <div className="error">{error}</div>}

        <form onSubmit={handleSubmit}>
          {step === 'credentials' ? (
            <>
              <input
                type="text"
                placeholder="Username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
              <input
                type="password"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </>
          ) : (
            <>
              <label>Enter the 6-digit code from your authenticator app:</label>
              <input
                type="text"
                placeholder="000000"
                value={totp}
                onChange={(e) => setTotp(e.target.value)}
                maxLength="6"
              />
            </>
          )}

          <button type="submit">
            {step === 'credentials' ? 'Sign In' : 'Verify'}
          </button>
        </form>
      </div>
    </div>
  );
}
```

---

## 🗄️ Part 6: Encryption Strategy

### 6.1 AES-256-GCM Encryption

**Sensitive fields encrypted:**

- API Keys (OpenAI, Anthropic, Google, etc.)
- Database passwords
- JWT signing keys
- Integration credentials
- User TOTP secrets

**Encryption implementation:**

```python
# src/cofounder_agent/services/encryption.py
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
import os
import base64
import json
from typing import Tuple

class SettingsEncryption:
    """AES-256-GCM encryption for sensitive settings"""

    def __init__(self, master_key: str = None):
        """
        Initialize encryption with master key.
        Master key should be stored in .env or Kubernetes secret.
        """
        self.master_key = master_key or os.getenv('ENCRYPTION_MASTER_KEY')
        if not self.master_key:
            raise ValueError("ENCRYPTION_MASTER_KEY not configured")

        # Derive encryption key from master key
        self.key = self._derive_key(self.master_key)

    def _derive_key(self, master_key: str) -> bytes:
        """Derive AES-256 key from master key using PBKDF2"""
        salt = b'glad_labs_settings_v1'  # Fixed salt for deterministic derivation
        kdf = PBKDF2(
            algorithm=hashes.SHA256(),
            length=32,  # 256 bits for AES-256
            salt=salt,
            iterations=100_000,
            backend=None
        )
        return kdf.derive(master_key.encode())

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext and return base64-encoded ciphertext with nonce
        Format: base64(nonce || ciphertext || tag)
        """
        nonce = os.urandom(12)  # 96-bit nonce for GCM
        cipher = AESGCM(self.key)
        ciphertext = cipher.encrypt(nonce, plaintext.encode(), None)

        # Format: nonce + ciphertext (includes auth tag)
        encrypted = nonce + ciphertext
        return base64.b64encode(encrypted).decode('utf-8')

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt base64-encoded ciphertext
        Returns plaintext or raises exception if authentication fails
        """
        try:
            encrypted = base64.b64decode(ciphertext)
            nonce = encrypted[:12]
            ciphertext_data = encrypted[12:]

            cipher = AESGCM(self.key)
            plaintext = cipher.decrypt(nonce, ciphertext_data, None)
            return plaintext.decode('utf-8')
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")

    def encrypt_dict(self, data: dict, keys_to_encrypt: list) -> dict:
        """Encrypt specific keys in a dictionary"""
        result = data.copy()
        for key in keys_to_encrypt:
            if key in result:
                result[key] = self.encrypt(str(result[key]))
        return result

    def decrypt_dict(self, data: dict, keys_to_decrypt: list) -> dict:
        """Decrypt specific keys in a dictionary"""
        result = data.copy()
        for key in keys_to_decrypt:
            if key in result:
                result[key] = self.decrypt(result[key])
        return result
```

### 6.2 Key Management

**Master Key Storage:**

```bash
# NEVER commit this key
# Option 1: Environment variable in Kubernetes secret
ENCRYPTION_MASTER_KEY=<64-character-hex-string>

# Option 2: AWS Secrets Manager
aws secretsmanager get-secret-value --secret-id encryption-master-key

# Option 3: HashiCorp Vault
vault kv get secret/encryption-master-key

# Key rotation policy:
# - Rotate every 90 days
# - Keep previous 3 keys for decryption
# - Test rotation with staging data first
```

**Database Column Encryption:**

```sql
-- Settings values are stored encrypted
UPDATE public.settings
SET value = pgcrypto.pgp_sym_encrypt(
  value,
  'master_key'
)
WHERE is_encrypted = true;

-- Query decrypted values
SELECT
  key,
  pgcrypto.pgp_sym_decrypt(value::bytea, 'master_key')::text as decrypted_value
FROM public.settings
WHERE is_encrypted = true;
```

---

## 🚀 Part 7: Implementation Roadmap

### Phase 1: Database & Authentication (Week 1)

**Sprint 1.1: Database Setup**

- [ ] Create PostgreSQL schema (all 10 tables)
- [ ] Create migration scripts
- [ ] Set up connection pooling (pgBouncer)
- [ ] Configure Redis
- [ ] Backup/restore procedures
- **Time:** 8 hours

**Sprint 1.2: Authentication Backend**

- [ ] Implement JWT token generation
- [ ] Implement bcrypt password hashing
- [ ] Create /api/auth/\* endpoints
- [ ] Implement session management
- [ ] Add rate limiting middleware
- **Time:** 12 hours

**Total Phase 1:** 20 hours

### Phase 2: Settings API & Encryption (Week 2)

**Sprint 2.1: Encryption Layer**

- [ ] Implement AES-256-GCM encryption
- [ ] Create encryption service
- [ ] Implement key derivation
- [ ] Test encryption/decryption
- **Time:** 6 hours

**Sprint 2.2: Settings API**

- [ ] Create /api/settings/\* endpoints
- [ ] Implement settings CRUD
- [ ] Add validation layer
- [ ] Create audit logging
- [ ] Add connection testing
- **Time:** 16 hours

**Total Phase 2:** 22 hours

### Phase 3: Frontend Components (Week 3)

**Sprint 3.1: Auth UI**

- [ ] Create Login page
- [ ] Implement 2FA UI
- [ ] Add session management
- [ ] Create logout functionality
- **Time:** 10 hours

**Sprint 3.2: Settings UI**

- [ ] Create Settings page with tabs
- [ ] Implement settings list component
- [ ] Create settings form component
- [ ] Add validation feedback
- [ ] Create audit log viewer
- **Time:** 14 hours

**Total Phase 3:** 24 hours

### Phase 4: Testing & Deployment (Week 4)

**Sprint 4.1: Testing**

- [ ] Unit tests (backend & frontend)
- [ ] Integration tests
- [ ] Security testing
- [ ] Performance testing
- [ ] Load testing
- **Time:** 16 hours

**Sprint 4.2: Deployment**

- [ ] Deploy to staging
- [ ] Production readiness checklist
- [ ] Deploy to production
- [ ] Monitor for issues
- [ ] Documentation
- **Time:** 12 hours

**Total Phase 4:** 28 hours

**Grand Total: ~95 hours** (roughly 2.5-3 weeks with team)

---

## 💡 Part 8: Operational Considerations

### 8.1 High Availability & Disaster Recovery

```
┌─────────────────────────────────────────────────────┐
│ Production Environment                              │
├─────────────────────────────────────────────────────┤
│                                                     │
│ PostgreSQL (Primary - glad_labs_production)        │
│ ├─ Connection Pool (pgBouncer): 100 connections   │
│ ├─ Backup: Daily + hourly snapshots               │
│ ├─ Replication: Hot standby (read-only)          │
│ └─ Failover: Automatic (via Railway)             │
│                                                     │
│ Redis Cluster                                       │
│ ├─ Nodes: 3 (master + 2 replicas)                 │
│ ├─ Persistence: AOF (Append-Only File)            │
│ ├─ TTL policies: Automatic key expiration         │
│ └─ Backup: Hourly to S3                           │
│                                                     │
│ Application Servers                                 │
│ ├─ Instances: 2-3 (auto-scaling based on load)   │
│ ├─ Health checks: Every 30 seconds                │
│ ├─ Circuit breakers: Database connection failures │
│ └─ Fallback: Cached settings for short outages    │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### 8.2 Monitoring & Alerting

**Metrics to track:**

```yaml
Database:
  - Connection pool utilization
  - Query performance (p95, p99)
  - Transaction rollback rate
  - Replication lag

Redis:
  - Memory usage
  - Hit/miss ratio
  - Commands per second
  - Eviction rate

Application:
  - Login success/failure rate
  - API response times
  - Error rate by endpoint
  - Settings change frequency

Security:
  - Failed login attempts
  - Brute force attempts (rate limited)
  - Unusual access patterns
  - Permission check failures
```

**Alert thresholds:**

```
Response time > 2s: Warning
Response time > 5s: Critical
Error rate > 1%: Warning
Error rate > 5%: Critical
Database connection pool > 90%: Warning
Redis memory > 80%: Warning
Failed logins > 10 in 5 min: Alert & review
```

### 8.3 Backup & Recovery

```bash
# Daily backup schedule
0 2 * * * pg_dump $DATABASE_URL | gzip | aws s3 cp - s3://backups/db-daily-$(date +%Y-%m-%d).sql.gz

# Weekly full backup
0 3 * * 0 pg_dump $DATABASE_URL | tar czf - | aws s3 cp - s3://backups/db-weekly-$(date +%Y-W%U).tar.gz

# Recovery test monthly
0 4 1 * * /scripts/test-backup-recovery.sh

# Point-in-time recovery capability
# PostgreSQL WAL archiving to S3
archive_command = 'aws s3 cp %p s3://wal-archives/%f'
```

---

## 📋 Part 9: Security Best Practices

### 9.1 OWASP Top 10 Mitigations

| Vulnerability                     | Mitigation                                                      |
| --------------------------------- | --------------------------------------------------------------- |
| Injection                         | Parameterized queries, ORM usage, input validation              |
| Authentication                    | JWT + 2FA, strong password policy, rate limiting                |
| Sensitive Data                    | AES-256 encryption, HTTPS only, no logging of secrets           |
| XML/XXE                           | XML parsers disabled/hardened                                   |
| Broken Access Control             | RBAC model, permission checks on every endpoint                 |
| Security Misconfiguration         | IaC (Infrastructure as Code), security headers, CORS restricted |
| XSS                               | React's built-in XSS protection, CSP headers, sanitization      |
| Insecure Deserialization          | JSON only, no pickle/yaml in user input                         |
| Using Components with Known Vulns | Automated scanning, dependency updates                          |
| Insufficient Logging              | Comprehensive audit logs, monitoring, alerting                  |

### 9.2 Data Classification

```
Level 1: PUBLIC
├─ Feature flags status
├─ Public documentation
└─ Non-sensitive UI content

Level 2: INTERNAL
├─ Settings metadata (not values)
├─ User profiles (non-sensitive fields)
└─ API response structure

Level 3: CONFIDENTIAL
├─ API Keys (all providers)
├─ Database credentials
├─ JWT signing keys
└─ User TOTP secrets

Level 4: RESTRICTED (Highly Sensitive)
├─ Master encryption key
├─ Production credentials
├─ Audit logs of sensitive changes
└─ Backup encryption keys
```

---

## 🎯 Part 10: Decision Points for Review

### 10.1 Database Architecture

**Decision:** Single PostgreSQL instance (shared with Strapi) vs separate instance

**Recommendation:** ✅ **Single instance** (Option A)

- Lower cost
- Simpler to maintain
- Proper schema isolation prevents conflicts
- Both databases backed up together

**Required Implementation:**

```sql
-- Schema isolation
CREATE SCHEMA app_settings;
CREATE SCHEMA app_auth;

-- All tables in app_settings schema
CREATE TABLE app_settings.users (...)
CREATE TABLE app_settings.settings (...)
CREATE TABLE app_settings.audit_log (...)

-- Grant permissions
GRANT USAGE ON SCHEMA app_settings TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app_settings TO app_user;
```

### 10.2 Encryption Strategy

**Decision:** How to store and manage the master encryption key?

**Options:**

```
Option 1: Environment Variable (Simplest)
PRO: Easy to implement, fast
CON: Exposed if .env leaked
RECOMMENDATION: ✅ For staging

Option 2: Kubernetes Secret (Recommended)
PRO: Standard practice, secure, audited
CON: Requires K8s deployment
RECOMMENDATION: ✅ For production

Option 3: HashiCorp Vault (Enterprise)
PRO: Centralized secret management, rotation
CON: Added infrastructure complexity
RECOMMENDATION: ⏳ Phase 2 upgrade
```

### 10.3 2FA Implementation

**Decision:** TOTP only vs TOTP + Email

**Recommended:** ✅ TOTP in Phase 1, add Email in Phase 2

- TOTP: App-based (Google Authenticator, Authy)
- Email: Backup method, requires email service setup

### 10.4 Redis Usage

**Decision:** Use Redis for sessions/cache?

**Recommendation:** ✅ YES

- Session storage (faster than database)
- Settings cache (reduce database load)
- Rate limiting counters (atomic operations)
- Feature flag evaluation cache (reduce latency)

**Estimated Redis Usage:** 100MB-500MB (depending on user count)

### 10.5 Backward Compatibility

**Decision:** Support existing authentication methods during transition?

**Recommendation:** ✅ Yes, gradual migration

- Week 1-2: New auth system running parallel
- Week 3: Default to new system, keep old for fallback
- Week 4: Deprecate old system

---

## ✅ Verification Checklist

Before implementation, verify:

- [ ] Database access configured for staging PostgreSQL
- [ ] Redis instance available and accessible
- [ ] Environment variables can be added to Railway
- [ ] Team members available for code review
- [ ] Testing infrastructure in place
- [ ] Monitoring/alerting dashboard ready
- [ ] Backup procedures documented and tested
- [ ] Security policy reviewed and approved
- [ ] Encryption keys generation procedure documented
- [ ] Compliance requirements identified (GDPR, HIPAA, etc)

---

## 📞 Next Steps

1. **Review this plan** - Technical team review
2. **Approve database strategy** - Decision on single vs dual instance
3. **Confirm encryption approach** - Environment variable vs Kubernetes secret
4. **Begin Phase 1** - Database setup and authentication
5. **Weekly sprints** - Complete roadmap in 4 weeks

---

## 📚 Appendices

### A. Environment Variables Required

```bash
# Encryption
ENCRYPTION_MASTER_KEY=<64-character-hex-string>

# Redis
REDIS_HOST=redis.railway.app
REDIS_PORT=6379
REDIS_PASSWORD=<redis-password>

# Database (uses existing Strapi connection)
DATABASE_URL=postgresql://${PROD_DB_USER}:${PROD_DB_PASSWORD}@${PROD_DB_HOST}:5432/glad_labs_production

# JWT
JWT_SECRET=<256-character-hex-string>
JWT_ALGORITHM=HS256
JWT_EXPIRATION_SECONDS=900
REFRESH_TOKEN_EXPIRATION_DAYS=7

# 2FA
TOTP_ISSUER=GLAD Labs
TOTP_WINDOW=1 # Allow ±1 time window

# Logging
LOG_LEVEL=INFO
LOG_RETENTION_DAYS=90
```

### B. Python Dependencies

```txt
fastapi==0.104.1
sqlalchemy==2.0.23
psycopg2-binary==2.9.9
pydantic==2.5.0
pydantic-settings==2.1.0
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.6
redis==5.0.1
cryptography==41.0.7
pyotp==2.9.0
structlog==23.2.0
```

### C. TypeScript/React Dependencies

```json
{
  "dependencies": {
    "react": "^18.3.1",
    "react-router-dom": "^6.20.0",
    "axios": "^1.6.2",
    "zustand": "^4.4.0",
    "react-hook-form": "^7.48.0",
    "zod": "^3.22.4",
    "@tanstack/react-query": "^5.26.0",
    "date-fns": "^2.30.0"
  }
}
```

---

**Document Status:** Ready for Review  
**Stakeholder Sign-off Required:** Architecture Team, Security Team, DevOps Team

---

_This implementation plan represents an industry-standard, production-ready Settings Management and Authentication system. All recommendations follow OWASP security practices and cloud-native architecture patterns._
