/**
 * `readRefSource` — the beacon's half of distribution attribution.
 *
 * The tag the backend places on every outbound link (`?utm_source=devto`,
 * `services/distribution_ref.py`) only becomes data if the beacon carries it.
 * Before this, ViewTracker sent `window.location.pathname` and dropped the
 * query string entirely, so a tagged link and an untagged one were
 * indistinguishable by the time they reached `page_views`.
 */

import { readRefSource } from '@/components/ViewTracker';

describe('readRefSource', () => {
  it('reads the utm_source the backend emits by default', () => {
    expect(readRefSource('?utm_source=devto&utm_medium=syndication')).toBe(
      'devto'
    );
  });

  it('accepts the shorter parameter names an operator may configure', () => {
    expect(readRefSource('?ref=bluesky')).toBe('bluesky');
    expect(readRefSource('?source=youtube')).toBe('youtube');
  });

  it('prefers utm_source when several are present', () => {
    expect(readRefSource('?ref=bluesky&utm_source=devto')).toBe('devto');
  });

  it('is empty for an untagged visit', () => {
    expect(readRefSource('')).toBe('');
    expect(readRefSource('?page=2')).toBe('');
  });

  it('normalises case so one surface is not two rows', () => {
    expect(readRefSource('?utm_source=DevTo')).toBe('devto');
  });

  it('drops anything that is not a recognisable surface token', () => {
    // This value reaches a GROUP BY and a Grafana legend, and the query string
    // is visitor-controlled — free text here is both a cardinality bomb and a
    // needless injection surface. Unrecognised reads as "untagged", which is
    // the honest answer.
    expect(
      readRefSource('?utm_source=' + encodeURIComponent("'; DROP TABLE"))
    ).toBe('');
    expect(readRefSource('?utm_source=' + 'a'.repeat(33))).toBe('');
    expect(readRefSource('?utm_source=-leading-dash')).toBe('');
    expect(
      readRefSource('?utm_source=' + encodeURIComponent('two words'))
    ).toBe('');
  });

  it('survives a malformed query string', () => {
    expect(readRefSource('%%%')).toBe('');
  });
});
