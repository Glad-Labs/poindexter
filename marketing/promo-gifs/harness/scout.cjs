/* Scout the console: find scroll container, snapshot each top-nav view. */
const path = require('path');
const { chromium } = require('@playwright/test');
const ROOT = __dirname;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 860 },
    colorScheme: 'dark',
  });
  const seed = await context.newPage();
  await seed.goto('http://localhost:8002/console/css/colors.css');
  await seed.evaluate((vals) => {
    for (const [k, v] of Object.entries(vals)) window.localStorage.setItem(k, v);
  }, {
    px_client_id: process.env.PX_CLIENT_ID || '',
    px_client_secret: process.env.PX_CLIENT_SECRET || '',
  });
  await seed.close();

  const page = await context.newPage();
  await page.goto('http://localhost:8002/console/', { waitUntil: 'domcontentloaded' });
  await sleep(6000);

  // find scrollable containers
  const scrollers = await page.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll('*')) {
      if (el.scrollHeight > el.clientHeight + 200 && el.clientHeight > 300) {
        const cs = getComputedStyle(el);
        if (/(auto|scroll)/.test(cs.overflowY)) {
          out.push({
            sel:
              el.tagName.toLowerCase() +
              (el.id ? '#' + el.id : '') +
              (el.className && typeof el.className === 'string'
                ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.')
                : ''),
            sh: el.scrollHeight,
            ch: el.clientHeight,
          });
        }
      }
    }
    return out;
  });
  console.log('scrollers:', JSON.stringify(scrollers, null, 1));

  // snapshot each top-nav view
  for (const label of ['CHAT', 'TRACE', 'MAP', 'WALL']) {
    try {
      const btn = page.getByRole('button', { name: label, exact: false }).first();
      const alt = page.locator(`text=${label}`).first();
      const target = (await btn.count()) ? btn : alt;
      await target.click({ timeout: 4000 });
      await sleep(4000);
      await page.screenshot({ path: path.join(ROOT, 'shots', `scout-${label}.png`) });
      console.log('shot', label);
    } catch (e) {
      console.log('skip', label, e.message.split('\n')[0]);
    }
  }
  await context.close();
  await browser.close();
})();
