// Unit tests for the sentry-relay Worker's pure helpers.
//
// The fetch handler is edge-runtime glue (origin allowlist, rate-limit binding,
// upstream fetch) verified via the README curl smoke tests. What genuinely
// needs locking down is the open-proxy guard: the relay must ONLY forward
// envelopes for known GlitchTip project IDs, and must fail closed when the
// allowlist is unset. Those are pure functions, tested here.

import { describe, expect, it } from 'vitest';

import { extractDsnParts, parseEnvelopeHeader, projectAllowed } from './index';

describe('parseEnvelopeHeader', () => {
  it('parses the first NDJSON line of an envelope', () => {
    const body =
      '{"event_id":"abc","dsn":"https://k@relay.example.com/42"}\n' +
      '{"type":"event"}\n' +
      '{"message":"boom"}';
    const header = parseEnvelopeHeader(body);
    expect(header).not.toBeNull();
    expect(header?.dsn).toBe('https://k@relay.example.com/42');
  });

  it('returns null for an empty body', () => {
    expect(parseEnvelopeHeader('')).toBeNull();
  });

  it('returns null when the header line is not valid JSON', () => {
    expect(parseEnvelopeHeader('not-json\n{"type":"event"}')).toBeNull();
  });
});

describe('extractDsnParts', () => {
  it('pulls host, projectId and publicKey from a DSN', () => {
    const parts = extractDsnParts('https://pubkey123@relay.example.com/7');
    expect(parts).toEqual({
      host: 'relay.example.com',
      projectId: '7',
      publicKey: 'pubkey123',
    });
  });

  it('returns null when the DSN has no project path', () => {
    expect(extractDsnParts('https://pubkey@relay.example.com')).toBeNull();
  });

  it('returns null for a non-URL string', () => {
    expect(extractDsnParts('clearly not a dsn')).toBeNull();
  });
});

describe('projectAllowed (open-proxy guard)', () => {
  it('allows a project id present in the allowlist', () => {
    expect(projectAllowed('7', '7,42,99')).toBe(true);
  });

  it('trims whitespace around allowlist entries', () => {
    expect(projectAllowed('42', ' 7, 42 , 99 ')).toBe(true);
  });

  it('rejects a project id not in the allowlist', () => {
    expect(projectAllowed('500', '7,42,99')).toBe(false);
  });

  it('FAILS CLOSED when the allowlist is empty (no open relay)', () => {
    expect(projectAllowed('7', '')).toBe(false);
    expect(projectAllowed('7', '   ')).toBe(false);
  });
});
