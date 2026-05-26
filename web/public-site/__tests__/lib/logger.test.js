/**
 * @jest-environment node
 *
 * lib/logger — universal logger contract tests.
 *
 * Pins the 2026-05-26 fix for the Next.js 16 bundler trace failure:
 * the logger uses ``require('fs')`` and ``require('path')`` for
 * server-side file logging, but is imported into Client Components
 * through ``lib/posts.ts``. Next.js 16's bundler statically traces
 * those requires and fails the client bundle with "Module not found:
 * Can't resolve 'fs'". The fix layers two defences:
 *
 *   1. ``webpackIgnore``/``turbopackIgnore`` magic comments so the
 *      bundler skips the require call entirely.
 *   2. ``IS_SERVER`` guards in both ``initLogDir()`` and
 *      ``writeToFile()`` so the runtime call only fires server-side.
 *
 * These tests focus on what we can verify in Node (the runtime
 * semantics). The bundler behaviour is verified by the Vercel
 * deployment going from failed → success after this lands.
 */

import logger from '../../lib/logger.js';

describe('lib/logger', () => {
  it('exposes the five-level public API', () => {
    expect(typeof logger.debug).toBe('function');
    expect(typeof logger.info).toBe('function');
    expect(typeof logger.log).toBe('function');
    expect(typeof logger.warn).toBe('function');
    expect(typeof logger.error).toBe('function');
  });

  it('does not throw when info() is called in a Node env', () => {
    // This exercises the IS_SERVER path. Pre-fix, a misconfigured
    // bundler could leave ``require('fs')`` as a stub at runtime
    // and ``initLogDir()`` would crash; the IS_SERVER guard plus
    // the bundler-ignore comments make the path resilient either
    // way. We accept either real-file write OR stdout-fallback —
    // both indicate the logger survived the call.
    expect(() => logger.info('hello from logger test')).not.toThrow();
  });

  it('does not throw on warn / error calls', () => {
    expect(() => logger.warn('warn message')).not.toThrow();
    expect(() => logger.error('error message', new Error('boom'))).not.toThrow();
  });

  it('serializes object args without crashing on circular refs', () => {
    const a = {};
    const b = { a };
    a.b = b; // circular
    // The serializer falls back to String(arg) on JSON.stringify
    // failure, so circular references must not throw.
    expect(() => logger.info('circular', a)).not.toThrow();
  });
});
