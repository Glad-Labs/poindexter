#!/usr/bin/env node
/**
 * validate-env.js
 *
 * Checks that required environment variables are present in the appropriate
 * .env.local files before starting services or running CI.
 *
 * Usage:
 *   node scripts/validate-env.js          # check all three services
 *   node scripts/validate-env.js backend  # check backend only
 *   node scripts/validate-env.js frontend # check frontend only
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');

// ---------------------------------------------------------------------------
// Rules
// ---------------------------------------------------------------------------

const BACKEND_RULES = [
  {
    key: 'DATABASE_URL',
    required: true,
    hint: 'postgresql://user:pass@localhost:5432/glad_labs_dev',
  },
  {
    key: 'JWT_SECRET_KEY',
    required: false,
    hint: 'Set in production — defaults to dev value if unset',
  },
];

const LLM_KEYS = [
  'ANTHROPIC_API_KEY',
  'OPENAI_API_KEY',
  'GOOGLE_API_KEY',
  'OLLAMA_BASE_URL',
];

const PUBLIC_RULES = [
  {
    key: 'NEXT_PUBLIC_API_URL',
    required: false,
    hint: 'http://localhost:8000  (defaults to this if unset)',
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return null;
  const content = fs.readFileSync(filePath, 'utf-8');
  const vars = {};
  for (const line of content.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eqIdx = trimmed.indexOf('=');
    if (eqIdx === -1) continue;
    const key = trimmed.slice(0, eqIdx).trim();
    const val = trimmed
      .slice(eqIdx + 1)
      .trim()
      .replace(/^["']|["']$/g, '');
    vars[key] = val;
  }
  return vars;
}

function checkRules(label, envVars, rules, extraChecks) {
  const errors = [];
  const warnings = [];

  if (envVars === null) {
    warnings.push(
      `  .env.local not found — using environment variables or defaults`
    );
  }

  const all = { ...process.env, ...(envVars || {}) };

  for (const rule of rules) {
    const val = all[rule.key];
    if (!val) {
      if (rule.required) {
        errors.push(
          `  MISSING ${rule.key}${rule.hint ? `  (e.g. ${rule.hint})` : ''}`
        );
      } else {
        warnings.push(
          `  optional ${rule.key} not set${rule.hint ? `  (${rule.hint})` : ''}`
        );
      }
    }
  }

  if (extraChecks) extraChecks(all, errors, warnings);

  return { label, errors, warnings };
}

function printResult({ label, errors, warnings }) {
  const ok = errors.length === 0;
  const icon = ok ? '✓' : '✗';
  console.log(`\n${icon} ${label}`);
  for (const w of warnings) console.log(`  ⚠  ${w.trim()}`);
  for (const e of errors) console.log(`  ✗  ${e.trim()}`);
  if (ok && warnings.length === 0) console.log('  All required vars present.');
  return ok;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const target = process.argv[2] || 'all';
const results = [];

if (target === 'all' || target === 'backend') {
  const envPath = path.join(ROOT, '.env.local');
  const envVars = loadEnvFile(envPath);
  results.push(
    checkRules(
      'Backend (.env.local)',
      envVars,
      BACKEND_RULES,
      (all, errors) => {
        const hasLLM = LLM_KEYS.some((k) => all[k]);
        if (!hasLLM) {
          errors.push(
            `  No LLM provider configured. Set one of: ${LLM_KEYS.join(', ')}`
          );
        }
      }
    )
  );
}

if (target === 'all' || target === 'frontend') {
  const publicEnv = loadEnvFile(path.join(ROOT, 'web/public-site/.env.local'));
  results.push(
    checkRules(
      'Public Site (web/public-site/.env.local)',
      publicEnv,
      PUBLIC_RULES
    )
  );
}

console.log('\n=== Glad Labs Environment Validation ===');
let allOk = true;
for (const r of results) {
  if (!printResult(r)) allOk = false;
}

console.log('');
if (allOk) {
  console.log('All environment checks passed.');
  process.exit(0);
} else {
  console.error(
    'Environment validation failed. Fix the errors above, then retry.'
  );
  process.exit(1);
}
