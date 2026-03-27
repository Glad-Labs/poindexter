# Deployment Checklist

**Last Updated:** March 2026
**Versioning System:** [Release Please](https://github.com/googleapis/release-please)

---

## Pre-Deployment (Staging)

### 1. Merge dev → staging

```bash
# Create PR from dev → staging via GitHub
gh pr create --base staging --head dev --title "Deploy to staging"
```

- [ ] All tests passing on `dev`
- [ ] PR reviewed and approved
- [ ] Merge triggers staging deployment to Railway

### 2. Release Please PR

After merging to `staging`, Release Please automatically opens a PR with:

- [ ] `CHANGELOG.md` updated with new entries from conventional commits
- [ ] Version bumped across all 6 package files
- [ ] Review the changelog for accuracy
- [ ] Merge the release PR into `staging`

### 3. Verify Staging

- [ ] Staging environment is healthy (`/health` endpoint)
- [ ] Key features work as expected
- [ ] No errors in staging logs

---

## Production Deployment

### 4. Merge staging → main

```bash
gh pr create --base main --head staging --title "Release v0.x.x"
```

- [ ] Changelog and version bump already included (from Release Please)
- [ ] PR reviewed and approved
- [ ] Merge triggers production deployment

### 5. Post-Deployment Verification

The production workflow (`deploy-production-with-environments.yml`) handles:

- [ ] Backend deployed to Railway
- [ ] Public site deployed to Vercel
- [ ] Smoke tests pass (automated)
- [ ] GitHub Release + tag created on `main` (automated)

### 6. Manual Verification

- [ ] Health endpoint: `curl https://api.glad-labs.com/health`
- [ ] Public site loads: `https://glad-labs.com`
- [ ] Check logs for errors
- [ ] Verify GitHub Release was created with correct version

---

## Version File Synchronization

Release Please keeps all 6 files in sync automatically:

| File                                 | Type |
| ------------------------------------ | ---- |
| `package.json` (root)                | JSON |
| `web/public-site/package.json`       | JSON |
| `src/cofounder_agent/package.json`   | JSON |
| `pyproject.toml` (root)              | TOML |
| `src/cofounder_agent/pyproject.toml` | TOML |

Configuration: `release-please-config.json` + `.release-please-manifest.json`

---

## Emergency Rollback

### Via GitHub Actions

1. Go to **Actions** → **Emergency Version Rollback**
2. Enter version to rollback to
3. Add reason (optional)
4. Click **Run workflow**

### Manual Rollback

```bash
# Revert the merge commit on main
git revert -m 1 <merge-commit-sha>
git push origin main
```

This triggers a new production deployment with the previous code.

---

## Workflow Files

| File                                      | Trigger            | Purpose                                        |
| ----------------------------------------- | ------------------ | ---------------------------------------------- |
| `release-please.yml`                      | Push to `staging`  | Opens changelog + version bump PR              |
| `deploy-staging-with-environments.yml`    | Push to `staging`  | Deploys to Railway staging                     |
| `deploy-production-with-environments.yml` | Push to `main`     | Deploys to production + creates GitHub Release |
| `test-on-dev.yml`                         | Push to `dev`      | Runs full test suite                           |
| `test-on-feat.yml`                        | Feature branch PRs | Runs lint + tests                              |
| `version-rollback.yml`                    | Manual             | Emergency version rollback                     |
