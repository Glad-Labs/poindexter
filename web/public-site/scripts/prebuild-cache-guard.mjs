#!/usr/bin/env node
/**
 * Prebuild guard: bust .next when its cache was produced by a different
 * Next.js version.
 *
 * Why this exists (2026-08): Vercel restores `.next/cache` from the previous
 * READY deployment. After the next 16.3.0 → 16.3.1 bump, every production
 * build of this site restored the 16.3.0-era cache, Next silently skipped
 * re-emitting `next-server.js.nft.json`, and Vercel's finalize step died on
 * the missing file — the build compiled clean and generated all 90 pages
 * first, so the only signal was an ENOENT after "Running onBuildComplete".
 * gladlabs.io ran a 13-day-old build before anyone noticed (ISR kept content
 * fresh, masking it). The same failure class is a candidate on every future
 * Next version bump, hence a guard rather than a one-off dashboard redeploy.
 *
 * Mechanism: a marker file INSIDE `.next/cache` (so it travels with the
 * restored cache) records the Next version that produced the cache. On
 * mismatch — or when the marker is absent, which is what a poisoned/legacy
 * cache looks like — wipe `.next` entirely and start clean. A matching
 * marker keeps the cache, so warm builds stay warm.
 */
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);

/**
 * Decide whether the cache must be wiped.
 * Pure so the policy is unit-testable: absent or unreadable marker counts as
 * a mismatch (fail-closed — a cache of unknown provenance is exactly the
 * poisoned case this guard exists for).
 */
export function shouldWipe(markerVersion, currentVersion) {
  return markerVersion !== currentVersion;
}

export function readMarker(markerPath) {
  try {
    return JSON.parse(readFileSync(markerPath, 'utf8')).nextVersion ?? null;
  } catch {
    return null; // absent or corrupt marker → unknown provenance
  }
}

export function runGuard({ siteDir, currentVersion, log = console.log }) {
  const nextDir = join(siteDir, '.next');
  const markerPath = join(nextDir, 'cache', 'next-version-marker.json');
  const markerVersion = readMarker(markerPath);

  if (existsSync(nextDir) && shouldWipe(markerVersion, currentVersion)) {
    log(
      `[cache-guard] .next cache is from next@${markerVersion ?? '<unknown>'}, ` +
        `installed is next@${currentVersion} — wiping .next to force a clean build`
    );
    rmSync(nextDir, { recursive: true, force: true });
  } else if (existsSync(nextDir)) {
    log(`[cache-guard] .next cache matches next@${currentVersion} — keeping it`);
  }

  // (Re)write the marker so the cache saved from THIS build carries its
  // provenance. Written pre-build: the version that is about to build is the
  // version that will populate the cache.
  mkdirSync(dirname(markerPath), { recursive: true });
  writeFileSync(markerPath, JSON.stringify({ nextVersion: currentVersion }) + '\n');
}

// CLI entry (what `npm run prebuild` executes). Guarded so importing the
// module from a test never touches the real filesystem.
const invokedDirectly =
  process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (invokedDirectly) {
  const siteDir = join(dirname(fileURLToPath(import.meta.url)), '..');
  const currentVersion = require('next/package.json').version;
  runGuard({ siteDir, currentVersion });
}
