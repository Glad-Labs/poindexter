/* Promo GIF recorder — records scripted browser tours as webm via Playwright.
 * Usage: node record.cjs <sceneName>
 * Env: PX_CLIENT_ID / PX_CLIENT_SECRET for console scenes.
 */
const path = require('path');
const fs = require('fs');
const { chromium } = require('@playwright/test');

const ROOT = __dirname;
const VIDEO_DIR = path.join(ROOT, 'video');
const SHOTS_DIR = path.join(ROOT, 'shots');

// ---------- helpers ----------
async function smoothScroll(page, { to, by, ms = 4000, container = null }) {
  await page.evaluate(
    async ({ to, by, ms, container }) => {
      const el = container
        ? document.querySelector(container)
        : document.scrollingElement || document.documentElement;
      const start = el.scrollTop;
      const target = to != null ? to : start + by;
      const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
      await new Promise((res) => {
        const t0 = performance.now();
        const step = (now) => {
          const p = Math.min(1, (now - t0) / ms);
          el.scrollTop = start + (target - start) * ease(p);
          if (p < 1) requestAnimationFrame(step);
          else res();
        };
        requestAnimationFrame(step);
      });
    },
    { to, by, ms, container }
  );
}

const CURSOR_INIT = `
  (() => {
    if (window.__pxCursorInstalled) return;
    window.__pxCursorInstalled = true;
    const mk = () => {
      const d = document.createElement('div');
      d.id = '--px-cursor';
      d.style.cssText =
        'position:fixed;z-index:2147483647;width:18px;height:18px;border-radius:50%;' +
        'background:rgba(255,255,255,0.85);border:2px solid rgba(0,0,0,0.55);' +
        'box-shadow:0 1px 6px rgba(0,0,0,0.45);pointer-events:none;' +
        'transform:translate(-50%,-50%);top:-40px;left:-40px;transition:width .12s,height .12s';
      document.body.appendChild(d);
      return d;
    };
    let dot = null;
    const get = () => (dot && dot.isConnected ? dot : (dot = mk()));
    window.addEventListener('mousemove', (e) => {
      const c = get();
      c.style.left = e.clientX + 'px';
      c.style.top = e.clientY + 'px';
    }, true);
    window.addEventListener('mousedown', () => {
      const c = get();
      c.style.width = '26px'; c.style.height = '26px';
      setTimeout(() => { c.style.width = '18px'; c.style.height = '18px'; }, 160);
    }, true);
  })();
`;

// glide the mouse to the center of a locator in n steps (fires mousemove for the fake cursor)
async function glide(page, locator, ms = 700) {
  const box = await locator.boundingBox();
  if (!box) throw new Error('no bounding box for glide target');
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  const steps = Math.max(12, Math.round(ms / 16));
  await page.mouse.move(x, y, { steps });
  return { x, y };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- scenes ----------
const scenes = {
  // Public site: home → scroll grid → open a post → scroll article
  site: {
    viewport: { width: 1280, height: 800 },
    cursor: true,
    async prepare(context) {
      // Dismiss the cookie banner (privacy-preserving choice) on a throwaway
      // page so the recorded tour is banner-free; choice persists in-context.
      const p = await context.newPage();
      await p.goto('https://www.gladlabs.io', { waitUntil: 'domcontentloaded' });
      const reject = p.getByRole('button', { name: /reject all/i }).first();
      await reject.waitFor({ state: 'visible', timeout: 8000 });
      await reject.click();
      await sleep(500);
      await p.close();
    },
    async run(page, shot) {
      await page.goto('https://www.gladlabs.io', { waitUntil: 'networkidle' });
      await sleep(2000);
      await shot('01-home');
      await smoothScroll(page, { by: 1250, ms: 2300 });
      await sleep(1200);
      await shot('02-grid');
      const card = page.locator('a[href*="/posts/"]').first();
      await card.waitFor({ state: 'visible', timeout: 10000 });
      await glide(page, card, 600);
      await sleep(350);
      await card.click();
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.mouse.move(1250, 780, { steps: 20 }); // park cursor out of the way
      await sleep(1800);
      await shot('03-article-top');
      await smoothScroll(page, { by: 1700, ms: 3000 });
      await sleep(1500);
      await shot('04-article-scrolled');
    },
  },

  // Operator console: live boot → dashboard tour
  console: {
    viewport: { width: 1440, height: 860 },
    cursor: true,
    initLocalStorage: {
      origin: 'http://localhost:8002',
      values: {
        px_client_id: process.env.PX_CLIENT_ID || '',
        px_client_secret: process.env.PX_CLIENT_SECRET || '',
      },
    },
    async run(page, shot) {
      await page.goto('http://localhost:8002/console/', { waitUntil: 'domcontentloaded' });
      await sleep(4000); // let live panels populate
      await shot('01-console-top');
      await smoothScroll(page, { by: 1500, ms: 2600, container: 'main.main' });
      await sleep(1100);
      await shot('02-console-mid');
      const trace = page.getByRole('button', { name: 'TRACE' }).first();
      await glide(page, trace, 600);
      await trace.click();
      await sleep(2200);
      await shot('04-trace');
      const map = page.getByRole('button', { name: 'MAP' }).first();
      await glide(page, map, 500);
      await map.click();
      await page.mouse.move(720, 820, { steps: 15 }); // park cursor low
      await sleep(3000); // animated data-flow edges
      await shot('05-map');
    },
  },

  // Grafana kiosk tour across three dashboards (anonymous viewer)
  grafana: {
    viewport: { width: 1440, height: 860 },
    cursor: false,
    async run(page, shot) {
      // Range per board: Postgres-backed boards can show 30d; Prometheus-backed
      // boards are capped by its ~15d retention, so 7d keeps the window full.
      const boards = [
        ['cost-analytics', 'cost', '30d'],
        ['pipeline-merged', 'pipeline', '30d'],
        ['hardware-power', 'hardware', '7d'],
      ];
      let i = 0;
      for (const [uid, label, range] of boards) {
        await page.goto(`http://localhost:3000/d/${uid}?kiosk&from=now-${range}&to=now`, {
          waitUntil: 'domcontentloaded',
        });
        await sleep(4500); // panels render
        await shot(`0${++i}-${label}`);
        await smoothScroll(page, { by: 800, ms: 2200 });
        await sleep(1300);
      }
    },
  },

  // Prefect: flow runs list → open a run
  prefect: {
    viewport: { width: 1440, height: 860 },
    cursor: true,
    async run(page, shot) {
      await page.goto('http://localhost:4200/runs', { waitUntil: 'networkidle' });
      await sleep(3500);
      await shot('01-runs');
      const row = page.locator('a[href*="/runs/flow-run/"]').first();
      await row.waitFor({ state: 'visible', timeout: 12000 });
      await glide(page, row, 800);
      await sleep(300);
      await row.click();
      await sleep(5000);
      await shot('02-flow-run');
      await smoothScroll(page, { by: 500, ms: 2500 });
      await sleep(1000);
      await shot('03-flow-run-detail');
    },
  },

  // Terminal typing demo rendered from terminal.html
  cli: {
    viewport: { width: 1000, height: 640 },
    cursor: false,
    async run(page, shot) {
      const tpl = path.join(ROOT, 'terminal.html');
      if (!fs.existsSync(tpl))
        throw new Error('terminal.html missing - capture *.out files and run build_terminal.py first (see README)');
      await page.goto('file://' + tpl, { waitUntil: 'networkidle' });
      // page signals completion by setting window.__done = true
      await sleep(800);
      await shot('01-terminal-start');
      await page.waitForFunction('window.__done === true', null, { timeout: 120000 });
      await sleep(1500);
      await shot('02-terminal-end');
    },
  },
};

// ---------- main ----------
(async () => {
  const name = process.argv[2];
  const scene = scenes[name];
  if (!scene) {
    console.error('unknown scene. available:', Object.keys(scenes).join(', '));
    process.exit(1);
  }
  fs.mkdirSync(VIDEO_DIR, { recursive: true });
  fs.mkdirSync(SHOTS_DIR, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: scene.viewport,
    deviceScaleFactor: 1,
    recordVideo: { dir: VIDEO_DIR, size: scene.viewport },
    colorScheme: 'dark',
  });

  if (scene.initLocalStorage) {
    const { origin, values } = scene.initLocalStorage;
    const seedPage = await context.newPage();
    // must be on the origin before touching its localStorage
    await seedPage.goto(origin + '/console/css/colors.css');
    await seedPage.evaluate((vals) => {
      for (const [k, v] of Object.entries(vals)) window.localStorage.setItem(k, v);
    }, values);
    await seedPage.close();
  }

  if (scene.prepare) await scene.prepare(context);

  const page = await context.newPage();
  if (scene.cursor) await page.addInitScript(CURSOR_INIT);

  const shot = async (label) => {
    await page.screenshot({ path: path.join(SHOTS_DIR, `${name}-${label}.png`) });
  };

  let failed = false;
  try {
    await scene.run(page, shot);
  } catch (e) {
    failed = true;
    console.error('scene error:', e.message);
    await shot('ERROR').catch(() => {});
  }

  const video = page.video();
  await context.close(); // flushes video
  await browser.close();
  if (video) {
    const p = await video.path();
    const dest = path.join(VIDEO_DIR, `${name}.webm`);
    fs.renameSync(p, dest);
    console.log('video:', dest);
  }
  process.exit(failed ? 2 : 0);
})();
