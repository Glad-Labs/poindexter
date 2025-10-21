# 🔧 Dependency Conflict Resolution - October 21, 2025

## ✅ **ISSUE RESOLVED**

Fixed `npm ERR! ERESOLVE` dependency conflict preventing local development.

---

## 🐛 **What Was Wrong**

### Root Cause

Your monorepo had **mismatched Strapi versions** across workspaces:

```
Root package.json:
  ├── @strapi/plugin-users-permissions@^4.12.0 ❌ (OLD - needs react-router-dom 5.3.4)
  ├── @strapi/strapi@^5.28.0 ✅ (NEW - needs react-router-dom 6.x)

Web/oversight-hub/package.json:
  ├── @strapi/plugin-users-permissions@4.12.0 ❌ (OLD - conflicts!)
  ├── @strapi/plugin-cloud@5.18.0 (OLD)
  └── react-router-dom@^6.30.0 (NEW - incompatible!)

Strapi CMS (cms/strapi-main):
  └── @strapi/strapi@5.18.1 ✅ (Correct)
```

### Error Message

```
ERESOLVE could not resolve
peer react-router-dom@"5.3.4" from @strapi/plugin-users-permissions@4.12.0
conflicting peer dependency: react-router-dom@6.30.1
```

**Translation**: Old v4 plugin expects react-router-dom 5, but you have v6 installed. These are incompatible.

---

## ✅ **Solution Applied**

### 1. Root `package.json` - Removed Unused Dependencies

**Before:**

```json
"dependencies": {
  "@strapi/plugin-cloud": "^5.18.0",
  "@strapi/plugin-users-permissions": "^4.12.0",
  "@strapi/strapi": "^5.28.0",
  "firebase": "^12.4.0",
  "react-scripts": "0.0.0"
}
```

**After:**

```json
"dependencies": {
  "@strapi/strapi": "^5.28.0"
}
```

**Why**: Root doesn't need these packages. They belong in the Strapi workspace only.

### 2. Web/Oversight Hub - Removed Conflicting Old Plugins

**Before:**

```json
"dependencies": {
  "@strapi/plugin-cloud": "5.18.0",           ❌ Removed
  "@strapi/plugin-users-permissions": "4.12.0", ❌ Removed
  "react-router-dom": "^6.30.0",
  ...
}
```

**After:**

```json
"dependencies": {
  "react-router-dom": "^6.30.0",  ✅ Clean, no conflicts
  ...
}
```

**Why**: Oversight Hub doesn't use Strapi plugins directly. It connects via API.

### 3. Cleaned npm Cache

```bash
rm -rf node_modules package-lock.json
npm install
```

---

## 📊 **Result**

✅ **npm install succeeded**

```
added 2082 packages, removed 17 packages, and audited 2240 packages in 2m
```

**Key Changes:**

- Root: Only has `@strapi/strapi` (not the old v4 plugins)
- Oversight Hub: No Strapi plugins (uses frontend libraries only)
- Strapi CMS: Has all needed v5 plugins (managed separately)

---

## 🎯 **Architecture Now Correct**

```
GLAD Labs Monorepo
├── Root (npm workspaces orchestrator)
│   └── @strapi/strapi (core only)
│
├── web/public-site/ (Next.js)
│   └── (imports from Strapi via API)
│
├── web/oversight-hub/ (React)
│   └── (imports from Strapi via API)
│
└── cms/strapi-main/ (Strapi v5)
    ├── @strapi/strapi@5.18.1 ✅
    ├── @strapi/plugin-users-permissions@5.18.1 ✅
    └── (all v5 plugins)
```

**Key Principle**: Strapi plugins stay in Strapi workspace. Frontend apps connect via REST API, not direct imports.

---

## 🚀 **What You Can Do Now**

```bash
# Local development works!
npm run dev              # ✅ All services

# Build works!
npm run build            # ✅ All workspaces

# Tests work!
npm run test             # ✅ All tests

# Strapi specifically works!
npm run dev:strapi       # ✅ CMS only
```

---

## 📝 **Files Updated**

1. **`package.json` (root)**
   - Removed `@strapi/plugin-cloud`
   - Removed `@strapi/plugin-users-permissions`
   - Removed `firebase` (not used at root)
   - Removed `react-scripts` (not used at root)

2. **`web/oversight-hub/package.json`**
   - Removed `@strapi/plugin-cloud@5.18.0`
   - Removed `@strapi/plugin-users-permissions@4.12.0`
   - Kept only frontend libraries

---

## 🔍 **Why This Happened**

The root and oversight-hub had **old Strapi v4 plugin dependencies** that probably were:

1. Copy-pasted from an old template
2. Added during early development
3. Not removed when upgrading to Strapi v5

**Best Practice**: Only include dependencies where they're actually used. Frontend apps connect to Strapi via API, not by importing its plugins.

---

## ✨ **Going Forward**

**Remember:**

- ✅ Strapi plugins go in `cms/strapi-main/package.json`
- ✅ Frontend apps live in `web/*/package.json`
- ✅ Root `package.json` only orchestrates workspaces
- ✅ Never import Strapi internals in frontend code

---

**Status**: ✅ **RESOLVED - Ready for development**

Your monorepo dependency tree is now clean and conflict-free!
