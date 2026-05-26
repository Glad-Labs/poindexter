/**
 * Glad Labs Public Site — Universal Logger
 *
 * Server-side  → appends to logs/app.log (all levels) and logs/error.log (warn+error)
 * Client dev   → structured console output
 * Client prod  → warn and error only (suppresses debug/info/log)
 */

const IS_SERVER = typeof window === 'undefined';
const IS_DEV = process.env.NODE_ENV !== 'production';

// Lazy-init log dir so we only pay the cost once and avoid repeated existsSync
let _logDir = null;
let _initDone = false;

// Next.js 16 bundler trace defence (captured 2026-05-26 after the
// glad-labs-codebase-public-site deployment failed with
// ``Module not found: Can't resolve 'fs'`` on this file). This logger
// is imported by both server code (route handlers, RSC) and client
// code (via ``lib/posts.ts`` → ``app/search/page.jsx``). Next.js 15
// tolerated bare ``require('fs')`` here because the ``IS_SERVER``
// runtime guard kept it from executing in the browser; Next.js 16's
// bundler statically traces ``require()`` strings regardless of
// runtime guards and trips ``Module not found`` on every Client
// Component import path that reaches this file.
//
// The ``webpackIgnore: true`` / ``turbopackIgnore: true`` magic
// comments tell Next.js's bundler to skip tree-shaking these
// requires — they stay as runtime CommonJS resolution. The
// IS_SERVER guards below ensure the requires only execute on the
// server, so the client bundle never tries to load 'fs'.
//
// Longer-term fix is to split into logger.server.js + logger.client.js
// with the ``server-only`` package, but the universal-logger shape is
// too widely used to refactor in the same hotfix.

function initLogDir() {
  if (_initDone) return _logDir;
  _initDone = true;
  if (!IS_SERVER) {
    _logDir = null;
    return _logDir;
  }
  try {
    const fs = require(/* webpackIgnore: true */ /* turbopackIgnore: true */ 'fs');
    const path = require(/* webpackIgnore: true */ /* turbopackIgnore: true */ 'path');
    const dir = path.join(process.cwd(), 'logs');
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    _logDir = dir;
  } catch (_) {
    // Running in an environment without fs (e.g. Edge runtime) — fall back to stdout
    _logDir = null;
  }
  return _logDir;
}

function serializeArg(arg) {
  if (arg instanceof Error) return arg.stack || arg.message;
  if (typeof arg === 'object' && arg !== null) {
    try {
      return JSON.stringify(arg);
    } catch (_) {
      return String(arg);
    }
  }
  return String(arg);
}

function buildLine(level, message, args) {
  const ts = new Date().toISOString();
  const extra = args.length ? ' ' + args.map(serializeArg).join(' ') : '';
  return `[${ts}] [${level.padEnd(5)}] ${message}${extra}`;
}

function writeToFile(filename, line) {
  // IS_SERVER guard: this function is only reachable from serverLog()
  // which is only called when IS_SERVER && _logDir resolved, but be
  // defensive here too so any future refactor that adds a non-server
  // caller fails closed (stdout fallback) instead of hard-crashing on
  // a bundler-stripped fs require.
  if (!IS_SERVER) {
    return;
  }
  try {
    const fs = require(/* webpackIgnore: true */ /* turbopackIgnore: true */ 'fs');
    const path = require(/* webpackIgnore: true */ /* turbopackIgnore: true */ 'path');
    fs.appendFileSync(path.join(_logDir, filename), line + '\n');
  } catch (_) {
    process.stdout && process.stdout.write(line + '\n');
  }
}

function serverLog(level, line) {
  const dir = initLogDir();
  if (dir) {
    writeToFile('app.log', line);
    if (level === 'WARN' || level === 'ERROR') writeToFile('error.log', line);
  } else {
    const out =
      level === 'ERROR' || level === 'WARN' ? process.stderr : process.stdout;
    out && out.write(line + '\n');
  }
}

function clientLog(level, line, rawArgs) {
  if (!IS_DEV && level !== 'WARN' && level !== 'ERROR') return;
  const fn =
    { ERROR: 'error', WARN: 'warn', INFO: 'info', DEBUG: 'debug', LOG: 'log' }[
      level
    ] || 'log';
  console[fn](line, ...rawArgs); // eslint-disable-line no-console
}

function emit(level, message, args) {
  const line = buildLine(level, message, args);
  if (IS_SERVER) {
    serverLog(level, line);
  } else {
    clientLog(level, line, args);
  }
}

const logger = {
  debug: (message, ...args) => {
    if (IS_DEV) emit('DEBUG', message, args);
  },
  info: (message, ...args) => emit('INFO', message, args),
  log: (message, ...args) => {
    if (IS_DEV) emit('LOG', message, args);
  },
  warn: (message, ...args) => emit('WARN', message, args),
  error: (message, ...args) => emit('ERROR', message, args),
};

export default logger;
