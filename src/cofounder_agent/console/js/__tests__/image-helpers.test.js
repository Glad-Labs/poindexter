'use strict';

// Contract tests for the draft-image parsing helpers
// (src/cofounder_agent/console/js/image-helpers.js) behind the approve
// drawer's Images action. Same vm harness as schedule-helpers.test.js: the
// REAL file evaluated in a Node vm context, no DOM, no build.
//
// The invariant pinned here: `inline:N` in the console MUST name the same
// image PostEditService rewrites for `--which inline:N`. The server numbers
// 1-based over src-carrying <img> tags (_IMG_TAG_RE, post_edit_service.py)
// in the latest pipeline_versions content — the same content the console
// parses via GET /api/tasks/{id}. If these tests and the server tests ever
// disagree about numbering, a console click regenerates the WRONG image.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// vm-realm arrays carry foreign prototypes, so deepStrictEqual against
// host literals fails on prototype identity. Normalize through JSON for
// structural comparison (same trick as schedule-helpers.test.js).
const j = (x) => JSON.parse(JSON.stringify(x));

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'image-helpers.js'),
  'utf8'
);

function load() {
  const sandbox = { console };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'image-helpers.js' });
  return sandbox.PXImages;
}

const H = load();

const BODY = [
  '# Post title',
  '',
  'Intro paragraph.',
  '',
  '## Cooling Systems',
  '',
  'Some prose about cooling.',
  '',
  '<img src="https://cdn.example/cool.png" alt="A liquid cooling loop" width="1024" height="1024" loading="lazy" />',
  '',
  'More prose.',
  '',
  '**Power Delivery**',
  '',
  '<IMG SRC="https://cdn.example/power.png">',
  '',
  '## Wrap-up',
  '',
  'Closing prose, no image.',
].join('\n');

// ── listImages: numbering + ordering ────────────────────────────

test('featured leads the list when set, inline images follow in body order', () => {
  const items = H.listImages(BODY, 'https://cdn.example/hero.png');
  assert.deepEqual(j(items.map((i) => i.which)), [
    'featured',
    'inline:1',
    'inline:2',
  ]);
  assert.equal(items[0].url, 'https://cdn.example/hero.png');
  assert.equal(items[1].url, 'https://cdn.example/cool.png');
  assert.equal(items[2].url, 'https://cdn.example/power.png');
});

test('no featured URL → inline numbering is UNCHANGED (starts at inline:1)', () => {
  // The server's inline:N never counts the featured image — the console
  // list must not renumber when the hero is absent.
  const items = H.listImages(BODY, null);
  assert.deepEqual(j(items.map((i) => i.which)), ['inline:1', 'inline:2']);
});

test('matches are case-insensitive and closing-tag-agnostic (server regex parity)', () => {
  // <IMG SRC=…> with no self-close matched above; a tag that never closes
  // still counts — _IMG_TAG_RE has no closing-`>` requirement either.
  const items = H.listImages('<img src="https://x/a.png"', null);
  assert.equal(items.length, 1);
  assert.equal(items[0].which, 'inline:1');
  assert.equal(items[0].url, 'https://x/a.png');
});

test('an <img> without src does not consume a number', () => {
  // Numbering parity: the server's replace path counts only src-carrying
  // tags, so a bare <img> must be invisible to N on this side too.
  const body =
    '<img class="deco">\n<img src="https://x/real.png" alt="real" />';
  const items = H.listImages(body, null);
  assert.equal(items.length, 1);
  assert.equal(items[0].which, 'inline:1');
  assert.equal(items[0].url, 'https://x/real.png');
});

test('empty / non-string content yields featured-only or nothing (no throw)', () => {
  assert.deepEqual(j(H.listImages('', null)), []);
  assert.deepEqual(j(H.listImages(undefined, null)), []);
  const items = H.listImages(null, 'https://x/hero.png');
  assert.equal(items.length, 1);
  assert.equal(items[0].which, 'featured');
});

// ── heading + alt context (prompt prefill inputs) ───────────────

test('each inline image carries its preceding heading (## and **bold** forms)', () => {
  const items = H.listImages(BODY, null);
  assert.equal(items[0].heading, 'Cooling Systems');
  assert.equal(items[1].heading, 'Power Delivery');
});

test('alt text is captured when present, null when absent', () => {
  const items = H.listImages(BODY, null);
  assert.equal(items[0].alt, 'A liquid cooling loop');
  assert.equal(items[1].alt, null);
});

test('an image before any heading has heading null', () => {
  const items = H.listImages('<img src="https://x/a.png" />\n\n## Later', null);
  assert.equal(items[0].heading, null);
});

// ── defaultPrompt: heading > alt > fallback, never invented ─────

test('defaultPrompt prefers heading, then alt, then the fallback', () => {
  assert.equal(
    H.defaultPrompt({ heading: 'Cooling Systems', alt: 'x' }, 'topic'),
    'Cooling Systems'
  );
  assert.equal(
    H.defaultPrompt({ heading: null, alt: 'A cooling loop' }, 'topic'),
    'A cooling loop'
  );
  assert.equal(H.defaultPrompt({ heading: null, alt: null }, 'topic'), 'topic');
});

test('defaultPrompt with nothing available returns "" (caller must require input)', () => {
  // feedback_no_dummy_data: the drawer disables Regenerate on an empty
  // prompt rather than inventing one here.
  assert.equal(H.defaultPrompt({ heading: null, alt: null }, ''), '');
  assert.equal(H.defaultPrompt(null, ''), '');
});
