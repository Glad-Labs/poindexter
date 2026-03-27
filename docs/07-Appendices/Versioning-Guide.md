# Versioning Guide

**Status:** ✅ Active
**System:** [Release Please](https://github.com/googleapis/release-please) (Google)

## How It Works

Versioning is fully automated via Release Please running on the `staging` branch:

1. **Push commits to `staging`** (via `dev → staging` PR merge)
2. **Release Please opens a PR** against `staging` with:
   - Updated `CHANGELOG.md` (generated from conventional commit messages)
   - Version bump across all package files
3. **Merge the release PR** into `staging` to finalize the version
4. **Merge `staging → main`** to deploy — the production workflow creates a GitHub Release + tag on `main`

### What Gets Updated Automatically

Release Please bumps all 6 version files via `release-please-config.json`:

| File                                 | Updater                        |
| ------------------------------------ | ------------------------------ |
| `package.json` (root)                | Built-in (node release-type)   |
| `web/public-site/package.json`       | JSON (`$.version`)             |
| `src/cofounder_agent/package.json`   | JSON (`$.version`)             |
| `pyproject.toml` (root)              | TOML (`$.tool.poetry.version`) |
| `src/cofounder_agent/pyproject.toml` | TOML (`$.tool.poetry.version`) |

The manifest file `.release-please-manifest.json` tracks the current version.

---

## Version Bump Rules

Release Please uses [Conventional Commits](https://www.conventionalcommits.org/) to determine bump type:

| Commit Prefix                     | Bump             | Example                      |
| --------------------------------- | ---------------- | ---------------------------- |
| `feat:`                           | Minor (0.X.0)    | `feat: add workflow builder` |
| `fix:`                            | Patch (0.0.X)    | `fix: task polling crash`    |
| `feat!:` or `BREAKING CHANGE:`    | Major (X.0.0)    | `feat!: redesign task API`   |
| `chore:`, `docs:`, `test:`, `ci:` | No bump (hidden) | `chore: update deps`         |

While the version is below 1.0.0, `bump-minor-pre-major` is enabled — breaking changes bump minor instead of major.

---

## Configuration Files

| File                                                        | Purpose                                                                  |
| ----------------------------------------------------------- | ------------------------------------------------------------------------ |
| `release-please-config.json`                                | Release Please settings (target branch, extra files, changelog sections) |
| `.release-please-manifest.json`                             | Tracks current version (`{"." : "0.1.0"}`)                               |
| `.github/workflows/release-please.yml`                      | Workflow triggered on `staging` push                                     |
| `.github/workflows/deploy-production-with-environments.yml` | Creates GitHub Release tag on `main` after deploy                        |

---

## Flow Diagram

```
dev → staging push
       ↓
Release Please opens PR (changelog + version bump)
       ↓
Merge release PR into staging
       ↓
staging → main PR
       ↓
Production deploy runs
       ↓
create-release-tag job reads version from package.json
       ↓
GitHub Release + tag (e.g., v0.2.0) created on main
```

---

## Emergency: Manual Version Override

If you need to manually set the version (e.g., after a reset):

1. Update all 6 version files to the desired version
2. Update `.release-please-manifest.json` to match
3. Run `npm install --package-lock-only` to sync `package-lock.json`
4. Commit and push — Release Please will use this as the new baseline

---

## Rollback

The `version-rollback.yml` workflow allows emergency rollback via GitHub Actions UI:

1. Go to **Actions** → **Emergency Version Rollback**
2. Enter the version to rollback to
3. Run the workflow
