#!/usr/bin/env node

/**
 * Glad Labs Automated Branch-Tier Version Bumping Script
 *
 * Automatically increments versions based on git branch:
 *   - dev/*     → 0.0.XXX (patch/build numbers)
 *   - staging/* → 0.X.000 (minor/feature releases)
 *   - main      → X.0.000 (major/production releases)
 *
 * Usage (automatic in CI):
 *   npm run bump-version:auto          # Auto-detect branch & bump
 *
 * Usage (local override):
 *   npm run bump-version -- --patch    # Force patch bump
 *   npm run bump-version -- --minor    # Force minor bump
 *   npm run bump-version -- --major    # Force major bump
 *
 * Updates all 5 version files:
 *   1. package.json (root)
 *   2. web/public-site/package.json
 *   3. src/cofounder_agent/package.json
 *   4. pyproject.toml (root)
 *   5. src/cofounder_agent/pyproject.toml
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// ============================================
// Configuration
// ============================================

const ROOT = path.resolve(__dirname, '..');

const VERSION_FILES = [
  {
    path: 'package.json',
    type: 'json',
    description: 'Root monorepo',
  },
  {
    path: 'web/public-site/package.json',
    type: 'json',
    description: 'Next.js Public Site',
  },
  {
    path: 'src/cofounder_agent/package.json',
    type: 'json',
    description: 'Backend wrapper',
  },
  {
    path: 'pyproject.toml',
    type: 'toml',
    description: 'Python root package',
  },
  {
    path: 'src/cofounder_agent/pyproject.toml',
    type: 'toml',
    description: 'Python backend package',
  },
];

// Branch tier configuration
const BRANCH_TIERS = {
  dev: {
    pattern: /^dev$|^dev\/|^feature\//,
    bumpType: 'patch',
    description: 'Development',
  },
  staging: {
    pattern: /^staging$|^staging\/|^release\//,
    bumpType: 'minor',
    description: 'Staging',
  },
  main: { pattern: /^main$/, bumpType: 'major', description: 'Production' },
};

// ============================================
// Utility Functions
// ============================================

function log(icon, msg) {
  console.log(`${icon} ${msg}`);
}

function logSection(title) {
  console.log('\n' + '='.repeat(60));
  console.log(`  ${title}`);
  console.log('='.repeat(60) + '\n');
}

function parseVersion(versionStr) {
  const match = versionStr.match(/^(\d+)\.(\d+)\.(\d+)/);
  if (!match) throw new Error(`Invalid version format: ${versionStr}`);
  return {
    major: parseInt(match[1]),
    minor: parseInt(match[2]),
    patch: parseInt(match[3]),
  };
}

function versionToString(v) {
  return `${v.major}.${v.minor}.${v.patch}`;
}

function bumpVersion(versionStr, bumpType) {
  const v = parseVersion(versionStr);

  switch (bumpType) {
    case 'major':
      return versionToString({ major: v.major + 1, minor: 0, patch: 0 });
    case 'minor':
      return versionToString({ major: v.major, minor: v.minor + 1, patch: 0 });
    case 'patch':
      return versionToString({
        major: v.major,
        minor: v.minor,
        patch: v.patch + 1,
      });
    default:
      throw new Error(`Unknown bump type: ${bumpType}`);
  }
}

function getCurrentBranch() {
  // In GitHub Actions, checkout is often detached (branch appears as HEAD),
  // so prefer CI-provided refs first.
  const envBranch =
    process.env.GITHUB_HEAD_REF ||
    process.env.GITHUB_REF_NAME ||
    process.env.BRANCH_NAME;

  if (envBranch && envBranch !== 'HEAD') {
    return envBranch.replace(/^refs\/heads\//, '');
  }

  try {
    const gitBranch = execSync('git rev-parse --abbrev-ref HEAD', {
      cwd: ROOT,
      encoding: 'utf8',
    }).trim();

    if (gitBranch && gitBranch !== 'HEAD') {
      return gitBranch;
    }

    // Final fallback for detached HEAD: derive from full ref if available.
    const fullRef = process.env.GITHUB_REF;
    if (fullRef && fullRef.startsWith('refs/heads/')) {
      return fullRef.replace('refs/heads/', '');
    }

    throw new Error('Branch is detached (HEAD) and no CI ref is available');
  } catch {
    throw new Error(
      'Could not detect git branch. Provide GITHUB_REF_NAME/BRANCH_NAME or run from a named branch.'
    );
  }
}

function determineBumpType(branch, forceBump = null) {
  if (forceBump) return forceBump;

  for (const [, tier] of Object.entries(BRANCH_TIERS)) {
    if (tier.pattern.test(branch)) {
      return tier.bumpType;
    }
  }

  throw new Error(
    `Branch "${branch}" does not match any tier (dev/*, staging/*, main). ` +
      `Create a branch matching the pattern or use --patch|--minor|--major to override.`
  );
}

function getTierName(bumpType) {
  for (const [tierName, tier] of Object.entries(BRANCH_TIERS)) {
    if (tier.bumpType === bumpType) {
      return `${tierName} (${tier.description})`;
    }
  }
  return 'unknown';
}

function readJsonFile(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function writeJsonFile(filePath, data) {
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2) + '\n');
}

function readTomlFile(filePath) {
  return fs.readFileSync(filePath, 'utf8');
}

function writeTomlFile(filePath, content) {
  fs.writeFileSync(filePath, content);
}

function updateTomlVersion(content, newVersion) {
  const regex = /^version\s*=\s*"[^"]+"/m;
  if (!regex.test(content)) {
    throw new Error('Could not find version in TOML file');
  }
  return content.replace(regex, `version = "${newVersion}"`);
}

function getCurrentVersion() {
  const filePath = path.join(ROOT, 'package.json');
  const data = readJsonFile(filePath);
  return data.version;
}

function updateVersionInFile(file, newVersion) {
  const filePath = path.join(ROOT, file.path);

  if (!fs.existsSync(filePath)) {
    throw new Error(`File not found: ${file.path}`);
  }

  if (file.type === 'json') {
    const data = readJsonFile(filePath);
    data.version = newVersion;
    writeJsonFile(filePath, data);
  } else if (file.type === 'toml') {
    const content = readTomlFile(filePath);
    const updated = updateTomlVersion(content, newVersion);
    writeTomlFile(filePath, updated);
  }
}

function verifyVersionUpdates(newVersion) {
  let verified = 0;
  for (const file of VERSION_FILES) {
    const filePath = path.join(ROOT, file.path);

    try {
      if (file.type === 'json') {
        const data = readJsonFile(filePath);
        if (data.version === newVersion) {
          log('✅', `${file.description}: ${data.version}`);
          verified++;
        } else {
          log(
            '❌',
            `${file.description}: Expected ${newVersion}, got ${data.version}`
          );
        }
      } else if (file.type === 'toml') {
        const content = readTomlFile(filePath);
        const versionMatch = content.match(/^version\s*=\s*"([^"]+)"/m);
        const version = versionMatch ? versionMatch[1] : 'NOT FOUND';

        if (version === newVersion) {
          log('✅', `${file.description}: ${version}`);
          verified++;
        } else {
          log(
            '❌',
            `${file.description}: Expected ${newVersion}, got ${version}`
          );
        }
      }
    } catch (error) {
      log('❌', `${file.description}: ${error.message}`);
    }
  }

  return verified === VERSION_FILES.length;
}

// ============================================
// Main CLI
// ============================================

function main() {
  const args = process.argv.slice(2);

  // Parse command-line arguments
  const forceBump = args.includes('--major')
    ? 'major'
    : args.includes('--minor')
      ? 'minor'
      : args.includes('--patch')
        ? 'patch'
        : null;

  const isDryRun = args.includes('--dry-run');
  const skipGit =
    args.includes('--skip-git') || process.env.SKIP_GIT === 'true';
  try {
    logSection('🚀 Glad Labs Automated Version Bump');

    // Get current branch and version
    const branch = getCurrentBranch();
    const currentVersion = getCurrentVersion();

    log('ℹ️', `Current branch: ${branch}`);
    log('ℹ️', `Current version: ${currentVersion}`);

    // Determine bump type based on branch
    const bumpType = determineBumpType(branch, forceBump);
    const tierName = getTierName(bumpType);

    log('ℹ️', `Bump type: ${bumpType} (${tierName})`);

    // Calculate new version
    const newVersion = bumpVersion(currentVersion, bumpType);
    log('✨', `New version: ${newVersion}`);

    if (isDryRun) {
      log('ℹ️', 'Dry run mode - no changes will be made');
      process.exit(0);
    }

    // Update all version files
    logSection('📝 Updating Version Files');
    for (const file of VERSION_FILES) {
      updateVersionInFile(file, newVersion);
      log('✅', `Updated: ${file.description}`);
    }

    // Verify all files were updated correctly
    logSection('✓ Verifying Updates');
    const allVerified = verifyVersionUpdates(newVersion);

    if (!allVerified) {
      throw new Error(
        'Version verification failed - not all files were updated correctly'
      );
    }

    // Git operations (skip if SKIP_GIT env var is set, or if --skip-git flag)
    if (!skipGit) {
      logSection('📦 Git Operations');

      try {
        // Stage changes
        execSync('git add .', { cwd: ROOT, stdio: 'pipe' });
        log('✅', 'Files staged');

        // Commit with [skip ci] to prevent infinite loops
        const commitMessage = `chore: bump version to ${newVersion} [skip ci]`;
        execSync(`git commit -m "${commitMessage}"`, {
          cwd: ROOT,
          stdio: 'pipe',
        });
        log('✅', `Git commit created: "${commitMessage}"`);

        // Create git tag
        const tagName = `v${newVersion}`;
        execSync(`git tag -a ${tagName} -m "Release ${newVersion}"`, {
          cwd: ROOT,
          stdio: 'pipe',
        });
        log('✅', `Git tag created: ${tagName}`);
      } catch (error) {
        log('❌', `Git operation failed: ${error.message}`);
        throw error;
      }
    } else {
      log('ℹ️', 'Skipping git operations (SKIP_GIT set or --skip-git flag)');
    }

    // Success summary
    logSection('✨ Version Bump Complete');
    log('✅', `Successfully bumped from ${currentVersion} to ${newVersion}`);
    log('ℹ️', `Tier: ${tierName}`);
    log('ℹ️', `All 6 version files updated and verified`);

    if (!skipGit) {
      log('ℹ️', '\nNext steps:');
      log('ℹ️', '  1. Review changes: git diff HEAD~1');
      log('ℹ️', '  2. Push to remote: git push && git push --tags');
    }
  } catch (error) {
    logSection('❌ Error');
    log('❌', error.message);
    process.exit(1);
  }
}

main();
