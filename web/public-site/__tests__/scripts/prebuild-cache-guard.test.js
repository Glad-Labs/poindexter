/**
 * @jest-environment node
 *
 * Guards the Vercel cache-poisoning fix (2026-08): a `.next/cache` restored
 * from a different Next version must be wiped before `next build`, or Next
 * skips re-emitting next-server.js.nft.json and Vercel's finalize step dies
 * on the ENOENT — after a fully green compile. gladlabs.io served a
 * 13-day-old build because of exactly this.
 */
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  readMarker,
  runGuard,
  shouldWipe,
} from '../../scripts/prebuild-cache-guard.mjs';

describe('shouldWipe policy', () => {
  it('wipes on version mismatch', () => {
    expect(shouldWipe('16.3.0', '16.3.1')).toBe(true);
  });

  it('keeps the cache when versions match', () => {
    expect(shouldWipe('16.3.1', '16.3.1')).toBe(false);
  });

  it('treats an absent marker as a mismatch (fail-closed)', () => {
    // The poisoned pre-guard cache has no marker at all — the guard's first
    // run in production must wipe it, or this fix fixes nothing.
    expect(shouldWipe(null, '16.3.1')).toBe(true);
  });
});

describe('runGuard filesystem behaviour', () => {
  let siteDir;
  const log = () => {};

  beforeEach(() => {
    siteDir = mkdtempSync(join(tmpdir(), 'cache-guard-'));
  });

  afterEach(() => {
    rmSync(siteDir, { recursive: true, force: true });
  });

  const seedNextDir = (markerContent) => {
    mkdirSync(join(siteDir, '.next', 'cache'), { recursive: true });
    writeFileSync(join(siteDir, '.next', 'stale-artifact'), 'old');
    if (markerContent !== undefined) {
      writeFileSync(
        join(siteDir, '.next', 'cache', 'next-version-marker.json'),
        markerContent
      );
    }
  };

  it('wipes a marker-less .next (the live poisoned-cache case)', () => {
    seedNextDir(undefined);
    runGuard({ siteDir, currentVersion: '16.3.1', log });
    expect(existsSync(join(siteDir, '.next', 'stale-artifact'))).toBe(false);
  });

  it('wipes .next when the marker names a different version', () => {
    seedNextDir(JSON.stringify({ nextVersion: '16.3.0' }));
    runGuard({ siteDir, currentVersion: '16.3.1', log });
    expect(existsSync(join(siteDir, '.next', 'stale-artifact'))).toBe(false);
  });

  it('keeps .next when the marker matches', () => {
    seedNextDir(JSON.stringify({ nextVersion: '16.3.1' }));
    runGuard({ siteDir, currentVersion: '16.3.1', log });
    expect(existsSync(join(siteDir, '.next', 'stale-artifact'))).toBe(true);
  });

  it('always leaves a marker for the version about to build', () => {
    runGuard({ siteDir, currentVersion: '16.3.1', log });
    const marker = JSON.parse(
      readFileSync(
        join(siteDir, '.next', 'cache', 'next-version-marker.json'),
        'utf8'
      )
    );
    expect(marker.nextVersion).toBe('16.3.1');
  });

  it('survives a corrupt marker by treating it as unknown provenance', () => {
    seedNextDir('not json{{{');
    runGuard({ siteDir, currentVersion: '16.3.1', log });
    expect(existsSync(join(siteDir, '.next', 'stale-artifact'))).toBe(false);
  });

  it('no-ops cleanly on a fresh checkout with no .next at all', () => {
    expect(() =>
      runGuard({ siteDir, currentVersion: '16.3.1', log })
    ).not.toThrow();
    expect(readMarker(join(siteDir, '.next', 'cache', 'next-version-marker.json'))).toBe(
      '16.3.1'
    );
  });
});
