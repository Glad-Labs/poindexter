#!/usr/bin/env node
/**
 * Replaces console.* calls with logger.* and injects the logger import.
 * Skips: test files, spec files, __tests__ dirs, and the logger files themselves.
 */

const fs = require('fs');
const path = require('path');

const TARGETS = [
  {
    dir: path.join(__dirname, '../web/public-site/lib'),
    importLine: "import logger from './logger';",
    loggerFile: 'logger.js',
  },
  {
    dir: path.join(__dirname, '../web/oversight-hub/src'),
    importLine: "import logger from '@/lib/logger';",
    loggerFile: path.join(__dirname, '../web/oversight-hub/src/lib/logger.js'),
  },
];

const CONSOLE_RE = /console\.(error|warn|info|debug|log)\(/g;
const SKIP_RE = /(__tests__|\.test\.|\.spec\.)/;

function isSkipped(filePath, loggerFile) {
  if (SKIP_RE.test(filePath)) return true;
  if (path.resolve(filePath) === path.resolve(loggerFile)) return true;
  // Also skip public-site/lib/logger.js
  if (filePath.endsWith('lib/logger.js') && filePath.includes('public-site')) return true;
  return false;
}

function hasImport(content, importLine) {
  // Flexible check — match on logger import regardless of exact quote style
  return content.includes("from '@/lib/logger'") ||
         content.includes("from \"@/lib/logger\"") ||
         content.includes("from './logger'") ||
         content.includes('from "./logger"');
}

function insertImport(content, importLine) {
  // Preserve 'use client' / 'use server' directives at the very top
  const directiveRe = /^(['"]use (?:client|server)['"];?\n)/;
  const match = content.match(directiveRe);
  if (match) {
    return match[1] + importLine + '\n' + content.slice(match[1].length);
  }
  return importLine + '\n' + content;
}

function processFile(filePath, importLine, loggerFile) {
  if (isSkipped(filePath, loggerFile)) return false;

  let content = fs.readFileSync(filePath, 'utf8');
  if (!CONSOLE_RE.test(content)) return false;
  CONSOLE_RE.lastIndex = 0; // reset after test()

  // Replace console.* → logger.*
  const updated = content.replace(CONSOLE_RE, (_, method) => `logger.${method}(`);

  // Inject import if not already present
  const withImport = hasImport(updated, importLine)
    ? updated
    : insertImport(updated, importLine);

  if (withImport !== content) {
    fs.writeFileSync(filePath, withImport, 'utf8');
    return true;
  }
  return false;
}

function walk(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === '.next' || entry.name === 'build') continue;
      files.push(...walk(full));
    } else if (/\.(js|jsx|ts|tsx)$/.test(entry.name)) {
      files.push(full);
    }
  }
  return files;
}

let total = 0;
for (const { dir, importLine, loggerFile } of TARGETS) {
  const files = walk(dir);
  for (const f of files) {
    if (processFile(f, importLine, loggerFile)) {
      console.log('  ✓', path.relative(process.cwd(), f));
      total++;
    }
  }
}
console.log(`\nDone. Modified ${total} files.`);
