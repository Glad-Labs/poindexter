'use strict';
// Settings-category taxonomy: the console mock's CATEGORIES must stay ⊆ the 13
// canonical ids (drift guard against services/settings_categories.py), and
// deriveCategories must emit the live sidebar in canonical order.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { loadApiWithRecorder } = require('./contracts/contract-runtime.js');

// Mirror of services/settings_categories.py CATEGORIES (id, in order).
const CANON = [
  'identity',
  'pipeline',
  'content',
  'quality',
  'models',
  'media',
  'distribution',
  'cost',
  'observability',
  'self_healing',
  'plugins',
  'integrations',
  'infrastructure',
];

function loadSettingsData() {
  const code = fs.readFileSync(
    path.join(__dirname, '..', 'settings-data.js'),
    'utf8'
  );
  const sandbox = { window: {}, console };
  sandbox.window = sandbox;
  vm.runInNewContext(code, sandbox);
  return sandbox.window.PX_SETTINGS;
}

test('settings-data CATEGORIES ids are all canonical (drift guard)', () => {
  const px = loadSettingsData();
  const ids = px.categories.map((c) => c.id);
  for (const id of ids) {
    assert.ok(CANON.includes(id), `non-canonical category id: ${id}`);
  }
});

test('settings-data CATEGORIES are in canonical order', () => {
  const px = loadSettingsData();
  // spread rehomes the vm-sandbox array into this realm so deepStrictEqual
  // (node:assert/strict) compares by value, not by cross-realm prototype.
  const ids = [...px.categories].map((c) => c.id);
  const expected = CANON.filter((id) => ids.includes(id));
  assert.deepEqual(ids, expected);
});

test('every settings-data mock row has a canonical category', () => {
  const px = loadSettingsData();
  for (const s of px.settings) {
    assert.ok(
      CANON.includes(s.category),
      `mock row ${s.key} has non-canonical category ${s.category}`
    );
  }
});

test('listSettings derives the live sidebar in canonical order', async () => {
  // Categories arrive from the API in scrambled order; deriveCategories must
  // re-emit them by canonical sidebar index (identity < quality < media < infra).
  const items = [
    {
      id: 1,
      key: 'gpu_x',
      value: '1',
      category: 'infrastructure',
      data_type: 'string',
    },
    { id: 2, key: 'img_x', value: '1', category: 'media', data_type: 'string' },
    {
      id: 3,
      key: 'site_x',
      value: '1',
      category: 'identity',
      data_type: 'string',
    },
    {
      id: 4,
      key: 'qa_x',
      value: '1',
      category: 'quality',
      data_type: 'string',
    },
  ];
  const responder = ({ url }) =>
    String(url).includes('/api/settings')
      ? { payload: { items, total: items.length } }
      : {};
  const { api } = loadApiWithRecorder(
    responder,
    {},
    {
      preload: ['settings-data.js'],
    }
  );
  const d = await api.listSettings();
  const ids = [...d.categories].map((c) => c.id); // rehome from vm sandbox realm
  assert.deepEqual(ids, ['identity', 'quality', 'media', 'infrastructure']);
});
