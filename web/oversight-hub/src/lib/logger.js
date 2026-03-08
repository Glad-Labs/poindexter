/**
 * Glad Labs Oversight Hub — Browser Logger
 *
 * Dev   → all levels, structured console output with timestamps
 * Prod  → warn and error only (debug/info/log are suppressed)
 *
 * Usage:
 *   import logger from '@/lib/logger';
 *   logger.error('Something broke', error);
 *   logger.warn('Unexpected state', { foo: 'bar' });
 *   logger.info('Task created', taskId);
 *   logger.debug('Raw response', data);   // dev only
 *   logger.log('arbitrary message');      // dev only
 */

const IS_DEV = process.env.NODE_ENV !== 'production';

const LEVEL_FN = {
  ERROR: 'error',
  WARN: 'warn',
  INFO: 'info',
  DEBUG: 'debug',
  LOG: 'log',
};

function emit(level, message, args) {
  if (!IS_DEV && level !== 'WARN' && level !== 'ERROR') return;
  const ts = new Date().toISOString();
  const prefix = `[${ts}] [${level.padEnd(5)}] ${message}`;
  const fn = LEVEL_FN[level] || 'log';
  if (args.length) {
    console[fn](prefix, ...args);
  } else {
    console[fn](prefix);
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
