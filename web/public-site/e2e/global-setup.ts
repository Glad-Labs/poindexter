/**
 * Global Test Setup
 * =================
 *
 * Runs once before all tests across all browsers. It reports what this run
 * is aimed at and whether each end answers, so a CI log names both targets
 * up front, and it probes the backend once and records the result for the
 * workers.
 *
 * It fails nothing. Most runs never call the backend (frontend-e2e and the
 * weekly public-surface lane don't), so each backend-calling spec checks
 * the backend itself with requireBackend() from ./backend. That check fails
 * the test when the run names a target or runs in CI, and it reads the probe
 * recorded here instead of probing again.
 */

import { request, type FullConfig } from '@playwright/test';
import {
  API_URL,
  BACKEND_REQUIRED,
  probeBackend,
  recordBackendProbe,
} from './backend';

async function globalSetup(config: FullConfig) {
  console.log('🚀 Global Test Setup Started');

  const backend = await probeBackend();
  recordBackendProbe(backend);
  if (backend.health) {
    console.log(
      `⚠️  Backend (PLAYWRIGHT_API_URL) ${API_URL}: ${backend.health}`
    );
  } else {
    console.log(
      `✓ Backend (PLAYWRIGHT_API_URL) ${API_URL}: answering GET /health; ${
        backend.devToken
          ? 'refuses Bearer dev-token, so the dev-token tests cannot run'
          : 'accepts Bearer dev-token (DEVELOPMENT_MODE)'
      }`
    );
  }
  console.log(
    BACKEND_REQUIRED
      ? '   Backend-calling tests FAIL if it is unusable (a target is configured, or this is CI).'
      : '   Backend-calling tests skip if it is unusable (bare local run: no target configured, not CI).'
  );

  const siteURL = config.projects[0]?.use.baseURL;
  if (siteURL) {
    const siteProblem = await probeSite(siteURL);
    console.log(
      siteProblem
        ? `⚠️  Site (PLAYWRIGHT_TEST_BASE_URL) ${siteURL}: ${siteProblem}`
        : `✓ Site (PLAYWRIGHT_TEST_BASE_URL) ${siteURL}: answering`
    );
  }

  console.log('✅ Global Test Setup Complete\n');
}

/** Null when the site answers, else why not. Never throws. */
async function probeSite(url: string): Promise<string | null> {
  const context = await request.newContext();
  try {
    await context.get(url, { timeout: 10_000 });
    return null;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return `not answering (${message.split('\n')[0]})`;
  } finally {
    await context.dispose();
  }
}

export default globalSetup;
