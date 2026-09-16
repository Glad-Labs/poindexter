#!/usr/bin/env node
/**
 * Browser captures for the README demo GIF. Companion to build-demo.sh;
 * conventions match scripts/capture-readme-screenshots.mjs (system Chrome,
 * Grafana anonymous viewer, kiosk URLs).
 *
 * Modes:
 *   node capture-frames.mjs timelapse   # screenshot the Pipeline dashboard
 *                                       # every FRAME_INTERVAL s into work/frames/
 *                                       # until killed (SIGTERM/SIGINT) or MAX_FRAMES
 *   node capture-frames.mjs site <url>  # slow-scroll reveal of the published
 *                                       # post → work/site-frames/
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = resolve(dirname(fileURLToPath(import.meta.url)));
const GRAFANA = process.env.GRAFANA_URL || 'http://localhost:3000';
const INTERVAL = Number(process.env.FRAME_INTERVAL || 5) * 1000;
const MAX_FRAMES = Number(process.env.MAX_FRAMES || 480); // 40 min at 5 s — hard stop
const VIEWPORT = { width: 1200, height: 750 };

const mode = process.argv[2];
if (!['timelapse', 'site'].includes(mode)) {
  console.error('usage: capture-frames.mjs timelapse | site <url>');
  process.exit(2);
}

const pad = (n) => String(n).padStart(5, '0');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: VIEWPORT });

let stopping = false;
for (const sig of ['SIGINT', 'SIGTERM'])
  process.on(sig, () => {
    stopping = true;
  });

if (mode === 'timelapse') {
  const outDir = join(here, 'work', 'frames');
  mkdirSync(outDir, { recursive: true });
  await page.goto(`${GRAFANA}/d/pipeline-merged/pipeline?kiosk&theme=dark&refresh=5s`, {
    waitUntil: 'domcontentloaded',
    timeout: 45_000,
  });
  await page.waitForTimeout(7000); // panels settle
  let i = 0;
  while (!stopping && i < MAX_FRAMES) {
    await page.screenshot({ path: join(outDir, `frame-${pad(i)}.png`) });
    i += 1;
    if (i % 12 === 0) console.log(`timelapse: ${i} frames`);
    const t0 = Date.now();
    while (!stopping && Date.now() - t0 < INTERVAL)
      await new Promise((r) => setTimeout(r, 250));
  }
  console.log(`timelapse done: ${i} frames in ${outDir}`);
}

if (mode === 'site') {
  const url = process.argv[3] || 'https://www.gladlabs.io';
  const outDir = join(here, 'work', 'site-frames');
  mkdirSync(outDir, { recursive: true });
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await page.waitForTimeout(6000);
  await page
    .addStyleTag({ content: '[aria-label="Cookie consent"]{display:none !important}' })
    .catch(() => {});
  // ~6 s reveal: hold the top, then scroll down one viewport in small steps.
  const HOLD = 12, STEPS = 48, STEP_PX = 18; // 60 frames → 6 s at 10 fps
  let i = 0;
  for (; i < HOLD; i++)
    await page.screenshot({ path: join(outDir, `frame-${pad(i)}.png`) });
  for (let s = 0; s < STEPS; s++, i++) {
    await page.evaluate((px) => window.scrollBy(0, px), STEP_PX);
    await page.waitForTimeout(30);
    await page.screenshot({ path: join(outDir, `frame-${pad(i)}.png`) });
  }
  console.log(`site reveal done: ${i} frames in ${outDir}`);
}

await browser.close();
