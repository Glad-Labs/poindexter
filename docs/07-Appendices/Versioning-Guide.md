# 🔄 Branch-Tier Automated Versioning System

**Status:** ✅ Ready for deployment

## Quick Reference

### Automatic Version Bumping

Every git push to any branch automatically increments the version based on branch tier:

| Branch               | Tier            | Version Bump  | Example           |
| -------------------- | --------------- | ------------- | ----------------- |
| `main`               | Production      | Major (X.0.0) | 3.0.2 → **4**.0.0 |
| `staging/*`          | Feature Release | Minor (0.X.0) | 3.0.2 → 3.**1**.0 |
| `dev/*`, `feature/*` | Build           | Patch (0.0.X) | 3.0.2 → 3.0.**3** |

### What Gets Updated

All 6 version files automatically synced:

- ✅ `package.json` (root)
- ✅ `web/public-site/package.json`
- ✅ `web/oversight-hub/package.json`
- ✅ `src/cofounder_agent/package.json`
- ✅ `pyproject.toml` (root)
- ✅ `src/cofounder_agent/pyproject.toml`

---

## Local Testing

### 1. Test Dry-Run (No Changes)

```bash
npm run bump-version:auto -- --dry-run
```

**Expected Output:**

```text
🚀 Glad Labs Automated Version Bump

ℹ️ Current branch: dev
ℹ️ Current version: 3.0.2
ℹ️ Bump type: patch (dev (Development))
✨ New version: 3.0.3
ℹ️ Dry run mode - no changes will be made
```

### 2. Test Actual Version Bump (dev branch)

```bash
# From dev branch:
npm run bump-version:auto

# Verify changes
git diff
git log --oneline -3
git tag -l | tail -5
```

**Expected:**

- Version bumped: 3.0.2 → 3.0.3
- Git commit created: `chore: bump version to 3.0.3 [skip ci]`
- Git tag created: `v3.0.3`
- All 6 files updated

### 3. Test Manual Override

```bash
# Force a specific bump type (e.g., patch from dev):
npm run bump-version:patch

# Or use longer form:
node scripts/bump-version.js -- --patch
```

---

## GitHub Actions Testing

### 1. Push to dev branch

```bash
git checkout dev
echo "test" >> README.md
git add .
git commit -m "test: verify version bump"
git push origin dev
```

**Expected:** GitHub Actions workflow runs, version bumps to 3.0.3, tag created

### 2. Merge to staging

```bash
git checkout staging
git merge dev --no-edit
git push origin staging
```

**Expected:** Version bumps to 3.1.0, tag created

### 3. Merge to main

```bash
git checkout main
git merge staging --no-edit
git push origin main
```

**Expected:**

- Version bumps to 4.0.0
- Tag created: v4.0.0
- GitHub Release created automatically

---

## Emergency Rollback

If something goes wrong, use the rollback workflow:

### Via GitHub UI

1. Go to **Actions** → **Emergency Version Rollback**
2. Click **Run workflow**
3. Enter version to rollback to (e.g., `3.0.2`)
4. Optionally add reason
5. Click **Run workflow**

### Via Command Line (manual)

```bash
# Create rollback commit manually
git reset --soft HEAD~1

# Or fully revert
git revert HEAD
git push
```

---

## Safety Features

✅ **[skip ci] Tag:** Prevents infinite version bump loops  
✅ **Concurrency Control:** Serializes version bumps per-branch  
✅ **Full Verification:** All 6 files checked after update  
✅ **Dry-Run Mode:** Test without committing  
✅ **Git Integration:** Auto-commit with proper messages  
✅ **Tag Management:** Automatic git tags and GitHub releases

---

## Troubleshooting

### Issue: "Branch does not match any tier"

**Check:** Branch name must match one of:

- `main` (exact match) → Major bump
- `staging` or `staging/*` → Minor bump
- `dev` or `dev/*` or `feature/*` → Patch bump

**Fix:**

```bash
# Rename branch to match tier
git branch -m feature/my-feature  # Now matches dev tier

# Or force version bump manually
npm run bump-version:patch
```

### Issue: "Could not detect git branch"

**Check:** You're in a git repository with a valid branch

**Fix:**

```bash
git status  # Verify you're in a repo
git rev-parse --abbrev-ref HEAD  # Check current branch
```

### Issue: Version bump didn't create tag

**Check:** Git user credentials are configured

**Fix:**

```bash
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
npm run bump-version:auto
```

---

## Workflow Summary

### Development Flow

```
1. Create feature branch
   git checkout -b feature/cool-feature

2. Make changes & push
   git push origin feature/cool-feature
   ↓ GitHub Actions: Auto-bumps patch (3.0.2 → 3.0.3)

3. Merge to staging
   git checkout staging && git merge feature/cool-feature
   ↓ GitHub Actions: Auto-bumps minor (3.0.3 → 3.1.0)

4. Test in staging
   ↓ (verify everything works)

5. Merge to main
   git checkout main && git merge staging
   ↓ GitHub Actions: Auto-bumps major (3.1.0 → 4.0.0)
   ↓ GitHub Release created automatically
   ↓ Deployed to production

6. Monitor tag creation
   git tag -l  # Should show v4.0.0
```

---

## Scripts Reference

### Auto-detect branch & bump

```bash
npm run bump-version:auto
```

### Force specific bump type

```bash
npm run bump-version:patch    # 3.0.2 → 3.0.3
npm run bump-version:minor    # 3.0.2 → 3.1.0
npm run bump-version:major    # 3.0.2 → 4.0.0
```

### Raw script with options

```bash
node scripts/bump-version.js              # Auto-detect & bump
node scripts/bump-version.js --patch      # Force patch
node scripts/bump-version.js --dry-run    # Test without committing
node scripts/bump-version.js --skip-git   # Update files only, no git ops
```

---

## GitHub Actions Workflows

### Auto-Bump Workflow

**File:** `.github/workflows/version-auto-bump.yml`

- Triggers: Any push to main, dev, feature/_, staging/_, release/\*
- Does: Auto-detects tier, bumps version, commits, tags
- Prevents: Infinite loops via [skip ci] tag

### Release Workflow

**File:** `.github/workflows/version-release.yml`

- Triggers: Push to main only
- Does: Creates GitHub Release with commit notes
- Skips: dev and staging (no releases for those)

### Rollback Workflow

**File:** `.github/workflows/version-rollback.yml`

- Triggers: Manual via workflow_dispatch
- Does: Rollback to any version with reason
- Creates: Rollback tag for history

---

## Verification Checklist

- [ ] Script detects branch correctly: `npm run bump-version:auto -- --dry-run`
- [ ] Version calculation is correct (dev→patch, staging→minor, main→major)
- [ ] All 6 files would be updated (shown in dry-run)
- [ ] Git commit message includes [skip ci] tag
- [ ] Git tag format is v{version} (e.g., v3.0.3)
- [ ] GitHub Actions workflows are valid YAML (no lint errors)
- [ ] Can manually force bump if needed: `npm run bump-version:patch`

---

## Questions?

For detailed script implementation details, see: `scripts/bump-version.js`  
For workflow definitions, see: `.github/workflows/version-*.yml`  
For version file locations, see: Root `package.json` field "version"
