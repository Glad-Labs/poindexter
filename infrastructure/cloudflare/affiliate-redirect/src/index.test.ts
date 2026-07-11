// Unit tests for the affiliate-redirect Worker's pure helpers.
//
// The fetch handler is edge-runtime glue (edge cache, AE writeDataPoint, 302)
// verified via the README curl smoke test. What genuinely needs locking down is
// the routing math: which path segment becomes the code, and that an unknown
// code resolves to null (→ HOME_URL fallback, never a broken redirect). Those
// are pure functions, tested here.

import { describe, expect, it } from 'vitest';

import { codeFromPath, resolveTarget } from './index';

describe('codeFromPath', () => {
  it('extracts the code from /go/<code>', () => {
    expect(codeFromPath('/go/mercury')).toBe('mercury');
  });
  it('extracts the code from a bare /<code> (subdomain routing)', () => {
    expect(codeFromPath('/mercury')).toBe('mercury');
  });
  it('returns empty string for /go/ and /', () => {
    expect(codeFromPath('/go/')).toBe('');
    expect(codeFromPath('/')).toBe('');
  });
  it('ignores trailing slashes and nested segments', () => {
    expect(codeFromPath('/go/mercury/')).toBe('mercury');
  });
});

describe('resolveTarget', () => {
  const map = { mercury: { url: 'https://mercury.com/r/x' } };

  it('resolves a known code to its url', () => {
    expect(resolveTarget(map, 'mercury')).toBe('https://mercury.com/r/x');
  });
  it('returns null for an unknown code', () => {
    expect(resolveTarget(map, 'nope')).toBeNull();
  });
  it('returns null against an empty map', () => {
    expect(resolveTarget({}, 'mercury')).toBeNull();
  });
});
