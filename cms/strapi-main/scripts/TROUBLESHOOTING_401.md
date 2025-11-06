# 🔧 Troubleshooting 401 Unauthorized Error

**Problem**: Content type registration fails with `401 Unauthorized - Missing or invalid credentials`

**When**: Running `npm run setup` or `npm run register-types`

**Status**: ✅ SOLVABLE - Three working options below

---

## 🎯 Quick Fix (RECOMMENDED - 3 minutes)

### Option 1: Use Strapi Auto-Discovery (Simplest)

This is the fastest solution - no token needed.

**Steps**:

1. **Open Strapi Admin Panel**

   ```
   http://localhost:1337/admin
   ```

2. **Create Admin Account** (First-time setup)
   - Email: `admin@gladlabs.com`
   - Password: `Admin@123456`
   - Username: `Admin`
   - Click "Let's start"

3. **Check Content Manager**
   - Go to: Content Manager → Collection Types
   - You should see: Post, Category, Tag, Author
   - Go to: Single Types
   - You should see: About, Privacy Policy

4. **If types don't appear**:
   - Restart Strapi: `npm run develop`
   - Go back to Content Manager
   - Types should auto-register from schema files

5. **Seed Sample Data** (Optional)
   ```powershell
   npm run seed
   ```

✅ **Done!** Content types are now registered and ready.

---

## 🔐 Option 2: Programmatic with API Token (For CI/CD)

Use this if you need automated/headless registration.

**Steps**:

1. **Create API Token in Strapi Admin**
   - Go to: `http://localhost:1337/admin`
   - Settings → API Tokens → Create new API Token
   - Name: `Content Type Registration`
   - Type: `Full access`
   - Click "Save"
   - Copy the generated token (looks like `1234567890abcdef...`)

2. **Set Environment Variable (PowerShell)**

   ```powershell
   # In the same terminal window:
   $env:STRAPI_API_TOKEN = "your-token-here"

   # Verify it's set:
   echo $env:STRAPI_API_TOKEN
   ```

   Or permanently (more advanced):

   ```powershell
   [Environment]::SetEnvironmentVariable("STRAPI_API_TOKEN", "your-token-here", "User")
   ```

3. **Run Registration Script**

   ```powershell
   npm run register-types:improved
   ```

4. **Expected Output**

   ```
   ✅ post: Registered successfully
   ✅ category: Registered successfully
   ✅ tag: Registered successfully
   ✅ author: Already registered (skipping)
   ✅ about: Already registered (skipping)
   ✅ privacy-policy: Already registered (skipping)
   ✅ content-metric: Registered successfully

   ✅ CHECK COMPLETE
   Summary:
     Registered: 7
     Skipped/Failed: 0
     Total: 7
   ```

5. **Seed Data**
   ```powershell
   npm run seed
   ```

✅ **Done!** All types programmatically registered.

---

## 🧪 Option 3: Use Improved Script (Hybrid Approach)

This script handles both token and non-token scenarios gracefully.

**Steps**:

1. **Without Token (Try First)**

   ```powershell
   npm run register-types:improved
   ```

   - Script checks if types exist
   - If missing and no token: Suggests next steps
   - No errors, just helpful guidance

2. **With Token (If Needed)**

   ```powershell
   $env:STRAPI_API_TOKEN = "your-token-here"
   npm run register-types:improved
   ```

   - Script registers all missing types
   - Shows which ones already exist
   - Clear success/failure summary

3. **What the Script Does**
   - ✅ Discovers all schemas automatically
   - ✅ Checks if each type already exists
   - ✅ Attempts registration if missing
   - ✅ Gracefully handles 401 errors
   - ✅ Provides helpful next-step guidance

✅ **Done!** Hybrid approach handles both scenarios.

---

## 📋 Understanding the Error

**Error Message**:

```json
{
  "status": 401,
  "name": "UnauthorizedError",
  "message": "Missing or invalid credentials",
  "details": {}
}
```

**Why It Happens**:

- Strapi's Content-Type Builder API requires authentication
- Default token `'test-token-development'` is not valid
- No API token provided in environment variables
- Two solutions: Use auto-discovery OR provide real token

**Why It's Safe**:

- Error is caught gracefully (no crash)
- Script suggests alternatives
- Multiple working solutions available
- Auto-discovery requires no extra setup

---

## ✅ Verification - Check If Registration Worked

### Check 1: Via Strapi Admin Panel

1. Go to: `http://localhost:1337/admin`
2. Content Manager → Collection Types
3. Should see: `post`, `category`, `tag`, `author`, `content-metric`
4. Content Manager → Single Types
5. Should see: `about`, `privacy-policy`

### Check 2: Via API

```powershell
# Get list of content types
curl http://localhost:1337/content-type-builder/content-types -H "Authorization: Bearer STRAPI_API_TOKEN"

# Get posts (if registered correctly)
curl http://localhost:1337/api/posts

# Should return:
# {"data":[],"meta":{"pagination":{"page":1,"pageSize":25,"pageCount":0,"total":0}}}
# (Empty but no 404 error = SUCCESS!)
```

### Check 3: Run Setup and Check Output

```powershell
npm run register-types:improved
```

Look for:

- ✅ Schemas discovered: `Found 7 content type(s)`
- ✅ Registration results: `Registered: 7` or `Already registered`
- ✅ No error messages about 401

---

## 🚨 Still Not Working?

### Symptom: Still getting 401 errors

**Solution**:

1. Make sure token is set: `echo $env:STRAPI_API_TOKEN`
2. If empty, set it again: `$env:STRAPI_API_TOKEN = "token"`
3. Check token is valid:
   - Go to Strapi admin Settings → API Tokens
   - Make sure token hasn't expired
   - Try regenerating if unsure

### Symptom: Types don't appear in Content Manager

**Solution**:

1. Restart Strapi: Stop (Ctrl+C) and run `npm run develop`
2. Wait 10 seconds for startup
3. Go to Content Manager
4. Refresh page (F5)
5. Types should now appear

### Symptom: API still returns 404

**Solution**:

1. First verify types exist in Content Manager (see Check 1 above)
2. If they exist, restart Strapi and try again
3. If they don't exist, ensure registration completed successfully
4. Try Option 1 (auto-discovery) first, then seed data

### Symptom: Seeding fails after registration

**Solution**:

1. Run: `npm run seed`
2. If it fails, make sure ALL content types are registered:
   - `npm run register-types:improved` (check output)
3. Check Strapi logs for other errors
4. Restart Strapi: `npm run develop`
5. Try seeding again: `npm run seed`

---

## 📊 Command Reference

### Without Token (Try First)

```powershell
npm run register-types:improved
```

- No setup needed
- Checks what exists
- Shows what's missing
- Suggests next steps

### With Token (For Automation)

```powershell
$env:STRAPI_API_TOKEN = "your-token-here"
npm run register-types:improved
```

- Registers all missing types
- Skips existing types
- Shows summary

### Quick Help Command

```powershell
npm run fix-401
```

- Displays help text
- Reminders for all steps

### Seed Data After Registration

```powershell
npm run seed
```

- Adds sample content
- Creates posts, categories, tags
- Tests API integration

---

## 🔑 Getting Your API Token

### Step 1: Go to Admin Panel

```
http://localhost:1337/admin
```

### Step 2: Navigate to API Tokens

```
Settings (gear icon) → API Tokens
```

### Step 3: Create New Token

- Click "Create new API Token"
- Name: `Content Type Setup`
- Type: `Full access`
- Description: `For automated content type registration`
- Click "Save"

### Step 4: Copy Token

- Token appears once (copy it immediately!)
- Click copy icon or select and Ctrl+C
- Format: `1234567890abcdef1234567890abcdef...`

### Step 5: Use in Script

```powershell
$env:STRAPI_API_TOKEN = "paste-token-here"
npm run register-types:improved
```

---

## 💡 Pro Tips

### Tip 1: Set Token Permanently (Optional)

```powershell
# Current session only (closes when terminal closes):
$env:STRAPI_API_TOKEN = "token-here"

# Current user permanently (survives terminal restart):
[Environment]::SetEnvironmentVariable("STRAPI_API_TOKEN", "token-here", "User")
```

### Tip 2: Create Multiple Tokens

- One for local dev (less restricted)
- One for CI/CD (full access)
- Rotate tokens monthly

### Tip 3: Reuse Previous Token

- If you created one before, use the same one
- No need to create new token each time
- Just set the environment variable

### Tip 4: Debug Registration

```powershell
# Verbose output:
node scripts/register-content-types-v2.js

# Check Strapi logs:
npm run develop

# Monitor API responses:
# Run in another terminal while registration happens
```

---

## 📞 When All Else Fails

1. **Clear Strapi Cache**

   ```powershell
   rm -r .cache -Force  # Remove cache directory
   npm run develop      # Restart
   ```

2. **Reset Database** (⚠️ LOSES ALL DATA)

   ```powershell
   rm .tmp/data.db -Force  # Remove SQLite database
   npm run develop          # Restart (will recreate)
   ```

3. **Check Node Modules**

   ```powershell
   rm -r node_modules -Force
   npm install
   npm run develop
   ```

4. **View Full Error Logs**
   ```powershell
   npm run develop  # Check terminal output
   # Look for any error messages beyond just 401
   ```

---

## 🎓 Next Steps After Successful Registration

1. **Verify Everything**
   - Check Strapi admin: http://localhost:1337/admin
   - See all content types in Content Manager

2. **Seed Sample Data**

   ```powershell
   npm run seed
   ```

3. **Test API Endpoints**

   ```powershell
   curl http://localhost:1337/api/posts
   curl http://localhost:1337/api/categories
   curl http://localhost:1337/api/tags
   ```

4. **Test Frontend**

   ```powershell
   cd ../../web/public-site
   npm run dev
   # Should display sample posts
   ```

5. **Enjoy!** 🎉
   - Content types registered ✅
   - Data seeded ✅
   - API working ✅
   - Frontend connected ✅

---

**Last Updated**: November 5, 2025  
**Status**: ✅ Three working solutions provided  
**Time to Fix**: 3-10 minutes depending on chosen option
