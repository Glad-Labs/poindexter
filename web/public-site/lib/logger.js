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

function initLogDir() {
  if (_initDone) return _logDir;
  _initDone = true;
  try {
    const fs = require('fs');
    const path = require('path');
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
  try {
    const fs = require('fs');
    const path = require('path');
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
