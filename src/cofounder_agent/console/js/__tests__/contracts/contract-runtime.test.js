'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  loadApiWithRecorder,
  assertRequest,
  requestMatches,
  pruneOpenApiSnapshot,
} = require('./contract-runtime.js');

test('loadApiWithRecorder captures a read surface request', async () => {
  const { api, calls } = loadApiWithRecorder(() => ({
    status: 200,
    payload: {},
  }));
  await api.seo();
  const nonToken = calls.filter((c) => !c.url.endsWith('/token'));
  assert.equal(nonToken.length, 1);
  assert.equal(nonToken[0].method, 'GET');
  assert.ok(nonToken[0].url.endsWith('/api/seo'));
});

test('loadApiWithRecorder parses a write surface body into the capture', async () => {
  const { api, calls } = loadApiWithRecorder(() => ({
    status: 200,
    payload: {},
  }));
  await api.reject('task_1');
  const call = calls.find((c) => c.url.includes('/reject'));
  assert.deepEqual(call.body, {
    reason: 'operator_rejected',
    feedback: '',
    allow_revisions: true,
  });
});

test('assertRequest throws (prefixed) on a path mismatch', () => {
  const call = { method: 'GET', url: '/api/seo', body: undefined };
  assert.throws(
    () => assertRequest(call, { method: 'GET', path: '/api/seooo' }, 'seo'),
    /\[seo\] path/
  );
});

test('requestMatches is the non-throwing form of assertRequest', () => {
  const call = { method: 'GET', url: '/api/seo?x=1', body: undefined };
  assert.equal(requestMatches(call, { method: 'GET', path: '/api/seo' }), true);
  assert.equal(
    requestMatches(call, { method: 'POST', path: '/api/seo' }),
    false
  );
});

test('assertRequest checks a Prometheus PromQL query', () => {
  const call = {
    method: 'GET',
    url: 'http://localhost:9091/api/v1/query?query=up',
    body: undefined,
  };
  assertRequest(call, { host: 'prometheus', query: 'up' }, 'promVector'); // no throw
  assert.throws(
    () =>
      assertRequest(call, { host: 'prometheus', query: 'down' }, 'promVector'),
    /\[promVector\] promql/
  );
});

const { validateAgainstSchema } = require('./contract-runtime.js');

const SNAP = {
  paths: {
    '/api/media-approval/pending': {
      get: {
        responses: {
          200: {
            content: {
              'application/json': {
                schema: { $ref: '#/components/schemas/MediaPendingResponse' },
              },
            },
          },
        },
      },
    },
    '/api/nomodel': { get: { responses: { 200: { description: 'ok' } } } },
  },
  components: {
    schemas: {
      MediaPendingResponse: {
        type: 'object',
        required: ['items', 'total'],
        properties: {
          total: { type: 'integer' },
          items: {
            type: 'array',
            items: { $ref: '#/components/schemas/MediaItem' },
          },
        },
      },
      MediaItem: {
        type: 'object',
        required: ['post_id', 'medium'],
        properties: { post_id: { type: 'string' }, medium: { type: 'string' } },
      },
    },
  },
};

test('validateAgainstSchema passes a conforming value (extra fields allowed)', () => {
  const value = {
    items: [{ post_id: 'p1', medium: 'video', extra: 1 }],
    total: 1,
    added_by_backend: true, // additive change must not break the console
  };
  validateAgainstSchema(
    value,
    SNAP,
    { path: '/api/media-approval/pending', method: 'get' },
    'mediaQueue'
  ); // no throw
});

test('validateAgainstSchema fails on a missing required key', () => {
  const value = { items: [{ medium: 'video' }], total: 1 }; // item missing post_id
  assert.throws(
    () =>
      validateAgainstSchema(
        value,
        SNAP,
        { path: '/api/media-approval/pending', method: 'get' },
        'mediaQueue'
      ),
    /\[mediaQueue\].*post_id/
  );
});

test('validateAgainstSchema fails on a wrong primitive type', () => {
  const value = { items: [], total: 'nope' }; // total should be a number
  assert.throws(
    () =>
      validateAgainstSchema(
        value,
        SNAP,
        { path: '/api/media-approval/pending', method: 'get' },
        'mediaQueue'
      ),
    /\[mediaQueue\].*total/
  );
});

test('validateAgainstSchema is a no-op when the endpoint has no response schema', () => {
  validateAgainstSchema(
    { anything: true },
    SNAP,
    { path: '/api/nomodel', method: 'get' },
    'nomodel'
  ); // no throw
});

test('validateAgainstSchema tolerates null values and empty arrays', () => {
  const value = { items: [], total: 0 };
  validateAgainstSchema(
    value,
    SNAP,
    { path: '/api/media-approval/pending', method: 'get' },
    'mediaQueue'
  ); // no throw
});

// ── pruneOpenApiSnapshot ────────────────────────────────────────────────────
// A full OpenAPI doc that models the exact drift the first nightly surfaced: an
// anchored path whose 2xx refs a success model, whose 422 refs the FastAPI error
// model, plus tags/params, and an unrelated path. The prune must keep only the
// success surface so an error-envelope change never churns a refresh PR.
const FULL = {
  openapi: '3.1.0',
  info: { title: 'X', version: '1', extra: 'dropme' },
  paths: {
    '/api/settings': {
      get: {
        tags: ['settings'],
        summary: 'List',
        parameters: [{ name: 'limit', in: 'query' }],
        responses: {
          200: {
            description: 'ok',
            content: {
              'application/json': {
                schema: { $ref: '#/components/schemas/SettingListResponse' },
              },
            },
          },
          422: {
            description: 'Validation Error',
            content: {
              'application/json': {
                schema: { $ref: '#/components/schemas/HTTPValidationError' },
              },
            },
          },
        },
      },
    },
    '/api/other': {
      get: { responses: { 200: { description: 'ok' } } },
    },
  },
  components: {
    schemas: {
      SettingListResponse: {
        type: 'object',
        properties: {
          settings: {
            type: 'array',
            items: { $ref: '#/components/schemas/SettingResponse' },
          },
        },
      },
      SettingResponse: {
        type: 'object',
        properties: { key: { type: 'string' } },
      },
      HTTPValidationError: {
        type: 'object',
        properties: {
          detail: {
            type: 'array',
            items: { $ref: '#/components/schemas/ValidationError' },
          },
        },
      },
      ValidationError: { type: 'object' },
      Unrelated: { type: 'object' },
    },
  },
};

test('pruneOpenApiSnapshot keeps only wanted paths', () => {
  const snap = pruneOpenApiSnapshot(FULL, new Set(['/api/settings']));
  assert.deepEqual(Object.keys(snap.paths), ['/api/settings']);
});

test('pruneOpenApiSnapshot keeps 2xx responses and drops 4xx/422', () => {
  const snap = pruneOpenApiSnapshot(FULL, ['/api/settings']);
  const codes = Object.keys(snap.paths['/api/settings'].get.responses);
  assert.deepEqual(codes, ['200']);
});

test('pruneOpenApiSnapshot strips tags / summary / parameters from operations', () => {
  const snap = pruneOpenApiSnapshot(FULL, ['/api/settings']);
  assert.deepEqual(Object.keys(snap.paths['/api/settings'].get), ['responses']);
});

test('pruneOpenApiSnapshot keeps the success-response component closure', () => {
  const snap = pruneOpenApiSnapshot(FULL, ['/api/settings']);
  const kept = Object.keys(snap.components.schemas);
  assert.ok(kept.includes('SettingListResponse'), 'success model kept');
  assert.ok(kept.includes('SettingResponse'), 'transitively-referenced kept');
});

test('pruneOpenApiSnapshot EXCLUDES error-only + unreferenced components', () => {
  const snap = pruneOpenApiSnapshot(FULL, ['/api/settings']);
  const kept = Object.keys(snap.components.schemas);
  assert.ok(
    !kept.includes('HTTPValidationError'),
    'error model (422-only) must not enter the snapshot'
  );
  assert.ok(
    !kept.includes('ValidationError'),
    'error model transitive dep must not enter the snapshot'
  );
  assert.ok(!kept.includes('Unrelated'), 'unreferenced component dropped');
});

test('pruneOpenApiSnapshot drops volatile top-level info fields', () => {
  const snap = pruneOpenApiSnapshot(FULL, ['/api/settings']);
  assert.deepEqual(Object.keys(snap.info).sort(), ['title', 'version']);
});
