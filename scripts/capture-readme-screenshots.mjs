#!/usr/bin/env node
/**
 * Re-bake the README marketing assets (docs/assets/readme/) from the live
 * local stack. Requires the operator stack up (Grafana :3000 anonymous
 * viewer) and system Chrome; uses the repo's Playwright install.
 *
 * Usage:
 *   node scripts/capture-readme-screenshots.mjs [--only banner,terminal,...]
 *
 * Shots: banner, terminal (runs `poindexter doctor` live), pipeline,
 * qa-reviewers, site. Review every image before committing — Grafana
 * panels can surface strings the public-mirror leak guard cannot see inside
 * pixels (hostnames, tailnet IPs).
 */
import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const outDir = join(repoRoot, 'docs', 'assets', 'readme');
mkdirSync(outDir, { recursive: true });

const only = (() => {
  const i = process.argv.indexOf('--only');
  return i === -1 ? null : new Set(process.argv[i + 1].split(','));
})();
const want = (name) => !only || only.has(name);

const GRAFANA = process.env.GRAFANA_URL || 'http://localhost:3000';
const VIEWPORT = { width: 1600, height: 900 };

function doctorOutput() {
  // Worktrees have no poetry venv — point POINDEXTER_REPO at the main checkout there.
  const pyRoot = process.env.POINDEXTER_REPO || repoRoot;
  try {
    return execFileSync(
      'poetry',
      [
        '-C',
        join(pyRoot, 'src', 'cofounder_agent'),
        'run',
        'poindexter',
        'doctor',
      ],
      {
        env: {
          ...process.env,
          POINDEXTER_API_URL:
            process.env.POINDEXTER_API_URL || 'http://localhost:8002',
        },
        encoding: 'utf8',
        timeout: 120_000,
      }
    );
  } catch (err) {
    // doctor exits 1 whenever any probe FAILs — the report on stdout is still good.
    if (err.stdout && /Health score/.test(err.stdout)) return err.stdout;
    console.error('poindexter doctor failed — is the stack up?', err.message);
    process.exit(1);
  }
}

const esc = (s) =>
  s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

/** Real `poindexter doctor` output inside a styled terminal window. */
function terminalHtml(raw) {
  // Keep it README-sized: score line, FAIL block, first OK lines, honest ellipsis.
  const lines = raw.replace(/\r/g, '').split('\n');
  const okStart = lines.findIndex((l) => l.startsWith('OK ('));
  const okTotal =
    okStart === -1
      ? 0
      : Number((lines[okStart].match(/OK \((\d+)\)/) || [])[1] || 0);
  const keptOk = 9;
  const kept =
    okStart === -1
      ? lines
      : [
          ...lines.slice(0, okStart + 1 + keptOk),
          `  … ${okTotal - keptOk} more probes OK`,
        ];
  const colored = kept
    .map((l) => {
      let cls = '';
      if (/^Health score/.test(l)) cls = 'score';
      else if (/^FAIL/.test(l)) cls = 'fail';
      else if (/^OK/.test(l)) cls = 'ok';
      else if (/^ {2}…/.test(l)) cls = 'dim';
      return `<span class="${cls}">${esc(l)}</span>`;
    })
    .join('\n');
  return `<!doctype html><meta charset="utf-8"><style>
  body{margin:0;background:#0d1117;display:flex;align-items:center;justify-content:center;height:100vh}
  .win{width:880px;background:#0a0f16;border:1px solid #1d2733;border-radius:12px;overflow:hidden;
       box-shadow:0 24px 70px rgba(0,0,0,.55);font-family:'SF Mono','Cascadia Code','JetBrains Mono',Menlo,Consolas,monospace}
  .bar{display:flex;align-items:center;gap:8px;padding:11px 14px;background:#111823;border-bottom:1px solid #1d2733}
  .dot{width:12px;height:12px;border-radius:50%}
  .title{margin-left:12px;color:#5b6b7c;font-size:12.5px}
  pre{margin:0;padding:18px 22px 22px;font-size:13.5px;line-height:1.52;color:#c9d5e1;white-space:pre-wrap}
  .prompt{color:#00e5ff;font-weight:600}
  .score{color:#e6edf3;font-weight:700}
  .fail{color:#ff7b72;font-weight:600}
  .ok{color:#3fdf8f;font-weight:600}
  .dim{color:#5b6b7c}
  </style><body><div class="win">
  <div class="bar"><span class="dot" style="background:#ff5f57"></span><span class="dot" style="background:#febc2e"></span><span class="dot" style="background:#28c840"></span><span class="title">poindexter — bash</span></div>
  <pre><span class="prompt">$ poindexter doctor</span>\n${colored}</pre></div>`;
}

/** Hero banner — brand cyan on dark, pipeline motif. */
function bannerHtml() {
  const stages = ['discover', 'research', 'write', 'review', 'publish'];
  const nodes = stages
    .map(
      (s, i) =>
        `<span class="node${i === 3 ? ' hot' : ''}">${s}</span>${
          i < stages.length - 1 ? '<span class="link"></span>' : ''
        }`
    )
    .join('');
  return `<!doctype html><meta charset="utf-8"><style>
  *{box-sizing:border-box}
  body{margin:0;width:1600px;height:420px;display:flex;align-items:center;
       background:radial-gradient(1200px 500px at 78% -10%,#07202b 0%,#050b10 55%),#050b10;
       background-color:#050b10;
       font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;position:relative;overflow:hidden}
  .grid{position:absolute;inset:0;background-image:radial-gradient(rgba(0,229,255,.10) 1px,transparent 1.4px);
        background-size:26px 26px;mask-image:linear-gradient(115deg,transparent 38%,#000 78%)}
  .wrap{position:relative;padding:0 96px;width:100%;max-width:1010px}
  h1{margin:0;font-size:84px;letter-spacing:-2.5px;color:#f2f7fa;font-weight:800}
  h1 .dot{color:#00e5ff}
  p{margin:14px 0 0;font-size:26px;color:#8fa3b3;font-weight:400;letter-spacing:.1px;max-width:700px}
  p b{color:#d7e3ec;font-weight:600}
  .chips{margin-top:30px;display:flex;gap:12px}
  .chip{font-size:15.5px;color:#00e5ff;border:1px solid rgba(0,229,255,.35);background:rgba(0,229,255,.07);
        padding:7px 16px;border-radius:999px;font-weight:600;letter-spacing:.2px}
  .pipe{position:absolute;right:72px;bottom:56px;display:flex;align-items:center}
  .node{font-family:'SF Mono',Menlo,Consolas,monospace;font-size:16px;color:#9fb4c4;
        border:1.5px solid #24455a;background:#0a1620;border-radius:9px;padding:10px 15px}
  .node.hot{color:#00e5ff;border-color:#00e5ff;box-shadow:0 0 26px rgba(0,229,255,.35)}
  .link{width:26px;height:1.5px;background:linear-gradient(90deg,#24455a,#00b3cc)}
  </style><body><div class="grid"></div><div class="wrap">
  <h1>Poindexter<span class="dot">.</span></h1>
  <p>The open-source AI content factory that runs on <b>your PC</b> — writes, reviews, and publishes on its own.</p>
  <div class="chips"><span class="chip">Local-first</span><span class="chip">Ollama-powered</span><span class="chip">$0 API costs</span><span class="chip">Apache 2.0</span></div>
  </div><div class="pipe">${nodes}</div>`;
}

const browser = await chromium.launch({ channel: 'chrome', headless: true });

async function shootUrl(
  name,
  url,
  {
    fullPage = false,
    wait = 7000,
    viewport = VIEWPORT,
    scale = 1,
    hide = [],
    scrollToText = null,
  } = {}
) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: scale });
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await page.waitForTimeout(wait);
  for (const sel of hide)
    await page
      .addStyleTag({ content: `${sel}{display:none !important}` })
      .catch(() => {});
  if (scrollToText) {
    // Grafana lazy-renders below-the-fold panels — scroll a section into view, let it settle.
    await page
      .getByText(scrollToText, { exact: true })
      .first()
      .scrollIntoViewIfNeeded()
      .catch(() => console.warn(`scrollToText miss: ${scrollToText}`));
    await page.waitForTimeout(4000);
  }
  const path = join(outDir, `${name}.png`);
  await page.screenshot({ path, fullPage });
  await page.close();
  console.log('wrote', path);
}

async function shootHtml(name, html, { width, height, scale = 2 } = {}) {
  const tmp = join(outDir, `.${name}.html`);
  writeFileSync(tmp, html);
  const page = await browser.newPage({
    viewport: { width, height },
    deviceScaleFactor: scale,
  });
  await page.goto(`file://${tmp}`);
  await page.waitForTimeout(400);
  const path = join(outDir, `${name}.png`);
  const el = await page.$('.win');
  if (el) await el.screenshot({ path });
  else await page.screenshot({ path });
  await page.close();
  const { unlinkSync } = await import('node:fs');
  unlinkSync(tmp);
  console.log('wrote', path);
}

if (want('banner'))
  await shootHtml('banner', bannerHtml(), { width: 1600, height: 420 });
if (want('terminal'))
  await shootHtml('terminal-doctor', terminalHtml(doctorOutput()), {
    width: 1000,
    height: 900,
  });
if (want('pipeline'))
  await shootUrl(
    'grafana-pipeline',
    `${GRAFANA}/d/pipeline-merged/pipeline?kiosk&theme=dark`,
    {
      viewport: { width: 1600, height: 1000 },
    }
  );
// Solo view of the "Avg Score by Reviewer" bargauge — the per-rail QA proof.
if (want('qa-reviewers'))
  await shootUrl(
    'grafana-qa-reviewers',
    `${GRAFANA}/d/qa-rails/qa-rails?viewPanel=7&kiosk&theme=dark&from=now-30d&to=now`,
    {
      viewport: { width: 1600, height: 700 },
    }
  );
if (want('site'))
  await shootUrl('site-gladlabs', 'https://www.gladlabs.io', {
    wait: 6000,
    hide: ['[aria-label="Cookie consent"]'],
  });

await browser.close();
console.log('done — review every PNG for private strings before committing.');
